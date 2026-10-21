"""用户管理路由，对应 SysUserController，路径前缀 /system/user"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.constants import BIZ_DELETE, BIZ_INSERT, BIZ_UPDATE
from app.core.database import get_db
from app.core.deps import get_login_user, has_perm
from app.core.oper_log import log
from app.core.request_utils import get_date_range_params
from app.core.response import ajax_success, table_data_info
from app.schemas.user import UserChangeStatus, UserCreate, UserProfileUpdate, UserPwdUpdate, UserResetPwd, UserUpdate
from app.services import dept_service, user_service

router = APIRouter(prefix="/system/user")


@router.get("/profile")
def get_profile(db: Session = Depends(get_db), login_user: dict = Depends(get_login_user)):
    return ajax_success(**user_service.get_profile(db, login_user["user_id"]))


@router.put("/profile")
def update_profile(
    body: UserProfileUpdate, db: Session = Depends(get_db), login_user: dict = Depends(get_login_user)
):
    user_service.update_profile(db, login_user["user_id"], body)
    return ajax_success()


@router.put("/profile/updatePwd")
def update_profile_pwd(
    body: UserPwdUpdate, db: Session = Depends(get_db), login_user: dict = Depends(get_login_user)
):
    user_service.update_password(db, login_user["user_id"], body.old_password, body.new_password)
    return ajax_success()


@router.get("/deptTree")
def dept_tree(db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:user:list"))):
    # 对应 deptService.selectDeptTreeList：部门列表带 @DataScope，按数据权限过滤
    return ajax_success(data=dept_service.dept_tree_select(db, login_user))


@router.get("/list")
def list_users(
    request: Request,
    userName: str | None = Query(default=None),
    status: str | None = Query(default=None),
    phonenumber: str | None = Query(default=None),
    deptId: int | None = Query(default=None),
    pageNum: int = Query(default=1),
    pageSize: int = Query(default=10),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:user:list")),
):
    begin_time, end_time = get_date_range_params(request)
    rows, total = user_service.list_users(
        db, login_user, userName, status, phonenumber, deptId, begin_time, end_time, pageNum, pageSize
    )
    return table_data_info(rows, total)


@router.get("/authRole/{user_id}")
def auth_role(user_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:user:query"))):
    return ajax_success(**user_service.get_auth_role(db, login_user, user_id))


@router.put("/authRole")
def update_auth_role(
    userId: int,
    roleIds: str = "",
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:user:edit")),
):
    role_ids = [int(x) for x in roleIds.split(",") if x]
    user_service.update_auth_role(db, login_user, userId, role_ids)
    return ajax_success()


# 对应 @GetMapping(value = { "/", "/{userId}" })：新增用户时前端请求 /system/user/（带末尾斜杠）
@router.get("/{user_id}")
@router.get("/", include_in_schema=False)
def get_user_info(
    user_id: int | None = None,
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:user:query")),
):
    return ajax_success(**user_service.get_user_detail(db, login_user, user_id))


@router.post("")
@log(title="用户管理", business_type=BIZ_INSERT)
def add_user(body: UserCreate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:user:add"))):
    user_service.create_user(db, login_user, body, login_user["user_name"])
    return ajax_success()


@router.put("")
@log(title="用户管理", business_type=BIZ_UPDATE)
def edit_user(body: UserUpdate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:user:edit"))):
    user_service.update_user(db, login_user, body, login_user["user_name"])
    return ajax_success()


@router.put("/resetPwd")
@log(title="用户管理", business_type=BIZ_UPDATE)
def reset_pwd(
    body: UserResetPwd, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:user:resetPwd"))
):
    user_service.reset_pwd(db, login_user, body.user_id, body.password, login_user["user_name"])
    return ajax_success()


@router.put("/changeStatus")
@log(title="用户管理", business_type=BIZ_UPDATE)
def change_status(
    body: UserChangeStatus, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:user:edit"))
):
    user_service.change_status(db, login_user, body.user_id, body.status, login_user["user_name"])
    return ajax_success()


@router.delete("/{user_ids}")
@log(title="用户管理", business_type=BIZ_DELETE)
def remove_users(
    user_ids: str, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:user:remove"))
):
    ids = [int(x) for x in user_ids.split(",") if x]
    user_service.delete_users(db, login_user, ids, login_user["user_id"])
    return ajax_success()
