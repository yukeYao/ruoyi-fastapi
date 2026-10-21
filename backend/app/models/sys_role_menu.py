"""角色和菜单关联表，对应 sys_role_menu（角色 1-N 菜单）"""
from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysRoleMenu(Base):
    __tablename__ = "sys_role_menu"

    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    menu_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
