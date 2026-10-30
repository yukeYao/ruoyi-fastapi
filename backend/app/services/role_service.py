"""角色管理业务逻辑，对应若依 SysRoleController + SysRoleServiceImpl"""
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, aliased

from app.core.audit import fill_create_fields, fill_update_fields
from app.core.constants import DEL_FLAG_DELETED, DEL_FLAG_NORMAL, SUPER_ADMIN_ID, SUPER_ADMIN_ROLE_ID
from app.core.data_scope import build_data_scope_filter
from app.core.exceptions import ServiceException
from app.core.response import model_to_camel_dict
from app.models.sys_dept import SysDept
from app.models.sys_role import SysRole
from app.models.sys_role_dept import SysRoleDept
from app.models.sys_role_menu import SysRoleMenu
from app.models.sys_user import SysUser
from app.models.sys_user_role import SysUserRole


def _role_query(
    db: Session,
    login_user: dict,
    role_name: str | None = None,
    status: str | None = None,
    role_key: str | None = None,
    begin_time: datetime | None = None,
    end_time: datetime | None = None,
):
    """对应 selectRoleList（带 @DataScope）：返回 (query, 角色别名 r)"""
    r = aliased(SysRole, name="r")
    ur = aliased(SysUserRole, name="ur")
    u = aliased(SysUser, name="u")
    d = aliased(SysDept, name="d")

    query = (
        db.query(r)
        .outerjoin(ur, ur.role_id == r.role_id)
        .outerjoin(u, u.user_id == ur.user_id)
        .outerjoin(d, d.dept_id == u.dept_id)
        .filter(r.del_flag == DEL_FLAG_NORMAL)
        .distinct()
    )
    if role_name:
        query = query.filter(r.role_name.like(f"%{role_name}%"))
    if status:
        query = query.filter(r.status == status)
    if role_key:
        query = query.filter(r.role_key.like(f"%{role_key}%"))
    # 对应 SysRoleMapper 的 date_format 按天比较：get_date_range_params(by_day=True) 已补全成当天 00:00:00 / 23:59:59
    if begin_time:
        query = query.filter(r.create_time >= begin_time)
    if end_time:
        query = query.filter(r.create_time <= end_time)

    scope_expr = build_data_scope_filter(login_user, dept_alias="d", user_alias="u")
    if scope_expr:
        query = query.filter(text(scope_expr))
    return query, r


def list_roles(
    db: Session,
    login_user: dict,
    role_name: str | None,
    status: str | None,
    role_key: str | None,
    begin_time: datetime | None,
    end_time: datetime | None,
    page_num: int,
    page_size: int,
) -> tuple[list[dict], int]:
    query, r = _role_query(db, login_user, role_name, status, role_key, begin_time, end_time)
    total = query.count()
    rows = query.order_by(r.role_sort).offset((page_num - 1) * page_size).limit(page_size).all()
    return [model_to_camel_dict(role) for role in rows], total


def check_role_data_scope(db: Session, login_user: dict, *role_ids: int) -> None:
    """对应 checkRoleDataScope(Long... roleIds)"""
    if login_user.get("user_id") == SUPER_ADMIN_ID:
        return
    for role_id in role_ids:
        query, r = _role_query(db, login_user)
        if query.filter(r.role_id == role_id).first() is None:
            raise ServiceException("没有权限访问角色数据！")


def check_role_allowed(role_id: int | None) -> None:
    if role_id == SUPER_ADMIN_ROLE_ID:
        raise ServiceException("不允许操作超级管理员角色")


def check_role_name_unique(db: Session, role_name: str, role_id: int | None = None) -> bool:
    existing = db.query(SysRole).filter(SysRole.role_name == role_name, SysRole.del_flag == DEL_FLAG_NORMAL).first()
    return existing is None or existing.role_id == role_id


def check_role_key_unique(db: Session, role_key: str, role_id: int | None = None) -> bool:
    existing = db.query(SysRole).filter(SysRole.role_key == role_key, SysRole.del_flag == DEL_FLAG_NORMAL).first()
    return existing is None or existing.role_id == role_id


