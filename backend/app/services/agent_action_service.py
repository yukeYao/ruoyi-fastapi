"""
写操作审批记录（agent_action）的数据访问与状态机。

状态：PENDING -> APPROVED / REJECTED / EXPIRED；APPROVED -> EXECUTING -> SUCCEEDED / FAILED。
所有状态变化都走 transition()：UPDATE ... WHERE action_id = ? AND status IN (旧状态)，
以 rowcount == 1 判断是否抢到。重复点击、并发审批、resume 重跑节点时，只有一个请求能完成同一次变化，
所以同一个 action 最多执行一次。
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.constants import ACTION_PENDING, AGENT_ACTION_EXPIRE_MINUTES, DATETIME_FORMAT
from app.core.exceptions import ServiceException
from app.models.agent_action import AgentAction

MSG_ACTION_NOT_FOUND = "操作不存在"


def canonical_json(data: dict) -> str:
    """参数的规范 JSON：键排序、无多余空格，同样的参数总是得到同样的字符串和哈希"""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_action(args_json: str, target_json: str) -> str:
    """参数和冻结目标一起哈希：任何一个在数据库里被改动，执行前都能发现"""
    payload = canonical_json({"args": json.loads(args_json), "target": json.loads(target_json)})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_action_once(
    db: Session,
    conversation_id: str,
    user_id: int,
    tool_call_id: str,
    tool_name: str,
    args_json: str,
    target: dict,
    preview: dict,
    perm: str,
) -> AgentAction:
    """
    幂等创建：(conversation_id, tool_call_id) 唯一，INSERT IGNORE 后再按唯一键查回。
    LangGraph 恢复时节点会从头重跑，重复调用也只会得到同一条 action，不会再建一条新的。
    """
    now = datetime.now()
    target_json = canonical_json(target)
    stmt = mysql_insert(AgentAction).values(
        action_id=uuid.uuid4().hex,
        conversation_id=conversation_id,
        user_id=user_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        args_json=args_json,
        target_json=target_json,
        args_hash=hash_action(args_json, target_json),
        preview_json=json.dumps(preview, ensure_ascii=False),
        perm=perm,
        status=ACTION_PENDING,
        expires_at=now + timedelta(minutes=AGENT_ACTION_EXPIRE_MINUTES),
        create_time=now,
        update_time=now,
    ).prefix_with("IGNORE")
    db.execute(stmt)
    db.commit()
    return (
        db.query(AgentAction)
        .filter(AgentAction.conversation_id == conversation_id, AgentAction.tool_call_id == tool_call_id)
        .one()
    )


def transition(db: Session, action_id: str, from_statuses: tuple[str, ...], to_status: str, **fields) -> bool:
    """条件更新状态：只有当前状态在 from_statuses 里才更新，返回是否由本次调用完成了变化"""
    count = (
        db.query(AgentAction)
        .filter(AgentAction.action_id == action_id, AgentAction.status.in_(from_statuses))
        .update({AgentAction.status: to_status, AgentAction.update_time: datetime.now(), **fields}, synchronize_session=False)
    )
    db.commit()
    return count == 1


def get_action(db: Session, action_id: str) -> AgentAction | None:
    # populate_existing：同一个 Session 里条件 UPDATE 之后要读到数据库里的最新状态，而不是缓存的旧对象
    return db.query(AgentAction).filter(AgentAction.action_id == action_id).populate_existing().first()


def get_owned_action(db: Session, login_user: dict, action_id: str) -> AgentAction:
    """只能读取/审批自己的 action；不存在和属于别人统一提示"操作不存在"，避免被用来探测"""
    action = get_action(db, action_id)
    if action is None or action.user_id != login_user["user_id"]:
        raise ServiceException(MSG_ACTION_NOT_FOUND)
    return action


def is_expired(action: AgentAction) -> bool:
    return datetime.now() > action.expires_at


def _fmt(value: datetime | None) -> str | None:
    return value.strftime(DATETIME_FORMAT) if value else None


def action_to_dict(action: AgentAction) -> dict:
    return {
        "actionId": action.action_id,
        "conversationId": action.conversation_id,
        "toolName": action.tool_name,
        "args": json.loads(action.args_json),
        "target": json.loads(action.target_json),
        "preview": json.loads(action.preview_json or "{}"),
        "perm": action.perm,
        "status": action.status,
        "expiresAt": _fmt(action.expires_at),
        "expired": action.status == ACTION_PENDING and is_expired(action),
        "decidedBy": action.decided_by,
        "decidedTime": _fmt(action.decided_time),
        "finishTime": _fmt(action.finish_time),
        "result": json.loads(action.result) if action.result else None,
        "error": action.error or "",
    }
