"""登录日志，对应若依的 AsyncFactory.recordLogininfor（简化为同步写库） + SysLogininforController 的读/删/清空/解锁"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.constants import PWD_ERR_CNT_KEY_PREFIX
from app.core.redis_client import redis_client
from app.core.request_utils import normalize_datetime_range
from app.core.response import model_to_camel_dict
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


def list_logininfor(
    db: Session,
    ipaddr: str | None,
    user_name: str | None,
    status: str | None,
    begin_time: str | None,
    end_time: str | None,
    page_num: int,
    page_size: int,
) -> tuple[list[dict], int]:
    query = db.query(SysLogininfor)
    if ipaddr:
        query = query.filter(SysLogininfor.ipaddr.like(f"%{ipaddr}%"))
    if user_name:
        query = query.filter(SysLogininfor.user_name.like(f"%{user_name}%"))
    if status:
        query = query.filter(SysLogininfor.status == status)
    begin_time, end_time = normalize_datetime_range(begin_time, end_time)
    if begin_time:
        query = query.filter(SysLogininfor.login_time >= begin_time)
    if end_time:
        query = query.filter(SysLogininfor.login_time <= end_time)

    total = query.count()
    rows = query.order_by(SysLogininfor.info_id.desc()).offset((page_num - 1) * page_size).limit(page_size).all()
    return [model_to_camel_dict(r) for r in rows], total


def delete_logininfor(db: Session, info_ids: list[int]) -> None:
    db.query(SysLogininfor).filter(SysLogininfor.info_id.in_(info_ids)).delete(synchronize_session=False)
    db.commit()


def clean_logininfor(db: Session) -> None:
    db.query(SysLogininfor).delete()
    db.commit()


def unlock_user(username: str) -> None:
    redis_client.delete(PWD_ERR_CNT_KEY_PREFIX + username)
