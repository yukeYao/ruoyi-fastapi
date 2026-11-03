"""
缺权限时不绕路：能力边界、无权限提示、替代写操作仍需审批、重复调用与无进展的提前结束。

来源：评估中操作者缺少写权限时，模型反复调用 search_users 直到 8 步上限（S2），
或在缺少强制下线权限时改用"停用账号"代替（S6）。修复不放宽任何权限或审批：
- 服务端按工具注册表和当前权限生成"能力边界"，写进本次运行的系统上下文，无权限的工具仍不绑定；
- 同一轮里重复相同调用、连续无进展时由服务端提前结束，并说明原因，不靠步数上限。
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agent import graph as agent_graph
from app.agent import policy
from app.agent.tools import TOOLS
from app.core.database import SessionLocal
from main import app
from tests.agent_helpers import (
    MENU_AGENT,
    MENU_ONLINE,
    MENU_USER,
    MENU_USER_QUERY,
    PASSWORD,
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


def _sql(query, **params):
    db = SessionLocal()
    try:
        return db.execute(text(query), params).fetchall()
    finally:
        db.close()


def _status(name):
    return _sql("SELECT status FROM sys_user WHERE user_name = :n AND del_flag = '0'", n=name)[0][0]


def _actions(conv_id):
    return _sql("SELECT tool_name, status FROM agent_action WHERE conversation_id = :c", c=conv_id)


def _system_text(fake, step=0):
    return fake.calls[step][0].content


def _readonly_operator(prefix):
    """有查询权限、没有任何写权限"""
    return user_with_menus(client, prefix, [MENU_AGENT, MENU_USER, MENU_USER_QUERY, MENU_ONLINE])


def _no_logout_operator(prefix):
    """能改状态、能看在线用户，但没有强制下线权限"""
    return user_with_menus(client, prefix, [MENU_AGENT, MENU_USER, MENU_USER_QUERY, MENU_USER_EDIT, MENU_ONLINE])


# ---------------------------------------------------------------------------
# 能力边界
# ---------------------------------------------------------------------------

def test_missing_edit_perm_boundary_is_in_trusted_context(scripted_model):
    fake = scripted_model(final("当前账号没有修改用户状态的权限，请联系管理员"))
    _, headers = _readonly_operator("cap_ro")
    assert chat(client, headers, "把 ry 停用")["code"] == 200

    # 无权限的写工具仍然不绑定
    assert "change_user_status" not in fake.bound_tool_names
    system = _system_text(fake)
    # 能力边界由服务端从工具注册表生成：写明缺哪个能力、需要哪个权限
    for name in ("change_user_status", "unlock_user_account", "force_logout_user"):
        assert name in system and TOOLS[name].perm in system
    # 有权限的工具不出现在"无权使用"清单里
    assert policy.capability_boundary({"permissions": ["*:*:*"]}) == ""


def test_boundary_matches_registry_for_partial_perms(scripted_model):
    fake = scripted_model(final("无法执行"))
    _, headers = _no_logout_operator("cap_nl")
    chat(client, headers, "让 ry 下线")
    boundary = policy.capability_boundary({"permissions": [TOOLS[n].perm for n in fake.bound_tool_names]})
    assert boundary and boundary in _system_text(fake)
    assert "force_logout_user" in boundary and "monitor:online:forceLogout" in boundary
    assert "change_user_status" not in boundary


def test_boundary_not_persisted_in_checkpoint(scripted_model):
    """能力边界是每次运行时按当前权限生成的上下文，不写进会话历史（权限变化后不会沿用旧说明）"""
    fake = scripted_model(final("好的"))
    _, headers = _readonly_operator("cap_cp")
    body = chat(client, headers, "你好")
    history = client.get(f"/agent/conversation/{body['data']['conversationId']}/messages", headers=headers).json()
    assert all("monitor:online:forceLogout" not in m["content"] for m in history["data"])
    assert "monitor:online:forceLogout" in _system_text(fake)


# ---------------------------------------------------------------------------
# 无权限的调用、替代写操作
# ---------------------------------------------------------------------------

def test_hard_call_without_force_logout_perm_names_missing_perm(scripted_model):
    add_user(client, auth_headers(client), "cap_t", [])
    auth_headers(client, "cap_t", PASSWORD)  # 目标有一个在线会话
    c = call("force_logout_user", {"user_name": "cap_t"}, "w1")
    scripted_model(tool_msg(c), final("当前账号没有强制下线权限"))
    _, headers = _no_logout_operator("cap_hl")
    body = chat(client, headers, "让 cap_t 下线")
    error = json.loads(tool_results(body)["w1"]["content"])["error"]
    assert error == policy.msg_no_permission(TOOLS["force_logout_user"])
    assert "monitor:online:forceLogout" in error
    assert _actions(body["data"]["conversationId"]) == [] and body["data"]["pendingAction"] is None


def test_substitute_write_still_requires_approval_and_preview_states_real_effect(scripted_model):
    """模型坚持用"停用"代替"下线"：服务端不推断意图、不放宽也不改写，照常生成审批，预览写明实际影响"""
    add_user(client, auth_headers(client), "cap_t", [])
    auth_headers(client, "cap_t", PASSWORD)
    scripted_model(tool_msg(call("change_user_status", {"user_name": "cap_t", "status": "1"}, "w1")), final("已提交"))
    _, headers = _no_logout_operator("cap_sub")
    body = chat(client, headers, "让 cap_t 立即下线")
    pending = body["data"]["pendingAction"]
    assert pending["toolName"] == "change_user_status" and pending["status"] == "PENDING"
    risk = pending["preview"]["risk"]
    assert "不会自动下线" in risk and "1 个" in risk  # 写明当前在线会话数及停用不会让它们下线
    assert _status("cap_t") == "0"

    client.post(f"/agent/action/{pending['actionId']}/reject", headers=headers)
    assert _status("cap_t") == "0"


def test_repeated_write_after_rejection_is_not_proposed_again(scripted_model):
    """用户刚拒绝，模型在同一轮里坚持再提交同一个写操作：不再生成审批，直接结束并说明"""
    add_user(client, auth_headers(client), "cap_t", [])
    args = {"user_name": "cap_t", "status": "1"}
    fake = scripted_model(tool_msg(call("change_user_status", args, "w1")),
                          tool_msg(call("change_user_status", args, "w2")),
                          final("不应该走到这里"))
    admin = auth_headers(client)
    body = chat(client, admin, "停用 cap_t")
    resp = client.post(f"/agent/action/{body['data']['pendingAction']['actionId']}/reject", headers=admin).json()
    data = resp["data"]
    assert data["pendingAction"] is None
    assert data["reply"].startswith(policy.MSG_LOOP_STOPPED)
    assert len(_actions(body["data"]["conversationId"])) == 1
    assert fake.script and _status("cap_t") == "0"


# ---------------------------------------------------------------------------
# 重复调用与无进展
# ---------------------------------------------------------------------------

def test_repeated_identical_search_stops_without_step_limit(scripted_model):
    _, headers = _readonly_operator("cap_rep")
    search = {"user_name": "ry"}
    fake = scripted_model(*[tool_msg(call("search_users", search, f"r{i}")) for i in range(8)], final("不应该走到这里"))
    body = chat(client, headers, "把 ry 停用")
    data = body["data"]
    # 第二次相同调用不执行，服务端直接结束本轮：只调用了 2 次模型，远未到 8 步上限
    assert len(fake.calls) == 2
    assert data["reply"] != agent_graph.MSG_STEP_LIMIT
    assert data["reply"].startswith(policy.MSG_LOOP_STOPPED)
    # 说明里带上能力边界：缺修改权限、联系管理员
    assert "system:user:edit" in data["reply"] and "管理员" in data["reply"]
    results = [m for m in data["messages"] if m["type"] == "tool"]
    assert json.loads(results[-1]["content"])["error"] == policy.MSG_REPEATED_CALL


def test_no_progress_stops_early(scripted_model):
    """参数不同但结果没有新信息（连续空结果）：连续无进展达到上限即结束"""
    admin = auth_headers(client)
    names = ["cap_nobody_1", "cap_nobody_2", "cap_nobody_3", "cap_nobody_4", "cap_nobody_5"]
    fake = scripted_model(*[tool_msg(call("search_users", {"user_name": n})) for n in names], final("不应该走到这里"))
    data = chat(client, admin, "找一下这个人")["data"]
    assert len(fake.calls) == 1 + policy.AGENT_MAX_STALL_ROUNDS
    assert data["reply"].startswith(policy.MSG_LOOP_STOPPED)


def test_progress_keeps_running(scripted_model):
    """每次都拿到新信息时正常继续，不会被误判"""
    admin = auth_headers(client)
    fake = scripted_model(tool_msg(call("search_users", {"user_name": "ry"})),
                          tool_msg(call("list_roles", {})),
                          tool_msg(call("list_departments", {})),
                          final("汇总完成"))
    assert chat(client, admin, "汇总一下")["data"]["reply"] == "汇总完成"
    assert len(fake.calls) == 4


def test_guard_resets_for_new_message(scripted_model):
    """新的一条用户消息重新计数：上一轮查过的内容可以再查"""
    admin = auth_headers(client)
    scripted_model(tool_msg(call("search_users", {"user_name": "ry"})), final("第一次"),
                   tool_msg(call("search_users", {"user_name": "ry"})), final("第二次"))
    conv = chat(client, admin, "查 ry")["data"]["conversationId"]
    assert chat(client, admin, "再查一次 ry", conv)["data"]["reply"] == "第二次"
