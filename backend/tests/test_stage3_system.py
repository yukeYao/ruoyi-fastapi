"""阶段3验收测试：用户/角色/菜单/部门/岗位/字典/参数 的系统管理接口"""
from fastapi.testclient import TestClient

from main import app
from tests.conftest import auth_headers

client = TestClient(app)


def _admin_headers():
    return auth_headers(client, "admin", "admin123")


# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------


def test_user_crud_flow_and_super_admin_guard():
    headers = _admin_headers()

    # 新增
    resp = client.post(
        "/system/user",
        headers=headers,
        json={
            "userName": "stage3_user1",
            "nickName": "测试用户1",
            "password": "Passw0rd!",
            "email": "stage3_user1@test.com",
            "phonenumber": "13900001111",
            "status": "0",
            "postIds": [],
            "roleIds": [2],
        },
    )
    assert resp.json()["code"] == 200

    # 查询列表能看到新用户
    resp = client.get("/system/user/list", headers=headers, params={"userName": "stage3_user1"})
    body = resp.json()
    assert body["total"] == 1
    user_id = body["rows"][0]["userId"]
    assert body["rows"][0]["userName"] == "stage3_user1"

    # 查询详情
    resp = client.get(f"/system/user/{user_id}", headers=headers)
    detail = resp.json()
    assert detail["data"]["nickName"] == "测试用户1"
    assert "password" not in detail["data"]

    # 修改
    resp = client.put(
        "/system/user",
        headers=headers,
        json={
            "userId": user_id,
            "nickName": "测试用户1-改",
            "email": "stage3_user1@test.com",
            "phonenumber": "13900001111",
            "status": "0",
            "postIds": [],
            "roleIds": [2],
        },
    )
    assert resp.json()["code"] == 200
    resp = client.get(f"/system/user/{user_id}", headers=headers)
    assert resp.json()["data"]["nickName"] == "测试用户1-改"

    # 不允许修改/重置超级管理员
    resp = client.put(
        "/system/user",
        headers=headers,
        json={"userId": 1, "nickName": "x", "status": "0", "postIds": [], "roleIds": []},
    )
    assert resp.json()["code"] == 500
    resp = client.put("/system/user/resetPwd", headers=headers, json={"userId": 1, "password": "hacked123"})
    assert resp.json()["code"] == 500

    # 不能删除自己
    resp = client.delete("/system/user/1", headers=headers)
    assert resp.json()["code"] == 500

    # 删除刚新增的用户
    resp = client.delete(f"/system/user/{user_id}", headers=headers)
    assert resp.json()["code"] == 200
    resp = client.get("/system/user/list", headers=headers, params={"userName": "stage3_user1"})
    assert resp.json()["total"] == 0


def test_duplicate_username_rejected():
    headers = _admin_headers()
    resp = client.post(
        "/system/user",
        headers=headers,
        json={"userName": "admin", "nickName": "x", "password": "Passw0rd!", "postIds": [], "roleIds": []},
    )
    assert resp.json()["code"] == 500
    assert "已存在" in resp.json()["msg"]


# ---------------------------------------------------------------------------
# 部门管理：ancestors 维护
# ---------------------------------------------------------------------------


def test_dept_ancestors_updated_when_parent_changes():
    headers = _admin_headers()

    # 在"深圳总公司"(101)下建一个新部门A
    resp = client.post(
        "/system/dept",
        headers=headers,
        json={"parentId": 101, "deptName": "stage3_deptA", "orderNum": 1, "status": "0"},
    )
    assert resp.json()["code"] == 200
    dept_a_id = client.get("/system/dept/list", headers=headers, params={"deptName": "stage3_deptA"}).json()["data"][0]["deptId"]

    # 在A下面建一个子部门B，B的ancestors此时应为 "0,100,101,{A}"
    resp = client.post(
        "/system/dept",
        headers=headers,
        json={"parentId": dept_a_id, "deptName": "stage3_deptB", "orderNum": 1, "status": "0"},
    )
    assert resp.json()["code"] == 200
    dept_b = client.get("/system/dept/list", headers=headers, params={"deptName": "stage3_deptB"}).json()["data"][0]
    assert dept_b["ancestors"] == f"0,100,101,{dept_a_id}"

    # 把A的父部门改成"长沙分公司"(102)，B的ancestors应该跟着变成 "0,100,102,{A}"
    resp = client.put(
        "/system/dept",
        headers=headers,
        json={"deptId": dept_a_id, "parentId": 102, "deptName": "stage3_deptA", "orderNum": 1, "status": "0"},
    )
    assert resp.json()["code"] == 200

    dept_b_after = client.get(f"/system/dept/{dept_b['deptId']}", headers=headers).json()["data"]
    assert dept_b_after["ancestors"] == f"0,100,102,{dept_a_id}"

    # 清理
    client.delete(f"/system/dept/{dept_b['deptId']}", headers=headers)
    client.delete(f"/system/dept/{dept_a_id}", headers=headers)


def test_dept_cannot_delete_with_children_or_users():
    headers = _admin_headers()
    # 101 "深圳总公司" 下面还有子部门，不能删
    resp = client.delete("/system/dept/101", headers=headers)
    assert resp.json()["code"] == 500
    # 103 "研发部门" 下面挂着 admin 用户，不能删
    resp = client.delete("/system/dept/103", headers=headers)
    assert resp.json()["code"] == 500


