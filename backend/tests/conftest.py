"""
pytest 全局夹具：所有测试都连接独立的测试库 `ry-vue-test` 和 Redis db 1，
不污染开发数据（见 TASK.md 第7节）。

必须在最开头、任何 app.* 模块被 import 之前设置好环境变量，
这样 app.config.settings 在创建时读到的就是测试库配置。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["MYSQL_DATABASE"] = "ry-vue-test"
os.environ["REDIS_DB"] = "1"

import pymysql
import pytest

from scripts.init_db import create_database_if_not_exists, import_sql_file


@pytest.fixture(scope="session", autouse=True)
def init_test_database():
    """每次测试会话开始前，重建测试库的表结构和初始数据"""
    create_database_if_not_exists()
    import_sql_file()
    yield


@pytest.fixture(autouse=True)
def clean_redis_test_db():
    """每个测试用例结束后清空 Redis 测试库，避免 token/验证码等状态串台"""
    from app.core.redis_client import redis_client

    yield
    redis_client.flushdb()


def _set_captcha_enabled(enabled: bool) -> None:
    from app.config import settings

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
    """大部分测试不关心验证码本身，全局关闭它简化登录流程；验证码专项测试里再临时开启"""
    _set_captcha_enabled(False)
    yield
    _set_captcha_enabled(False)


def login_and_get_token(client, username="admin", password="admin123") -> str:
    """测试通用：登录并返回 token（调用前验证码必须是关闭状态）"""
    resp = client.post("/login", json={"username": username, "password": password})
    return resp.json()["token"]


def auth_headers(client, username="admin", password="admin123") -> dict:
    return {"Authorization": f"Bearer {login_and_get_token(client, username, password)}"}
