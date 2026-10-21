"""审计字段：新增填 create_by/create_time，修改填 update_by/update_time（对应若依 BaseEntity + Mapper 里的 sysdate()）"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.conftest import auth_headers

client = TestClient(app)


def _admin_headers():
    return auth_headers(client, "admin", "admin123")


def _assert_recent(time_str: str | None):
    assert time_str, "时间字段为空"
    delta = abs((datetime.now() - datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")).total_seconds())
    assert delta < 60


# 每个模块：新增入参、按名称查 id、修改入参、详情地址、删除地址
MODULES = {
    "user": dict(
        url="/system/user",
        create={"userName": "audit_user", "nickName": "审计用户", "password": "Passw0rd!", "postIds": [], "roleIds": [2]},
        find=lambda h: client.get("/system/user/list", headers=h, params={"userName": "audit_user"}).json()["rows"][0]["userId"],
        update=lambda i: {"userId": i, "nickName": "审计用户-改", "postIds": [], "roleIds": [2]},
    ),
    "role": dict(
        url="/system/role",
        create={"roleName": "audit_role", "roleKey": "audit_role", "roleSort": 30, "menuIds": []},
        find=lambda h: client.get("/system/role/list", headers=h, params={"roleName": "audit_role"}).json()["rows"][0]["roleId"],
        update=lambda i: {"roleId": i, "roleName": "audit_role", "roleKey": "audit_role", "roleSort": 31, "menuIds": []},
    ),
    "menu": dict(
        url="/system/menu",
        create={"menuName": "audit_menu", "parentId": 0, "orderNum": 99, "path": "audit", "menuType": "M"},
        find=lambda h: client.get("/system/menu/list", headers=h, params={"menuName": "audit_menu"}).json()["data"][0]["menuId"],
        update=lambda i: {"menuId": i, "menuName": "audit_menu", "parentId": 0, "orderNum": 98, "path": "audit", "menuType": "M"},
    ),
    "dept": dict(
        url="/system/dept",
        create={"parentId": 100, "deptName": "audit_dept", "orderNum": 1, "status": "0"},
        find=lambda h: client.get("/system/dept/list", headers=h, params={"deptName": "audit_dept"}).json()["data"][0]["deptId"],
        update=lambda i: {"deptId": i, "parentId": 100, "deptName": "audit_dept", "orderNum": 2, "status": "0"},
    ),
    "post": dict(
        url="/system/post",
        create={"postCode": "audit_post", "postName": "审计岗位", "postSort": 1, "status": "0"},
        find=lambda h: client.get("/system/post/list", headers=h, params={"postCode": "audit_post"}).json()["rows"][0]["postId"],
        update=lambda i: {"postId": i, "postCode": "audit_post", "postName": "审计岗位-改", "postSort": 2, "status": "0"},
    ),
}


@pytest.fixture
def created(request):
    """按模块新增一条数据，返回 (模块配置, id)，测试结束后删除"""
    headers = _admin_headers()
    module = MODULES[request.param]
    assert client.post(module["url"], headers=headers, json=module["create"]).json()["code"] == 200
    entity_id = module["find"](headers)
    yield module, entity_id
    client.delete(f"{module['url']}/{entity_id}", headers=headers)


def _detail(module, entity_id) -> dict:
    return client.get(f"{module['url']}/{entity_id}", headers=_admin_headers()).json()["data"]


@pytest.mark.parametrize("created", list(MODULES), indirect=True)
def test_create_and_update_fill_audit_fields(created):
    module, entity_id = created

    data = _detail(module, entity_id)
    assert data["createBy"] == "admin"
    _assert_recent(data["createTime"])
    assert not data["updateBy"] and data["updateTime"] is None
    create_time = data["createTime"]

    assert client.put(module["url"], headers=_admin_headers(), json=module["update"](entity_id)).json()["code"] == 200
    data = _detail(module, entity_id)
    assert data["updateBy"] == "admin"
    _assert_recent(data["updateTime"])
    # 修改不能动创建信息
    assert data["createBy"] == "admin" and data["createTime"] == create_time


@pytest.mark.parametrize(
    "created, path, body",
    [
        ("user", "/system/user/resetPwd", lambda i: {"userId": i, "password": "NewPassw0rd!"}),
        ("user", "/system/user/changeStatus", lambda i: {"userId": i, "status": "1"}),
        ("role", "/system/role/changeStatus", lambda i: {"roleId": i, "status": "1"}),
        ("role", "/system/role/dataScope", lambda i: {"roleId": i, "dataScope": "3", "deptIds": []}),
    ],
    indirect=["created"],
)
def test_other_modify_endpoints_fill_update_fields(created, path, body):
    module, entity_id = created
    assert client.put(path, headers=_admin_headers(), json=body(entity_id)).json()["code"] == 200
    data = _detail(module, entity_id)
    assert data["updateBy"] == "admin"
    _assert_recent(data["updateTime"])


def test_profile_update_fills_update_fields_with_self():
    """个人中心修改资料/密码：操作人是当前登录用户自己"""
    # 单独用一个用户名：前面的用例会软删除 audit_user，而登录查用户时没有排除已删除的同名记录
    admin = _admin_headers()
    module = MODULES["user"]
    body = {**module["create"], "userName": "audit_profile"}
    assert client.post(module["url"], headers=admin, json=body).json()["code"] == 200
    user_id = client.get("/system/user/list", headers=admin, params={"userName": "audit_profile"}).json()["rows"][0]["userId"]
    headers = auth_headers(client, "audit_profile", "Passw0rd!")

    try:
        resp = client.put("/system/user/profile", headers=headers, json={"nickName": "审计用户-个人中心"})
        assert resp.json()["code"] == 200
        data = _detail(module, user_id)
        assert data["updateBy"] == "audit_profile"
        _assert_recent(data["updateTime"])

        resp = client.put(
            "/system/user/profile/updatePwd", headers=headers, json={"oldPassword": "Passw0rd!", "newPassword": "NewPassw0rd!"}
        )
        assert resp.json()["code"] == 200
        assert _detail(module, user_id)["updateBy"] == "audit_profile"
    finally:
        client.delete(f"/system/user/{user_id}", headers=admin)
