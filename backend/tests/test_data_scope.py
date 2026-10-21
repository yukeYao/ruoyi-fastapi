"""
数据权限：Java 中带 @DataScope 的查询（selectDeptList / selectRoleList / selectUserList /
selectAllocatedList / selectUnallocatedList）及经它们实现的 selectDeptTreeList、selectRoleAll、
check*DataScope，都要按登录用户的数据权限过滤。

场景：scope_user 在"测试部门"(105)，角色数据权限为"本部门"(3)；
范围外的对照数据：研发部门(103)、没有成员的角色 outside_role、研发部门下的用户 outside_user。
"""
import pytest
from fastapi.testclient import TestClient

from main import app
from tests.conftest import auth_headers

client = TestClient(app)

# 用户管理(100)、角色管理(101)、用户查询/新增/修改/重置密码(1000/1001/1002/1006)、角色查询(1007)
SCOPE_ROLE_MENUS = [100, 101, 1000, 1001, 1002, 1006, 1007]
NO_DEPT = "没有权限访问部门数据！"
NO_ROLE = "没有权限访问角色数据！"
NO_USER = "没有权限访问用户数据！"


def _add_role(admin, key, menu_ids):
    body = {"roleName": key, "roleKey": key, "roleSort": 50, "menuIds": menu_ids}
    assert client.post("/system/role", headers=admin, json=body).json()["code"] == 200
    return client.get("/system/role/list", headers=admin, params={"roleKey": key}).json()["rows"][0]["roleId"]


def _add_user(admin, name, dept_id, role_ids):
    body = {"userName": name, "nickName": name, "password": "Passw0rd!", "deptId": dept_id, "postIds": [], "roleIds": role_ids}
    assert client.post("/system/user", headers=admin, json=body).json()["code"] == 200
    return client.get("/system/user/list", headers=admin, params={"userName": name}).json()["rows"][0]["userId"]


@pytest.fixture
def scope():
    admin = auth_headers(client, "admin", "admin123")
    scope_role = _add_role(admin, "scope_role", SCOPE_ROLE_MENUS)
    client.put("/system/role/dataScope", headers=admin, json={"roleId": scope_role, "dataScope": "3", "deptIds": []})
    outside_role = _add_role(admin, "outside_role", [])
    scope_user = _add_user(admin, "scope_user", 105, [scope_role])
    outside_user = _add_user(admin, "outside_user", 103, [])

    yield {
        "admin": admin,
        "headers": auth_headers(client, "scope_user", "Passw0rd!"),
        "scope_role": scope_role,
        "outside_role": outside_role,
        "scope_user": scope_user,
        "outside_user": outside_user,
    }

    client.delete(f"/system/user/{scope_user},{outside_user}", headers=admin)
    client.delete(f"/system/role/{scope_role},{outside_role}", headers=admin)


def _tree_ids(nodes):
    ids = []
    for n in nodes:
        ids.append(n["id"])
        ids += _tree_ids(n.get("children", []))
    return ids


def _role_keys(roles):
    return {r["roleKey"] for r in roles}


# ---------------------------------------------------------------------------
# 查询类接口
# ---------------------------------------------------------------------------

def test_user_dept_tree_filtered_by_data_scope(scope):
    """用户管理左侧部门树（selectDeptTreeList -> selectDeptList）"""
    resp = client.get("/system/user/deptTree", headers=scope["headers"]).json()
    assert _tree_ids(resp["data"]) == [105]
    # 超级管理员不过滤
    admin_tree = client.get("/system/user/deptTree", headers=scope["admin"]).json()["data"]
    assert admin_tree[0]["id"] == 100 and {103, 105} <= set(_tree_ids(admin_tree))


def test_role_dept_tree_filtered(scope):
    """角色"数据权限"弹窗的部门树（同样是 selectDeptTreeList），修复前已正确，作回归保护"""
    resp = client.get(f"/system/role/deptTree/{scope['scope_role']}", headers=scope["headers"]).json()
    assert resp["code"] == 200 and _tree_ids(resp["depts"]) == [105]


