"""
get_user_detail 只按登录账号精确查询：提交 user_id 被拒绝（没有按 ID 的回退路径）；结果里是实际解析到的账号；
范围外、不存在、已删除返回同一句话。冒烟中模型曾猜 user_id=1，把 admin 的详情当成目标用户回答。
"""
import json

from fastapi.testclient import TestClient

from app.agent import policy
from app.agent.tools import MSG_USER_UNAVAILABLE, TOOLS
from main import app
from tests.agent_helpers import (
    MENU_AGENT,
    MENU_USER,
    MENU_USER_QUERY,
    add_user,
    call,
    chat,
    final,
    tool_msg,
    tool_results,
    user_with_menus,
)
from tests.conftest import auth_headers

client = TestClient(app)


def _payloads(body):
    return {k: json.loads(v["content"]) for k, v in tool_results(body).items()}


def test_schema_has_only_user_name():
    schema = TOOLS["get_user_detail"].args_model.model_json_schema()
    assert set(schema["properties"]) == {"user_name"} and schema.get("additionalProperties") is False


def test_user_id_is_rejected(scripted_model):
    by_id = call("get_user_detail", {"user_id": 1}, "a")
    both = call("get_user_detail", {"user_name": "admin", "user_id": 1}, "b")
    scripted_model(tool_msg(by_id, both), final("好的"))
    payloads = _payloads(chat(client, auth_headers(client), "查用户"))
    for key in ("a", "b"):
        assert payloads[key]["ok"] is False
        assert payloads[key]["error"].startswith(policy.MSG_INVALID_ARGS) and "user_id" in payloads[key]["error"]


def test_exact_user_name_returns_that_user(scripted_model):
    admin = auth_headers(client)
    add_user(client, admin, "kb_ud_a", [])
    add_user(client, admin, "kb_ud_ab", [])
    scripted_model(tool_msg(call("get_user_detail", {"user_name": "kb_ud_a"}, "a")), final("好的"))
    data = _payloads(chat(client, admin, "查 kb_ud_a"))["a"]["untrusted_data"]
    # 精确匹配，不会命中前缀相同的 kb_ud_ab；结果里的 userName 就是实际查到的账号
    assert data["userName"] == "kb_ud_a" and "roles" in data and "password" not in data


def test_out_of_scope_and_missing_and_deleted_are_identical(scripted_model):
    admin = auth_headers(client)
    deleted_id = add_user(client, admin, "kb_ud_del", [], dept_id=105)
    assert client.delete(f"/system/user/{deleted_id}", headers=admin).json()["code"] == 200
    # 本部门（105）数据范围的操作者：admin 在 103，范围外
    _, headers = user_with_menus(client, "udscope", [MENU_AGENT, MENU_USER, MENU_USER_QUERY], data_scope="3")
    scripted_model(tool_msg(call("get_user_detail", {"user_name": "admin"}, "out"),
                            call("get_user_detail", {"user_name": "kb_no_such_user"}, "missing"),
                            call("get_user_detail", {"user_name": "kb_ud_del"}, "deleted")), final("好的"))
    payloads = _payloads(chat(client, headers, "查这几个用户"))
    expected = {"ok": False, "error": MSG_USER_UNAVAILABLE}
    assert payloads["out"] == payloads["missing"] == payloads["deleted"] == expected


def test_in_scope_user_found_with_scope_perm_context(scripted_model):
    """数据范围按 system:user:query 计算：本部门的用户能查到"""
    admin = auth_headers(client)
    add_user(client, admin, "kb_ud_same", [], dept_id=105)
    _, headers = user_with_menus(client, "udsame", [MENU_AGENT, MENU_USER, MENU_USER_QUERY], data_scope="3")
    scripted_model(tool_msg(call("get_user_detail", {"user_name": "kb_ud_same"}, "a")), final("好的"))
    assert _payloads(chat(client, headers, "查"))["a"]["untrusted_data"]["userName"] == "kb_ud_same"
