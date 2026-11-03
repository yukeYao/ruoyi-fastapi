"""
审批恢复后"模型总结失败"：写操作的真实状态以 agent_action 为准。

复现的线上问题：批准 change_user_status 后 execute 已成功（SUCCEEDED、操作日志一条），
随后 agent 节点调用模型总结时连接失败，接口却返回"智能助手处理失败，请稍后重试"，
页面把整次操作显示成失败。这里覆盖 SUCCEEDED / FAILED / REJECTED / EXPIRED 与普通、流式两种模式。
"""
import dataclasses
import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agent.tools import TOOLS
from app.core.constants import AGENT_RUN_LOCK_KEY_PREFIX
from app.core.database import SessionLocal
from app.core.exceptions import ServiceException
from app.core.redis_client import redis_client
from app.services import agent_service
from main import app
from tests.agent_helpers import add_user, call, chat, final, tool_msg, tool_results
from tests.conftest import auth_headers
from tests.test_agent_stream import _events

client = TestClient(app)

# 模拟第三方 SDK 的异常信息里带出密钥、JWT、tokenId：日志和 checkpoint 都不能出现
SECRET_KEY = "sk-replyfail-canary-0123456789"
SECRET_JWT = "eyJhbGciOiJIUzI1NiJ9.replyfail-canary.sig"
SECRET_TOKEN_ID = "replyfail-tokenid-canary"


def _boom(messages, tools):
    raise RuntimeError(f"Connection error. api_key={SECRET_KEY} Authorization: Bearer {SECRET_JWT} tokenId={SECRET_TOKEN_ID}")


def _sql(query, **params):
    db = SessionLocal()
    try:
        return db.execute(text(query), params).mappings().all()
    finally:
        db.close()


def _status(name):
    return _sql("SELECT status FROM sys_user WHERE user_name = :n AND del_flag = '0'", n=name)[0]["status"]


def _action_status(action_id):
    return _sql("SELECT status FROM agent_action WHERE action_id = :i", i=action_id)[0]["status"]


def _oper_log_count(action_id):
    return len(_sql("SELECT oper_id FROM sys_oper_log WHERE oper_param LIKE :p", p=f"%{action_id}%"))


def _lock_free():
    return redis_client.get(AGENT_RUN_LOCK_KEY_PREFIX + "1") is None


@pytest.fixture
def counting_execute(monkeypatch):
    """统计 change_user_status 的写操作真正执行了几次（sys_user 只能被改一次）"""
    calls = []
    spec = TOOLS["change_user_status"]

    def execute(*args):
        calls.append(1)
        return spec.execute(*args)

    monkeypatch.setitem(TOOLS, "change_user_status", dataclasses.replace(spec, execute=execute))
    return calls


def _propose(scripted_model, admin, name, *after):
    add_user(client, admin, name, [])
    scripted_model(tool_msg(call("change_user_status", {"user_name": name, "status": "1"}, "w1")), *after)
    body = chat(client, admin, "停用 " + name)
    assert body["code"] == 200, body
    return body["data"]["pendingAction"]


def _decide(admin, action_id, decision, stream):
    url = f"/agent/action/{action_id}/{decision}" + ("/stream" if stream else "")
    return client.post(url, headers=admin)


# ---------------------------------------------------------------------------
# SUCCEEDED：用户反馈的场景
# ---------------------------------------------------------------------------

def test_succeeded_then_model_fails_normal(scripted_model, counting_execute, agent_checkpoint, caplog):
    admin = auth_headers(client)
    action = _propose(scripted_model, admin, "rf_t", _boom, final("继续"))
    caplog.set_level(logging.DEBUG)

    body = _decide(admin, action["actionId"], "approve", stream=False).json()
    # 不能把已成功的操作返回成未知失败：业务成功返回，带上 action 真实状态和"回复生成失败"的说明
    assert body["code"] == 200, body
    data = body["data"]
    assert data["action"]["status"] == "SUCCEEDED"
    assert data["replyError"] == agent_service.MSG_REPLY_FAILED["SUCCEEDED"] == "操作已执行成功，但回复生成失败"
    assert data["reply"] == "" and data["pendingAction"] is None
    outcome = json.loads(tool_results(body)["w1"]["content"])
    assert outcome["status"] == "SUCCEEDED" and outcome["executed"] is True

    # 数据库：执行一次、改一次、日志一条
    assert _action_status(action["actionId"]) == "SUCCEEDED"
    assert counting_execute == [1] and _status("rf_t") == "1"
    assert _oper_log_count(action["actionId"]) == 1
    assert _lock_free()

    # 页面可依赖的接口：审批详情、消息历史都显示执行成功，且没有待审批操作
    detail = client.get(f"/agent/action/{action['actionId']}", headers=admin).json()["data"]
    assert detail["status"] == "SUCCEEDED"
    history = client.get(f"/agent/conversation/{action['conversationId']}/messages", headers=admin).json()
    assert history["pendingAction"] is None
    assert json.loads(history["data"][-1]["content"])["status"] == "SUCCEEDED"

    # 再次批准不会重复执行；下一条消息正常继续
    assert _decide(admin, action["actionId"], "approve", stream=False).json()["msg"] == agent_service.MSG_ALREADY_DECIDED
    assert chat(client, admin, "好的", action["conversationId"])["data"]["reply"] == "继续"
    assert counting_execute == [1] and _oper_log_count(action["actionId"]) == 1
    assert _lock_free()

    # 模型异常信息里的密钥、JWT、tokenId 不能进日志，也不能随异常 repr 写进 checkpoint
    for secret in (SECRET_KEY, SECRET_JWT, SECRET_TOKEN_ID):
        assert secret not in caplog.text
        for suffix in ("", "-wal", "-shm"):
            path = agent_checkpoint.with_name(agent_checkpoint.name + suffix)
            if path.exists():
                assert secret.encode() not in path.read_bytes()


