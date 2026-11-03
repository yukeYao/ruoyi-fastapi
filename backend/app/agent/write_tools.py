"""
写工具的预览与执行。

目标解析（为什么不让模型提供 user_id）：
  冒烟测试里真实模型没有先查询，直接猜了 user_id=1（实际是 admin），虽然被"不允许操作超级管理员"
  拦住，但说明"让模型提供 ID"这个设计本身就容易操作错对象。所以三个写工具的模型入参都只有
  user_name（登录账号），由服务端在预览阶段精确解析出唯一的目标用户，再把 {userId, userName}
  冻结到 agent_action（纳入参数哈希）；执行时只按冻结的 userId 操作，并确认它仍是预览时那个用户。

原则：
- 预览只读：只查询数据库和 Redis，不产生任何业务副作用；预览内容全部由服务端查询生成；
- 执行前把预览阶段的全部校验重新跑一遍，因为从预览到执行之间可能过了好几分钟；
- 执行一律调用现有 service，不另写 SQL，不绕过 service 里的校验；
- 错误文案明确写出目标用户名和拒绝原因，避免模型把错误归到别的原因上。
不存在和不在数据范围内用同一句话，不暴露范围外用户是否存在。
"""
from sqlalchemy.orm import Session

from app.core.constants import DEL_FLAG_NORMAL, STATUS_DISABLE, STATUS_NORMAL, SUPER_ADMIN_ID
from app.core.exceptions import ServiceException
from app.models.sys_dept import SysDept
from app.models.sys_user import SysUser
from app.services import logininfor_service, online_service, user_service

_STATUS_TEXT = {STATUS_NORMAL: "正常", STATUS_DISABLE: "停用"}


def msg_unavailable(user_name: str) -> str:
    return f"目标用户“{user_name}”不存在或不在你的数据范围内，操作未提交"


def msg_ambiguous(user_name: str) -> str:
    return f"目标用户“{user_name}”匹配到多个账号，无法确定操作对象，操作未提交"


def msg_super_admin(user_name: str) -> str:
    return f"目标用户“{user_name}”是超级管理员，不允许执行该操作"


def msg_self(user_name: str) -> str:
    return f"目标用户“{user_name}”是当前登录用户，不允许对自己执行该操作"


def msg_changed(user_name: str) -> str:
    return f"目标用户“{user_name}”在审批期间已被改名、删除或重建，操作未执行"


def msg_same_status(user_name: str, status: str) -> str:
    return f"目标用户“{user_name}”当前已是{_STATUS_TEXT.get(status, status)}状态，无需操作"


def msg_not_locked(user_name: str) -> str:
    return f"目标用户“{user_name}”没有密码错误记录，无需解锁"


def msg_no_session(user_name: str) -> str:
    return f"目标用户“{user_name}”当前没有在线会话"


def _check_target(db: Session, scoped_user: dict, user: SysUser, user_name: str) -> None:
    """
    通用校验，顺序有讲究：
    1. 当前用户：针对自己的提示不泄露任何信息，先判断；
    2. 数据范围：范围外和不存在用同一句话，必须在"超级管理员"之前判断，否则范围外的人
       也能通过不同的报错知道某个账号是超级管理员；
    3. 超级管理员。
    数据范围用当前工具的权限字符（scoped_user 里的 PERMISSION_CONTEXT_KEY）计算。
    unlock / force_logout 的现有路由按用户名操作、不校验数据范围（与若依一致，路由保持不变），
    智能助手更严格。
    """
    if user.user_id == scoped_user["user_id"]:
        raise ServiceException(msg_self(user_name))
    try:
        user_service.check_user_data_scope(db, scoped_user, user.user_id)
    except ServiceException:
        raise ServiceException(msg_unavailable(user_name))
    if user.user_id == SUPER_ADMIN_ID:
        raise ServiceException(msg_super_admin(user_name))


def resolve_target(db: Session, scoped_user: dict, user_name: str) -> tuple[SysUser, str]:
    """预览阶段：按用户名精确解析唯一的未删除用户，并做通用校验；返回 (用户, 部门名)"""
    users = db.query(SysUser).filter(SysUser.user_name == user_name, SysUser.del_flag == DEL_FLAG_NORMAL).limit(2).all()
    if not users:
        raise ServiceException(msg_unavailable(user_name))
    if len(users) > 1:
        # sys_user.user_name 没有唯一索引，只靠应用层校验；直接改库可能造成重名，宁可不操作
        raise ServiceException(msg_ambiguous(user_name))
    user = users[0]
    _check_target(db, scoped_user, user, user_name)
    return user, _dept_name(db, user)


def verify_frozen_target(db: Session, scoped_user: dict, target: dict) -> SysUser:
    """
    执行阶段：只按冻结的 userId 取用户，并确认它仍是预览时的那个用户：
    未删除、用户名没变。改名、删除、删除后重建同名用户都会失败，不会转而操作到别的用户。
    """
    user_name = target["userName"]
    user = db.query(SysUser).filter(SysUser.user_id == target["userId"]).first()
    if user is None or user.del_flag != DEL_FLAG_NORMAL or user.user_name != user_name:
        raise ServiceException(msg_changed(user_name))
    _check_target(db, scoped_user, user, user_name)
    return user