# ---------------------------------------------------------------------------
# 角色管理
# ---------------------------------------------------------------------------


def test_role_crud_and_cannot_delete_when_assigned():
    headers = _admin_headers()

    resp = client.post(
        "/system/role",
        headers=headers,
        json={"roleName": "stage3_role", "roleKey": "stage3_role", "roleSort": 10, "status": "0", "menuIds": [100]},
    )
    assert resp.json()["code"] == 200
    role_id = client.get("/system/role/list", headers=headers, params={"roleName": "stage3_role"}).json()["rows"][0]["roleId"]

    # 不允许操作超级管理员角色
    resp = client.put("/system/role/changeStatus", headers=headers, json={"roleId": 1, "status": "1"})
    assert resp.json()["code"] == 500
    resp = client.delete("/system/role/1", headers=headers)
    assert resp.json()["code"] == 500

    # 已分配给用户的角色不能删（role_id=2 "普通角色" 分配给了 ry）
    resp = client.delete("/system/role/2", headers=headers)
    assert resp.json()["code"] == 500
    assert "已分配" in resp.json()["msg"]

    # 没人用的新角色可以正常删除
    resp = client.delete(f"/system/role/{role_id}", headers=headers)
    assert resp.json()["code"] == 200


def test_role_menu_treeselect_reflects_assigned_menus():
    headers = _admin_headers()
    # role_id=2 "普通角色" 种子数据里 menu_check_strictly=1（严格模式），
    # menu_id=100(用户管理) 和它下面的按钮(1000"用户查询"等)都被分配了，
    # 严格模式下父节点应该被过滤掉，只留叶子节点 1000，前端据此把 100 显示成"半选"。
    resp = client.get("/system/menu/roleMenuTreeselect/2", headers=headers)
    body = resp.json()
    assert body["code"] == 200
    assert 1000 in body["checkedKeys"]
    assert 100 not in body["checkedKeys"]
    assert len(body["menus"]) > 0


# ---------------------------------------------------------------------------
# 数据权限：不同角色数据范围下，用户列表结果应该不同
# ---------------------------------------------------------------------------


def test_data_scope_dept_only_sees_own_department_users():
    headers = _admin_headers()

    dept_resp = client.post(
        "/system/dept", headers=headers, json={"parentId": 100, "deptName": "stage3_scope_dept", "orderNum": 1, "status": "0"}
    )
    assert dept_resp.json()["code"] == 200
    dept_id = client.get("/system/dept/list", headers=headers, params={"deptName": "stage3_scope_dept"}).json()["data"][0]["deptId"]

    # 角色：数据范围=本部门(3)，并授予 system:user:list 权限（menu_id=100）
    role_resp = client.post(
        "/system/role",
        headers=headers,
        json={"roleName": "stage3_scope_role", "roleKey": "stage3_scope_role", "roleSort": 20, "status": "0", "menuIds": [100]},
    )
    assert role_resp.json()["code"] == 200
    role_id = client.get("/system/role/list", headers=headers, params={"roleName": "stage3_scope_role"}).json()["rows"][0]["roleId"]
    client.put("/system/role/dataScope", headers=headers, json={"roleId": role_id, "dataScope": "3", "deptIds": []})

    # 在这个部门下建一个用户，绑定上面这个角色
    user_resp = client.post(
        "/system/user",
        headers=headers,
        json={
            "deptId": dept_id,
            "userName": "stage3_scope_user",
            "nickName": "数据权限测试",
            "password": "Passw0rd!",
            "status": "0",
            "postIds": [],
            "roleIds": [role_id],
        },
    )
    assert user_resp.json()["code"] == 200

    # 用这个新用户登录，看用户列表——应该只能看到自己（同部门），看不到 admin / ry
    login_headers = auth_headers(client, "stage3_scope_user", "Passw0rd!")
    resp = client.get("/system/user/list", headers=login_headers)
    body = resp.json()
    user_names = {row["userName"] for row in body["rows"]}
    assert user_names == {"stage3_scope_user"}


# ---------------------------------------------------------------------------
# 岗位管理
# ---------------------------------------------------------------------------


def test_post_crud_and_delete_guard_when_assigned():
    headers = _admin_headers()

    resp = client.post(
        "/system/post", headers=headers, json={"postCode": "stage3_post", "postName": "测试岗位", "postSort": 10, "status": "0"}
    )
    assert resp.json()["code"] == 200
    post_id = client.get("/system/post/list", headers=headers, params={"postCode": "stage3_post"}).json()["rows"][0]["postId"]

    # post_id=1 "董事长" 已分配给 admin，不能删
    resp = client.delete("/system/post/1", headers=headers)
    assert resp.json()["code"] == 500

    # 没人用的新岗位可以删
    resp = client.delete(f"/system/post/{post_id}", headers=headers)
    assert resp.json()["code"] == 200


# ---------------------------------------------------------------------------
# 字典 / 参数（只读）
# ---------------------------------------------------------------------------


def test_dict_data_by_type():
    headers = _admin_headers()
    resp = client.get("/system/dict/data/type/sys_user_sex", headers=headers)
    body = resp.json()
    assert body["code"] == 200
    values = {item["dictValue"] for item in body["data"]}
    assert values == {"0", "1", "2"}


def test_config_key_lookup():
    headers = _admin_headers()
    resp = client.get("/system/config/configKey/sys.user.initPassword", headers=headers)
    assert resp.json()["data"] == "123456"