def test_succeeded_then_model_fails_stream(scripted_model, counting_execute, caplog):
    admin = auth_headers(client)
    action = _propose(scripted_model, admin, "rf_t", _boom, final("继续"))
    caplog.set_level(logging.DEBUG)

    events = _events(_decide(admin, action["actionId"], "approve", stream=True))
    steps = [d["node"] for e, d in events if e == "step"]
    assert steps == ["approval", "execute"]
    name, data = events[-1]
    # error 事件带上 action 真实状态，文案说明操作已成功、只是回复生成失败
    assert name == "error"
    assert data["msg"] == "操作已执行成功，但回复生成失败"
    assert data["action"]["status"] == "SUCCEEDED" and data["action"]["actionId"] == action["actionId"]
    assert counting_execute == [1] and _status("rf_t") == "1"
    assert _oper_log_count(action["actionId"]) == 1
    assert _lock_free()

    assert _decide(admin, action["actionId"], "approve", stream=True).json()["msg"] == agent_service.MSG_ALREADY_DECIDED
    body = {"message": "好的", "conversationId": action["conversationId"]}
    assert _events(client.post("/agent/chat/stream", headers=admin, json=body))[-1][1]["reply"] == "继续"
    assert counting_execute == [1] and _lock_free()
    for secret in (SECRET_KEY, SECRET_JWT, SECRET_TOKEN_ID):
        assert secret not in caplog.text


# ---------------------------------------------------------------------------
# 同类状态：FAILED / REJECTED / EXPIRED，普通与流式
# ---------------------------------------------------------------------------

def _make_failed(monkeypatch):
    spec = TOOLS["change_user_status"]

    def failing(*args):
        raise ServiceException("目标用户状态不允许修改")

    monkeypatch.setitem(TOOLS, "change_user_status", dataclasses.replace(spec, execute=failing))


def _make_expired(action_id):
    db = SessionLocal()
    try:
        db.execute(text("UPDATE agent_action SET expires_at = DATE_SUB(NOW(), INTERVAL 1 MINUTE) WHERE action_id = :i"),
                   {"i": action_id})
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("expected,decision", [
    ("FAILED", "approve"),
    ("REJECTED", "reject"),
    ("EXPIRED", "approve"),
])
def test_terminal_status_then_model_fails(scripted_model, monkeypatch, expected, decision, stream):
    admin = auth_headers(client)
    action = _propose(scripted_model, admin, "rf_t", _boom, final("继续"))
    if expected == "FAILED":
        _make_failed(monkeypatch)
    if expected == "EXPIRED":
        _make_expired(action["actionId"])

    resp = _decide(admin, action["actionId"], decision, stream)
    msg = agent_service.MSG_REPLY_FAILED[expected]
    if stream:
        name, data = _events(resp)[-1]
        assert name == "error" and data["msg"] == msg and data["action"]["status"] == expected
    else:
        body = resp.json()
        assert body["code"] == 200, body
        assert body["data"]["action"]["status"] == expected and body["data"]["replyError"] == msg
    assert _action_status(action["actionId"]) == expected
    assert _status("rf_t") == "0"
    assert _lock_free()
    # 已经是终态：不能再批准，下一条消息正常继续
    assert _decide(admin, action["actionId"], "approve", stream=False).json()["msg"] == agent_service.MSG_ALREADY_DECIDED
    assert chat(client, admin, "好的", action["conversationId"])["data"]["reply"] == "继续"


@pytest.mark.parametrize("stream", [False, True])
def test_failure_inside_execute_before_claim_keeps_retry(scripted_model, monkeypatch, stream):
    """
    execute 节点在抢到执行权之前出错（仍是 APPROVED，图停在 execute 上）：
    保持"处理失败"并允许重试；重试只执行一次；期间对话接口不能顺带执行这个写操作。
    """
    admin = auth_headers(client)
    action = _propose(scripted_model, admin, "rf_t", final("已停用"))
    from app.agent import graph as agent_graph

    original = agent_graph.approval.execute
    failures = []

    def flaky(*args, **kwargs):
        if not failures:
            failures.append(1)
            raise RuntimeError("恢复时数据库连接中断")
        return original(*args, **kwargs)

    monkeypatch.setattr(agent_graph.approval, "execute", flaky)
    resp = _decide(admin, action["actionId"], "approve", stream)
    if stream:
        name, data = _events(resp)[-1]
        assert name == "error" and data["msg"] == agent_service.MSG_FAILED
        assert data["action"]["status"] == "APPROVED"
    else:
        assert resp.json() == {"code": 500, "msg": agent_service.MSG_FAILED}
    assert _action_status(action["actionId"]) == "APPROVED"
    assert _status("rf_t") == "0" and _lock_free()

    # 对话接口：提示回到审批卡片重试，不顺带执行
    assert chat(client, admin, "怎么样了", action["conversationId"])["msg"] == agent_service.MSG_APPROVED_UNFINISHED
    assert _status("rf_t") == "0"

    resp = _decide(admin, action["actionId"], "approve", stream)
    data = _events(resp)[-1][1] if stream else resp.json()["data"]
    assert data["action"]["status"] == "SUCCEEDED" and _status("rf_t") == "1"
    assert _oper_log_count(action["actionId"]) == 1
