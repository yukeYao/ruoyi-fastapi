"""
SSE 接口：事件类型与内容、开始推流前的错误仍是 HTTP 200 + JSON、推流中的错误走 error 事件且释放运行锁。
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.constants import AGENT_RUN_LOCK_KEY_PREFIX
from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from app.services import agent_service
from main import app
from tests.agent_helpers import MENU_USER, add_user, call, final, tool_msg, user_with_menus
from tests.conftest import auth_headers

client = TestClient(app)


def _events(resp):
    """解析 SSE 文本：[(event, data), ...]"""
    assert resp.headers["content-type"].startswith("text/event-stream"), resp.text
    events = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def _stream_chat(headers, message, conversation_id=None):
    body = {"message": message}
    if conversation_id:
        body["conversationId"] = conversation_id
    return client.post("/agent/chat/stream", headers=headers, json=body)


def _status(name):
    db = SessionLocal()
    try:
        return db.execute(text("SELECT status FROM sys_user WHERE user_name = :n AND del_flag = '0'"), {"n": name}).scalar_one()
    finally:
        db.close()


def test_stream_read_only_query(scripted_model):
    c = call("list_roles", {}, "r1")
    scripted_model(tool_msg(c), final("共有两个角色"))
    events = _events(_stream_chat(auth_headers(client), "有哪些角色"))
    names = [e for e, _ in events]

    assert names[-1] == "final" and "error" not in names and "approval" not in names
    steps = [d for e, d in events if e == "step"]
    assert [s["node"] for s in steps] == ["agent", "tools", "agent"]
    assert steps[0]["messages"][0]["toolCalls"][0]["name"] == "list_roles"
    assert steps[1]["messages"][0]["toolCallId"] == "r1"
    # 文本按片段推送，拼起来就是最终回复；工具调用参数不会出现在 token 里
    tokens = "".join(d["text"] for e, d in events if e == "token")
    assert tokens == "共有两个角色"
    final_data = events[-1][1]
    assert final_data["reply"] == "共有两个角色" and final_data["pendingAction"] is None
    assert redis_client.get(AGENT_RUN_LOCK_KEY_PREFIX + "1") is None


def test_stream_approval_then_approve(scripted_model):
    add_user(client, auth_headers(client), "st_t", [])
    scripted_model(tool_msg(call("change_user_status", {"user_name": "st_t", "status": "1"}, "w1")), final("已停用"))
    admin = auth_headers(client)
    events = _events(_stream_chat(admin, "停用 st_t"))
    approval = [d for e, d in events if e == "approval"]
    assert len(approval) == 1
    pending = approval[0]["pendingAction"]
    assert pending["status"] == "PENDING" and pending["preview"]["target"]["userName"] == "st_t"
    assert events[-1][0] == "final" and events[-1][1]["pendingAction"]["actionId"] == pending["actionId"]
    assert _status("st_t") == "0"

    events = _events(client.post(f"/agent/action/{pending['actionId']}/approve/stream", headers=admin))
    # 恢复时被 interrupt 的 approval 节点先完成（不带消息），再执行 execute，最后模型总结
    steps = [d for e, d in events if e == "step"]
    assert [s["node"] for s in steps] == ["approval", "execute", "agent"]
    assert steps[0]["messages"] == [] and steps[1]["messages"][0]["toolCallId"] == "w1"
    final_data = events[-1][1]
    assert events[-1][0] == "final"
    assert final_data["action"]["status"] == "SUCCEEDED" and final_data["reply"] == "已停用"
    assert _status("st_t") == "1"

    # 重复批准：开始推流前就拒绝，HTTP 200 + JSON
    again = client.post(f"/agent/action/{pending['actionId']}/approve/stream", headers=admin)
    assert again.headers["content-type"].startswith("application/json")
    assert again.json() == {"code": 500, "msg": agent_service.MSG_ALREADY_DECIDED}


def test_stream_shows_expired_settlement(scripted_model):
    """会话停在已过期的审批上时发新消息：先收尾（过期）产生的消息也要实时推送，并附带 action 的真实状态"""
    add_user(client, auth_headers(client), "st_t", [])
    scripted_model(tool_msg(call("change_user_status", {"user_name": "st_t", "status": "1"}, "w1")),
                   final("该审批已过期"), final("你好"))
    admin = auth_headers(client)
    pending = [d for e, d in _events(_stream_chat(admin, "停用 st_t")) if e == "approval"][0]["pendingAction"]
    db = SessionLocal()
    db.execute(text("UPDATE agent_action SET expires_at = DATE_SUB(NOW(), INTERVAL 1 MINUTE) WHERE action_id = :i"),
               {"i": pending["actionId"]})
    db.commit()
    db.close()

    events = _events(_stream_chat(admin, "你好", pending["conversationId"]))
    steps = [d for e, d in events if e == "step"]
    settle = steps[0]
    assert settle["node"] == "settle" and settle["action"]["status"] == "EXPIRED"
    tool = next(m for m in settle["messages"] if m["type"] == "tool")
    assert json.loads(tool["content"])["status"] == "EXPIRED"
    assert any(m["type"] == "ai" and m["content"] == "该审批已过期" for m in settle["messages"])
    # 收尾的消息不会在后续 step 里重复推送；本轮回复正常
    assert all(m.get("toolCallId") != "w1" for s in steps[1:] for m in s["messages"])
    assert events[-1][0] == "final" and events[-1][1]["reply"] == "你好"
    assert _status("st_t") == "0"


def test_stream_reject(scripted_model):
    add_user(client, auth_headers(client), "st_t", [])
    scripted_model(tool_msg(call("change_user_status", {"user_name": "st_t", "status": "1"}, "w1")), final("已取消"))
    admin = auth_headers(client)
    pending = [d for e, d in _events(_stream_chat(admin, "停用 st_t")) if e == "approval"][0]["pendingAction"]
    events = _events(client.post(f"/agent/action/{pending['actionId']}/reject/stream", headers=admin))
    assert events[-1][0] == "final" and events[-1][1]["action"]["status"] == "REJECTED"
    assert _status("st_t") == "0"


def test_stream_errors_before_start_are_json(scripted_model):
    scripted_model(final())
    resp = client.post("/agent/chat/stream", json={"message": "你好"})
    assert resp.status_code == 200 and resp.json()["code"] == 401

    _, no_agent = user_with_menus(client, "st_noagent", [MENU_USER])
    resp = client.post("/agent/chat/stream", headers=no_agent, json={"message": "你好"})
    assert resp.status_code == 200 and resp.json()["code"] == 403

    redis_client.set(AGENT_RUN_LOCK_KEY_PREFIX + "1", "another-run", ex=60)
    resp = _stream_chat(auth_headers(client), "你好")
    assert resp.json() == {"code": 500, "msg": agent_service.MSG_BUSY}
    assert redis_client.get(AGENT_RUN_LOCK_KEY_PREFIX + "1") == "another-run"

    resp = client.post("/agent/action/not-exist/approve/stream", headers=auth_headers(client))
    assert resp.json()["code"] == 500


def test_stream_error_event_releases_lock(scripted_model):
    def boom(messages, tools):
        raise RuntimeError("模型服务异常")

    scripted_model(boom, final("恢复"))
    admin = auth_headers(client)
    events = _events(_stream_chat(admin, "你好"))
    assert events[-1] == ("error", {"code": 500, "msg": agent_service.MSG_FAILED})
    assert redis_client.get(AGENT_RUN_LOCK_KEY_PREFIX + "1") is None
    assert _events(_stream_chat(admin, "再试一次"))[-1][1]["reply"] == "恢复"