def test_role_lists_filtered_by_data_scope(scope):
    """selectRoleAll 的三个使用处：用户新增/修改表单、分配角色页、角色下拉框；以及角色列表本身"""
    headers = scope["headers"]
    visible = {"common", "scope_role"}  # 普通角色下有 ry(105)，scope_role 下有 scope_user(105)

    form = client.get("/system/user/", headers=headers).json()
    assert _role_keys(form["roles"]) == visible

    auth = client.get(f"/system/user/authRole/{scope['scope_user']}", headers=headers).json()
    assert _role_keys(auth["roles"]) == visible

    options = client.get("/system/role/optionselect", headers=headers).json()
    assert _role_keys(options["data"]) == visible

    rows = client.get("/system/role/list", headers=headers).json()["rows"]
    assert _role_keys(rows) == visible

    # 超级管理员能看到范围外的角色
    admin_options = client.get("/system/role/optionselect", headers=scope["admin"]).json()["data"]
    assert "outside_role" in _role_keys(admin_options)


# ---------------------------------------------------------------------------
# check*DataScope 校验
# ---------------------------------------------------------------------------

def _msg(resp):
    body = resp.json()
    assert body["code"] == 500, body
    return body["msg"]


def test_add_and_edit_user_check_dept_and_role_scope(scope):
    headers = scope["headers"]
    base = {"userName": "scope_new", "nickName": "scope_new", "password": "Passw0rd!", "postIds": []}

    assert _msg(client.post("/system/user", headers=headers, json={**base, "deptId": 103, "roleIds": []})) == NO_DEPT
    assert _msg(client.post("/system/user", headers=headers, json={**base, "deptId": 105, "roleIds": [scope["outside_role"]]})) == NO_ROLE

    # 范围内的部门和角色可以正常新增
    resp = client.post("/system/user", headers=headers, json={**base, "deptId": 105, "roleIds": [scope["scope_role"]]})
    assert resp.json()["code"] == 200
    new_id = client.get("/system/user/list", headers=scope["admin"], params={"userName": "scope_new"}).json()["rows"][0]["userId"]

    edit = {"userId": new_id, "nickName": "scope_new", "postIds": [], "roleIds": [scope["scope_role"]]}
    assert _msg(client.put("/system/user", headers=headers, json={**edit, "deptId": 103})) == NO_DEPT
    assert _msg(client.put("/system/user", headers=headers, json={**edit, "deptId": 105, "roleIds": [scope["outside_role"]]})) == NO_ROLE
    assert client.put("/system/user", headers=headers, json={**edit, "deptId": 105}).json()["code"] == 200

    client.delete(f"/system/user/{new_id}", headers=scope["admin"])


def test_reset_pwd_and_change_status_check_user_scope(scope):
    headers = scope["headers"]
    outside = scope["outside_user"]
    assert _msg(client.put("/system/user/resetPwd", headers=headers, json={"userId": outside, "password": "NewPassw0rd!"})) == NO_USER
    assert _msg(client.put("/system/user/changeStatus", headers=headers, json={"userId": outside, "status": "1"})) == NO_USER

    # 范围内的用户可以操作
    resp = client.put("/system/user/resetPwd", headers=headers, json={"userId": scope["scope_user"], "password": "Passw0rd!"})
    assert resp.json()["code"] == 200


def test_auth_role_checks_user_and_role_scope(scope):
    headers = scope["headers"]
    resp = client.put("/system/user/authRole", headers=headers, params={"userId": scope["outside_user"], "roleIds": ""})
    assert _msg(resp) == NO_USER

    resp = client.put(
        "/system/user/authRole", headers=headers, params={"userId": scope["scope_user"], "roleIds": str(scope["outside_role"])}
    )
    assert _msg(resp) == NO_ROLE
