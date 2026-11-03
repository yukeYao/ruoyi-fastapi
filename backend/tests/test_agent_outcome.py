"""
审批结果 ToolMessage 的可信字段与 untrusted_data 边界，以及"接口状态以 agent_action 为准，不依赖模型文案"。
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agent import approval
from app.core.database import SessionLocal
from app.services import agent_action_service as actions
from main import app
from tests.agent_helpers import add_user, call, chat, final, tool_msg, tool_results
from tests.conftest import auth_headers

client = TestClient(app)

TRUSTED_KEYS = {"ok", "executed", "status", "action_id", "tool_name", "message", "untrusted_data"}
# 目标用户名是用户可控内容：只允许出现在 untrusted_data 里
TARGET = "oc_evil_name"


def _sql(query, **params):
    db = SessionLocal()
    try:
        result = db.execute(text(query), params)
        if result.returns_rows:
            return result.fetchall()
        db.commit()
    finally:
        db.close()


def _pending(scripted_model, *after):
    add_user(client, auth_headers(client), TARGET, [])
    scripted_model(tool_msg(call("change_user_status", {"user_name": TARGET, "status": "1"}, "w1")), *(after or (final(),)))
    body = chat(client, auth_headers(client), "停用")
    return body["data"]["pendingAction"]


@pytest.mark.parametrize("status, ok, executed, message", [
    ("PENDING", False, False, approval.MSG_NOT_APPROVED),
    ("SUCCEEDED", True, True, approval.MSG_EXECUTED),
    ("REJECTED", False, False, approval.MSG_REJECTED),
    ("EXPIRED", False, False, approval.MSG_EXPIRED),
    ("FAILED", False, False, approval.MSG_EXECUTE_FAILED),
    ("EXECUTING", False, None, approval.MSG_EXECUTION_UNKNOWN),
])
def test_outcome_trusted_fields_and_untrusted_boundary(scripted_model, status, ok, executed, message):
    pending = _pending(scripted_model)
    result = json.dumps({"userName": TARGET, "status": "1"}) if status == "SUCCEEDED" else ""
    error = f"目标用户“{TARGET}”在审批期间已被改名、删除或重建，操作未执行" if status == "FAILED" else ""
    _sql("UPDATE agent_action SET status = :s, result = :r, error = :e WHERE action_id = :i",
         s=status, r=result, e=error, i=pending["actionId"])

    db = SessionLocal()
    payload = approval.outcome_payload(actions.get_action(db, pending["actionId"]))
    db.close()

    assert set(payload) == TRUSTED_KEYS
    assert payload["ok"] is ok and payload["executed"] is executed
    assert payload["status"] == status and payload["message"] == message
    assert payload["action_id"] == pending["actionId"] and payload["tool_name"] == "change_user_status"
    # 外层只有服务端生成的内容；用户名、执行结果、失败详情都在 untrusted_data 里
    outer = json.dumps({k: v for k, v in payload.items() if k != "untrusted_data"}, ensure_ascii=False)
    assert TARGET not in outer
    data = payload["untrusted_data"]
    assert data["target"]["userName"] == TARGET
    assert ("result" in data) == (status == "SUCCEEDED")
    assert ("errorDetail" in data) == (status == "FAILED")
    if status == "FAILED":
        assert TARGET in data["errorDetail"]


def test_model_wrong_summary_does_not_change_api_status(scripted_model):
    """模型在执行成功后仍然写"等待审批"：接口、详情、消息接口都显示 agent_action 的真实状态"""
    seen = {}

    def wrong_summary(messages, tools):
        seen["tool"] = json.loads(messages[-1].content)
        return final("已提交停用操作，等待审批中。")

    pending = _pending(scripted_model, wrong_summary)
    admin = auth_headers(client)
    body = client.post(f"/agent/action/{pending['actionId']}/approve", headers=admin).json()["data"]

    # 模型拿到的是明确的可信字段
    assert seen["tool"]["executed"] is True and seen["tool"]["status"] == "SUCCEEDED"
    # 模型总结写错了，但接口状态来自 agent_action
    assert body["reply"] == "已提交停用操作，等待审批中。"
    assert body["action"]["status"] == "SUCCEEDED" and body["pendingAction"] is None
    detail = client.get(f"/agent/action/{pending['actionId']}", headers=admin).json()["data"]
    assert detail["status"] == "SUCCEEDED"
    history = client.get(f"/agent/conversation/{pending['conversationId']}/messages", headers=admin).json()
    assert history["pendingAction"] is None
    assert _sql("SELECT status FROM sys_user WHERE user_name = :n AND del_flag = '0'", n=TARGET)[0][0] == "1"


def test_model_claims_executed_after_reject(scripted_model):
    """拒绝后模型声称"已执行"：接口仍显示 REJECTED，数据没有变化"""
    seen = {}

    def lying(messages, tools):
        seen["tool"] = json.loads(messages[-1].content)
        return final("已经成功停用。")

    pending = _pending(scripted_model, lying)
    admin = auth_headers(client)
    body = client.post(f"/agent/action/{pending['actionId']}/reject", headers=admin).json()["data"]
    assert seen["tool"]["executed"] is False and seen["tool"]["status"] == "REJECTED"
    assert body["action"]["status"] == "REJECTED"
    assert client.get(f"/agent/action/{pending['actionId']}", headers=admin).json()["data"]["status"] == "REJECTED"
    assert _sql("SELECT status FROM sys_user WHERE user_name = :n AND del_flag = '0'", n=TARGET)[0][0] == "0"


def test_failed_execution_tool_message(scripted_model):
    """执行失败（审批期间目标被改名）：模型拿到 executed=false、status=FAILED，详情在 untrusted_data"""
    seen = {}

    def summarize(messages, tools):
        seen["tool"] = json.loads(messages[-1].content)
        return final("执行失败")

    pending = _pending(scripted_model, summarize)
    _sql("UPDATE sys_user SET user_name = 'oc_renamed' WHERE user_name = :n AND del_flag = '0'", n=TARGET)
    body = client.post(f"/agent/action/{pending['actionId']}/approve", headers=auth_headers(client)).json()
    tool = tool_results(body)["w1"]
    assert tool["status"] == "error"
    assert seen["tool"]["executed"] is False and seen["tool"]["status"] == "FAILED"
    assert TARGET in seen["tool"]["untrusted_data"]["errorDetail"]
    assert body["data"]["action"]["status"] == "FAILED"
