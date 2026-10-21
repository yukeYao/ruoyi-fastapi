"""登录日志路由，对应 SysLogininforController，路径前缀 /monitor/logininfor"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.constants import BIZ_CLEAN, BIZ_DELETE, BIZ_OTHER
from app.core.database import get_db
from app.core.deps import has_perm
from app.core.oper_log import log
from app.core.request_utils import get_date_range_params
from app.core.response import ajax_success, table_data_info
from app.services import logininfor_service

router = APIRouter(prefix="/monitor/logininfor")


@router.get("/list")
def list_logininfor(
    request: Request,
    ipaddr: str | None = Query(default=None),
    userName: str | None = Query(default=None),
    status: str | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("monitor:logininfor:list")),
):
    begin_time, end_time = get_date_range_params(request)
    rows, total = logininfor_service.list_logininfor(db, ipaddr, userName, status, begin_time, end_time, pageNum, pageSize)
    return table_data_info(rows, total)


@router.delete("/clean")
@log(title="登录日志", business_type=BIZ_CLEAN)
def clean_logininfor(db: Session = Depends(get_db), login_user: dict = Depends(has_perm("monitor:logininfor:remove"))):
    logininfor_service.clean_logininfor(db)
    return ajax_success()


@router.get("/unlock/{user_name}")
@log(title="账户解锁", business_type=BIZ_OTHER)
def unlock(user_name: str, login_user: dict = Depends(has_perm("monitor:logininfor:unlock"))):
    logininfor_service.unlock_user(user_name)
    return ajax_success()


@router.delete("/{info_ids}")
@log(title="登录日志", business_type=BIZ_DELETE)
def remove_logininfor(
    info_ids: str, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("monitor:logininfor:remove"))
):
    ids = [int(x) for x in info_ids.split(",") if x]
    logininfor_service.delete_logininfor(db, ids)
    return ajax_success()
