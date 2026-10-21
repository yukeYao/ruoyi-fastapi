"""
数据库连接：engine、SessionLocal、get_db 依赖。
使用同步 SQLAlchemy（不用 async），对应若依的 Mapper 层数据访问。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# pool_pre_ping=True：每次取连接前先探活，避免 MySQL 空闲超时断开后报错
engine = create_engine(settings.sqlalchemy_database_uri, pool_pre_ping=True, echo=False)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""
    pass


def get_db():
    """FastAPI 依赖：每个请求一个 Session，请求结束后自动关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
