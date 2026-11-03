"""
LangGraph checkpointer：官方 SqliteSaver（langgraph-checkpoint-sqlite）。

取舍（详见 docs/agent-design.md）：官方生产推荐 Postgres，不在本项目技术栈里；
langgraph-checkpoint-redis 需要 RedisJSON + RediSearch，本机 Redis 5 没有。学习项目单进程部署，
用一个 SQLite 文件足够；多进程/多实例部署时需要换成 Postgres。

- check_same_thread=False：FastAPI 同步路由在线程池里执行，不同请求可能在不同线程；
  SqliteSaver 内部用 threading.Lock 串行化对连接的访问（官方源码注释说明这样用是安全的）。
- 序列化用默认的 JsonPlusSerializer，不开 pickle_fallback：state 里只有消息和基本类型。
"""
import sqlite3
import threading
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import settings

_lock = threading.Lock()
_saver: SqliteSaver | None = None
_conn: sqlite3.Connection | None = None


def _open(path: Path) -> tuple[sqlite3.Connection, SqliteSaver]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return conn, saver


def get_checkpointer() -> SqliteSaver:
    global _saver, _conn
    with _lock:
        if _saver is None:
            _conn, _saver = _open(settings.agent_checkpoint_file)
        return _saver


def close_checkpointer() -> None:
    """关闭连接。测试用它模拟服务重启：关闭后下次 get_checkpointer 会重新打开文件"""
    global _saver, _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _saver, _conn = None, None
