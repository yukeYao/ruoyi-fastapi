"""
恢复：模拟服务重启（关闭并重新打开 SQLite checkpoint、换一个新的模型实例和新的 Runtime context），
同一个 conversation_id 能接着之前的历史继续对话；并确认 context（登录态）没有落盘。
阶段 2 再补充 interrupt 之后的恢复测试。
"""
import json

from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from app.agent import checkpointer
from app.core.constants import LOGIN_TOKEN_KEY_PREFIX, LOGIN_USER_KEY
from app.core.redis_client import redis_client
from app.core.security import decode_jwt_token
from main import app
from tests.agent_helpers import call, chat, final, tool_msg
from tests.conftest import login_and_get_token

client = TestClient(app)

CTX_CANARY = "ctxcanary:perm:only-in-login-state"


def _headers_with_context_canary():
    """登录后往 Redis 登录态里塞一个 canary 权限字符：它只会通过 Runtime context 进入图"""
    token = login_and_get_token(client)
    token_id = decode_jwt_token(token)[LOGIN_USER_KEY]
    key = LOGIN_TOKEN_KEY_PREFIX + token_id
    login_user = json.loads(redis_client.get(key))
    login_user["permissions"].append(CTX_CANARY)
    redis_client.set(key, json.dumps(login_user, ensure_ascii=False), ex=redis_client.ttl(key))
    return {"Authorization": f"Bearer {token}"}, token_id


def test_resume_conversation_after_restart(scripted_model, agent_checkpoint):
    headers, token_id = _headers_with_context_canary()
    search = call("search_users", {"user_name": "ry"})
    scripted_model(tool_msg(search), final("找到了 ry"))
    first = chat(client, headers, "查一下 ry")
    conv_id = first["data"]["conversationId"]

    # 模拟重启：关闭 checkpoint 连接，下次请求重新打开同一个文件、重新编译图；模型也是新实例
    checkpointer.close_checkpointer()
    seen = {}

    def remember(messages, tools):
        seen["messages"] = messages
        return final("ry 的状态正常")

    fake = scripted_model(remember)
    second = chat(client, headers, "他的状态呢？", conv_id)
    assert second["code"] == 200, second
    assert second["data"]["conversationId"] == conv_id
    assert second["data"]["reply"] == "ry 的状态正常"

    # 新模型看到了重启前的完整历史（含工具结果）
    contents = [m.content for m in seen["messages"]]
    assert "查一下 ry" in contents and "找到了 ry" in contents and "他的状态呢？" in contents
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == search["id"] for m in seen["messages"])
    assert len(fake.calls) == 1

    history = client.get(f"/agent/conversation/{conv_id}/messages", headers=headers).json()["data"]
    assert [m["type"] for m in history] == ["human", "ai", "tool", "ai", "human", "ai"]

    # Runtime context（登录态）没有落盘：canary 权限字符和 tokenId 都不在 SQLite 文件里
    checkpointer.close_checkpointer()
    disk = agent_checkpoint.read_bytes()
    assert "他的状态呢？".encode() in disk  # 确认读到的确实是这次对话的 checkpoint，避免空跑
    assert CTX_CANARY.encode() not in disk and token_id.encode() not in disk
