"""字典只读路由，对应 SysDictTypeController / SysDictDataController 的读接口"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_login_user, has_perm
from app.core.exceptions import ServiceException
from app.core.request_utils import get_date_range_params
from app.core.response import ajax_success, table_data_info
from app.services import dict_service

router = APIRouter(prefix="/system/dict")


@router.get("/data/type/{dict_type}")
def get_dicts_by_type(dict_type: str, db: Session = Depends(get_db), login_user: dict = Depends(get_login_user)):
    """前端几乎每个列表页都靠这个接口显示状态标签，只要登录即可访问，不额外校验字典权限"""
    return ajax_success(data=dict_service.get_dict_data_by_type(db, dict_type))


@router.get("/data/list")
def list_dict_data(
    dictType: str | None = Query(default=None),
    dictLabel: str | None = Query(default=None),
    status: str | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:dict:list")),
):
    rows, total = dict_service.list_dict_data(db, dictType, dictLabel, status, pageNum, pageSize)
    return table_data_info(rows, total)


@router.get("/data/{dict_code}")
def get_dict_data(
    dict_code: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:dict:query"))
):
    row = dict_service.get_dict_data(db, dict_code)
    if row is None:
        raise ServiceException("字典数据不存在")
    return ajax_success(data=row)


@router.get("/type/optionselect")
def dict_type_optionselect(db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:dict:query"))):
    return ajax_success(data=dict_service.list_all_dict_types(db))


@router.get("/type/list")
def list_dict_types(
    request: Request,
    dictName: str | None = Query(default=None),
    dictType: str | None = Query(default=None),
    status: str | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:dict:list")),
):
    begin_time, end_time = get_date_range_params(request)
    rows, total = dict_service.list_dict_types(db, dictName, dictType, status, begin_time, end_time, pageNum, pageSize)
    return table_data_info(rows, total)


@router.get("/type/{dict_id}")
def get_dict_type(
    dict_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:dict:query"))
):
    row = dict_service.get_dict_type(db, dict_id)
    if row is None:
        raise ServiceException("字典类型不存在")
    return ajax_success(data=row)
