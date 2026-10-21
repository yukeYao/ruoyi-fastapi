"""
读取 backend/.env 配置。
使用 pydantic-settings，字段名与 .env 中的变量名一一对应（大小写不敏感）。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 用绝对路径定位 backend/.env，避免因启动时的工作目录不同导致读取不到配置
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    # MySQL
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "ry-vue"

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # JWT
    jwt_secret: str = "dev-secret"
    jwt_algorithm: str = "HS256"

    # 服务
    app_port: int = 8080

    @property
    def sqlalchemy_database_uri(self) -> str:
        # ry-vue 数据库名含短横线，PyMySQL 连接字符串直接拼接即可，无需额外转义
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )


# 全局单例，其他模块直接 `from app.config import settings` 使用
settings = Settings()
