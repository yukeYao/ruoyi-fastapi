"""智能助手会话表，对应 agent_conversation（sql/agent.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentConversation(Base):
    __tablename__ = "agent_conversation"

    # 同时作为 LangGraph checkpoint 的 thread_id
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(100), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
