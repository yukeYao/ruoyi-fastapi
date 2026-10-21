"""岗位信息表，对应 sys_post"""
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysPost(Base):
    __tablename__ = "sys_post"

    post_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    post_code: Mapped[str] = mapped_column(String(64))
    post_name: Mapped[str] = mapped_column(String(50))
    post_sort: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(CHAR(1))
    create_by: Mapped[str | None] = mapped_column(String(64), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64), default="")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(500))
