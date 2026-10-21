"""角色信息表，对应 sys_role"""
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysRole(Base):
    __tablename__ = "sys_role"

    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    role_name: Mapped[str] = mapped_column(String(30))
    role_key: Mapped[str] = mapped_column(String(100))
    role_sort: Mapped[int] = mapped_column(Integer)
    # 数据范围：1全部 2自定义 3本部门 4本部门及以下 5仅本人
    data_scope: Mapped[str | None] = mapped_column(CHAR(1), default="1")
    menu_check_strictly: Mapped[int | None] = mapped_column(SmallInteger, default=1)
    dept_check_strictly: Mapped[int | None] = mapped_column(SmallInteger, default=1)
    status: Mapped[str] = mapped_column(CHAR(1))
    del_flag: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    create_by: Mapped[str | None] = mapped_column(String(64), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64), default="")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(500))
