"""
CLAUDE.md "已知问题"的回归测试（Agent 依赖这两处，开发 Agent 前先修复）：
- check_user_data_scope 只查前 1000 条用户再在内存里找，第 1001 个之后的用户会被误判为越权；
- 在线用户列表用 KEYS 遍历 Redis，KEYS 会阻塞 Redis，生产环境应改为 SCAN。
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from main import app
from tests.conftest import auth_headers

client = TestClient(app)

_BULK_PREFIX = "bulk_scope_"


@pytest.fixture
def many_users_in_dept_105():
    """在 ry 的数据范围内（部门 105）批量插入 1000 个用户，再插入目标用户，使目标排在第 1001 位之后"""
    db = SessionLocal()
    now = datetime.now()
    rows = [
        {"name": f"{_BULK_PREFIX}{i}", "now": now}
        for i in range(1000)
    ]
    db.execute(
        text(
            "INSERT INTO sys_user (dept_id, user_name, nick_name, status, del_flag, create_by, create_time) "
            "VALUES (105, :name, :name, '0', '0', 'admin', :now)"
        ),
        rows,
    )
    db.execute(
        text(
            "INSERT INTO sys_user (dept_id, user_name, nick_name, status, del_flag, create_by, create_time) "
            "VALUES (105, 'bulk_target', 'bulk_target', '0', '0', 'admin', :now)"
        ),
        {"now": now},
    )
    db.commit()
    target_id = db.execute(text("SELECT user_id FROM sys_user WHERE user_name = 'bulk_target'")).scalar_one()
    db.close()
    yield target_id
    db = SessionLocal()
    db.execute(text("DELETE FROM sys_user WHERE user_name LIKE :p OR user_name = 'bulk_target'"), {"p": _BULK_PREFIX + "%"})
    db.commit()
    db.close()


def test_user_data_scope_check_not_limited_to_first_1000(many_users_in_dept_105):
    # ry 的角色 2 数据范围是自定义（含部门 105），目标用户在范围内，只是排在 1000 名之后
    headers = auth_headers(client, "ry", "admin123")
    body = client.get(f"/system/user/{many_users_in_dept_105}", headers=headers).json()
    assert body["code"] == 200, body
    assert body["data"]["userName"] == "bulk_target"


def test_user_data_scope_check_still_blocks_out_of_scope_user():
    # 修复后仍要拦截范围外用户：admin(部门 103) 不在 ry 的自定义范围(100/101/105)内
    headers = auth_headers(client, "ry", "admin123")
    body = client.get("/system/user/1", headers=headers).json()
    assert body["code"] == 500
    assert body["msg"] == "没有权限访问用户数据！"


def test_online_list_uses_scan_instead_of_keys(monkeypatch):
    headers = auth_headers(client, "admin", "admin123")

    def _forbidden_keys(*args, **kwargs):
        raise AssertionError("不允许使用 KEYS，会阻塞 Redis")

    monkeypatch.setattr(redis_client, "keys", _forbidden_keys)
    body = client.get("/monitor/online/list", headers=headers).json()
    assert body["code"] == 200
    assert any(row["userName"] == "admin" for row in body["rows"])
