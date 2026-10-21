"""系统访问记录表，对应 sys_logininfor"""
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysLogininfor(Base):
    __tablename__ = "sys_logininfor"

    info_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_name: Mapped[str | None] = mapped_column(String(50), default="")
    ipaddr: Mapped[str | None] = mapped_column(String(128), default="")
    login_location: Mapped[str | None] = mapped_column(String(255), default="")
    browser: Mapped[str | None] = mapped_column(String(50), default="")
    os: Mapped[str | None] = mapped_column(String(50), default="")
    # 登录状态：0成功 1失败
    status: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    msg: Mapped[str | None] = mapped_column(String(255), default="")
    login_time: Mapped[datetime | None] = mapped_column(DateTime)
