"""数据权限过滤规则，对应 DataScopeAspect.dataScopeFilter：停用角色跳过、按当前接口权限字符挑选角色"""
import pytest
from fastapi.testclient import TestClient

from app.core.constants import PERMISSION_CONTEXT_KEY
from app.core.data_scope import build_data_scope_filter
from main import app
from tests.conftest import auth_headers

client = TestClient(app)


def _user(*roles, perm=None):
    login_user = {"user_id": 99, "dept_id": 105, "roles": list(roles)}
    if perm is not None:
        login_user[PERMISSION_CONTEXT_KEY] = perm
    return login_user


def _role(role_id, scope, status="0", perms=("system:user:list",)):
    return {"role_id": role_id, "data_scope": scope, "status": status, "permissions": list(perms)}


# ---------------------------------------------------------------------------
# 单元测试：build_data_scope_filter
# ---------------------------------------------------------------------------

def test_disabled_role_is_skipped():
    # 停用的"全部数据权限"角色不生效，只剩本部门
    expr = build_data_scope_filter(_user(_role(10, "1", status="1", perms=()), _role(11, "3")), "d", "u")
    assert expr == "(d.dept_id = 105)"


def test_roles_filtered_by_permission_context():
    roles = (_role(10, "1", perms=("system:role:list",)), _role(11, "3", perms=("system:user:list",)))
    # 查用户列表：只有拥有 system:user:list 的角色参与 -> 本部门
    assert build_data_scope_filter(_user(*roles, perm="system:user:list")) == "(d.dept_id = 105)"
    # 查角色列表：只有拥有 system:role:list 的角色参与 -> 全部
    assert build_data_scope_filter(_user(*roles, perm="system:role:list")) is None
    # 没有权限字符上下文（不经 has_perm 的接口）：所有正常角色参与 -> 全部
    assert build_data_scope_filter(_user(*roles)) is None
    # 逗号分隔的多个权限字符，命中任意一个即可
    assert build_data_scope_filter(_user(*roles, perm="system:menu:list,system:role:list")) is None


def test_no_role_matches_sees_nothing():
    expr = build_data_scope_filter(_user(_role(10, "3", perms=("system:post:list",)), perm="system:user:list"))
    assert expr == "(d.dept_id = 0)"


def test_multiple_custom_roles_merged_and_same_scope_counted_once():
    expr = build_data_scope_filter(_user(_role(10, "2"), _role(11, "2"), _role(12, "3"), _role(13, "3")))
    assert expr == "(d.dept_id IN (SELECT dept_id FROM sys_role_dept WHERE role_id IN (10,11)) OR d.dept_id = 105)"


# ---------------------------------------------------------------------------
# 接口测试：用户同时有两个角色
# ---------------------------------------------------------------------------

def _add_role(admin, key, menu_ids, data_scope):
    body = {"roleName": key, "roleKey": key, "roleSort": 60, "menuIds": menu_ids}
    assert client.post("/system/role", headers=admin, json=body).json()["code"] == 200
    role_id = client.get("/system/role/list", headers=admin, params={"roleKey": key}).json()["rows"][0]["roleId"]
    client.put("/system/role/dataScope", headers=admin, json={"roleId": role_id, "dataScope": data_scope, "deptIds": []})
    return role_id


@pytest.fixture
def two_roles(request):
    """ds_all：全部数据权限；ds_dept：本部门 + 用户管理菜单。用户 ds_user 在测试部门(105)"""
    all_menus, all_disabled = request.param
    admin = auth_headers(client, "admin", "admin123")
    all_role = _add_role(admin, "ds_all", all_menus, "1")
    dept_role = _add_role(admin, "ds_dept", [100], "3")
    if all_disabled:
        client.put("/system/role/changeStatus", headers=admin, json={"roleId": all_role, "status": "1"})
    body = {"userName": "ds_user", "nickName": "ds_user", "password": "Passw0rd!", "deptId": 105, "postIds": [], "roleIds": [all_role, dept_role]}
    assert client.post("/system/user", headers=admin, json=body).json()["code"] == 200
    user_id = client.get("/system/user/list", headers=admin, params={"userName": "ds_user"}).json()["rows"][0]["userId"]

    yield auth_headers(client, "ds_user", "Passw0rd!")

    client.delete(f"/system/user/{user_id}", headers=admin)
    client.delete(f"/system/role/{all_role},{dept_role}", headers=admin)


def _user_list_dept_ids(headers):
    rows = client.get("/system/user/list", headers=headers, params={"pageSize": 100}).json()["rows"]
    return {r["deptId"] for r in rows}


@pytest.mark.parametrize("two_roles", [([100], True)], indirect=True)
def test_disabled_all_scope_role_does_not_widen_user_list(two_roles):
    assert _user_list_dept_ids(two_roles) == {105}


@pytest.mark.parametrize("two_roles", [([101], False)], indirect=True)
def test_data_scope_follows_endpoint_permission(two_roles):
    # 用户列表（system:user:list）只按 ds_dept 的"本部门"过滤
    assert _user_list_dept_ids(two_roles) == {105}
    # 角色列表（system:role:list）只按 ds_all 的"全部"过滤：能看到超级管理员角色（其成员 admin 在研发部门）
    rows = client.get("/system/role/list", headers=two_roles, params={"pageSize": 100}).json()["rows"]
    assert "admin" in {r["roleKey"] for r in rows}
