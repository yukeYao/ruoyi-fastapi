"""
登录核心逻辑，对应若依的 SysLoginService + UserDetailsServiceImpl + SysPasswordService。

校验顺序严格对照 Java 版（见 UserDetailsServiceImpl.loadUserByUsername +
SysPasswordService.validate）：
  验证码 -> 用户名/密码非空与长度 -> 用户是否存在 -> 是否已删除 -> 是否停用
  -> 密码错误次数是否已锁定 -> 密码是否匹配
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.constants import (
    ADMIN_ROLE_KEY,
    ALL_PERMISSION,
    CAPTCHA_CODE_KEY_PREFIX,
    DEL_FLAG_DELETED,
    DEL_FLAG_NORMAL,
    PASSWORD_LOCK_MINUTES,
    PASSWORD_MAX_RETRY_COUNT,
    PWD_ERR_CNT_KEY_PREFIX,
    STATUS_DISABLE,
    STATUS_NORMAL,
    SUPER_ADMIN_ID,
    SUPER_ADMIN_ROLE_ID,
)
from app.core.exceptions import ServiceException
from app.core.redis_client import redis_client
from app.core.request_utils import get_login_location
from app.core.response import ajax_success, model_to_camel_dict
from app.core.security import verify_password
from app.models.sys_dept import SysDept
from app.models.sys_menu import SysMenu
from app.models.sys_role import SysRole
from app.models.sys_role_menu import SysRoleMenu
from app.models.sys_user import SysUser
from app.models.sys_user_role import SysUserRole
from app.services import captcha_service, logininfor_service

LOGIN_FAIL = "1"
LOGIN_SUCCESS = "0"


def _log(db: Session, username: str, status: str, msg: str, ip: str, browser: str, os_name: str) -> None:
    logininfor_service.record_login_info(db, username, status, msg, ip, get_login_location(ip), browser, os_name)


def validate_captcha(db: Session, username: str, code: str | None, uuid_: str | None, ip: str, browser: str, os_name: str) -> None:
    if not captcha_service.is_captcha_enabled(db):
        return

    key = CAPTCHA_CODE_KEY_PREFIX + (uuid_ or "")
    answer = redis_client.get(key)
    if answer is None:
        _log(db, username, LOGIN_FAIL, "验证码已失效", ip, browser, os_name)
        raise ServiceException("验证码已失效")

    redis_client.delete(key)  # 一次性：无论对错都立即删除
    if not code or code.lower() != answer.lower():
        _log(db, username, LOGIN_FAIL, "验证码错误", ip, browser, os_name)
        raise ServiceException("验证码错误")


def login_pre_check(db: Session, username: str, password: str, ip: str, browser: str, os_name: str) -> None:
    if not username or not password:
        _log(db, username, LOGIN_FAIL, "* 必须填写", ip, browser, os_name)
        raise ServiceException("用户不存在/密码错误")
    if len(password) < 5 or len(password) > 20:
        _log(db, username, LOGIN_FAIL, "用户密码不在指定范围", ip, browser, os_name)
        raise ServiceException("用户不存在/密码错误")
    if len(username) < 2 or len(username) > 20:
        _log(db, username, LOGIN_FAIL, "用户名不在指定范围", ip, browser, os_name)
        raise ServiceException("用户不存在/密码错误")


def authenticate(db: Session, username: str, password: str, ip: str, browser: str, os_name: str) -> SysUser:
    # 对应 selectUserByUserName：排除已删除用户，否则删除后重建的同名用户会查到旧记录而无法登录
    user = db.query(SysUser).filter(SysUser.user_name == username, SysUser.del_flag == DEL_FLAG_NORMAL).first()
    if user is None:
        _log(db, username, LOGIN_FAIL, "用户不存在/密码错误", ip, browser, os_name)
        raise ServiceException("用户不存在/密码错误")
    if user.del_flag == DEL_FLAG_DELETED:
        _log(db, username, LOGIN_FAIL, "对不起，您的账号已被删除", ip, browser, os_name)
        raise ServiceException("对不起，您的账号已被删除")
    if user.status == STATUS_DISABLE:
        _log(db, username, LOGIN_FAIL, "用户已封禁，请联系管理员", ip, browser, os_name)
        raise ServiceException("用户已封禁，请联系管理员")

    lock_key = PWD_ERR_CNT_KEY_PREFIX + username
    retry_count = int(redis_client.get(lock_key) or 0)
    if retry_count >= PASSWORD_MAX_RETRY_COUNT:
        msg = f"密码输入错误{PASSWORD_MAX_RETRY_COUNT}次，帐户锁定{PASSWORD_LOCK_MINUTES}分钟"
        _log(db, username, LOGIN_FAIL, msg, ip, browser, os_name)
        raise ServiceException(msg)

    if not verify_password(password, user.password):
        retry_count += 1
        redis_client.set(lock_key, retry_count, ex=PASSWORD_LOCK_MINUTES * 60)
        _log(db, username, LOGIN_FAIL, "用户不存在/密码错误", ip, browser, os_name)
        raise ServiceException("用户不存在/密码错误")

    redis_client.delete(lock_key)
    return user


def build_login_user(db: Session, user: SysUser, ip: str = "", browser: str = "", os_name: str = "") -> dict:
    """
    组装存入 Redis 的登录态字典，对应 Java 的 LoginUser（只保留本项目用得到的字段）。
    ip/browser/os_name 在线用户监控页要用；dept_name 操作日志和在线用户列表都要用。
    """
    is_admin = user.user_id == SUPER_ADMIN_ID
    if is_admin:
        role_keys: list[str] = [ADMIN_ROLE_KEY]
        permissions: list[str] = [ALL_PERMISSION]
        roles_meta: list[dict] = []
    else:
        roles = (
            db.query(SysRole)
            .join(SysUserRole, SysUserRole.role_id == SysRole.role_id)
            .filter(SysUserRole.user_id == user.user_id, SysRole.del_flag == DEL_FLAG_NORMAL)
            .all()
        )
        role_keys = [r.role_key for r in roles]
        roles_meta = [{"role_id": r.role_id, "data_scope": r.data_scope} for r in roles]

        perm_set: set[str] = set()
        for role in roles:
            if role.status == STATUS_NORMAL and role.role_id != SUPER_ADMIN_ROLE_ID:
                rows = (
                    db.query(SysMenu.perms)
                    .join(SysRoleMenu, SysRoleMenu.menu_id == SysMenu.menu_id)
                    .filter(SysRoleMenu.role_id == role.role_id, SysMenu.status == STATUS_NORMAL)
                    .all()
                )
                for (perm,) in rows:
                    if perm:
                        perm_set.update(p for p in perm.split(",") if p)
        permissions = list(perm_set)

    dept_name = ""
    if user.dept_id:
        dept = db.query(SysDept).filter(SysDept.dept_id == user.dept_id).first()
        if dept:
            dept_name = dept.dept_name

    return {
        "user_id": user.user_id,
        "dept_id": user.dept_id,
        "dept_name": dept_name,
        "user_name": user.user_name,
        "nick_name": user.nick_name,
        "is_admin": is_admin,
        "role_keys": role_keys,
        "roles": roles_meta,
        "permissions": permissions,
        "ip": ip,
        "browser": browser,
        "os": os_name,
    }


def record_login_success(db: Session, user: SysUser, ip: str, browser: str, os_name: str) -> None:
    _log(db, user.user_name, LOGIN_SUCCESS, "登录成功", ip, browser, os_name)
    user.login_ip = ip
    user.login_date = datetime.now()
    db.commit()


def get_user_info_response(db: Session, login_user: dict) -> dict:
    """
    对应 SysLoginController.getInfo。isDefaultModifyPwd / isPasswordExpired / pwdChrtype
    这几个字段按 TASK.md 的约定简化为固定值，不读取 sys_config（前端只在为 true 时弹提示框）。
    """
    user = db.query(SysUser).filter(SysUser.user_id == login_user["user_id"]).first()
    user_dict = model_to_camel_dict(user, exclude={"password"})

    if user.dept_id:
        dept = db.query(SysDept).filter(SysDept.dept_id == user.dept_id).first()
        if dept:
            user_dict["dept"] = model_to_camel_dict(dept)

    return ajax_success(
        user=user_dict,
        roles=login_user["role_keys"],
        permissions=login_user["permissions"],
        pwdChrtype="0",
        isDefaultModifyPwd=False,
        isPasswordExpired=False,
    )
