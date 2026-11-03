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
import logging
import uuid
from datetime import datetime

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.agent.context import build_context
from app.agent.graph import get_graph
from app.agent.serialization import tool_error
from app.core.constants import (
    AGENT_MAX_INPUT_CHARS,
    AGENT_RATE_LIMIT_KEY_PREFIX,
    AGENT_RATE_LIMIT_MAX_REQUESTS,
    AGENT_RATE_LIMIT_WINDOW_SECONDS,
    AGENT_RECURSION_LIMIT,
    AGENT_RUN_LOCK_KEY_PREFIX,
    AGENT_RUN_LOCK_SECONDS,
    AGENT_TITLE_CHARS,
    DATETIME_FORMAT,
)
from app.core.exceptions import ServiceException
from app.core.redis_client import redis_client
from app.models.agent_conversation import AgentConversation

logger = logging.getLogger("ruoyi.agent")

MSG_CONVERSATION_NOT_FOUND = "会话不存在"
MSG_BUSY = "上一个请求仍在处理中，请稍后再试"
MSG_RATE_LIMITED = "请求过于频繁，请稍后再试"
MSG_FAILED = "智能助手处理失败，请稍后重试"
MSG_INTERRUPTED_CALL = "上次处理意外中断，该调用未执行"

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


def get_messages(db: Session, login_user: dict, conversation_id: str) -> list[dict]:
    get_owned_conversation(db, login_user, conversation_id)
    snapshot = get_graph().get_state(_thread_config(conversation_id))
    return [_message_to_dict(m) for m in (snapshot.values or {}).get("messages", [])]


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


# ---------------------------------------------------------------------------
# 对话
# ---------------------------------------------------------------------------

def chat(db: Session, login_user: dict, conversation_id: str | None, message: str, model) -> dict:
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
        history = (graph.get_state(config).values or {}).get("messages", [])

        inputs = {
            "messages": [*_dangling_tool_responses(history), HumanMessage(content=message)],
            "conversation_id": conv_id,
            "step_count": 0,
        }
        try:
            result = graph.invoke(inputs, config, context=build_context(login_user, model))
        except GraphRecursionError:
            raise ServiceException("处理步数超过上限，请缩小问题范围后重试")
        except ServiceException:
            raise
        except Exception:
            logger.exception("智能助手运行失败：conversation=%s", conv_id)
            raise ServiceException(MSG_FAILED)

        conv.update_time = datetime.now()
        db.commit()

        new_messages = result["messages"][len(history):]
        reply = next((m.content for m in reversed(new_messages) if isinstance(m, AIMessage) and not m.tool_calls), "")
        return {
            "conversationId": conv_id,
            "reply": reply if isinstance(reply, str) else str(reply),
            "messages": [_message_to_dict(m) for m in new_messages],
        }
    finally:
        # 无论成功、业务异常还是意外异常都要释放，否则用户要等锁过期（几分钟）才能再用
        release_run_lock(user_id, lock_token)
