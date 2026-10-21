"""参数配置表，对应 sys_config"""
from datetime import datetime

from sqlalchemy import CHAR, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysConfig(Base):
    __tablename__ = "sys_config"

    config_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_name: Mapped[str | None] = mapped_column(String(100), default="")
    config_key: Mapped[str | None] = mapped_column(String(100), default="")
    config_value: Mapped[str | None] = mapped_column(String(500), default="")
    config_type: Mapped[str | None] = mapped_column(CHAR(1), default="N")
    create_by: Mapped[str | None] = mapped_column(String(64), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64), default="")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(500))