def get_role_detail(db: Session, role_id: int) -> dict:
    role = db.query(SysRole).filter(SysRole.role_id == role_id).first()
    if role is None:
        raise ServiceException("角色不存在")
    return model_to_camel_dict(role)


def list_all_roles(db: Session, login_user: dict) -> list[dict]:
    """对应 selectRoleAll：不分页的角色列表，Java 里经 AOP 代理调用 selectRoleList，同样受数据权限过滤"""
    query, r = _role_query(db, login_user)
    return [model_to_camel_dict(role) for role in query.order_by(r.role_sort).all()]


def create_role(db: Session, body, operator_name: str) -> int:
    if not check_role_name_unique(db, body.role_name):
        raise ServiceException(f"新增角色'{body.role_name}'失败，角色名称已存在")
    if not check_role_key_unique(db, body.role_key):
        raise ServiceException(f"新增角色'{body.role_name}'失败，角色权限已存在")

    role = SysRole(
        role_name=body.role_name,
        role_key=body.role_key,
        role_sort=body.role_sort,
        status=body.status,
        menu_check_strictly=1 if body.menu_check_strictly else 0,
        dept_check_strictly=1 if body.dept_check_strictly else 0,
        remark=body.remark,
    )
    fill_create_fields(role, operator_name)
    db.add(role)
    db.flush()
    for menu_id in body.menu_ids:
        db.add(SysRoleMenu(role_id=role.role_id, menu_id=menu_id))
    db.commit()
    return role.role_id


def update_role(db: Session, login_user: dict, body, operator_name: str) -> None:
    check_role_allowed(body.role_id)
    check_role_data_scope(db, login_user, body.role_id)
    if not check_role_name_unique(db, body.role_name, body.role_id):
        raise ServiceException(f"修改角色'{body.role_name}'失败，角色名称已存在")
    if not check_role_key_unique(db, body.role_key, body.role_id):
        raise ServiceException(f"修改角色'{body.role_name}'失败，角色权限已存在")

    role = db.query(SysRole).filter(SysRole.role_id == body.role_id).first()
    if role is None:
        raise ServiceException("角色不存在")
    role.role_name = body.role_name
    role.role_key = body.role_key
    role.role_sort = body.role_sort
    role.status = body.status
    role.menu_check_strictly = 1 if body.menu_check_strictly else 0
    role.dept_check_strictly = 1 if body.dept_check_strictly else 0
    role.remark = body.remark
    fill_update_fields(role, operator_name)

    db.query(SysRoleMenu).filter(SysRoleMenu.role_id == body.role_id).delete()
    for menu_id in body.menu_ids:
        db.add(SysRoleMenu(role_id=body.role_id, menu_id=menu_id))
    db.commit()


def update_data_scope(db: Session, login_user: dict, body, operator_name: str) -> None:
    check_role_allowed(body.role_id)
    check_role_data_scope(db, login_user, body.role_id)
    role = db.query(SysRole).filter(SysRole.role_id == body.role_id).first()
    if role is None:
        raise ServiceException("角色不存在")
    role.data_scope = body.data_scope
    role.dept_check_strictly = 1 if body.dept_check_strictly else 0
    fill_update_fields(role, operator_name)

    db.query(SysRoleDept).filter(SysRoleDept.role_id == body.role_id).delete()
    if body.data_scope == "2":
        for dept_id in body.dept_ids:
            db.add(SysRoleDept(role_id=body.role_id, dept_id=dept_id))
    db.commit()


def change_status(db: Session, login_user: dict, role_id: int, status: str, operator_name: str) -> None:
    check_role_allowed(role_id)
    check_role_data_scope(db, login_user, role_id)
    role = db.query(SysRole).filter(SysRole.role_id == role_id).first()
    if role is None:
        raise ServiceException("角色不存在")
    role.status = status
    fill_update_fields(role, operator_name)
    db.commit()


