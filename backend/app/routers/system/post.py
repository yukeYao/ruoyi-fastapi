"""岗位管理路由，对应 SysPostController，路径前缀 /system/post"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.constants import BIZ_DELETE, BIZ_INSERT, BIZ_UPDATE
from app.core.database import get_db
from app.core.deps import get_login_user, has_perm
from app.core.oper_log import log
from app.core.response import ajax_success, table_data_info
from app.schemas.post import PostCreate, PostUpdate
from app.services import post_service

router = APIRouter(prefix="/system/post")


@router.get("/list")
def list_posts(
    postCode: str | None = Query(default=None),
    postName: str | None = Query(default=None),
    status: str | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:post:list")),
):
    rows, total = post_service.list_posts(db, postCode, postName, status, pageNum, pageSize)
    return table_data_info(rows, total)


@router.get("/optionselect")
def optionselect(db: Session = Depends(get_db), login_user: dict = Depends(get_login_user)):
    return ajax_success(data=post_service.list_all_posts(db))


@router.get("/{post_id}")
def get_post(post_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:post:query"))):
    return ajax_success(data=post_service.get_post_detail(db, post_id))


@router.post("")
@log(title="岗位管理", business_type=BIZ_INSERT)
def add_post(body: PostCreate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:post:add"))):
    post_service.create_post(db, body, login_user["user_name"])
    return ajax_success()


@router.put("")
@log(title="岗位管理", business_type=BIZ_UPDATE)
def edit_post(
    body: PostUpdate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:post:edit"))
):
    post_service.update_post(db, body, login_user["user_name"])
    return ajax_success()


@router.delete("/{post_ids}")
@log(title="岗位管理", business_type=BIZ_DELETE)
def remove_posts(
    post_ids: str, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:post:remove"))
):
    ids = [int(x) for x in post_ids.split(",") if x]
    post_service.delete_posts(db, ids)
    return ajax_success()
