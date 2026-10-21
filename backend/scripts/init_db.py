"""
初始化数据库：创建 `ry-vue`（utf8mb4）并导入 sql/ry_20260417.sql。

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

SQL_FILE = Path(__file__).resolve().parent.parent / "sql" / "ry_20260417.sql"


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


def import_sql_file() -> None:
    """
    用 CLIENT.MULTI_STATEMENTS 一次性执行整份 SQL 脚本。
    之所以不手动按分号切分语句，是因为 sql 文件里公告表的插入数据包含大量
    带分号的 HTML 内容，简单按 ';' split 会把语句切坏；MULTI_STATEMENTS
    让 MySQL 自己按协议解析语句边界，更稳妥。
    """
    sql_text = SQL_FILE.read_text(encoding="utf-8")

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
        print(f"已导入 {SQL_FILE.name}")
    finally:
        conn.close()


if __name__ == "__main__":
    create_database_if_not_exists()
    import_sql_file()
    print("数据库初始化完成")
