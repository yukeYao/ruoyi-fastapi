"""首页铃铛用到的通知公告最小接口：listTop / markRead / markReadAll"""
from fastapi.testclient import TestClient

from main import app
from tests.conftest import auth_headers

client = TestClient(app)


def test_list_top_requires_login():
    resp = client.get("/system/notice/listTop")
    assert resp.status_code == 200
    assert resp.json()["code"] == 401


def test_list_top_returns_empty_list_for_logged_in_user():
    # 普通用户 ry 也能访问（Java 版没有权限注解，只要求登录）
    headers = auth_headers(client, "ry", "admin123")
    body = client.get("/system/notice/listTop", headers=headers).json()
    assert body == {"code": 200, "msg": "操作成功", "data": [], "unreadCount": 0}


def test_mark_read_endpoints_return_success():
    headers = auth_headers(client, "admin", "admin123")
    assert client.post("/system/notice/markRead", headers=headers, params={"noticeId": 1}).json()["code"] == 200
    assert client.post("/system/notice/markReadAll", headers=headers, params={"ids": "1,2"}).json()["code"] == 200
    assert client.post("/system/notice/markRead").json()["code"] == 401
