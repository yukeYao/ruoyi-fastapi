"""
读取 backend/.env 配置。
使用 pydantic-settings，字段名与 .env 中的变量名一一对应（大小写不敏感）。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 用绝对路径定位 backend/.env，避免因启动时的工作目录不同导致读取不到配置
BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = BACKEND_DIR / ".env"


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

    # 智能助手：模型走 OpenAI 兼容协议。密钥只放 .env 或环境变量，不写进仓库；
    # 没配置 AGENT_API_KEY 时 /agent/chat 直接提示未配置，不会去猜默认值
    agent_model: str = ""
    agent_api_key: str = ""
    agent_base_url: str = ""
    # LangGraph checkpoint 的 SQLite 文件，相对路径按 backend/ 解析；backend/data/ 已加入 .gitignore
    agent_checkpoint_path: str = "data/agent_checkpoints.sqlite"

    # 知识库 Embedding：同样走 OpenAI 兼容协议，Key 只放 .env。未配置时知识库检索不可用（安全失败），
    # 构建索引脚本必须显式指定 --fake 才会用假 Embedding（只用于开发和测试）
    agent_embedding_model: str = ""
    agent_embedding_api_key: str = ""
    agent_embedding_base_url: str = ""
    # 本地 faiss 索引目录，相对路径按 backend/ 解析；backend/data/ 已加入 .gitignore。
    # pytest 在 conftest 里改成临时目录，绝不读写开发索引
    knowledge_index_path: str = "data/knowledge_index"

    @property
    def sqlalchemy_database_uri(self) -> str:
        # ry-vue 数据库名含短横线，PyMySQL 连接字符串直接拼接即可，无需额外转义
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )


    @property
    def agent_checkpoint_file(self) -> Path:
        path = Path(self.agent_checkpoint_path)
        return path if path.is_absolute() else BACKEND_DIR / path

    @property
    def knowledge_index_dir(self) -> Path:
        path = Path(self.knowledge_index_path)
        return path if path.is_absolute() else BACKEND_DIR / path


# 全局单例，其他模块直接 `from app.config import settings` 使用
settings = Settings()
