"""
StateGraph 定义（阶段 1：只读）：

    START -> agent -(无工具调用)-> END
                  \\-(有工具调用)-> tools -> agent

阶段 2 增加写操作分支：agent -> propose -> approval(interrupt) -> execute -> agent。
节点里不持有 db / login_user：登录态和模型从 runtime.context 取（不落盘），数据库每次工具调用临时开 Session。
"""
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agent import policy
from app.agent.checkpointer import get_checkpointer
from app.agent.context import AgentContext
from app.agent.state import AgentState
from app.core.constants import AGENT_MAX_MODEL_STEPS

MSG_STEP_LIMIT = "本次问题的处理步数已达上限，请缩小问题范围后重新提问。"


def agent_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    step_count = state.get("step_count", 0)
    if step_count >= AGENT_MAX_MODEL_STEPS:
        # 不再调用模型，直接给出固定回复结束本轮，防止"查询 -> 再查询"无限循环
        return {"messages": [AIMessage(content=MSG_STEP_LIMIT)]}

    login_user = runtime.context.login_user
    schemas = policy.bindable_tool_schemas(login_user)
    model = runtime.context.model.bind_tools(schemas) if schemas else runtime.context.model
    # 系统提示词每次拼在最前面，不写进 state：避免 checkpoint 里存多份，也方便以后修改提示词
    prompt = [SystemMessage(content=policy.SYSTEM_PROMPT), *policy.trim_history(state["messages"])]
    response = model.invoke(prompt)
    return {"messages": [response], "step_count": step_count + 1}


def tools_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    last = state["messages"][-1]
    return {"messages": policy.handle_tool_calls(runtime.context.login_user, last)}


def route_after_agent(state: AgentState) -> str:
    return "tools" if policy.has_tool_calls(state["messages"][-1]) else END


def build_graph(checkpointer):
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


_compiled: tuple = (None, None)  # (checkpointer, 编译后的图)


def get_graph():
    """按 checkpointer 实例缓存编译结果；测试模拟重启换了 checkpointer 后会重新编译（用 is 比较，不用 id()，防止地址复用）"""
    global _compiled
    checkpointer = get_checkpointer()
    if _compiled[0] is not checkpointer:
        _compiled = (checkpointer, build_graph(checkpointer))
    return _compiled[1]
