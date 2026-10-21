"""菜单权限表，对应 sys_menu"""
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysMenu(Base):
    __tablename__ = "sys_menu"

    menu_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    menu_name: Mapped[str] = mapped_column(String(50))
    parent_id: Mapped[int | None] = mapped_column(BigInteger, default=0)
    order_num: Mapped[int | None] = mapped_column(Integer, default=0)
    path: Mapped[str | None] = mapped_column(String(200), default="")
    component: Mapped[str | None] = mapped_column(String(255))
    query: Mapped[str | None] = mapped_column(String(255))
    route_name: Mapped[str | None] = mapped_column(String(50), default="")
    is_frame: Mapped[int | None] = mapped_column(SmallInteger, default=1)
    is_cache: Mapped[int | None] = mapped_column(SmallInteger, default=0)
    # 菜单类型：M目录 C菜单 F按钮
    menu_type: Mapped[str | None] = mapped_column(CHAR(1), default="")
    # 菜单状态：0显示 1隐藏
    visible: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    # 菜单状态：0正常 1停用
    status: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    perms: Mapped[str | None] = mapped_column(String(100))
    icon: Mapped[str | None] = mapped_column(String(100), default="#")
    create_by: Mapped[str | None] = mapped_column(String(64), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64), default="")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(500), default="")
