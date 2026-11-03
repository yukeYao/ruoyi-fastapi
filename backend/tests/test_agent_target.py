"""
写工具的目标解析：模型只能提供登录账号，服务端精确解析唯一用户并冻结 {userId, userName}，
执行时只按冻结的目标操作。覆盖 schema 拒绝 user_id、各种解析失败、预览后改名/删除重建。
"""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agent import write_tools
from app.agent.policy import MSG_INVALID_ARGS
from app.core.constants import LOGIN_TOKEN_KEY_PREFIX, PWD_ERR_CNT_KEY_PREFIX
from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from main import app
from tests.agent_helpers import (
    MENU_AGENT,
    MENU_USER,
    PASSWORD,
    add_user,
    call,
    chat,
    final,
    tool_msg,
    tool_results,
    user_with_menus,
)
from tests.conftest import auth_headers, login_and_get_token

client = TestClient(app)

MENU_USER_EDIT = 1002


def _sql(query, **params):
    db = SessionLocal()
    try:
        result = db.execute(text(query), params)
        if result.returns_rows:
            return result.fetchall()
        db.commit()
    finally:
        db.close()


def _status(user_id):
    return _sql("SELECT status FROM sys_user WHERE user_id = :i", i=user_id)[0][0]


def _propose(scripted_model, headers, tool, args, *after):
    scripted_model(tool_msg(call(tool, args, "w1")), *(after or (final("完成"),)))
    body = chat(client, headers, "处理")
    assert body["code"] == 200, body
    result = tool_results(body).get("w1")
    return body["data"]["pendingAction"], (json.loads(result["content"]) if result else None)


def _approve(headers, action_id):
    return client.post(f"/agent/action/{action_id}/approve", headers=headers).json()


def _action_count():
    return _sql("SELECT COUNT(*) FROM agent_action")[0][0]


def _tokens_of(user_id):
    return [k for k in redis_client.scan_iter(LOGIN_TOKEN_KEY_PREFIX + "*")
            if json.loads(redis_client.get(k))["user_id"] == user_id]


# ---------------------------------------------------------------------------
# schema：模型不能提供 user_id / target_user_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool", ["change_user_status", "unlock_user_account", "force_logout_user"])
@pytest.mark.parametrize("field", ["user_id", "target_user_id"])
def test_schema_rejects_user_id(scripted_model, tool, field):
    target = add_user(client, auth_headers(client), "tg_t", [])
    args = {"user_name": "tg_t", field: target}
    if tool == "change_user_status":
        args["status"] = "1"
    before = _action_count()
    pending, result = _propose(scripted_model, auth_headers(client), tool, args)
    assert pending is None
    assert result["error"].startswith(MSG_INVALID_ARGS) and field in result["error"]
    assert _action_count() == before


def test_user_id_only_is_rejected(scripted_model):
    """旧调用方式（只给 user_id）不再被接受：缺少 user_name 且多了 user_id"""
    pending, result = _propose(scripted_model, auth_headers(client), "change_user_status", {"user_id": 2, "status": "1"})
    assert pending is None and result["error"].startswith(MSG_INVALID_ARGS)


def test_tool_schema_has_no_user_id():
    from app.agent.tools import TOOLS, tool_schema

    for name in ("change_user_status", "unlock_user_account", "force_logout_user"):
        props = tool_schema(TOOLS[name])["function"]["parameters"]["properties"]
        assert "user_name" in props and "user_id" not in props and "target_user_id" not in props


# ---------------------------------------------------------------------------
# 解析：按用户名精确匹配唯一的未删除用户
# ---------------------------------------------------------------------------

def test_user_name_resolved_and_frozen(scripted_model):
    target = add_user(client, auth_headers(client), "tg_t", [])
    admin = auth_headers(client)
    pending, _ = _propose(scripted_model, admin, "change_user_status", {"user_name": "tg_t", "status": "1"}, final("已停用"))
    assert pending["target"] == {"userId": target, "userName": "tg_t"}
    assert pending["preview"]["target"]["userId"] == target
    row = _sql("SELECT target_json FROM agent_action WHERE action_id = :i", i=pending["actionId"])[0][0]
    assert json.loads(row) == {"userId": target, "userName": "tg_t"}
    assert _approve(admin, pending["actionId"])["data"]["action"]["status"] == "SUCCEEDED"
    assert _status(target) == "1"


def test_user_name_is_exact_match(scripted_model):
    """部分匹配不算：tg 不会匹配到 tg_t"""
    add_user(client, auth_headers(client), "tg_t", [])
    pending, result = _propose(scripted_model, auth_headers(client), "change_user_status", {"user_name": "tg", "status": "1"})
    assert pending is None and result["error"] == write_tools.msg_unavailable("tg")


