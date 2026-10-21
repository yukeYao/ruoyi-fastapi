"""
登录态管理，对应若依 Java 版的 TokenService。

设计要点（见 TASK.md 阶段2）：
- JWT 里只放 token 的 uuid（login_user_key）；
- 完整的登录用户信息（LoginUser）以 JSON 形式存 Redis `login_tokens:{uuid}`，有效期30分钟；
- 剩余有效期不足20分钟时自动续期到30分钟（verify_token）。
"""
import json
import time
import uuid as uuid_lib

from app.core.constants import LOGIN_TOKEN_EXPIRE_MINUTES, LOGIN_TOKEN_KEY_PREFIX, TOKEN_REFRESH_THRESHOLD_MINUTES
from app.core.redis_client import redis_client
from app.core.security import create_jwt_token


def _token_key(token_uuid: str) -> str:
    return LOGIN_TOKEN_KEY_PREFIX + token_uuid


def create_token(login_user: dict) -> str:
    """生成 token：login_user 是登录用户的信息字典（由 login_service 构造）"""
    token_uuid = uuid_lib.uuid4().hex
    login_user["token"] = token_uuid
    refresh_token(login_user)
    return create_jwt_token(token_uuid, login_user["user_name"])


def refresh_token(login_user: dict) -> None:
    """刷新登录态有效期，重新写入 Redis"""
    now_ms = int(time.time() * 1000)
    login_user["login_time"] = now_ms
    login_user["expire_time"] = now_ms + LOGIN_TOKEN_EXPIRE_MINUTES * 60 * 1000
    redis_client.set(
        _token_key(login_user["token"]),
        json.dumps(login_user, ensure_ascii=False),
        ex=LOGIN_TOKEN_EXPIRE_MINUTES * 60,
    )


def get_login_user(token_uuid: str) -> dict | None:
    """根据 token uuid 从 Redis 取登录用户信息，取不到返回 None（对应已过期/未登录）"""
    raw = redis_client.get(_token_key(token_uuid))
    if raw is None:
        return None
    return json.loads(raw)


def verify_token(login_user: dict) -> None:
    """剩余有效期不足20分钟时自动续期"""
    now_ms = int(time.time() * 1000)
    if login_user["expire_time"] - now_ms <= TOKEN_REFRESH_THRESHOLD_MINUTES * 60 * 1000:
        refresh_token(login_user)


def delete_login_user(token_uuid: str) -> None:
    """登出 / 强制下线：删除 Redis 里的登录态"""
    redis_client.delete(_token_key(token_uuid))
