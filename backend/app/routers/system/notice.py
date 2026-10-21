"""
通知公告路由，对应 SysNoticeController，路径前缀 /system/notice

只做了首页铃铛（layout/components/HeaderNotice）会调用的三个接口的最小实现，
让首页不再报 404；通知公告的增删改查、已读记录表都没有实现：
- listTop 固定返回空列表，unreadCount=0（格式与 Java 版一致）；
- markRead / markReadAll 直接返回成功，不落库。
这三个接口在 Java 版里都没有 @PreAuthorize，只要求登录。
"""
from fastapi import APIRouter, Depends, Query

from app.core.deps import get_login_user
from app.core.response import ajax_success

router = APIRouter(prefix="/system/notice")


@router.get("/listTop")
def list_top(login_user: dict = Depends(get_login_user)):
    return ajax_success(data=[], unreadCount=0)


@router.post("/markRead")
def mark_read(noticeId: int | None = Query(default=None), login_user: dict = Depends(get_login_user)):
    return ajax_success()


@router.post("/markReadAll")
def mark_read_all(ids: str | None = Query(default=None), login_user: dict = Depends(get_login_user)):
    return ajax_success()