def test_deleted_history_ignored_and_active_user_resolved(scripted_model):
    admin = auth_headers(client)
    old = add_user(client, admin, "tg_same", [])
    assert client.delete(f"/system/user/{old}", headers=admin).json()["code"] == 200
    # 只有已删除记录时：不存在
    pending, result = _propose(scripted_model, admin, "change_user_status", {"user_name": "tg_same", "status": "1"})
    assert pending is None and result["error"] == write_tools.msg_unavailable("tg_same")
    # 重建同名用户后：解析到新用户，而不是已删除的旧记录
    new = add_user(client, admin, "tg_same", [])
    pending, _ = _propose(scripted_model, admin, "change_user_status", {"user_name": "tg_same", "status": "1"})
    assert pending["target"]["userId"] == new != old


def test_ambiguous_active_users_rejected(scripted_model):
    """sys_user.user_name 没有唯一索引：直接改库造成两个未删除的同名用户时，拒绝而不是任选一个"""
    now = datetime.now()
    for _ in range(2):
        _sql("INSERT INTO sys_user (dept_id, user_name, nick_name, status, del_flag, create_by, create_time) "
             "VALUES (105, 'tg_dup', 'tg_dup', '0', '0', 'admin', :t)", t=now)
    try:
        before = _action_count()
        pending, result = _propose(scripted_model, auth_headers(client), "change_user_status", {"user_name": "tg_dup", "status": "1"})
        assert pending is None and result["error"] == write_tools.msg_ambiguous("tg_dup")
        assert _action_count() == before
        assert [r[0] for r in _sql("SELECT status FROM sys_user WHERE user_name = 'tg_dup'")] == ["0", "0"]
    finally:
        _sql("DELETE FROM sys_user WHERE user_name = 'tg_dup'")


def test_out_of_scope_and_missing_get_same_message(scripted_model):
    """范围外用户和不存在的用户报同一句话，不暴露范围外用户是否存在"""
    admin = auth_headers(client)
    outsider = add_user(client, admin, "tg_out", [], dept_id=103)
    _, headers = user_with_menus(client, "tg_op", [MENU_AGENT, MENU_USER, MENU_USER_EDIT], data_scope="3")
    _, out = _propose(scripted_model, headers, "change_user_status", {"user_name": "tg_out", "status": "1"})
    _, missing = _propose(scripted_model, headers, "change_user_status", {"user_name": "tg_nobody", "status": "1"})
    assert out["error"] == write_tools.msg_unavailable("tg_out")
    assert missing["error"] == write_tools.msg_unavailable("tg_nobody")
    assert out["error"].replace("tg_out", "X") == missing["error"].replace("tg_nobody", "X")
    assert _status(outsider) == "0"


def test_super_admin_hidden_when_out_of_scope(scripted_model):
    """范围外的人尝试操作 admin：只看到"不存在或不在范围内"，看不到"超级管理员"这个信息"""
    _, headers = user_with_menus(client, "tg_op", [MENU_AGENT, MENU_USER, MENU_USER_EDIT], data_scope="3")
    _, result = _propose(scripted_model, headers, "change_user_status", {"user_name": "admin", "status": "1"})
    assert result["error"] == write_tools.msg_unavailable("admin")


@pytest.mark.parametrize("case", ["super_admin", "self", "same_status", "bad_status"])
def test_rule_rejections_name_the_target(scripted_model, case):
    _, headers = user_with_menus(client, "tg_op", [MENU_AGENT, MENU_USER, MENU_USER_EDIT], data_scope="1")
    add_user(client, auth_headers(client), "tg_t", [])
    args, expected = {
        "super_admin": ({"user_name": "admin", "status": "1"}, write_tools.msg_super_admin("admin")),
        "self": ({"user_name": "ag_tg_op_u", "status": "1"}, write_tools.msg_self("ag_tg_op_u")),
        "same_status": ({"user_name": "tg_t", "status": "0"}, write_tools.msg_same_status("tg_t", "0")),
        "bad_status": ({"user_name": "tg_t", "status": "2"}, MSG_INVALID_ARGS),
    }[case]
    before = _action_count()
    pending, result = _propose(scripted_model, headers, "change_user_status", args)
    assert pending is None
    assert result["error"].startswith(expected)
    assert _action_count() == before


