"""登录日志，对应若依的 AsyncFactory.recordLogininfor（简化为同步写库） + SysLogininforController 的读/删/清空/解锁"""
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.constants import LOGIN_STATUS_FAIL, PASSWORD_MAX_RETRY_COUNT, PWD_ERR_CNT_KEY_PREFIX
from app.core.redis_client import redis_client
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
    begin_time: datetime | None,
    end_time: datetime | None,
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
    # 日期范围已在 core/date_range.py 校验并补全成 datetime，这里直接比较，列上不套函数以便走 login_time 索引
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


def top_failed_logins(db: Session, begin_time: datetime, limit: int) -> list[dict]:
    """
    统计 begin_time 以来登录失败次数最多的账号。用 SQL 聚合（GROUP BY + LIMIT），
    不把日志全量拉到内存：登录日志会无限增长，而模型只需要排名靠前的几条。
    先按 status、login_time 过滤（两列都有索引），再按用户名分组。
    """
    rows = (
        db.query(
            SysLogininfor.user_name,
            func.count(SysLogininfor.info_id).label("fail_count"),
            func.count(func.distinct(SysLogininfor.ipaddr)).label("ip_count"),
            func.max(SysLogininfor.login_time).label("last_time"),
        )
        .filter(SysLogininfor.status == LOGIN_STATUS_FAIL, SysLogininfor.login_time >= begin_time)
        .group_by(SysLogininfor.user_name)
        .order_by(func.count(SysLogininfor.info_id).desc(), SysLogininfor.user_name)
        .limit(limit)
        .all()
    )
    return [
        {"userName": r.user_name, "failCount": int(r.fail_count), "ipCount": int(r.ip_count), "lastFailTime": r.last_time}
        for r in rows
    ]


def get_lock_status(username: str) -> dict:
    """只读：密码错误计数和剩余锁定秒数（GET + TTL），不修改任何 Redis 数据"""
    key = PWD_ERR_CNT_KEY_PREFIX + username
    count = int(redis_client.get(key) or 0)
    ttl = redis_client.ttl(key)
    return {
        "userName": username,
        "errorCount": count,
        "locked": count >= PASSWORD_MAX_RETRY_COUNT,
        # TTL 返回 -2 表示 key 不存在，-1 表示没有过期时间，统一按 0 处理
        "remainSeconds": ttl if ttl and ttl > 0 else 0,
    }
