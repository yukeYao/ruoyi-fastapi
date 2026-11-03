"""智能助手写操作审批表，对应 agent_action（sql/agent.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentAction(Base):
    __tablename__ = "agent_action"

    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(BigInteger)
    tool_call_id: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(64))
    args_json: Mapped[str] = mapped_column(String(2000))
    args_hash: Mapped[str] = mapped_column(String(64))
    preview_json: Mapped[str | None] = mapped_column(Text)
    perm: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    decided_by: Mapped[str | None] = mapped_column(String(64), default="")
    decided_time: Mapped[datetime | None] = mapped_column(DateTime)
    execute_start_time: Mapped[datetime | None] = mapped_column(DateTime)
    finish_time: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[str | None] = mapped_column(String(2000), default="")
    error: Mapped[str | None] = mapped_column(String(2000), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