@pytest.mark.parametrize("tool, menus", [
    ("unlock_user_account", [501, 1045]),   # 登录日志 + 账户解锁
    ("force_logout_user", [109, 1048]),     # 在线用户 + 强退
])
def test_user_name_tools_reject_missing_self_and_super_admin(scripted_model, tool, menus):
    # 非超级管理员操作者（全部数据范围），否则操作 admin 时会先命中"当前用户"
    _, headers = user_with_menus(client, "tg_op", [MENU_AGENT, *menus], data_scope="1")
    for user_name, expected in (("tg_nobody", write_tools.msg_unavailable("tg_nobody")),
                                ("admin", write_tools.msg_super_admin("admin")),
                                ("ag_tg_op_u", write_tools.msg_self("ag_tg_op_u"))):
        pending, result = _propose(scripted_model, headers, tool, {"user_name": user_name})
        assert pending is None and result["error"] == expected


# ---------------------------------------------------------------------------
# 预览之后目标被改名、删除、删除重建：执行失败，且不会改到其他用户
# ---------------------------------------------------------------------------

def test_renamed_after_preview_fails(scripted_model):
    admin = auth_headers(client)
    target = add_user(client, admin, "tg_t", [])
    pending, _ = _propose(scripted_model, admin, "change_user_status", {"user_name": "tg_t", "status": "1"})
    _sql("UPDATE sys_user SET user_name = 'tg_renamed' WHERE user_id = :i", i=target)
    newcomer = add_user(client, admin, "tg_t", [])  # 又有人用了原来的用户名

    body = _approve(admin, pending["actionId"])
    action = body["data"]["action"]
    assert action["status"] == "FAILED" and action["error"] == write_tools.msg_changed("tg_t")
    assert _status(target) == "0" and _status(newcomer) == "0"


def test_deleted_and_recreated_after_preview_fails(scripted_model):
    admin = auth_headers(client)
    old = add_user(client, admin, "tg_t", [])
    pending, _ = _propose(scripted_model, admin, "change_user_status", {"user_name": "tg_t", "status": "1"})
    assert client.delete(f"/system/user/{old}", headers=admin).json()["code"] == 200
    new = add_user(client, admin, "tg_t", [])

    action = _approve(admin, pending["actionId"])["data"]["action"]
    assert action["status"] == "FAILED" and action["error"] == write_tools.msg_changed("tg_t")
    assert _status(new) == "0"


def test_force_logout_after_recreate_does_not_kick_new_user(scripted_model):
    admin = auth_headers(client)
    old = add_user(client, admin, "tg_t", [])
    login_and_get_token(client, "tg_t", PASSWORD)
    pending, _ = _propose(scripted_model, admin, "force_logout_user", {"user_name": "tg_t"})
    assert client.delete(f"/system/user/{old}", headers=admin).json()["code"] == 200
    new = add_user(client, admin, "tg_t", [])
    login_and_get_token(client, "tg_t", PASSWORD)
    new_sessions = _tokens_of(new)
    assert new_sessions

    action = _approve(admin, pending["actionId"])["data"]["action"]
    assert action["status"] == "FAILED" and action["error"] == write_tools.msg_changed("tg_t")
    assert _tokens_of(new) == new_sessions  # 新建的同名用户的会话没有被踢


def test_unlock_after_rename_fails(scripted_model):
    admin = auth_headers(client)
    target = add_user(client, admin, "tg_t", [])
    redis_client.set(PWD_ERR_CNT_KEY_PREFIX + "tg_t", 5, ex=300)
    pending, _ = _propose(scripted_model, admin, "unlock_user_account", {"user_name": "tg_t"})
    _sql("UPDATE sys_user SET user_name = 'tg_renamed' WHERE user_id = :i", i=target)
    action = _approve(admin, pending["actionId"])["data"]["action"]
    assert action["status"] == "FAILED"
    assert redis_client.get(PWD_ERR_CNT_KEY_PREFIX + "tg_t") == "5"


def test_target_moved_out_of_scope_after_preview_fails(scripted_model):
    """预览后目标被调到范围外的部门：执行时重新校验数据范围，失败"""
    _, headers = user_with_menus(client, "tg_op", [MENU_AGENT, MENU_USER, MENU_USER_EDIT], data_scope="3")
    target = add_user(client, auth_headers(client), "tg_t", [])
    pending, _ = _propose(scripted_model, headers, "change_user_status", {"user_name": "tg_t", "status": "1"})
    _sql("UPDATE sys_user SET dept_id = 103 WHERE user_id = :i", i=target)
    action = _approve(headers, pending["actionId"])["data"]["action"]
    assert action["status"] == "FAILED" and action["error"] == write_tools.msg_unavailable("tg_t")
    assert _status(target) == "0"
