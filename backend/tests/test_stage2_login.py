"""阶段2验收测试：验证码 / 登录 / getInfo / getRouters / 鉴权 / 密码锁定"""
import pymysql
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.redis_client import redis_client
from main import app

client = TestClient(app)


def _set_captcha_enabled(enabled: bool) -> None:
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE sys_config SET config_value=%s WHERE config_key='sys.account.captchaEnabled'",
                ("true" if enabled else "false",),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def disable_captcha():
    """登录相关大部分测试不关心验证码本身，先关闭它简化流程；验证码专项测试里再临时开启"""
    _set_captcha_enabled(False)
    yield
    _set_captcha_enabled(False)


def _login(username="admin", password="admin123", code=None, uuid=None):
    return client.post("/login", json={"username": username, "password": password, "code": code, "uuid": uuid})


def test_captcha_image_disabled():
    resp = client.get("/captchaImage")
    body = resp.json()
    assert body["code"] == 200
    assert body["captchaEnabled"] is False


def test_captcha_image_enabled_and_validate():
    """开启验证码后：能拿到 uuid/img，答案能从 Redis 读到，且是一次性的"""
    _set_captcha_enabled(True)
    resp = client.get("/captchaImage")
    body = resp.json()
    assert body["captchaEnabled"] is True
    assert "uuid" in body and "img" in body

    answer = redis_client.get(f"captcha_codes:{body['uuid']}")
    assert answer is not None

    # 用错误验证码登录，答案会被立即删除（一次性）
    resp = _login(code="0", uuid=body["uuid"])
    assert resp.json()["code"] == 500
    assert redis_client.get(f"captcha_codes:{body['uuid']}") is None


def test_full_login_flow_and_getinfo_getrouters():
    resp = _login("admin", "admin123")
    body = resp.json()
    assert body["code"] == 200
    token = body["token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/getInfo", headers=headers)
    info = resp.json()
    assert info["code"] == 200
    assert info["user"]["userName"] == "admin"
    assert "password" not in info["user"]
    assert info["roles"] == ["admin"]
    assert info["permissions"] == ["*:*:*"]

    resp = client.get("/getRouters", headers=headers)
    routers = resp.json()
    assert routers["code"] == 200
    assert len(routers["data"]) > 0
    # 第一个一级目录应该是"系统管理"，component 应该是 Layout
    system_dir = next(r for r in routers["data"] if r["path"] == "/system")
    assert system_dir["component"] == "Layout"
    assert system_dir["alwaysShow"] is True
    assert system_dir["children"][0]["path"] == "user"


def test_login_wrong_password():
    resp = _login("admin", "wrong-password")
    body = resp.json()
    assert body["code"] == 500
    assert "密码" in body["msg"] or "不存在" in body["msg"]


def test_login_user_not_exists():
    resp = _login("no-such-user", "whatever")
    assert resp.json()["code"] == 500


def test_password_retry_lockout():
    for _ in range(5):
        resp = _login("admin", "wrong-password")
        assert resp.json()["code"] == 500

    resp = _login("admin", "admin123")
    body = resp.json()
    assert body["code"] == 500
    assert "锁定" in body["msg"]


def test_no_token_returns_401():
    resp = client.get("/getInfo")
    assert resp.status_code == 200
    assert resp.json()["code"] == 401


def test_invalid_token_returns_401():
    resp = client.get("/getInfo", headers={"Authorization": "Bearer invalid-token"})
    assert resp.json()["code"] == 401


def test_user_without_permission_gets_403():
    """种子角色 ry/common 几乎分配了全部菜单权限，专门建一个零权限角色才能测出 403"""
    admin_headers = {"Authorization": f"Bearer {_login('admin', 'admin123').json()['token']}"}

    role_resp = client.post(
        "/system/role",
        headers=admin_headers,
        json={"roleName": "no_perm_role", "roleKey": "no_perm_role", "roleSort": 99, "status": "0", "menuIds": []},
    )
    assert role_resp.json()["code"] == 200
    role_id = client.get("/system/role/list", headers=admin_headers, params={"roleName": "no_perm_role"}).json()["rows"][0]["roleId"]

    user_resp = client.post(
        "/system/user",
        headers=admin_headers,
        json={
            "userName": "no_perm_user",
            "nickName": "无权限用户",
            "password": "Passw0rd!",
            "status": "0",
            "postIds": [],
            "roleIds": [role_id],
        },
    )
    assert user_resp.json()["code"] == 200

    resp = _login("no_perm_user", "Passw0rd!")
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/system/user/list", headers=headers)
    assert resp.json()["code"] == 403


def test_normal_user_has_limited_role_and_no_admin_permission():
    """普通用户 ry 登录后角色不是 admin，权限不是 *:*:*"""
    resp = _login("ry", "admin123")
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    info = client.get("/getInfo", headers=headers).json()
    assert info["roles"] == ["common"]
    assert "*:*:*" not in info["permissions"]
    assert "system:user:list" in info["permissions"]


def test_logout_invalidates_token():
    resp = _login("admin", "admin123")
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/getInfo", headers=headers).json()["code"] == 200

    logout_resp = client.post("/logout", headers=headers)
    assert logout_resp.json()["code"] == 200

    assert client.get("/getInfo", headers=headers).json()["code"] == 401
