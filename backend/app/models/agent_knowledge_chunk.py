"""智能助手知识分块表，对应 agent_knowledge_chunk（sql/knowledge.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentKnowledgeChunk(Base):
    __tablename__ = "agent_knowledge_chunk"

    chunk_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(100))
    section: Mapped[str] = mapped_column(String(200), default="")
    seq: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    # Embedding 输入文本（标题 + 章节 + 内容）的哈希：未变化时复用旧向量，不重新调用 Embedding
    content_hash: Mapped[str] = mapped_column(String(64))
    # 冗余文档的权限：检索前直接按它确定可读集合
    required_perms: Mapped[str] = mapped_column(String(500), default="[]")
    vector_id: Mapped[int] = mapped_column(BigInteger)
    embedding_model: Mapped[str] = mapped_column(String(100))
    index_version: Mapped[str] = mapped_column(String(32))
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
