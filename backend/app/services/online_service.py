"""在线用户监控，对应若依 SysUserOnlineController（直接扫 Redis login_tokens:*，没有单独的表）"""
import json

from app.core.constants import LOGIN_TOKEN_KEY_PREFIX
from app.core.redis_client import redis_client
from app.core.request_utils import get_login_location


def list_online_users(ipaddr: str | None, user_name: str | None) -> list[dict]:
    keys = redis_client.keys(LOGIN_TOKEN_KEY_PREFIX + "*")
    result = []
    for key in keys:
        raw = redis_client.get(key)
        if not raw:
            continue
        login_user = json.loads(raw)

        if ipaddr and login_user.get("ip") != ipaddr:
            continue
        if user_name and login_user.get("user_name") != user_name:
            continue

        result.append(
            {
                "tokenId": key[len(LOGIN_TOKEN_KEY_PREFIX):],
                "deptName": login_user.get("dept_name") or "",
                "userName": login_user.get("user_name"),
                "ipaddr": login_user.get("ip"),
                "loginLocation": get_login_location(login_user.get("ip", "")),
                "browser": login_user.get("browser"),
                "os": login_user.get("os"),
                "loginTime": login_user.get("login_time"),
            }
        )
    result.reverse()
    return result


def force_logout(token_id: str) -> None:
    redis_client.delete(LOGIN_TOKEN_KEY_PREFIX + token_id)
