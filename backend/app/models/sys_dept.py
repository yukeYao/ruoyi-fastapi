"""部门表，对应 sys_dept"""
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysDept(Base):
    __tablename__ = "sys_dept"

    dept_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, default=0)
    ancestors: Mapped[str | None] = mapped_column(String(50), default="")
    dept_name: Mapped[str | None] = mapped_column(String(30), default="")
    order_num: Mapped[int | None] = mapped_column(Integer, default=0)
    leader: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(11))
    email: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    del_flag: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    create_by: Mapped[str | None] = mapped_column(String(64), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64), default="")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
