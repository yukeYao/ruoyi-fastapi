"""角色和部门关联表，对应 sys_role_dept（角色 1-N 部门，用于数据权限的"自定义"数据范围）"""
from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysRoleDept(Base):
    __tablename__ = "sys_role_dept"

    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dept_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