def _dept_name(db: Session, user: SysUser) -> str:
    if not user.dept_id:
        return ""
    dept = db.query(SysDept).filter(SysDept.dept_id == user.dept_id).first()
    return dept.dept_name if dept else ""


def _target(user: SysUser, dept_name: str) -> dict:
    """预览里的目标；其中 userId、userName 会被冻结到 agent_action.target_json"""
    return {"userId": user.user_id, "userName": user.user_name, "nickName": user.nick_name, "deptName": dept_name}


def _sessions_of(user: SysUser, operator_token: str | None) -> list[str]:
    # 按 userId 匹配会话（不只按用户名），删除后重建的同名用户的会话不会被误踢；
    # 再排除当前操作者自己的会话：目标是别人时本来不会命中，这里是防御性的第二道检查
    return [t for t in online_service.list_session_token_ids(user.user_name, user.user_id) if t != operator_token]


# ---------------------------------------------------------------------------
# change_user_status
# ---------------------------------------------------------------------------

def _status_preview(user: SysUser, dept_name: str, status: str) -> dict:
    if user.status == status:
        raise ServiceException(msg_same_status(user.user_name, status))
    disable = status == STATUS_DISABLE
    return {
        "operation": "停用用户账号" if disable else "启用用户账号",
        "target": _target(user, dept_name),
        "currentState": _STATUS_TEXT.get(user.status, user.status),
        "afterState": _STATUS_TEXT[status],
        "risk": (
            "停用后该用户无法再登录。已经登录的会话不会自动下线，如需立即下线，请另外提交“强制下线”操作并单独审批。"
            if disable else "启用后该用户可以重新登录。"
        ),
    }


def preview_change_user_status(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    user, dept_name = resolve_target(db, scoped_user, args.user_name)
    return _status_preview(user, dept_name, args.status)


def execute_change_user_status(db: Session, scoped_user: dict, args, operator_token: str | None, target: dict) -> dict:
    user = verify_frozen_target(db, scoped_user, target)
    _status_preview(user, "", args.status)
    user_service.change_status(db, scoped_user, user.user_id, args.status, scoped_user["user_name"])
    return {"userName": user.user_name, "status": args.status}


# ---------------------------------------------------------------------------
# unlock_user_account
# ---------------------------------------------------------------------------

def _unlock_preview(user: SysUser, dept_name: str) -> dict:
    lock = logininfor_service.get_lock_status(user.user_name)
    if lock["errorCount"] == 0:
        raise ServiceException(msg_not_locked(user.user_name))
    current = f"密码错误 {lock['errorCount']} 次" + (f"，已锁定，剩余 {lock['remainSeconds']} 秒" if lock["locked"] else "，未锁定")
    return {
        "operation": "解除账号锁定",
        "target": _target(user, dept_name),
        "currentState": current,
        "afterState": "清除密码错误次数，可立即重新登录",
        "risk": "如果是他人在暴力破解该账号，解锁后对方可以继续尝试密码，请先确认登录失败来源。",
    }


def preview_unlock_user_account(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    user, dept_name = resolve_target(db, scoped_user, args.user_name)
    return _unlock_preview(user, dept_name)


def execute_unlock_user_account(db: Session, scoped_user: dict, args, operator_token: str | None, target: dict) -> dict:
    user = verify_frozen_target(db, scoped_user, target)
    _unlock_preview(user, "")
    logininfor_service.unlock_user(user.user_name)
    return {"userName": user.user_name, "unlocked": True}


# ---------------------------------------------------------------------------
# force_logout_user
# ---------------------------------------------------------------------------

def preview_force_logout_user(db: Session, scoped_user: dict, args, operator_token: str | None) -> dict:
    user, dept_name = resolve_target(db, scoped_user, args.user_name)
    sessions = _sessions_of(user, operator_token)
    if not sessions:
        raise ServiceException(msg_no_session(user.user_name))
    return {
        "operation": "强制下线",
        "target": _target(user, dept_name),
        "currentState": f"在线会话 {len(sessions)} 个",
        "afterState": "该用户的全部会话下线，需要重新登录",
        "risk": "只会让现有会话失效，不会停用账号，用户仍可重新登录；如需禁止登录请另外提交“停用账号”操作。",
    }


def execute_force_logout_user(db: Session, scoped_user: dict, args, operator_token: str | None, target: dict) -> dict:
    user = verify_frozen_target(db, scoped_user, target)
    # 执行时重新在服务端解析会话：预览之后新登录的会话也一并下线；tokenId 不进入返回结果
    sessions = _sessions_of(user, operator_token)
    if not sessions:
        raise ServiceException(msg_no_session(user.user_name))
    for token_id in sessions:
        online_service.force_logout(token_id)
    return {"userName": user.user_name, "loggedOutSessions": len(sessions)}
