"""字典数据表，对应 sys_dict_data"""
from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysDictData(Base):
    __tablename__ = "sys_dict_data"

    dict_code: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dict_sort: Mapped[int | None] = mapped_column(Integer, default=0)
    dict_label: Mapped[str | None] = mapped_column(String(100), default="")
    dict_value: Mapped[str | None] = mapped_column(String(100), default="")
    dict_type: Mapped[str | None] = mapped_column(String(100), default="")
    css_class: Mapped[str | None] = mapped_column(String(100))
    list_class: Mapped[str | None] = mapped_column(String(100))
    is_default: Mapped[str | None] = mapped_column(CHAR(1), default="N")
    status: Mapped[str | None] = mapped_column(CHAR(1), default="0")
    create_by: Mapped[str | None] = mapped_column(String(64), default="")
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    update_by: Mapped[str | None] = mapped_column(String(64), default="")
    update_time: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(500))
