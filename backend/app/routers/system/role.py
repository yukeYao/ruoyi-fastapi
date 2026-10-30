"""角色管理路由，对应 SysRoleController，路径前缀 /system/role"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.constants import BIZ_DELETE, BIZ_GRANT, BIZ_INSERT, BIZ_UPDATE
from app.core.database import get_db
from app.core.deps import has_perm
from app.core.oper_log import log
from app.core.request_utils import get_date_range_params
from app.core.response import ajax_success, table_data_info
from app.schemas.auth_user import AuthUserCancelBody
from app.schemas.role import RoleChangeStatus, RoleCreate, RoleDataScope, RoleUpdate
from app.services import dept_service, role_service

router = APIRouter(prefix="/system/role")


@router.get("/list")
def list_roles(
    request: Request,
    roleName: str | None = Query(default=None),
    status: str | None = Query(default=None),
    roleKey: str | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:role:list")),
):
    begin_time, end_time = get_date_range_params(request, by_day=True)
    rows, total = role_service.list_roles(db, login_user, roleName, status, roleKey, begin_time, end_time, pageNum, pageSize)
    return table_data_info(rows, total)


@router.get("/optionselect")
def optionselect(db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:query"))):
    return ajax_success(data=role_service.list_all_roles(db, login_user))


@router.get("/authUser/allocatedList")
def allocated_user_list(
    roleId: int = Query(...),
    userName: str | None = Query(default=None),
    phonenumber: str | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:role:list")),
):
    rows, total = role_service.allocated_user_list(db, login_user, roleId, userName, phonenumber, pageNum, pageSize)
    return table_data_info(rows, total)


@router.get("/authUser/unallocatedList")
def unallocated_user_list(
    roleId: int = Query(...),
    userName: str | None = Query(default=None),
    phonenumber: str | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:role:list")),
):
    rows, total = role_service.unallocated_user_list(db, login_user, roleId, userName, phonenumber, pageNum, pageSize)
    return table_data_info(rows, total)


@router.put("/authUser/cancel")
@log(title="角色管理", business_type=BIZ_GRANT)
def cancel_auth_user(
    body: AuthUserCancelBody, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:edit"))
):
    role_service.cancel_auth_user(db, body.user_id, body.role_id)
    return ajax_success()


@router.put("/authUser/cancelAll")
@log(title="角色管理", business_type=BIZ_GRANT)
def cancel_auth_user_all(
    roleId: int = Query(...),
    userIds: list[int] = Query(default=[]),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:role:edit")),
):
    role_service.cancel_auth_user_all(db, roleId, userIds)
    return ajax_success()


@router.put("/authUser/selectAll")
@log(title="角色管理", business_type=BIZ_GRANT)
def select_auth_user_all(
    roleId: int = Query(...),
    userIds: list[int] = Query(default=[]),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:role:edit")),
):
    role_service.check_role_data_scope(db, login_user, roleId)
    role_service.select_auth_user_all(db, roleId, userIds)
    return ajax_success()


@router.get("/deptTree/{role_id}")
def dept_tree(role_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:query"))):
    role = role_service.get_role_detail(db, role_id)
    checked_keys = dept_service.dept_list_by_role_id(db, role_id, role["deptCheckStrictly"] in (1, True))
    return ajax_success(checkedKeys=checked_keys, depts=dept_service.dept_tree_select(db, login_user))


@router.get("/{role_id}")
def get_role(role_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:query"))):
    role_service.check_role_data_scope(db, login_user, role_id)
    return ajax_success(data=role_service.get_role_detail(db, role_id))


@router.post("")
@log(title="角色管理", business_type=BIZ_INSERT)
def add_role(body: RoleCreate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:add"))):
    role_service.create_role(db, body, login_user["user_name"])
    return ajax_success()


@router.put("")
@log(title="角色管理", business_type=BIZ_UPDATE)
def edit_role(body: RoleUpdate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:edit"))):
    role_service.update_role(db, login_user, body, login_user["user_name"])
    return ajax_success()


@router.put("/dataScope")
@log(title="角色管理", business_type=BIZ_UPDATE)
def data_scope(
    body: RoleDataScope, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:edit"))
):
    role_service.update_data_scope(db, login_user, body, login_user["user_name"])
    return ajax_success()


@router.put("/changeStatus")
@log(title="角色管理", business_type=BIZ_UPDATE)
def change_status(
    body: RoleChangeStatus, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:edit"))
):
    role_service.change_status(db, login_user, body.role_id, body.status, login_user["user_name"])
    return ajax_success()


@router.delete("/{role_ids}")
@log(title="角色管理", business_type=BIZ_DELETE)
def remove_roles(
    role_ids: str, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:role:remove"))
):
    ids = [int(x) for x in role_ids.split(",") if x]
    role_service.delete_roles(db, login_user, ids)
    return ajax_success()
