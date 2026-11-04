"""智能助手权限：接口权限、工具按权限过滤、硬调用拦截、数据范围与现有路由一致"""
import json

from fastapi.testclient import TestClient

from app.agent.policy import msg_no_permission
from app.agent.tools import TOOLS
from main import app
from tests.agent_helpers import (
    MENU_AGENT,
    MENU_ROLE,
    MENU_USER,
    call,
    chat,
    final,
    tool_msg,
    tool_results,
    user_with_menus,
)
from tests.conftest import auth_headers

client = TestClient(app)

ALL_TOOLS = sorted(TOOLS)


def test_without_agent_perm_returns_403(scripted_model):
    scripted_model(final())
    _, headers = user_with_menus(client, "noagent", [MENU_USER])
    assert chat(client, headers, "你好")["code"] == 403
    assert client.get("/agent/conversation/list", headers=headers).json()["code"] == 403
    assert client.get("/agent/conversation/abc/messages", headers=headers).json()["code"] == 403


def test_not_logged_in_returns_401(scripted_model):
    scripted_model(final())
    assert client.post("/agent/chat", json={"message": "你好"}).json()["code"] == 401


def test_model_only_sees_permitted_tools(scripted_model):
    fake = scripted_model(final("好的"))
    _, headers = user_with_menus(client, "onlyuser", [MENU_AGENT, MENU_USER])
    body = chat(client, headers, "查一下用户")
    assert body["code"] == 200, body
    # search_knowledge 只需要 agent:chat:use，所有能用智能助手的账号都有
    assert sorted(fake.bound_tool_names) == ["search_knowledge", "search_users"]


def test_admin_sees_all_tools(scripted_model):
    fake = scripted_model(final())
    chat(client, auth_headers(client), "你好")
    assert sorted(fake.bound_tool_names) == ALL_TOOLS


def test_write_tool_bound_only_with_its_perm(scripted_model):
    """写工具同样按权限过滤：有用户修改权限(1002)才看得到 change_user_status，看不到另外两个写工具"""
    fake = scripted_model(final())
    _, headers = user_with_menus(client, "editor", [MENU_AGENT, MENU_USER, 1002])
    chat(client, headers, "你好")
    assert sorted(fake.bound_tool_names) == ["change_user_status", "search_knowledge", "search_users"]


def test_user_without_any_business_perm_gets_only_knowledge_tool(scripted_model):
    """只有智能助手菜单：业务工具一个都看不到，只有知识库检索（同样只需要 agent:chat:use）"""
    fake = scripted_model(final("我只能查询知识库"))
    _, headers = user_with_menus(client, "notool", [MENU_AGENT])
    assert chat(client, headers, "你好")["code"] == 200
    assert fake.bound_tool_names == ["search_knowledge"]


def test_hard_call_to_unpermitted_tool_is_rejected(scripted_model):
    """模型（例如被注入）直接调用没绑定、也没权限的工具：只得到错误 ToolMessage，不执行"""
    c = call("search_login_logs", {"days": 7})
    scripted_model(tool_msg(c), final())
    _, headers = user_with_menus(client, "hardcall", [MENU_AGENT, MENU_USER])
    body = chat(client, headers, "查登录日志")
    result = tool_results(body)[c["id"]]
    assert result["status"] == "error"
    assert json.loads(result["content"]) == {"ok": False, "error": msg_no_permission(TOOLS["search_login_logs"])}


def _ids(body):
    return sorted(r["userId"] for r in json.loads(body)["untrusted_data"]["rows"])


def test_multi_role_data_scope_matches_route(scripted_model):
    """
    用户同时有"全部数据"角色（只有角色管理菜单）和"本部门"角色（有用户管理菜单）。
    查用户时只有拥有 system:user:list 的"本部门"角色参与数据权限计算，结果应与 /system/user/list 一致；
    如果工具没有带上 PERMISSION_CONTEXT_KEY，两个角色都会参与，"全部数据"会让结果变成全部用户。
    """
    from tests.agent_helpers import PASSWORD, add_role, add_user

    admin = auth_headers(client)
    all_role = add_role(client, admin, "ag_mr_all", [MENU_AGENT, MENU_ROLE], "1")
    dept_role = add_role(client, admin, "ag_mr_dept", [MENU_AGENT, MENU_USER], "3")
    add_user(client, admin, "ag_mr_u", [all_role, dept_role], dept_id=105)
    headers = auth_headers(client, "ag_mr_u", PASSWORD)

    route_ids = sorted(r["userId"] for r in client.get("/system/user/list", headers=headers, params={"pageSize": 20}).json()["rows"])
    c = call("search_users", {"page_size": 20})
    scripted_model(tool_msg(c), final())
    body = chat(client, headers, "查用户")
    agent_ids = _ids(tool_results(body)[c["id"]]["content"])

    all_users = client.get("/system/user/list", headers=admin, params={"pageSize": 20}).json()["total"]
    assert agent_ids == route_ids
    assert len(agent_ids) < all_users  # 确认确实被限制在本部门，而不是看到全部


def test_get_user_detail_out_of_scope_is_blocked(scripted_model):
    from tests.agent_helpers import MENU_USER_QUERY

    c = call("get_user_detail", {"user_id": 1})
    scripted_model(tool_msg(c), final())
    # 本部门(105)数据权限，查部门 103 的 admin
    _, headers = user_with_menus(client, "scope", [MENU_AGENT, MENU_USER, MENU_USER_QUERY], data_scope="3")
    result = tool_results(chat(client, headers, "查 admin"))[c["id"]]
    assert result["status"] == "error"
    assert "没有权限访问用户数据" in result["content"]
