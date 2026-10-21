"""
鉴权相关的 FastAPI 依赖：get_login_user（解析 token）、has_perm（权限校验）。

对应若依的 JwtAuthenticationTokenFilter + @PreAuthorize("@ss.hasPermi(...)")。
"""
from fastapi import Depends, Header

from app.core import token_service
from app.core.constants import ALL_PERMISSION, LOGIN_USER_KEY, SUPER_ADMIN_ID, TOKEN_PREFIX
from app.core.exceptions import ServiceException
from app.core.security import decode_jwt_token

_RELOGIN_MSG = "无效的会话，或者会话已过期，请重新登录。"


def get_login_user(authorization: str | None = Header(default=None)) -> dict:
    """
    解析 Authorization 头 -> 取出 JWT 里的 token uuid -> 到 Redis 查完整登录态。
    查不到（未登录/已过期/被强制下线）统一返回 401，剩余有效期不足20分钟会自动续期。
    """
    if not authorization or not authorization.startswith(TOKEN_PREFIX):
        raise ServiceException(_RELOGIN_MSG, code=401)

    token = authorization[len(TOKEN_PREFIX):]
    try:
        payload = decode_jwt_token(token)
    except Exception:
        raise ServiceException(_RELOGIN_MSG, code=401)

    token_uuid = payload.get(LOGIN_USER_KEY)
    login_user = token_service.get_login_user(token_uuid) if token_uuid else None
    if login_user is None:
        raise ServiceException(_RELOGIN_MSG, code=401)

    token_service.verify_token(login_user)
    return login_user


def has_perm(perm: str):
    """
    权限依赖工厂：用法 `Depends(has_perm("system:user:list"))`。
    超级管理员（userId=1）权限是 *:*:*，拥有一切权限。
    """

    def _check(login_user: dict = Depends(get_login_user)) -> dict:
        if login_user.get("user_id") == SUPER_ADMIN_ID:
            return login_user
        permissions = set(login_user.get("permissions", []))
        if ALL_PERMISSION in permissions or perm in permissions:
            return login_user
        raise ServiceException("没有权限，请联系管理员授权", code=403)

    return _check


def has_role(role_key: str):
    """角色依赖工厂：用法 `Depends(has_role("admin"))`"""

    def _check(login_user: dict = Depends(get_login_user)) -> dict:
        if login_user.get("user_id") == SUPER_ADMIN_ID:
            return login_user
        if role_key in set(login_user.get("role_keys", [])):
            return login_user
        raise ServiceException("没有权限，请联系管理员授权", code=403)

    return _check
