"""字典类型表，对应 sys_dict_type"""
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysDictType(Base):
    __tablename__ = "sys_dict_type"

    dict_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dict_name: Mapped[str | None] = mapped_column(String(100), default="")
    dict_type: Mapped[str | None] = mapped_column(String(100), default="")
    status: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    create_by: Mapped[str | None] = mapped_column(String(64), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64), default="")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(500))
