"""操作日志记录表，对应 sys_oper_log"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SysOperLog(Base):
    __tablename__ = "sys_oper_log"

    oper_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str | None] = mapped_column(String(50), default="")
    # 业务类型：0其它 1新增 2修改 3删除
    business_type: Mapped[int | None] = mapped_column(Integer, default=0)
    method: Mapped[str | None] = mapped_column(String(200), default="")
    request_method: Mapped[str | None] = mapped_column(String(10), default="")
    # 操作类别：0其它 1后台用户 2手机端用户
    operator_type: Mapped[int | None] = mapped_column(Integer, default=0)
    oper_name: Mapped[str | None] = mapped_column(String(50), default="")
    dept_name: Mapped[str | None] = mapped_column(String(50), default="")
    oper_url: Mapped[str | None] = mapped_column(String(255), default="")
    oper_ip: Mapped[str | None] = mapped_column(String(128), default="")
    oper_location: Mapped[str | None] = mapped_column(String(255), default="")
    oper_param: Mapped[str | None] = mapped_column(String(2000), default="")
    json_result: Mapped[str | None] = mapped_column(String(2000), default="")
    # 操作状态：0正常 1异常
    status: Mapped[int | None] = mapped_column(Integer, default=0)
    error_msg: Mapped[str | None] = mapped_column(String(2000), default="")
    oper_time: Mapped[datetime | None] = mapped_column(DateTime)
    cost_time: Mapped[int | None] = mapped_column(BigInteger, default=0)
