"""在线用户监控，对应若依 SysUserOnlineController（直接扫 Redis login_tokens:*，没有单独的表）"""
import json

from app.core.constants import LOGIN_TOKEN_KEY_PREFIX, ONLINE_SCAN_COUNT
from app.core.redis_client import redis_client
from app.core.request_utils import get_login_location


def list_online_users(ipaddr: str | None, user_name: str | None) -> list[dict]:
    # 用 SCAN 增量遍历而不是 KEYS：KEYS 一次性遍历整个库，key 多时会阻塞单线程的 Redis
    result = []
    for key in redis_client.scan_iter(match=LOGIN_TOKEN_KEY_PREFIX + "*", count=ONLINE_SCAN_COUNT):
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


def list_session_token_ids(user_name: str) -> list[str]:
    """
    按用户名在服务端解析该用户的全部会话 tokenId，供智能助手"强制下线"使用。
    tokenId 只在服务端内部流转：不返回给模型、不写进预览、agent_action 和操作日志，
    否则拿到它就能单独踢掉任意会话。
    """
    token_ids = []
    for key in redis_client.scan_iter(match=LOGIN_TOKEN_KEY_PREFIX + "*", count=ONLINE_SCAN_COUNT):
        raw = redis_client.get(key)
        if raw and json.loads(raw).get("user_name") == user_name:
            token_ids.append(key[len(LOGIN_TOKEN_KEY_PREFIX):])
    return token_ids
