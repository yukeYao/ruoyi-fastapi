"""在线用户路由，对应 SysUserOnlineController，路径前缀 /monitor/online"""
from fastapi import APIRouter, Depends, Query

from app.core.constants import BIZ_FORCE
from app.core.deps import has_perm
from app.core.oper_log import log
from app.core.response import ajax_success, table_data_info
from app.services import online_service

router = APIRouter(prefix="/monitor/online")


@router.get("/list")
def list_online(
    ipaddr: str | None = Query(default=None),
    userName: str | None = Query(default=None),
    login_user: dict = Depends(has_perm("monitor:online:list")),
):
    rows = online_service.list_online_users(ipaddr, userName)
    return table_data_info(rows, len(rows))


@router.delete("/{token_id}")
@log(title="在线用户", business_type=BIZ_FORCE)
def force_logout(token_id: str, login_user: dict = Depends(has_perm("monitor:online:forceLogout"))):
    online_service.force_logout(token_id)
    return ajax_success()
