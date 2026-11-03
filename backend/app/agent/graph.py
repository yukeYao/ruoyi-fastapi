"""
StateGraph 定义：

    START -> agent -(无工具调用)----------------------------------> END
                  \\-(含只读调用)-> tools ---------------------------> agent
                  \\-(只有写调用)-> propose -(生成了 action)-> approval -> execute -> agent
                                           \\-(全部被拒绝)-------------------------> agent

节点里不持有 db / login_user：登录态和模型从 runtime.context 取（不落盘），数据库每次临时开 Session。
"""
import time

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent import approval, policy
from app.agent.checkpointer import get_checkpointer
from app.agent.context import AgentContext
from app.agent.state import AgentState
from app.core.constants import AGENT_MAX_MODEL_STEPS, AGENT_MODEL_TIMEOUT_SECONDS

MSG_STEP_LIMIT = "本次问题的处理步数已达上限，请缩小问题范围后重新提问。"
MSG_TIME_LIMIT = "本次处理时间已达上限，请稍后继续提问。"


def agent_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    ctx = runtime.context
    step_count = state.get("step_count", 0)
    if step_count >= AGENT_MAX_MODEL_STEPS:
        # 不再调用模型，直接给出固定回复结束本轮，防止"查询 -> 再查询"无限循环
        return {"messages": [AIMessage(content=MSG_STEP_LIMIT)]}
    if ctx.deadline and time.monotonic() + AGENT_MODEL_TIMEOUT_SECONDS > ctx.deadline:
        # 剩余时间不够一次模型调用：停止，保证整次运行在运行锁过期前结束
        return {"messages": [AIMessage(content=MSG_TIME_LIMIT)]}

    schemas = policy.bindable_tool_schemas(ctx.login_user)
    model = ctx.model.bind_tools(schemas) if schemas else ctx.model
    # 系统提示词每次拼在最前面，不写进 state：避免 checkpoint 里存多份，也方便以后修改提示词
    prompt = [SystemMessage(content=policy.SYSTEM_PROMPT), *policy.trim_history(state["messages"])]
    response = model.invoke(prompt)
    return {"messages": [response], "step_count": step_count + 1}


def tools_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    return {"messages": policy.handle_tool_calls(runtime.context.login_user, state["messages"][-1])}


def propose_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    return approval.propose(runtime.context, state["conversation_id"], state["messages"][-1])


def approval_node(state: AgentState) -> dict:
    """
    只调用 interrupt：恢复时本节点会从头重跑，所以这里不能写库或产生任何副作用，
    也不用 try/except 包住 interrupt（它靠抛出特殊异常暂停图）。
    resume 的值不被使用：批准与否以数据库里 agent_action 的状态为准，客户端无法通过 resume 夹带参数。
    """
    interrupt({"action_id": state.get("pending_action_id")})
    return {}


def execute_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    return approval.execute(runtime.context, state["conversation_id"], state.get("pending_action_id"),
                            state.get("pending_tool_call_id"))


def route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    if not policy.has_tool_calls(last):
        return END
    return policy.route_tool_calls(last)


def route_after_propose(state: AgentState) -> str:
    return "approval" if state.get("pending_action_id") else "agent"


def build_graph(checkpointer):
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("propose", propose_node)
    graph.add_node("approval", approval_node)
    graph.add_node("execute", execute_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "propose": "propose", END: END})
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("propose", route_after_propose, {"approval": "approval", "agent": "agent"})
    graph.add_edge("approval", "execute")
    graph.add_edge("execute", "agent")
    return graph.compile(checkpointer=checkpointer)


_compiled: tuple = (None, None)  # (checkpointer, 编译后的图)


def get_graph():
    """按 checkpointer 实例缓存编译结果；测试模拟重启换了 checkpointer 后会重新编译（用 is 比较，不用 id()，防止地址复用）"""
    global _compiled
    checkpointer = get_checkpointer()
    if _compiled[0] is not checkpointer:
        _compiled = (checkpointer, build_graph(checkpointer))
    return _compiled[1]
