"""
check_user_permission(user_name, perm)：服务端按目标用户的实际角色判断是否具备某权限。
覆盖：有/无权限、多角色、停用角色、停用菜单、超级管理员、范围外与不存在一致、两项权限缺一不绑定也不能硬调用、
参数校验、不泄露完整权限列表和范围外角色、R2 式排查不需要再去查别的角色。
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agent import policy
from app.agent.tools import (MSG_USER_UNAVAILABLE, NOTE_PERM_MISSING, NOTE_PERM_PRESENT, NOTE_USER_PERM_SESSION,
                             NOTE_VISIBLE_ROLES, TOOLS)
from app.core.database import SessionLocal
from main import app
from tests.agent_helpers import (
    MENU_AGENT,
    MENU_ROLE,
    MENU_USER,
    MENU_USER_QUERY,
    PASSWORD,
    add_role,
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

MENU_ROLE_QUERY = 1007   # system:role:query（按钮）
TOOL = "check_user_permission"
FIELDS = {"targetUser", "perm", "hasPerm", "matchedRoles", "checkedRoles"}


def _check(headers, user_name, perm="agent:chat:use", call_id="c1"):
    return _checks(headers, [(user_name, perm, call_id)])[call_id]


def _checks(headers, items):
    """一次对话里发出若干 check_user_permission 调用，返回 {调用 id: 工具结果}"""
    calls = [call(TOOL, {"user_name": u, "perm": p}, cid) for u, p, cid in items]
    _fake_installer["install"](tool_msg(*calls), final("好的"))
    results = tool_results(chat(client, headers, "查权限"))
    return {c["id"]: json.loads(results[c["id"]]["content"]) for c in calls}


# scripted_model 夹具的安装函数：辅助函数里要用，autouse 夹具把它存在这里
_fake_installer = {}


@pytest.fixture(autouse=True)
def _scripted(scripted_model):
    _fake_installer["install"] = scripted_model
    yield


def _names(roles):
    return [r["roleName"] for r in roles]


def test_user_with_permission():
    admin = auth_headers(client)
    role = add_role(client, admin, "cup_yes_r", [MENU_AGENT])
    add_user(client, admin, "cup_yes_u", [role])
    payload = _check(admin, "cup_yes_u")
    data = payload["untrusted_data"]
    assert set(data) == FIELDS
    assert data["targetUser"] == "cup_yes_u" and data["perm"] == "agent:chat:use" and data["hasPerm"] is True
    assert _names(data["matchedRoles"]) == ["cup_yes_r"] and _names(data["checkedRoles"]) == ["cup_yes_r"]
    # 外层可信说明：数据库配置判断，当前会话通常要重新登录
    assert payload["note"].startswith(NOTE_USER_PERM_SESSION) and "重新登录" in payload["note"]


def test_user_without_permission():
    admin = auth_headers(client)
    role = add_role(client, admin, "cup_no_r", [MENU_USER])
    add_user(client, admin, "cup_no_u", [role])
    payload = _check(admin, "cup_no_u")
    data = payload["untrusted_data"]
    assert data["hasPerm"] is False and data["matchedRoles"] == [] and _names(data["checkedRoles"]) == ["cup_no_r"]
    # 没有权限：外层可信说明明确需要管理员授权、只重新登录不能解决，不出现"可能需要重新登录"的提示
    assert payload["note"] == NOTE_USER_PERM_SESSION + NOTE_PERM_MISSING + NOTE_VISIBLE_ROLES
    assert "管理员" in payload["note"] and "只重新登录不能解决" in payload["note"]
    assert NOTE_PERM_PRESENT not in payload["note"]


def test_note_depends_only_on_conclusion():
    """有权限时才提示"可能需要重新登录"；停用角色导致没有权限时，同样给出"需要管理员授权"的说明"""
    admin = auth_headers(client)
    role = add_role(client, admin, "cup_note_r", [MENU_AGENT])
    add_user(client, admin, "cup_note_u", [role])
    present = _check(admin, "cup_note_u")
    assert present["untrusted_data"]["hasPerm"] is True
    assert present["note"] == NOTE_USER_PERM_SESSION + NOTE_PERM_PRESENT + NOTE_VISIBLE_ROLES
    assert NOTE_PERM_MISSING not in present["note"]
    assert client.put("/system/role/changeStatus", headers=admin, json={"roleId": role, "status": "1"}).json()["code"] == 200
    missing = _check(admin, "cup_note_u")
    assert missing["untrusted_data"]["hasPerm"] is False
    assert missing["note"] == NOTE_USER_PERM_SESSION + NOTE_PERM_MISSING + NOTE_VISIBLE_ROLES


def test_multiple_roles_only_matching_one_listed():
    admin = auth_headers(client)
    r1 = add_role(client, admin, "cup_multi_a_r", [MENU_USER])
    r2 = add_role(client, admin, "cup_multi_b_r", [MENU_AGENT])
    add_user(client, admin, "cup_multi_u", [r1, r2])
    data = _check(admin, "cup_multi_u")["untrusted_data"]
    assert data["hasPerm"] is True and _names(data["matchedRoles"]) == ["cup_multi_b_r"]
    assert sorted(_names(data["checkedRoles"])) == ["cup_multi_a_r", "cup_multi_b_r"]


def test_disabled_role_does_not_grant():
    admin = auth_headers(client)
    role = add_role(client, admin, "cup_dis_r", [MENU_AGENT])
    add_user(client, admin, "cup_dis_u", [role])
    assert client.put("/system/role/changeStatus", headers=admin, json={"roleId": role, "status": "1"}).json()["code"] == 200
    data = _check(admin, "cup_dis_u")["untrusted_data"]
    assert data["hasPerm"] is False and data["matchedRoles"] == []
    assert data["checkedRoles"] == [{"roleName": "cup_dis_r", "status": "1"}]


def test_disabled_menu_does_not_grant():
    """停用的菜单不带来权限（与登录时加载权限的规则一致）。用 system:role:query 按钮测试，测试结束恢复"""
    admin = auth_headers(client)
    role = add_role(client, admin, "cup_menu_r", [MENU_ROLE, MENU_ROLE_QUERY])
    add_user(client, admin, "cup_menu_u", [role])
    assert _check(admin, "cup_menu_u", "system:role:query")["untrusted_data"]["hasPerm"] is True
    db = SessionLocal()
    try:
        db.execute(text("UPDATE sys_menu SET status = '1' WHERE menu_id = :m"), {"m": MENU_ROLE_QUERY})
        db.commit()
        assert _check(admin, "cup_menu_u", "system:role:query")["untrusted_data"]["hasPerm"] is False
    finally:
        db.execute(text("UPDATE sys_menu SET status = '0' WHERE menu_id = :m"), {"m": MENU_ROLE_QUERY})
        db.commit()
        db.close()


def test_super_admin_has_everything():
    payload = _check(auth_headers(client), "admin", "monitor:online:forceLogout")
    assert payload["untrusted_data"]["hasPerm"] is True and "超级管理员" in payload["note"]


def test_out_of_scope_and_missing_are_identical():
    _, headers = user_with_menus(client, "cupscope", [MENU_AGENT, MENU_USER, MENU_USER_QUERY, MENU_ROLE, MENU_ROLE_QUERY],
                                 data_scope="3")
    results = _checks(headers, [("admin", "agent:chat:use", "out"), ("cup_no_such_user", "agent:chat:use", "missing")])
    assert results["out"] == results["missing"] == {"ok": False, "error": MSG_USER_UNAVAILABLE}


@pytest.mark.parametrize("menus", [
    [MENU_AGENT, MENU_USER, MENU_USER_QUERY],                    # 缺 system:role:query
    [MENU_AGENT, MENU_ROLE, MENU_ROLE_QUERY],                    # 缺 system:user:query
])
def test_missing_either_perm_not_bound_and_hard_call_rejected(menus):
    fake = _fake_installer["install"](tool_msg(call(TOOL, {"user_name": "admin", "perm": "agent:chat:use"}, "h1")),
                                      final("好的"))
    _, headers = user_with_menus(client, f"cupperm{len(menus)}{menus[1]}", menus)
    body = chat(client, headers, "查权限")
    assert TOOL not in fake.bound_tool_names
    payload = json.loads(tool_results(body)["h1"]["content"])
    assert payload == {"ok": False, "error": policy.msg_no_permission(TOOLS[TOOL])}
    assert "system:user:query" in payload["error"] and "system:role:query" in payload["error"]


def test_bound_with_both_perms():
    fake = _fake_installer["install"](final("好的"))
    _, headers = user_with_menus(client, "cupboth", [MENU_AGENT, MENU_USER, MENU_USER_QUERY, MENU_ROLE, MENU_ROLE_QUERY])
    chat(client, headers, "你好")
    assert TOOL in fake.bound_tool_names


@pytest.mark.parametrize("perm,field", [
    ("agent:chat", "perm"), ("agent:chat:use; drop table", "perm"), ("a" * 50 + ":b:" + "c" * 60, "perm"),
])
def test_invalid_perm_rejected(perm, field):
    payload = _check(auth_headers(client), "admin", perm)
    assert payload["ok"] is False and payload["error"].startswith(policy.MSG_INVALID_ARGS) and field in payload["error"]
    assert perm not in payload["error"]


def test_no_full_perm_list_or_other_user_fields():
    admin = auth_headers(client)
    role = add_role(client, admin, "cup_leak_r", [MENU_USER, MENU_AGENT])
    add_user(client, admin, "cup_leak_u", [role], phonenumber="13912345678", email="leak@example.com")
    payload = _check(admin, "cup_leak_u")
    text_ = json.dumps(payload, ensure_ascii=False)
    assert set(payload["untrusted_data"]) == FIELDS
    # 不返回角色的完整权限列表，也不返回用户的其他字段
    assert "system:user:list" not in text_ and "1391234" not in text_ and "leak@" not in text_
    assert "userId" not in text_ and "roleId" not in text_


def test_roles_outside_role_scope_are_not_named_or_counted():
    """
    操作者查用户的数据范围（本部门）和查角色的数据范围（仅本人）来自不同角色：目标用户在范围内，
    但他的角色不在操作者的角色数据范围内——结论照常计算；角色名不列出，note 与没有范围外角色时完全相同，不提数量
    """
    admin = auth_headers(client)
    user_side = add_role(client, admin, "cup_us_r", [MENU_AGENT, MENU_USER, MENU_USER_QUERY], data_scope="3")
    role_side = add_role(client, admin, "cup_rs_r", [MENU_ROLE, MENU_ROLE_QUERY], data_scope="5")
    add_user(client, admin, "cup_op_u", [user_side, role_side], dept_id=105)
    hidden = add_role(client, admin, "cup_hidden_r", [MENU_AGENT])
    add_user(client, admin, "cup_target_u", [hidden], dept_id=105)
    # 对照：操作者自己（角色都在他的角色数据范围内，没有范围外角色）
    headers = auth_headers(client, "cup_op_u", PASSWORD)
    results = _checks(headers, [("cup_target_u", "agent:chat:use", "hidden"), ("cup_op_u", "agent:chat:use", "visible")])
    with_hidden, without_hidden = results["hidden"], results["visible"]
    assert with_hidden["untrusted_data"]["hasPerm"] is True
    assert with_hidden["untrusted_data"]["matchedRoles"] == [] and with_hidden["untrusted_data"]["checkedRoles"] == []
    assert _names(without_hidden["untrusted_data"]["checkedRoles"]) == ["cup_us_r", "cup_rs_r"]
    # note 完全相同，不出现数量
    assert with_hidden["note"] == without_hidden["note"] == NOTE_USER_PERM_SESSION + NOTE_PERM_PRESENT + NOTE_VISIBLE_ROLES
    text_ = json.dumps(with_hidden, ensure_ascii=False)
    assert "cup_hidden_r" not in text_ and "1 个" not in text_


def test_r2_style_flow_needs_no_other_role():
    """排查"为什么某用户看不到智能助手"：一次 check_user_permission 就拿到该用户自己角色的结论，不需要再去查别的角色"""
    admin = auth_headers(client)
    role = add_role(client, admin, "cup_r2_r", [MENU_USER])
    add_user(client, admin, "cup_r2_u", [role])
    fake = _fake_installer["install"](tool_msg(call(TOOL, {"user_name": "cup_r2_u", "perm": "agent:chat:use"}, "p1")),
                                      final("cup_r2_u 的角色 cup_r2_r 没有 agent:chat:use。"))
    body = chat(client, admin, "为什么 cup_r2_u 看不到智能助手")
    data = json.loads(tool_results(body)["p1"]["content"])["untrusted_data"]
    assert data["hasPerm"] is False and _names(data["checkedRoles"]) == ["cup_r2_r"]
    called = [c["name"] for m in body["data"]["messages"] if m["type"] == "ai" for c in m["toolCalls"]]
    assert called == [TOOL] and "get_role_permissions" not in called and len(fake.calls) == 2
