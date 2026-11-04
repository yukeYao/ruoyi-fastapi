"""
初始化数据库：创建 `ry-vue`（utf8mb4），导入 sql/ry_20260417.sql，再导入智能助手的 sql/agent.sql、sql/knowledge.sql。

不依赖本机 `mysql` 命令行（PATH 里没有），纯用 PyMySQL 执行。
用法：在 backend/ 目录下运行 `.venv/Scripts/python.exe scripts/init_db.py`
"""
import sys
from pathlib import Path

# 让脚本不论从哪个目录启动都能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql
from pymysql.constants import CLIENT

from app.config import settings

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
SQL_FILE = SQL_DIR / "ry_20260417.sql"
# 若依脚本会 drop 重建 sys_menu/sys_role_menu，Agent 的菜单必须在它之后导入
AGENT_SQL_FILE = SQL_DIR / "agent.sql"
# 知识库表（不依赖若依的表，顺序无要求）
KNOWLEDGE_SQL_FILE = SQL_DIR / "knowledge.sql"


def create_database_if_not_exists() -> None:
    """先不指定数据库连接，执行 CREATE DATABASE IF NOT EXISTS"""
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cursor:
            # 数据库名含短横线，需要用反引号包裹
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
                "DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_general_ci"
            )
        conn.commit()
        print(f"数据库 `{settings.mysql_database}` 已就绪")
    finally:
        conn.close()


def import_sql_file(sql_file: Path = SQL_FILE) -> None:
    """
    用 CLIENT.MULTI_STATEMENTS 一次性执行整份 SQL 脚本。
    之所以不手动按分号切分语句，是因为 sql 文件里公告表的插入数据包含大量
    带分号的 HTML 内容，简单按 ';' split 会把语句切坏；MULTI_STATEMENTS
    让 MySQL 自己按协议解析语句边界，更稳妥。
    """
    sql_text = sql_file.read_text(encoding="utf-8")

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        client_flag=CLIENT.MULTI_STATEMENTS,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql_text)
            # MULTI_STATEMENTS 模式下需要用 nextset() 把剩余的结果集都消费掉
            while cursor.nextset():
                pass
        conn.commit()
        print(f"已导入 {sql_file.name}")
    finally:
        conn.close()


def remove_agent_checkpoint_files() -> None:
    """
    agent.sql 会 drop 重建会话表，旧的 checkpoint 线程就成了没有归属的孤儿数据，所以一并删除。
    SQLite 在 WAL 模式下还会有 -wal、-shm 两个伴随文件，里面同样可能有未合并的对话内容。
    只在手动执行本脚本时调用：测试的 conftest 也会 import_sql_file，但不能删开发环境的文件。
    """
    base = settings.agent_checkpoint_file
    for path in (base, Path(str(base) + "-wal"), Path(str(base) + "-shm")):
        if path.exists():
            path.unlink()
            print(f"已删除 checkpoint 文件 {path}")


def remove_knowledge_index_files() -> None:
    """
    knowledge.sql 会清空"当前生效索引"那一行，旧的索引文件就和库对不上了，一并删除，之后重新运行
    scripts/build_knowledge_index.py。只删 settings.knowledge_index_dir 目录下本项目生成的 index-* 文件。
    只在手动执行本脚本时调用：conftest 也会 import 本模块，但测试用临时索引目录，且从不调用这个函数。
    """
    base = settings.knowledge_index_dir
    if not base.is_dir():
        return
    for path in base.glob("index-*"):
        if path.is_file():
            path.unlink()
            print(f"已删除知识库索引文件 {path}")


if __name__ == "__main__":
    create_database_if_not_exists()
    import_sql_file()
    import_sql_file(AGENT_SQL_FILE)
    import_sql_file(KNOWLEDGE_SQL_FILE)
    remove_agent_checkpoint_files()
    remove_knowledge_index_files()
    print("数据库初始化完成")
