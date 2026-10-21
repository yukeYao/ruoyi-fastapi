"""操作日志查询/删除/清空，对应若依 SysOperlogController + ISysOperLogService"""
from sqlalchemy.orm import Session

from app.core.response import model_to_camel_dict
from app.models.sys_oper_log import SysOperLog


def list_oper_logs(
    db: Session,
    title: str | None,
    business_type: int | None,
    status: int | None,
    oper_name: str | None,
    begin_time: str | None,
    end_time: str | None,
    page_num: int,
    page_size: int,
) -> tuple[list[dict], int]:
    query = db.query(SysOperLog)
    if title:
        query = query.filter(SysOperLog.title.like(f"%{title}%"))
    if business_type is not None:
        query = query.filter(SysOperLog.business_type == business_type)
    if status is not None:
        query = query.filter(SysOperLog.status == status)
    if oper_name:
        query = query.filter(SysOperLog.oper_name.like(f"%{oper_name}%"))
    if begin_time:
        query = query.filter(SysOperLog.oper_time >= begin_time[:10])
    if end_time:
        query = query.filter(SysOperLog.oper_time <= end_time[:10])

    total = query.count()
    rows = query.order_by(SysOperLog.oper_id.desc()).offset((page_num - 1) * page_size).limit(page_size).all()
    return [model_to_camel_dict(r) for r in rows], total


def delete_oper_logs(db: Session, oper_ids: list[int]) -> None:
    db.query(SysOperLog).filter(SysOperLog.oper_id.in_(oper_ids)).delete(synchronize_session=False)
    db.commit()


def clean_oper_logs(db: Session) -> None:
    db.query(SysOperLog).delete()
    db.commit()
