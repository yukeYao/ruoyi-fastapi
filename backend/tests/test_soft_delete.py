"""软删除（del_flag='2'）的数据在按名称/关联查询时要被排除，对应若依 Mapper 里的 del_flag = '0' 条件"""
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models import SysRole
from main import app
from tests.conftest import auth_headers

client = TestClient(app)


def _admin_headers():
    return auth_headers(client, "admin", "admin123")


def _create_user(headers, user_name: str, role_ids: list[int]) -> int:
    body = {"userName": user_name, "nickName": user_name, "password": "Passw0rd!", "postIds": [], "roleIds": role_ids}
    assert client.post("/system/user", headers=headers, json=body).json()["code"] == 200
    return client.get("/system/user/list", headers=headers, params={"userName": user_name}).json()["rows"][0]["userId"]


def test_recreated_user_with_same_name_can_login():
    """删除用户后再新增同名用户，新用户可以正常登录；只剩已删除记录时登录失败"""
    headers = _admin_headers()
    old_id = _create_user(headers, "softdel_user", [2])
    assert client.delete(f"/system/user/{old_id}", headers=headers).json()["code"] == 200

    resp = client.post("/login", json={"username": "softdel_user", "password": "Passw0rd!"}).json()
    assert resp["code"] == 500

    new_id = _create_user(headers, "softdel_user", [2])
    assert new_id != old_id
    resp = client.post("/login", json={"username": "softdel_user", "password": "Passw0rd!"}).json()
    assert resp["code"] == 200 and resp["token"]

    client.delete(f"/system/user/{new_id}", headers=headers)


def test_deleted_role_excluded_from_login_roles_and_profile():
    """已删除角色不出现在登录态的角色/权限和个人中心的 roleGroup 里"""
    headers = _admin_headers()
    resp = client.post(
        "/system/role",
        headers=headers,
        json={"roleName": "softdel_role", "roleKey": "softdel_role", "roleSort": 40, "menuIds": [100]},
    )
    assert resp.json()["code"] == 200
    role_id = client.get("/system/role/list", headers=headers, params={"roleName": "softdel_role"}).json()["rows"][0]["roleId"]
    user_id = _create_user(headers, "softdel_role_user", [2, role_id])

    # 已分配用户的角色不能通过接口删除，这里直接把库里的 del_flag 置为删除
    db = SessionLocal()
    try:
        db.query(SysRole).filter(SysRole.role_id == role_id).update({SysRole.del_flag: "2"})
        db.commit()
    finally:
        db.close()

    try:
        user_headers = auth_headers(client, "softdel_role_user", "Passw0rd!")
        info = client.get("/getInfo", headers=user_headers).json()
        assert "softdel_role" not in info["roles"]
        assert "common" in info["roles"]

        profile = client.get("/system/user/profile", headers=user_headers).json()
        assert "softdel_role" not in profile["roleGroup"]
        assert profile["roleGroup"] == "普通角色"
    finally:
        client.delete(f"/system/user/{user_id}", headers=headers)


def test_deleted_child_dept_does_not_block_disabling_parent():
    """停用部门时只统计未删除的正常子部门（对应 selectNormalChildrenDeptById）"""
    headers = _admin_headers()

    def create(parent_id, name):
        body = {"parentId": parent_id, "deptName": name, "orderNum": 1, "status": "0"}
        assert client.post("/system/dept", headers=headers, json=body).json()["code"] == 200
        return client.get("/system/dept/list", headers=headers, params={"deptName": name}).json()["data"][0]["deptId"]

    parent_id = create(100, "softdel_parent")
    child_id = create(parent_id, "softdel_child")
    assert client.delete(f"/system/dept/{child_id}", headers=headers).json()["code"] == 200

    resp = client.put(
        "/system/dept",
        headers=headers,
        json={"deptId": parent_id, "parentId": 100, "deptName": "softdel_parent", "orderNum": 1, "status": "1"},
    )
    assert resp.json()["code"] == 200

    client.delete(f"/system/dept/{parent_id}", headers=headers)
