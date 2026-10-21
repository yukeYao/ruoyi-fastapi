"""用户信息表，对应 sys_user"""
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysUser(Base):
    __tablename__ = "sys_user"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dept_id: Mapped[int | None] = mapped_column(BigInteger)
    user_name: Mapped[str] = mapped_column(String(30))
    nick_name: Mapped[str] = mapped_column(String(30))
    user_type: Mapped[str | None] = mapped_column(String(2), default="00")
    email: Mapped[str | None] = mapped_column(String(50), default="")
    phonenumber: Mapped[str | None] = mapped_column(String(11), default="")
    sex: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    avatar: Mapped[str | None] = mapped_column(String(100), default="")
    password: Mapped[str | None] = mapped_column(String(100), default="")
    status: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    del_flag: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    login_ip: Mapped[str | None] = mapped_column(String(128), default="")
    login_date: Mapped[datetime | None] = mapped_column(DateTime)
    pwd_update_date: Mapped[datetime | None] = mapped_column(DateTime)
    create_by: Mapped[str | None] = mapped_column(String(64), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64), default="")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(500))
