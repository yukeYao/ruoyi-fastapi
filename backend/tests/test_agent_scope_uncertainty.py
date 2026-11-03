"""
"不存在"与"不在数据范围内"无法区分时，交给模型的工具结果必须保留这种不确定性。

复现的手动验收问题：change_user_status 返回"不存在或不在你的数据范围内"，模型接着用 search_users
查询，因数据权限得到 0 条，于是回复"目标用户不存在"。空结果只说明当前数据范围内没找到，
所以受数据权限限制的查询为空时，结果外层附带服务端生成的可信说明（note），模型据此回复。
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.agent import write_tools
from app.agent.serialization import MSG_EMPTY_IN_SCOPE
from main import app
from tests.agent_helpers import (
    MENU_AGENT,
    MENU_DEPT,
    MENU_ROLE,
    MENU_USER,
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

MENU_USER_EDIT = 1002  # system:user:edit


def _content(body, call_id):
    return json.loads(tool_results(body)[call_id]["content"])


def _scoped_operator():
    """操作者在部门 105，只能看本部门（data_scope=3）；目标用户在部门 103，范围外但真实存在"""
    add_user(client, auth_headers(client), "su_out", [], dept_id=103)
    _, headers = user_with_menus(client, "su_op", [MENU_AGENT, MENU_USER, MENU_USER_EDIT, MENU_ROLE, MENU_DEPT],
                                 data_scope="3")
    return headers


def test_reported_flow_model_sees_uncertainty(scripted_model):
    """完整复现：写工具报"不存在或不在范围内" -> search_users 0 条；模型收到的两条结果都不能被读成"不存在\""""
    headers = _scoped_operator()
    seen = {}

    def capture(messages, tools):
        seen["messages"] = messages
        return final("目标用户 su_out 不存在或不在你的数据范围内")

    scripted_model(
        tool_msg(call("change_user_status", {"user_name": "su_out", "status": "0"}, "w1")),
        tool_msg(call("search_users", {"user_name": "su_out"}, "r1")),
        capture,
    )
    body = chat(client, headers, "把 su_out 的状态改为正常")
    assert body["code"] == 200, body

    write = _content(body, "w1")
    assert write["ok"] is False and write["error"] == write_tools.msg_unavailable("su_out")
    search = _content(body, "r1")
    assert search["untrusted_data"]["total"] == 0
    # 说明放在外层（服务端生成、可信），不在 untrusted_data 里
    assert search["note"] == MSG_EMPTY_IN_SCOPE
    assert "不存在" in MSG_EMPTY_IN_SCOPE and "数据范围" in MSG_EMPTY_IN_SCOPE

    # 模型下一轮实际收到的 ToolMessage 里带着这条说明
    tool_inputs = [m.content for m in seen["messages"] if getattr(m, "type", "") == "tool"]
    assert any(MSG_EMPTY_IN_SCOPE in c for c in tool_inputs)


def test_note_does_not_reveal_existence(scripted_model):
    """范围外的真实用户和根本不存在的用户，查询结果完全相同（说明本身不能泄露是否存在）"""
    headers = _scoped_operator()
    scripted_model(
        tool_msg(call("search_users", {"user_name": "su_out"}, "r1"), call("search_users", {"user_name": "su_nobody"}, "r2")),
        final(),
    )
    body = chat(client, headers, "查一下这两个用户")
    assert _content(body, "r1") == _content(body, "r2")
    assert _content(body, "r1")["note"] == MSG_EMPTY_IN_SCOPE


@pytest.mark.parametrize("tool,args", [
    ("search_users", {"user_name": "su_nobody"}),
    ("list_roles", {"role_name": "su_no_such_role"}),
    ("list_departments", {"dept_name": "su_no_such_dept"}),
])
def test_scoped_tools_empty_result_has_note(scripted_model, tool, args):
    """举一反三：受数据权限限制的查询（用户、角色、部门）为空时都附带说明"""
    headers = _scoped_operator()
    scripted_model(tool_msg(call(tool, args, "r1")), final())
    result = _content(chat(client, headers, "查询"), "r1")
    assert result["ok"] is True and result["untrusted_data"]["total"] == 0
    assert result["note"] == MSG_EMPTY_IN_SCOPE


def test_non_empty_result_has_no_note(scripted_model):
    scripted_model(tool_msg(call("search_users", {"user_name": "admin"}, "r1")), final())
    result = _content(chat(client, auth_headers(client), "查询 admin"), "r1")
    assert result["untrusted_data"]["total"] >= 1 and "note" not in result