def delete_roles(db: Session, login_user: dict, role_ids: list[int]) -> None:
    for role_id in role_ids:
        check_role_allowed(role_id)
        check_role_data_scope(db, login_user, role_id)
        role = db.query(SysRole).filter(SysRole.role_id == role_id).first()
        if role is None:
            continue
        assigned_count = db.query(SysUserRole).filter(SysUserRole.role_id == role_id).count()
        if assigned_count > 0:
            raise ServiceException(f"{role.role_name}已分配,不能删除")

    db.query(SysRoleMenu).filter(SysRoleMenu.role_id.in_(role_ids)).delete(synchronize_session=False)
    db.query(SysRoleDept).filter(SysRoleDept.role_id.in_(role_ids)).delete(synchronize_session=False)
    db.query(SysRole).filter(SysRole.role_id.in_(role_ids)).update({SysRole.del_flag: DEL_FLAG_DELETED}, synchronize_session=False)
    db.commit()


def allocated_user_list(
    db: Session, login_user: dict, role_id: int, user_name: str | None, phonenumber: str | None, page_num: int, page_size: int
) -> tuple[list[dict], int]:
    u = aliased(SysUser, name="u")
    d = aliased(SysDept, name="d")
    query = (
        db.query(u, d.dept_name)
        .outerjoin(d, d.dept_id == u.dept_id)
        .join(SysUserRole, SysUserRole.user_id == u.user_id)
        .filter(u.del_flag == DEL_FLAG_NORMAL, SysUserRole.role_id == role_id)
    )
    if user_name:
        query = query.filter(u.user_name.like(f"%{user_name}%"))
    if phonenumber:
        query = query.filter(u.phonenumber.like(f"%{phonenumber}%"))
    scope_expr = build_data_scope_filter(login_user, dept_alias="d", user_alias="u")
    if scope_expr:
        query = query.filter(text(scope_expr))

    total = query.count()
    rows = query.order_by(u.user_id).offset((page_num - 1) * page_size).limit(page_size).all()
    result = []
    for user, dept_name in rows:
        d_ = model_to_camel_dict(user, exclude={"password"})
        if dept_name is not None:
            d_["dept"] = {"deptName": dept_name}
        result.append(d_)
    return result, total


def unallocated_user_list(
    db: Session, login_user: dict, role_id: int, user_name: str | None, phonenumber: str | None, page_num: int, page_size: int
) -> tuple[list[dict], int]:
    from app.core.response import model_to_camel_dict as to_camel

    assigned_ids = [row.user_id for row in db.query(SysUserRole).filter(SysUserRole.role_id == role_id).all()]

    u = aliased(SysUser, name="u")
    d = aliased(SysDept, name="d")
    query = db.query(u, d.dept_name).outerjoin(d, d.dept_id == u.dept_id).filter(u.del_flag == DEL_FLAG_NORMAL)
    if assigned_ids:
        query = query.filter(~u.user_id.in_(assigned_ids))
    if user_name:
        query = query.filter(u.user_name.like(f"%{user_name}%"))
    if phonenumber:
        query = query.filter(u.phonenumber.like(f"%{phonenumber}%"))
    scope_expr = build_data_scope_filter(login_user, dept_alias="d", user_alias="u")
    if scope_expr:
        query = query.filter(text(scope_expr))

    total = query.count()
    rows = query.order_by(u.user_id).offset((page_num - 1) * page_size).limit(page_size).all()
    result = []
    for user, dept_name in rows:
        d_ = model_to_camel_dict(user, exclude={"password"})
        if dept_name is not None:
            d_["dept"] = {"deptName": dept_name}
        result.append(d_)
    return result, total


def cancel_auth_user(db: Session, user_id: int, role_id: int) -> None:
    db.query(SysUserRole).filter(SysUserRole.user_id == user_id, SysUserRole.role_id == role_id).delete()
    db.commit()


def cancel_auth_user_all(db: Session, role_id: int, user_ids: list[int]) -> None:
    db.query(SysUserRole).filter(SysUserRole.role_id == role_id, SysUserRole.user_id.in_(user_ids)).delete(synchronize_session=False)
    db.commit()


def select_auth_user_all(db: Session, role_id: int, user_ids: list[int]) -> None:
    for user_id in user_ids:
        db.add(SysUserRole(user_id=user_id, role_id=role_id))
    db.commit()
