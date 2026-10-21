"""登录日志记录，对应若依的 AsyncFactory.recordLogininfor（这里简化为同步写库）"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.sys_logininfor import SysLogininfor


def record_login_info(
    db: Session, username: str, status: str, msg: str, ip: str, location: str, browser: str, os_name: str
) -> None:
    record = SysLogininfor(
        user_name=username,
        ipaddr=ip,
        login_location=location,
        browser=browser,
        os=os_name,
        status=status,
        msg=msg,
        login_time=datetime.now(),
    )
    db.add(record)
    db.commit()
