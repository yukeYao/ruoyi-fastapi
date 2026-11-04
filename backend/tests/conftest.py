"""
pytest 全局夹具：所有测试都连接独立的测试库 `ry-vue-test` 和 Redis db 1，
不污染开发数据（见 TASK.md 第7节）。

必须在最开头、任何 app.* 模块被 import 之前设置好环境变量，
这样 app.config.settings 在创建时读到的就是测试库配置。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["MYSQL_DATABASE"] = "ry-vue-test"
os.environ["REDIS_DB"] = "1"
# 测试不调用真实模型：清掉可能来自 .env 的配置，Agent 测试用 ScriptedChatModel 显式注入
os.environ["AGENT_API_KEY"] = ""
os.environ["AGENT_EMBEDDING_API_KEY"] = ""
# 知识库索引一律放临时目录：测试会构建、删除、篡改索引文件，绝不能碰开发环境的 backend/data/knowledge_index
os.environ["KNOWLEDGE_INDEX_PATH"] = tempfile.mkdtemp(prefix="ruoyi-test-knowledge-")

import pymysql
import pytest

from scripts.init_db import AGENT_SQL_FILE, KNOWLEDGE_SQL_FILE, create_database_if_not_exists, import_sql_file


@pytest.fixture(scope="session", autouse=True)
def init_test_database():
    """每次测试会话开始前，重建测试库的表结构和初始数据"""
    create_database_if_not_exists()
    import_sql_file()
    # 连续导入两次，验证 agent.sql 可重复执行（drop 重建，每次测试会话都从空表开始）
    import_sql_file(AGENT_SQL_FILE)
    import_sql_file(AGENT_SQL_FILE)
    # 同样连续导入两次，验证 knowledge.sql 可重复执行
    import_sql_file(KNOWLEDGE_SQL_FILE)
    import_sql_file(KNOWLEDGE_SQL_FILE)
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


# ---------------------------------------------------------------------------
# 智能助手测试夹具
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_checkpoint(tmp_path, monkeypatch):
    """每个用例使用独立的临时 checkpoint 文件，不碰开发环境的 backend/data/"""
    from app.agent import checkpointer
    from app.config import settings

    checkpointer.close_checkpointer()
    path = tmp_path / "agent_checkpoints.sqlite"
    monkeypatch.setattr(settings, "agent_checkpoint_path", str(path))
    yield path
    checkpointer.close_checkpointer()


@pytest.fixture
def scripted_model(monkeypatch, agent_checkpoint):
    """
    返回一个函数：传入脚本，得到注入到 /agent/chat 的 ScriptedChatModel。
    路由每次请求都调用 get_chat_model()，这里让它总是返回同一个实例，测试结束后可以检查
    模型看到的工具和消息。
    """
    from app.agent import model as agent_model

    def _install(*script):
        fake = agent_model.ScriptedChatModel(script=list(script))
        monkeypatch.setattr(agent_model, "get_chat_model", lambda: fake)
        return fake

    return _install


@pytest.fixture(autouse=True)
def cleanup_agent_test_data():
    """删除 tests/agent_helpers 建的用户和角色（只在用到这些 helper 的用例里有数据）"""
    yield
    from fastapi.testclient import TestClient

    from main import app
    from tests.agent_helpers import cleanup_created

    cleanup_created(TestClient(app))
