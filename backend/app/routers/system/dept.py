"""部门管理路由，对应 SysDeptController，路径前缀 /system/dept"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.constants import BIZ_DELETE, BIZ_INSERT, BIZ_UPDATE
from app.core.database import get_db
from app.core.deps import has_perm
from app.core.oper_log import log
from app.core.response import ajax_success
from app.schemas.dept import DeptCreate, DeptUpdate
from app.services import dept_service

router = APIRouter(prefix="/system/dept")


@router.get("/list")
def list_depts(
    deptName: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:dept:list")),
):
    return ajax_success(data=dept_service.list_depts(db, login_user, deptName, status))


@router.get("/list/exclude/{dept_id}")
def list_depts_exclude(
    dept_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:dept:list"))
):
    return ajax_success(data=dept_service.list_depts(db, login_user, None, None, exclude_dept_id=dept_id))


@router.get("/{dept_id}")
def get_dept(dept_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:dept:query"))):
    dept_service.check_dept_data_scope(db, login_user, dept_id)
    return ajax_success(data=dept_service.get_dept_detail(db, dept_id))


@router.post("")
@log(title="部门管理", business_type=BIZ_INSERT)
def add_dept(body: DeptCreate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:dept:add"))):
    dept_service.create_dept(db, body, login_user["user_name"])
    return ajax_success()


@router.put("")
@log(title="部门管理", business_type=BIZ_UPDATE)
def edit_dept(
    body: DeptUpdate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:dept:edit"))
):
    dept_service.check_dept_data_scope(db, login_user, body.dept_id)
    dept_service.update_dept(db, body, login_user["user_name"])
    return ajax_success()


@router.delete("/{dept_id}")
@log(title="部门管理", business_type=BIZ_DELETE)
def remove_dept(
    dept_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:dept:remove"))
):
    dept_service.check_dept_data_scope(db, login_user, dept_id)
    dept_service.delete_dept(db, dept_id)
    return ajax_success()
