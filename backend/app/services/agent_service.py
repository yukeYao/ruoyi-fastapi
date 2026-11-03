"""
智能助手业务流程：会话归属、运行锁、频率限制、调用 graph、整理返回。

- conversation_id 只能由服务端生成：客户端不传表示新会话；传了就必须是自己已有的会话，
  否则一律提示"会话不存在"（不区分"不存在"和"属于别人"，避免被用来探测别人的会话 ID）。
  这样客户端无法指定一个新的 thread_id，也无法读取或续写别人的 checkpoint。
- 每个用户同一时间只允许一个运行（SET NX EX），模型调用期间重复点击不会并发跑两个图；
  释放时用 Lua 比较锁的值再删除，防止锁过期后误删别人重新拿到的锁。
- 频率限制用 Lua 把 INCR 和"首次设置过期时间"放在一个原子操作里：
  本机 Redis 5 不支持 EXPIRE NX，分两条命令执行的话，中间崩溃会留下永不过期的计数。
"""
import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.context import build_context
from app.agent.graph import get_graph
from app.agent.serialization import tool_error
from app.core.constants import (
    ACTION_APPROVED,
    ACTION_EXPIRED,
    ACTION_PENDING,
    ACTION_REJECTED,
    AGENT_MAX_INPUT_CHARS,
    AGENT_RATE_LIMIT_KEY_PREFIX,
    AGENT_RATE_LIMIT_MAX_REQUESTS,
    AGENT_RATE_LIMIT_WINDOW_SECONDS,
    AGENT_RECURSION_LIMIT,
    AGENT_RUN_LOCK_KEY_PREFIX,
    AGENT_RUN_LOCK_SECONDS,
    AGENT_TITLE_CHARS,
    DATETIME_FORMAT,
    DECISION_APPROVE,
    DECISION_EXPIRE,
    DECISION_REJECT,
)
from app.core.database import SessionLocal
from app.core.exceptions import ServiceException
from app.core.redis_client import redis_client
from app.models.agent_conversation import AgentConversation
from app.services import agent_action_service as actions

logger = logging.getLogger("ruoyi.agent")

MSG_CONVERSATION_NOT_FOUND = "会话不存在"
MSG_BUSY = "上一个请求仍在处理中，请稍后再试"
MSG_RATE_LIMITED = "请求过于频繁，请稍后再试"
MSG_FAILED = "智能助手处理失败，请稍后重试"
MSG_INTERRUPTED_CALL = "上次处理意外中断，该调用未执行"
MSG_HAS_PENDING = "当前会话有待审批的操作，请先批准或拒绝"
MSG_APPROVED_UNFINISHED = "当前会话有已批准但尚未完成的操作，请在审批卡片中重试"
MSG_ALREADY_DECIDED = "该操作已处理"
MSG_ALREADY_APPROVED = "该操作已批准，不能再拒绝"

_RATE_LIMIT_LUA = redis_client.register_script(
    """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return count
    """
)

_RELEASE_LOCK_LUA = redis_client.register_script(
    """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end
    return 0
    """
)


# ---------------------------------------------------------------------------
# 运行锁与频率限制
# ---------------------------------------------------------------------------

def check_rate_limit(user_id: int) -> None:
    count = _RATE_LIMIT_LUA(keys=[AGENT_RATE_LIMIT_KEY_PREFIX + str(user_id)], args=[AGENT_RATE_LIMIT_WINDOW_SECONDS])
    if int(count) > AGENT_RATE_LIMIT_MAX_REQUESTS:
        raise ServiceException(MSG_RATE_LIMITED)


def acquire_run_lock(user_id: int) -> str:
    token = uuid.uuid4().hex
    if not redis_client.set(AGENT_RUN_LOCK_KEY_PREFIX + str(user_id), token, nx=True, ex=AGENT_RUN_LOCK_SECONDS):
        raise ServiceException(MSG_BUSY)
    return token


