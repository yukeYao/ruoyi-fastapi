"""字典只读查询，对应若依 SysDictTypeController / SysDictDataController 的读接口（本项目不做字典增删改）"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.constants import STATUS_NORMAL
from app.core.response import model_to_camel_dict
from app.models.sys_dict_data import SysDictData
from app.models.sys_dict_type import SysDictType


def list_dict_types(
    db: Session,
    dict_name: str | None,
    dict_type: str | None,
    status: str | None,
    begin_time: datetime | None,
    end_time: datetime | None,
    page_num: int,
    page_size: int,
) -> tuple[list[dict], int]:
    query = db.query(SysDictType)
    if dict_name:
        query = query.filter(SysDictType.dict_name.like(f"%{dict_name}%"))
    if dict_type:
        query = query.filter(SysDictType.dict_type.like(f"%{dict_type}%"))
    if status:
        query = query.filter(SysDictType.status == status)
    # 对应 SysDictTypeMapper 的 date_format 按天比较：get_date_range_params(by_day=True) 已补全成当天 00:00:00 / 23:59:59
    if begin_time:
        query = query.filter(SysDictType.create_time >= begin_time)
    if end_time:
        query = query.filter(SysDictType.create_time <= end_time)

    total = query.count()
    rows = query.order_by(SysDictType.dict_id).offset((page_num - 1) * page_size).limit(page_size).all()
    return [model_to_camel_dict(r) for r in rows], total


def get_dict_type(db: Session, dict_id: int) -> dict | None:
    row = db.query(SysDictType).filter(SysDictType.dict_id == dict_id).first()
    return model_to_camel_dict(row) if row else None


def list_all_dict_types(db: Session) -> list[dict]:
    return [model_to_camel_dict(r) for r in db.query(SysDictType).order_by(SysDictType.dict_id).all()]


def list_dict_data(
    db: Session, dict_type: str | None, dict_label: str | None, status: str | None, page_num: int, page_size: int
) -> tuple[list[dict], int]:
    query = db.query(SysDictData)
    if dict_type:
        query = query.filter(SysDictData.dict_type == dict_type)
    if dict_label:
        query = query.filter(SysDictData.dict_label.like(f"%{dict_label}%"))
    if status:
        query = query.filter(SysDictData.status == status)

    total = query.count()
    rows = query.order_by(SysDictData.dict_sort).offset((page_num - 1) * page_size).limit(page_size).all()
    return [model_to_camel_dict(r) for r in rows], total


def get_dict_data(db: Session, dict_code: int) -> dict | None:
    row = db.query(SysDictData).filter(SysDictData.dict_code == dict_code).first()
    return model_to_camel_dict(row) if row else None


def get_dict_data_by_type(db: Session, dict_type: str) -> list[dict]:
    rows = (
        db.query(SysDictData)
        .filter(SysDictData.dict_type == dict_type, SysDictData.status == STATUS_NORMAL)
        .order_by(SysDictData.dict_sort)
        .all()
    )
    return [model_to_camel_dict(r) for r in rows]
