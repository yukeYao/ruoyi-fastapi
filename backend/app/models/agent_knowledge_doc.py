"""智能助手知识文档表，对应 agent_knowledge_doc（sql/knowledge.sql）"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentKnowledgeDoc(Base):
    __tablename__ = "agent_knowledge_doc"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    # JSON 数组；需全部满足，空数组表示所有智能助手用户可读
    required_perms: Mapped[str] = mapped_column(String(500), default="[]")
    sources: Mapped[str] = mapped_column(String(1000), default="[]")
    content_hash: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer)
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
