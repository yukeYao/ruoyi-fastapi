"""
StateGraph 定义：

    START -> agent -(无工具调用)----------------------------------> END
                  \\-(含只读调用)-> tools ---------------------------> agent
                  \\-(只有写调用)-> propose -(生成了 action)-> approval -> execute -> agent
                                           \\-(全部被拒绝)-------------------------> agent
    tools / propose 发现重复调用或连续无进展时，追加结束说明后直接 -> END（循环保护）

节点里不持有 db / login_user：登录态和模型从 runtime.context 取（不落盘），数据库每次临时开 Session。
"""
import hashlib
import json
import time

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent import approval, policy
from app.agent.checkpointer import get_checkpointer
from app.agent.context import AgentContext
from app.agent.serialization import tool_error
from app.agent.state import AgentState
from app.core.constants import AGENT_MAX_MODEL_STEPS, AGENT_MAX_STALL_ROUNDS, AGENT_MODEL_TIMEOUT_SECONDS

MSG_STEP_LIMIT = "本次问题的处理步数已达上限，请缩小问题范围后重新提问。"
MSG_TIME_LIMIT = "本次处理时间已达上限，请稍后继续提问。"


class ModelCallError(RuntimeError):
    """
    模型调用失败的脱敏异常。第三方 SDK 的异常信息可能带有部分 Key、请求头、上游返回的原文，
    而 LangGraph 会把节点异常的 repr 写进 checkpoint，logger.exception 也会打印异常链，
    所以只保留异常类型和 HTTP 状态码，并在 except 块之外抛出，不挂原异常（__cause__、__context__ 都为空）。
    """


