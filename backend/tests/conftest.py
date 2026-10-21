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
