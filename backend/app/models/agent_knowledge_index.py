"""智能助手知识库当前生效的索引，对应 agent_knowledge_index（sql/knowledge.sql），只有 id=1 一行"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentKnowledgeIndex(Base):
    __tablename__ = "agent_knowledge_index"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    build_id: Mapped[str] = mapped_column(String(64))
    index_version: Mapped[str] = mapped_column(String(32))
    embedding_model: Mapped[str] = mapped_column(String(100))
    dimension: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    vectors_digest: Mapped[str] = mapped_column(String(64))
    built_time: Mapped[datetime | None] = mapped_column(DateTime)
