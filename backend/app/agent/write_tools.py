"""
写工具的预览与执行。

原则：
- 预览只读：只查询数据库和 Redis，不产生任何业务副作用；预览内容全部由服务端查询生成，
  不使用模型给出的任何文案（模型只能提供参数，参数也已经过 Pydantic 校验）；
- 执行前把预览阶段的全部校验重新跑一遍（目标是否存在、超级管理员、当前用户、数据范围），
  因为从预览到执行之间可能过了好几分钟，数据和权限都可能已经变化；
- 执行一律调用现有 service，不另写 SQL，不绕过 service 里的校验。

预览或执行不允许时抛 ServiceException，文案由服务端给出。
"""
from sqlalchemy.orm import Session

from app.core.constants import DEL_FLAG_NORMAL, STATUS_DISABLE, STATUS_NORMAL
from app.core.exceptions import ServiceException
from app.models.sys_dept import SysDept
from app.models.sys_user import SysUser
from app.services import logininfor_service, online_service, user_service

MSG_USER_NOT_FOUND = "用户不存在"
MSG_SELF = "不能对当前登录用户执行该操作"
MSG_SAME_STATUS = "用户已经是该状态，无需操作"
MSG_NOT_LOCKED = "该账号没有密码错误记录，无需解锁"
MSG_NO_SESSION = "该用户当前没有在线会话"

_STATUS_TEXT = {STATUS_NORMAL: "正常", STATUS_DISABLE: "停用"}


def _resolve_target(db: Session, scoped_user: dict, user_id: int | None = None, user_name: str | None = None) -> tuple[SysUser, str]:
    """
    按 ID 或用户名解析真实用户并做通用校验。unlock / force_logout 的现有路由按用户名操作、
    不校验数据范围（与若依一致，路由保持不变）；智能助手更严格：先解析成真实用户，
    再用当前工具的权限字符（scoped_user 里的 PERMISSION_CONTEXT_KEY）校验数据范围。
    """
    query = db.query(SysUser).filter(SysUser.del_flag == DEL_FLAG_NORMAL)
    query = query.filter(SysUser.user_id == user_id) if user_id is not None else query.filter(SysUser.user_name == user_name)
    user = query.first()
    if user is None:
        raise ServiceException(MSG_USER_NOT_FOUND)
    user_service.check_user_allowed(user.user_id)
    if user.user_id == scoped_user["user_id"]:
        raise ServiceException(MSG_SELF)
    user_service.check_user_data_scope(db, scoped_user, user.user_id)

    dept_name = ""
    if user.dept_id:
        dept = db.query(SysDept).filter(SysDept.dept_id == user.dept_id).first()
        dept_name = dept.dept_name if dept else ""
    return user, dept_name


def _target(user: SysUser, dept_name: str) -> dict:
    return {"userId": user.user_id, "userName": user.user_name, "nickName": user.nick_name, "deptName": dept_name}


def _sessions_of(user_name: str, operator_token: str | None) -> list[str]:
    # 排除当前操作者自己的会话：目标是别人时本来不会命中，这里是防御性的第二道检查
    return [t for t in online_service.list_session_token_ids(user_name) if t != operator_token]


# ---------------------------------------------------------------------------
# change_user_status
# ---------------------------------------------------------------------------

def preview_change_user_status(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    user, dept_name = _resolve_target(db, scoped_user, user_id=args.user_id)
    if user.status == args.status:
        raise ServiceException(MSG_SAME_STATUS)
    disable = args.status == STATUS_DISABLE
    return {
        "operation": "停用用户账号" if disable else "启用用户账号",
        "target": _target(user, dept_name),
        "currentState": _STATUS_TEXT.get(user.status, user.status),
        "afterState": _STATUS_TEXT[args.status],
        "risk": (
            "停用后该用户无法再登录。已经登录的会话不会自动下线，如需立即下线，请另外提交“强制下线”操作并单独审批。"
            if disable else "启用后该用户可以重新登录。"
        ),
    }


def execute_change_user_status(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    preview_change_user_status(db, scoped_user, args, operator_token)
    user_service.change_status(db, scoped_user, args.user_id, args.status, scoped_user["user_name"])
    return {"userId": args.user_id, "status": args.status}


# ---------------------------------------------------------------------------
# unlock_user_account
# ---------------------------------------------------------------------------

def preview_unlock_user_account(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    user, dept_name = _resolve_target(db, scoped_user, user_name=args.user_name)
    lock = logininfor_service.get_lock_status(user.user_name)
    if lock["errorCount"] == 0:
        raise ServiceException(MSG_NOT_LOCKED)
    current = f"密码错误 {lock['errorCount']} 次" + (f"，已锁定，剩余 {lock['remainSeconds']} 秒" if lock["locked"] else "，未锁定")
    return {
        "operation": "解除账号锁定",
        "target": _target(user, dept_name),
        "currentState": current,
        "afterState": "清除密码错误次数，可立即重新登录",
        "risk": "如果是他人在暴力破解该账号，解锁后对方可以继续尝试密码，请先确认登录失败来源。",
    }


def execute_unlock_user_account(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    preview_unlock_user_account(db, scoped_user, args, operator_token)
    logininfor_service.unlock_user(args.user_name)
    return {"userName": args.user_name, "unlocked": True}


# ---------------------------------------------------------------------------
# force_logout_user
# ---------------------------------------------------------------------------

def preview_force_logout_user(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    user, dept_name = _resolve_target(db, scoped_user, user_name=args.user_name)
    sessions = _sessions_of(user.user_name, operator_token)
    if not sessions:
        raise ServiceException(MSG_NO_SESSION)
    return {
        "operation": "强制下线",
        "target": _target(user, dept_name),
        "currentState": f"在线会话 {len(sessions)} 个",
        "afterState": "该用户的全部会话下线，需要重新登录",
        "risk": "只会让现有会话失效，不会停用账号，用户仍可重新登录；如需禁止登录请另外提交“停用账号”操作。",
    }


def execute_force_logout_user(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    user, _ = _resolve_target(db, scoped_user, user_name=args.user_name)
    # 执行时重新在服务端解析会话：预览之后新登录的会话也一并下线；tokenId 不进入返回结果
    sessions = _sessions_of(user.user_name, operator_token)
    if not sessions:
        raise ServiceException(MSG_NO_SESSION)
    for token_id in sessions:
        online_service.force_logout(token_id)
    return {"userName": user.user_name, "loggedOutSessions": len(sessions)}
