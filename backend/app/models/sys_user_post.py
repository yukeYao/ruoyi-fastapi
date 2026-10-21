"""用户与岗位关联表，对应 sys_user_post（用户 1-N 岗位）"""
from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysUserPost(Base):
    __tablename__ = "sys_user_post"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
