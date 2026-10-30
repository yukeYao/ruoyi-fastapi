"""操作日志路由，对应 SysOperlogController，路径前缀 /monitor/operlog"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.constants import BIZ_CLEAN, BIZ_DELETE
from app.core.database import get_db
from app.core.deps import has_perm
from app.core.oper_log import log
from app.core.request_utils import get_date_range_params
from app.core.response import ajax_success, table_data_info
from app.services import operlog_service

router = APIRouter(prefix="/monitor/operlog")


@router.get("/list")
def list_oper_logs(
    request: Request,
    title: str | None = Query(default=None),
    businessType: int | None = Query(default=None),
    status: int | None = Query(default=None),
    operName: str | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("monitor:operlog:list")),
):
    begin_time, end_time = get_date_range_params(request, by_day=False)
    rows, total = operlog_service.list_oper_logs(db, title, businessType, status, operName, begin_time, end_time, pageNum, pageSize)
    return table_data_info(rows, total)


@router.delete("/clean")
@log(title="操作日志", business_type=BIZ_CLEAN)
def clean_oper_logs(db: Session = Depends(get_db), login_user: dict = Depends(has_perm("monitor:operlog:remove"))):
    operlog_service.clean_oper_logs(db)
    return ajax_success()


@router.delete("/{oper_ids}")
@log(title="操作日志", business_type=BIZ_DELETE)
def remove_oper_logs(
    oper_ids: str, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("monitor:operlog:remove"))
):
    ids = [int(x) for x in oper_ids.split(",") if x]
    operlog_service.delete_oper_logs(db, ids)
    return ajax_success()