def release_run_lock(user_id: int, token: str) -> None:
    _RELEASE_LOCK_LUA(keys=[AGENT_RUN_LOCK_KEY_PREFIX + str(user_id)], args=[token])


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

def get_owned_conversation(db: Session, login_user: dict, conversation_id: str) -> AgentConversation:
    conv = (
        db.query(AgentConversation)
        .filter(AgentConversation.conversation_id == conversation_id, AgentConversation.user_id == login_user["user_id"])
        .first()
    )
    if conv is None:
        raise ServiceException(MSG_CONVERSATION_NOT_FOUND)
    return conv


def _create_conversation(db: Session, login_user: dict, first_message: str) -> AgentConversation:
    now = datetime.now()
    conv = AgentConversation(
        conversation_id=uuid.uuid4().hex,
        user_id=login_user["user_id"],
        title=first_message[:AGENT_TITLE_CHARS],
        create_time=now,
        update_time=now,
    )
    db.add(conv)
    db.commit()
    return conv


def list_conversations(db: Session, login_user: dict, page_num: int, page_size: int) -> tuple[list[dict], int]:
    query = db.query(AgentConversation).filter(AgentConversation.user_id == login_user["user_id"])
    total = query.count()
    rows = (
        query.order_by(AgentConversation.update_time.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [
        {
            "conversationId": c.conversation_id,
            "title": c.title,
            "createTime": c.create_time.strftime(DATETIME_FORMAT) if c.create_time else None,
            "updateTime": c.update_time.strftime(DATETIME_FORMAT) if c.update_time else None,
        }
        for c in rows
    ], total


# ---------------------------------------------------------------------------
# 消息序列化（返回给前端）
# ---------------------------------------------------------------------------

def _message_to_dict(message: BaseMessage) -> dict:
    data = {"type": message.type, "content": message.content if isinstance(message.content, str) else str(message.content)}
    if isinstance(message, AIMessage):
        data["toolCalls"] = [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in message.tool_calls]
    if isinstance(message, ToolMessage):
        data["toolCallId"] = message.tool_call_id
        data["name"] = message.name
        data["status"] = message.status
    return data


def _thread_config(conversation_id: str) -> dict:
    return {"configurable": {"thread_id": conversation_id}, "recursion_limit": AGENT_RECURSION_LIMIT}


def _waiting_action_id(snapshot) -> str | None:
    """图是否停在审批的 interrupt 上；是的话返回等待中的 action_id"""
    if "approval" in (snapshot.next or ()):
        return (snapshot.values or {}).get("pending_action_id")
    return None


def _pending_action_dict(db: Session, snapshot) -> dict | None:
    action_id = _waiting_action_id(snapshot)
    action = actions.get_action(db, action_id) if action_id else None
    return actions.action_to_dict(action) if action else None


def get_messages(db: Session, login_user: dict, conversation_id: str) -> tuple[list[dict], dict | None]:
    get_owned_conversation(db, login_user, conversation_id)
    snapshot = get_graph().get_state(_thread_config(conversation_id))
    messages = [_message_to_dict(m) for m in (snapshot.values or {}).get("messages", [])]
    return messages, _pending_action_dict(db, snapshot)


def _dangling_tool_responses(messages: list[BaseMessage]) -> list[ToolMessage]:
    """
    上一次运行如果在"模型发出工具调用"之后意外中断（进程崩溃、模型超时等），历史里会留下没有
    ToolMessage 的 tool_call，OpenAI 协议下下一轮请求会直接报错。新消息进来前补上"未执行"的回复。
    """
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    result = []
    for m in messages:
        if isinstance(m, AIMessage):
            for call in [*m.tool_calls, *m.invalid_tool_calls]:
                if call.get("id") and call["id"] not in answered:
                    result.append(ToolMessage(content=tool_error(MSG_INTERRUPTED_CALL), tool_call_id=call["id"],
                                              name=call.get("name") or "unknown", status="error"))
    return result


def _invoke(graph, graph_input, config: dict, ctx) -> None:
    """
    运行或恢复图。durability="sync"：每一步的 checkpoint 写完才进入下一步，
    execute 节点完成后即使后续模型调用失败，恢复时也不会因为 checkpoint 没写上而重跑 execute。
    """
    try:
        graph.invoke(graph_input, config, context=ctx, durability="sync")
    except GraphRecursionError:
        raise ServiceException("处理步数超过上限，请缩小问题范围后重试")
    except ServiceException:
        raise
    except Exception:
        logger.exception("智能助手运行失败：thread=%s", config["configurable"]["thread_id"])
        raise ServiceException(MSG_FAILED)


def _resume(graph, config: dict, ctx, decision: str) -> None:
    # resume 的值只是给图看的提示；execute 节点只认数据库里的 action 状态
    _invoke(graph, Command(resume=decision), config, ctx)


def _run_result(db: Session, graph, config: dict, conv_id: str, history_len: int) -> dict:
    snapshot = graph.get_state(config)
    new_messages = (snapshot.values or {}).get("messages", [])[history_len:]
    reply = next((m.content for m in reversed(new_messages) if isinstance(m, AIMessage) and not m.tool_calls), "")
    return {
        "conversationId": conv_id,
        "reply": reply if isinstance(reply, str) else str(reply),
        "messages": [_message_to_dict(m) for m in new_messages],
        # 停在审批上时，前端用它渲染审批卡片；预览取自数据库（服务端生成），不是模型输出
        "pendingAction": _pending_action_dict(db, snapshot),
    }


def _settle_waiting_action(db: Session, graph, config: dict, ctx) -> None:
    """
    会话停在审批上时收到新消息：
    - 待审批且未过期：拒绝新消息，必须先批准或拒绝（否则审批悬空时模型会继续操作）；
    - 待审批但已过期：先改成 EXPIRED 并恢复图（execute 回复"已过期"），再处理新消息；
    - 已批准但上次恢复失败：不能在对话里顺带执行写操作，提示回到审批卡片重试；
    - 其他（已拒绝/已过期/已执行/执行中断）：恢复图，execute 只汇报状态、不会执行。
    """
    action_id = _waiting_action_id(graph.get_state(config))
    if not action_id:
        return
    action = actions.get_action(db, action_id)
    if action is not None and action.status == ACTION_PENDING:
        if not actions.is_expired(action):
            raise ServiceException(MSG_HAS_PENDING)
        actions.transition(db, action_id, (ACTION_PENDING,), ACTION_EXPIRED)
        action = actions.get_action(db, action_id)
    if action is not None and action.status == ACTION_APPROVED:
        raise ServiceException(MSG_APPROVED_UNFINISHED)
    _resume(graph, config, ctx, DECISION_EXPIRE)


# ---------------------------------------------------------------------------
# 一次运行 = 预检与加锁（prepare）-> 执行图（普通请求 invoke / SSE stream）-> 整理结果（finish）-> 释放锁
# 拆开是为了让普通接口和 SSE 接口共用同一套校验：SSE 在开始推流之前完成全部预检，
# 未登录、无权限、会话不存在、正忙等错误仍按 HTTP 200 + JSON 的 code/msg 返回，前端统一处理。
# ---------------------------------------------------------------------------

@dataclass
class PreparedRun:
    user_id: int
    lock_token: str
    conversation_id: str
    graph: Any
    config: dict
    ctx: Any
    history_len: int
    graph_input: Any
    # 审批运行：被审批的 action，finish 时把它的最新状态一并返回
    action_id: str | None = None
    decision: str | None = None


def release_run(run: PreparedRun) -> None:
    # Lua 比较锁的值再删除，重复调用无害（SSE 的生成器和兜底的后台任务都会调用）
    release_run_lock(run.user_id, run.lock_token)


def prepare_chat(db: Session, login_user: dict, conversation_id: str | None, message: str, model,
                 client_ip: str = "") -> PreparedRun:
    message = (message or "").strip()
    if not message:
        raise ServiceException("消息不能为空")
    if len(message) > AGENT_MAX_INPUT_CHARS:
        raise ServiceException(f"消息不能超过 {AGENT_MAX_INPUT_CHARS} 个字符")

    user_id = login_user["user_id"]
    check_rate_limit(user_id)
    # 先校验会话归属再拿锁：别人的会话直接拒绝，不占用锁
    conv = get_owned_conversation(db, login_user, conversation_id) if conversation_id else None

    lock_token = acquire_run_lock(user_id)
    try:
        if conv is None:
            conv = _create_conversation(db, login_user, message)
        conv_id = conv.conversation_id
        graph = get_graph()
        config = _thread_config(conv_id)
        ctx = build_context(login_user, model, client_ip)
        history_len = len((graph.get_state(config).values or {}).get("messages", []))

        _settle_waiting_action(db, graph, config, ctx)
        settled = (graph.get_state(config).values or {}).get("messages", [])
        inputs = {
            "messages": [*_dangling_tool_responses(settled), HumanMessage(content=message)],
            "conversation_id": conv_id,
            "step_count": 0,
            "pending_action_id": None,
            "pending_tool_call_id": None,
        }
        return PreparedRun(user_id, lock_token, conv_id, graph, config, ctx, history_len, inputs)
    except BaseException:
        release_run_lock(user_id, lock_token)
        raise


def prepare_decide(db: Session, login_user: dict, action_id: str, decision: str, model,
                   client_ip: str = "") -> PreparedRun:
    """
    批准或拒绝的预检。只接受 action_id 和决定：参数、预览都以数据库为准，不能修改。

    流程：校验归属 -> 拿运行锁 -> 条件 UPDATE 完成 PENDING -> APPROVED/REJECTED/EXPIRED
    -> 图确实停在这个 action 上时才允许恢复（execute 节点负责 APPROVED -> EXECUTING）。
    - 重复请求：状态已经变化且图不再等待，直接提示"已处理"，不会再次执行；
    - 已批准但上次 resume 失败（图仍停在审批上）：同一用户再次批准会继续恢复，
      execute 用条件 UPDATE 抢执行权，最多执行一次；
    - 停在 EXECUTING（执行中途崩溃）：恢复后 execute 只汇报"结果未知，需人工确认"，不自动重试。
    """
    user_id = login_user["user_id"]
    check_rate_limit(user_id)
    action = actions.get_owned_action(db, login_user, action_id)
    conv = get_owned_conversation(db, login_user, action.conversation_id)

    lock_token = acquire_run_lock(user_id)
    try:
        graph = get_graph()
        config = _thread_config(conv.conversation_id)
        ctx = build_context(login_user, model, client_ip)
        snapshot = graph.get_state(config)
        history_len = len((snapshot.values or {}).get("messages", []))
        waiting = _waiting_action_id(snapshot) == action_id

        # 拿到锁之后重新读状态：等锁期间可能已被同一用户的另一个请求处理
        action = actions.get_action(db, action_id)
        decided = {"decided_by": login_user["user_name"], "decided_time": datetime.now()}
        if action.status == ACTION_PENDING:
            if actions.is_expired(action):
                actions.transition(db, action_id, (ACTION_PENDING,), ACTION_EXPIRED)
            elif decision == DECISION_APPROVE:
                actions.transition(db, action_id, (ACTION_PENDING,), ACTION_APPROVED, **decided)
            else:
                actions.transition(db, action_id, (ACTION_PENDING,), ACTION_REJECTED, **decided)
        elif decision == DECISION_REJECT and action.status == ACTION_APPROVED:
            raise ServiceException(MSG_ALREADY_APPROVED)

        if not waiting:
            # 图已经不在等这个 action：之前的请求已经处理完，重复点击不做任何事
            raise ServiceException(MSG_ALREADY_DECIDED)
        # resume 的值只是给图看的提示；execute 节点只认数据库里的 action 状态
        return PreparedRun(user_id, lock_token, conv.conversation_id, graph, config, ctx, history_len,
                           Command(resume=decision), action_id=action_id, decision=decision)
    except BaseException:
        release_run_lock(user_id, lock_token)
        raise


def finish_run(db: Session, run: PreparedRun) -> dict:
    """整理返回：状态一律取自 checkpoint 和 agent_action，不解析模型回复"""
    db.query(AgentConversation).filter(AgentConversation.conversation_id == run.conversation_id).update(
        {AgentConversation.update_time: datetime.now()}, synchronize_session=False
    )
    db.commit()
    result = _run_result(db, run.graph, run.config, run.conversation_id, run.history_len)
    if run.action_id:
        result["action"] = actions.action_to_dict(actions.get_action(db, run.action_id))
    return result


# ---------------------------------------------------------------------------
# 普通请求
# ---------------------------------------------------------------------------

def chat(db: Session, login_user: dict, conversation_id: str | None, message: str, model, client_ip: str = "") -> dict:
    run = prepare_chat(db, login_user, conversation_id, message, model, client_ip)
    try:
        _invoke(run.graph, run.graph_input, run.config, run.ctx)
        return finish_run(db, run)
    finally:
        # 无论成功、业务异常还是意外异常都要释放，否则用户要等锁过期（几分钟）才能再用
        release_run(run)


def get_action_detail(db: Session, login_user: dict, action_id: str) -> dict:
    """只读：过期的 PENDING 在返回里标记 expired=true，真正改状态留给审批/对话接口（持锁时）做"""
    return actions.action_to_dict(actions.get_owned_action(db, login_user, action_id))


def decide(db: Session, login_user: dict, action_id: str, decision: str, model, client_ip: str = "") -> dict:
    run = prepare_decide(db, login_user, action_id, decision, model, client_ip)
    try:
        _resume(run.graph, run.config, run.ctx, decision)
        return finish_run(db, run)
    finally:
        release_run(run)


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def stream_run(run: PreparedRun) -> Iterator[str]:
    """
    以 SSE 推送一次运行的过程。同步生成器：Starlette 在线程池里迭代，图和 service 保持同步写法。
    事件：
    - step：某个节点完成，附带这一步新增的消息（工具调用、工具结果），用于展示工具步骤；
    - token：模型输出的文本片段（只取 agent 节点、只取文本，不含工具调用参数）；
    - approval：图停在审批上，附带服务端生成的 pendingAction（预览来自数据库，不是模型输出）；
    - final：运行结束，内容与普通接口的返回一致（状态取自 agent_action）；
    - error：运行中出错，{code, msg}。开始推流之前的错误不走这里，仍是 HTTP 200 + JSON。
    推流开始后客户端断开时生成器被关闭，finally 里释放运行锁。
    """
    db = SessionLocal()
    try:
        try:
            for mode, chunk in run.graph.stream(run.graph_input, run.config, context=run.ctx, durability="sync",
                                                stream_mode=["updates", "messages"]):
                if mode == "messages":
                    message, metadata = chunk
                    if (metadata.get("langgraph_node") == "agent" and isinstance(message, AIMessageChunk)
                            and isinstance(message.content, str) and message.content):
                        yield _sse("token", {"text": message.content})
                    continue
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        yield _sse("approval", {"pendingAction": _pending_action_dict(db, run.graph.get_state(run.config))})
                    else:
                        messages = update.get("messages", []) if isinstance(update, dict) else []
                        yield _sse("step", {"node": node, "messages": [_message_to_dict(m) for m in messages]})
        except GraphRecursionError:
            yield _sse("error", {"code": 500, "msg": "处理步数超过上限，请缩小问题范围后重试"})
            return
        except ServiceException as exc:
            yield _sse("error", {"code": exc.code, "msg": exc.message})
            return
        except Exception:
            logger.exception("智能助手运行失败（SSE）：thread=%s", run.conversation_id)
            yield _sse("error", {"code": 500, "msg": MSG_FAILED})
            return
        yield _sse("final", finish_run(db, run))
    finally:
        release_run(run)
        db.close()
