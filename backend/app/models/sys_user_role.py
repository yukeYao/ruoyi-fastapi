"""用户和角色关联表，对应 sys_user_role（用户 N-1 角色）"""
from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysUserRole(Base):
    __tablename__ = "sys_user_role"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