def _describe_model_error(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    suffix = f"（HTTP {int(status_code)}）" if isinstance(status_code, int) else ""
    return f"模型调用失败：{type(exc).__name__}{suffix}"


def agent_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    ctx = runtime.context
    step_count = state.get("step_count", 0)
    if step_count >= AGENT_MAX_MODEL_STEPS:
        # 不再调用模型，直接给出固定回复结束本轮，防止"查询 -> 再查询"无限循环
        return {"messages": [AIMessage(content=MSG_STEP_LIMIT)]}
    if ctx.deadline and time.monotonic() + AGENT_MODEL_TIMEOUT_SECONDS > ctx.deadline:
        # 剩余时间不够一个模型超时：不再调用模型，尽量让运行在运行锁过期前结束（软时限，见 constants）
        return {"messages": [AIMessage(content=MSG_TIME_LIMIT)]}

    schemas = policy.bindable_tool_schemas(ctx.login_user)
    model = ctx.model.bind_tools(schemas) if schemas else ctx.model
    # 系统提示词每次拼在最前面，不写进 state：避免 checkpoint 里存多份，也方便以后修改提示词。
    # 能力边界按本次运行的登录态生成（可信的服务端上下文），权限变化后下一次运行立即生效
    boundary = policy.capability_boundary(ctx.login_user)
    system = policy.SYSTEM_PROMPT + ("\n\n" + boundary if boundary else "")
    prompt = [SystemMessage(content=system), *policy.trim_history(state["messages"])]
    error = ""
    try:
        response = model.invoke(prompt)
    except Exception as exc:
        error = _describe_model_error(exc)
    # 在 except 块外抛出：from None 只是不显示原异常，__context__ 里仍会挂着 SDK 异常（可能带 Key）
    if error:
        raise ModelCallError(error)
    return {"messages": [response], "step_count": step_count + 1}


# ---------------------------------------------------------------------------
# 循环保护：同一轮里重复相同调用、或连续没有得到新信息时，由服务端提前结束并说明原因，
# 而不是让模型一直调用到步数上限。只影响"何时停止"，不放宽任何权限或审批。
# ---------------------------------------------------------------------------

def _call_key(call: dict) -> str:
    return (call.get("name") or "") + ":" + json.dumps(call.get("args") or {}, ensure_ascii=False, sort_keys=True)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _repeated_ids(calls: list[dict], seen: set[str]) -> tuple[frozenset[str], list[str]]:
    """返回 (本轮之前或同一条消息里已经出现过的调用 id, 更新后的调用记录)"""
    keys, repeated = set(seen), set()
    for call in calls:
        key = _call_key(call)
        if key in keys:
            repeated.add(call.get("id") or "")
        keys.add(key)
    return frozenset(repeated), sorted(keys)


def _stop(ctx: AgentContext) -> AIMessage:
    return AIMessage(content=policy.stop_message(ctx.login_user))


def tools_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    last = state["messages"][-1]
    repeated, keys = _repeated_ids(last.tool_calls, set(state.get("call_keys") or []))
    results = policy.handle_tool_calls(runtime.context.login_user, last, skip_ids=repeated)

    # 有进展 = 拿到了本轮之前没见过的结果（参数不同但结果相同，例如连续空结果，也算没有进展）
    seen_hashes = set(state.get("result_hashes") or [])
    new_hashes = {_digest(str(m.content)) for m in results if m.tool_call_id not in repeated} - seen_hashes
    stall = 0 if new_hashes else state.get("stall_count", 0) + 1
    update = {"messages": results, "call_keys": keys, "result_hashes": sorted(seen_hashes | new_hashes), "stall_count": stall}
    if repeated or stall >= AGENT_MAX_STALL_ROUNDS:
        update["messages"] = [*results, _stop(runtime.context)]
    return update


def propose_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    last = state["messages"][-1]
    repeated, keys = _repeated_ids(last.tool_calls, set(state.get("call_keys") or []))
    if repeated:
        # 本轮已经提交过完全相同的写操作（例如用户刚拒绝，模型又提交）：不再生成审批，直接结束
        replies = [ToolMessage(content=tool_error(policy.MSG_REPEATED_CALL), tool_call_id=c["id"], name=c["name"],
                               status="error") for c in last.tool_calls]
        return {"messages": [*replies, _stop(runtime.context)], "call_keys": keys}
    return {**approval.propose(runtime.context, state["conversation_id"], last), "call_keys": keys}


def approval_node(state: AgentState) -> dict:
    """
    只调用 interrupt：恢复时本节点会从头重跑，所以这里不能写库或产生任何副作用，
    也不用 try/except 包住 interrupt（它靠抛出特殊异常暂停图）。
    resume 的值不被使用：批准与否以数据库里 agent_action 的状态为准，客户端无法通过 resume 夹带参数。
    """
    interrupt({"action_id": state.get("pending_action_id")})
    return {}


def execute_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    result = approval.execute(runtime.context, state["conversation_id"], state.get("pending_action_id"),
                              state.get("pending_tool_call_id"))
    try:
        executed = json.loads(result["messages"][0].content).get("executed") is True
    except (ValueError, AttributeError):
        executed = False
    if executed:
        # 数据已经变化：之后重新查询同样的条件会得到新结果，循环保护重新计数。
        # 拒绝、过期、失败不重置：模型不能借此把同一个写操作再提交一遍
        result.update({"call_keys": [], "result_hashes": [], "stall_count": 0})
    return result


def _ended(state: AgentState) -> bool:
    """循环保护已经追加了结束说明（一条没有工具调用的 AIMessage）"""
    last = state["messages"][-1]
    return isinstance(last, AIMessage) and not last.tool_calls


def route_after_tools(state: AgentState) -> str:
    return END if _ended(state) else "agent"


def route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    if not policy.has_tool_calls(last):
        return END
    return policy.route_tool_calls(last)


def route_after_propose(state: AgentState) -> str:
    if _ended(state):
        return END
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
    graph.add_conditional_edges("tools", route_after_tools, {"agent": "agent", END: END})
    graph.add_conditional_edges("propose", route_after_propose, {"approval": "approval", "agent": "agent", END: END})
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
