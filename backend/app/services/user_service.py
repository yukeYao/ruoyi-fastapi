"""
用户管理业务逻辑，对应若依 SysUserController + SysUserServiceImpl。

数据权限、唯一性校验、"不允许操作超级管理员"、"不能删除自己" 等规则严格对照 Java 版。
"""
from sqlalchemy import func, text
from sqlalchemy.orm import Session, aliased

from app.core.audit import fill_create_fields, fill_update_fields
from app.core.constants import DEL_FLAG_DELETED, DEL_FLAG_NORMAL, SUPER_ADMIN_ID
from app.core.data_scope import build_data_scope_filter
from app.core.exceptions import ServiceException
from app.core.response import model_to_camel_dict
from app.core.security import hash_password, verify_password
from app.core.tree_utils import build_tree_select
from app.models.sys_dept import SysDept
from app.models.sys_post import SysPost
from app.models.sys_role import SysRole
from app.models.sys_user import SysUser
from app.models.sys_user_post import SysUserPost
from app.models.sys_user_role import SysUserRole


def _row_to_dict(user: SysUser, dept_name: str | None, leader: str | None) -> dict:
    data = model_to_camel_dict(user, exclude={"password"})
    if dept_name is not None:
        data["dept"] = {"deptName": dept_name, "leader": leader}
    return data


def list_users(
    db: Session,
    login_user: dict,
    user_name: str | None,
    status: str | None,
    phonenumber: str | None,
    dept_id: int | None,
    begin_time: str | None,
    end_time: str | None,
    page_num: int,
    page_size: int,
) -> tuple[list[dict], int]:
    u = aliased(SysUser, name="u")
    d = aliased(SysDept, name="d")

    query = db.query(u, d.dept_name, d.leader).outerjoin(d, u.dept_id == d.dept_id).filter(u.del_flag == DEL_FLAG_NORMAL)

    if user_name:
        query = query.filter(u.user_name.like(f"%{user_name}%"))
    if status:
        query = query.filter(u.status == status)
    if phonenumber:
        query = query.filter(u.phonenumber.like(f"%{phonenumber}%"))
    if dept_id:
        query = query.filter(
            text(f"(u.dept_id = {int(dept_id)} OR u.dept_id IN (SELECT dept_id FROM sys_dept WHERE FIND_IN_SET({int(dept_id)}, ancestors)))")
        )
    if begin_time:
        query = query.filter(func.date(u.create_time) >= begin_time[:10])
    if end_time:
        query = query.filter(func.date(u.create_time) <= end_time[:10])

    scope_expr = build_data_scope_filter(login_user, dept_alias="d", user_alias="u")
    if scope_expr:
        query = query.filter(text(scope_expr))

    total = query.count()
    rows = query.order_by(u.user_id).offset((page_num - 1) * page_size).limit(page_size).all()
    return [_row_to_dict(user, dept_name, leader) for user, dept_name, leader in rows], total


def check_user_data_scope(db: Session, login_user: dict, user_id: int) -> None:
    if login_user.get("user_id") == SUPER_ADMIN_ID:
        return
    rows, _ = list_users(db, login_user, None, None, None, None, None, None, 1, 1000)
    if not any(r["userId"] == user_id for r in rows):
        raise ServiceException("没有权限访问用户数据！")


def check_user_allowed(user_id: int | None) -> None:
    if user_id == SUPER_ADMIN_ID:
        raise ServiceException("不允许操作超级管理员用户")


def check_user_name_unique(db: Session, user_name: str, user_id: int | None = None) -> bool:
    existing = db.query(SysUser).filter(SysUser.user_name == user_name, SysUser.del_flag == DEL_FLAG_NORMAL).first()
    return existing is None or existing.user_id == user_id


def check_phone_unique(db: Session, phonenumber: str, user_id: int | None = None) -> bool:
    if not phonenumber:
        return True
    existing = db.query(SysUser).filter(SysUser.phonenumber == phonenumber, SysUser.del_flag == DEL_FLAG_NORMAL).first()
    return existing is None or existing.user_id == user_id


def check_email_unique(db: Session, email: str, user_id: int | None = None) -> bool:
    if not email:
        return True
    existing = db.query(SysUser).filter(SysUser.email == email, SysUser.del_flag == DEL_FLAG_NORMAL).first()
    return existing is None or existing.user_id == user_id


def get_user_detail(db: Session, login_user: dict, user_id: int | None) -> dict:
    """对应 SysUserController.getInfo：userId 为空时是"新增"表单，否则是"编辑"表单"""
    result: dict = {}

    if user_id is not None:
        check_user_data_scope(db, login_user, user_id)
        user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
        if user is None:
            raise ServiceException("用户不存在")
        result["data"] = model_to_camel_dict(user, exclude={"password"})
        post_ids = [row.post_id for row in db.query(SysUserPost).filter(SysUserPost.user_id == user_id).all()]
        role_ids = [row.role_id for row in db.query(SysUserRole).filter(SysUserRole.user_id == user_id).all()]
        result["postIds"] = post_ids
        result["roleIds"] = role_ids

    is_target_admin = user_id == SUPER_ADMIN_ID
    roles = db.query(SysRole).filter(SysRole.del_flag == DEL_FLAG_NORMAL).order_by(SysRole.role_sort).all()
    if not is_target_admin:
        roles = [r for r in roles if r.role_id != SUPER_ADMIN_ID]
    result["roles"] = [model_to_camel_dict(r) for r in roles]
    result["posts"] = [model_to_camel_dict(p) for p in db.query(SysPost).order_by(SysPost.post_sort).all()]
    return result


def create_user(db: Session, body, operator_name: str) -> None:
    if not check_user_name_unique(db, body.user_name):
        raise ServiceException(f"新增用户'{body.user_name}'失败，登录账号已存在")
    if body.phonenumber and not check_phone_unique(db, body.phonenumber):
        raise ServiceException(f"新增用户'{body.user_name}'失败，手机号码已存在")
    if body.email and not check_email_unique(db, body.email):
        raise ServiceException(f"新增用户'{body.user_name}'失败，邮箱账号已存在")

    user = SysUser(
        dept_id=body.dept_id,
        user_name=body.user_name,
        nick_name=body.nick_name,
        email=body.email or "",
        phonenumber=body.phonenumber or "",
        sex=body.sex or "0",
        password=hash_password(body.password),
        status=body.status or "0",
        remark=body.remark,
    )
    fill_create_fields(user, operator_name)
    db.add(user)
    db.flush()  # 拿到 user_id

    for post_id in body.post_ids:
        db.add(SysUserPost(user_id=user.user_id, post_id=post_id))
    for role_id in body.role_ids:
        db.add(SysUserRole(user_id=user.user_id, role_id=role_id))
    db.commit()


def update_user(db: Session, login_user: dict, body, operator_name: str) -> None:
    check_user_allowed(body.user_id)
    check_user_data_scope(db, login_user, body.user_id)
    if body.phonenumber and not check_phone_unique(db, body.phonenumber, body.user_id):
        raise ServiceException("修改用户失败，手机号码已存在")
    if body.email and not check_email_unique(db, body.email, body.user_id):
        raise ServiceException("修改用户失败，邮箱账号已存在")

    user = db.query(SysUser).filter(SysUser.user_id == body.user_id).first()
    if user is None:
        raise ServiceException("用户不存在")

    user.dept_id = body.dept_id
    user.nick_name = body.nick_name
    user.email = body.email
    user.phonenumber = body.phonenumber
    user.sex = body.sex
    user.status = body.status
    user.remark = body.remark
    fill_update_fields(user, operator_name)

    db.query(SysUserRole).filter(SysUserRole.user_id == body.user_id).delete()
    for role_id in body.role_ids:
        db.add(SysUserRole(user_id=body.user_id, role_id=role_id))

    db.query(SysUserPost).filter(SysUserPost.user_id == body.user_id).delete()
    for post_id in body.post_ids:
        db.add(SysUserPost(user_id=body.user_id, post_id=post_id))

    db.commit()


def delete_users(db: Session, login_user: dict, user_ids: list[int], current_user_id: int) -> None:
    if current_user_id in user_ids:
        raise ServiceException("当前用户不能删除")
    for user_id in user_ids:
        check_user_allowed(user_id)
        check_user_data_scope(db, login_user, user_id)

    db.query(SysUserRole).filter(SysUserRole.user_id.in_(user_ids)).delete(synchronize_session=False)
    db.query(SysUserPost).filter(SysUserPost.user_id.in_(user_ids)).delete(synchronize_session=False)
    db.query(SysUser).filter(SysUser.user_id.in_(user_ids)).update({SysUser.del_flag: DEL_FLAG_DELETED}, synchronize_session=False)
    db.commit()


def reset_pwd(db: Session, user_id: int, new_password: str, operator_name: str) -> None:
    check_user_allowed(user_id)
    user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
    if user is None:
        raise ServiceException("用户不存在")
    user.password = hash_password(new_password)
    fill_update_fields(user, operator_name)
    db.commit()


def change_status(db: Session, user_id: int, status: str, operator_name: str) -> None:
    check_user_allowed(user_id)
    user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
    if user is None:
        raise ServiceException("用户不存在")
    user.status = status
    fill_update_fields(user, operator_name)
    db.commit()


def get_profile(db: Session, user_id: int) -> dict:
    user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
    data = model_to_camel_dict(user, exclude={"password"})
    if user.dept_id:
        dept = db.query(SysDept).filter(SysDept.dept_id == user.dept_id).first()
        if dept:
            data["dept"] = model_to_camel_dict(dept)
    post_names = [
        p.post_name
        for p in db.query(SysPost).join(SysUserPost, SysUserPost.post_id == SysPost.post_id).filter(SysUserPost.user_id == user_id).all()
    ]
    role_names = [
        r.role_name
        for r in db.query(SysRole).join(SysUserRole, SysUserRole.role_id == SysRole.role_id).filter(SysUserRole.user_id == user_id, SysRole.del_flag == DEL_FLAG_NORMAL).all()
    ]
    return {"data": data, "postGroup": ",".join(post_names), "roleGroup": ",".join(role_names)}


def update_profile(db: Session, user_id: int, body) -> None:
    user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
    if user is None:
        raise ServiceException("用户不存在")
    user.nick_name = body.nick_name
    user.email = body.email
    user.phonenumber = body.phonenumber
    user.sex = body.sex
    fill_update_fields(user, user.user_name)  # 个人中心：操作人就是自己
    db.commit()


def update_password(db: Session, user_id: int, old_password: str, new_password: str) -> None:
    user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
    if user is None:
        raise ServiceException("用户不存在")
    if not verify_password(old_password, user.password):
        raise ServiceException("修改密码失败，旧密码错误")
    if verify_password(new_password, user.password):
        raise ServiceException("新密码不能与旧密码相同")
    user.password = hash_password(new_password)
    fill_update_fields(user, user.user_name)
    db.commit()


def get_auth_role(db: Session, user_id: int) -> dict:
    user = db.query(SysUser).filter(SysUser.user_id == user_id).first()
    if user is None:
        raise ServiceException("用户不存在")
    assigned_role_ids = {row.role_id for row in db.query(SysUserRole).filter(SysUserRole.user_id == user_id).all()}

    is_target_admin = user_id == SUPER_ADMIN_ID
    roles = db.query(SysRole).filter(SysRole.del_flag == DEL_FLAG_NORMAL).order_by(SysRole.role_sort).all()
    if not is_target_admin:
        roles = [r for r in roles if r.role_id != SUPER_ADMIN_ID]

    role_dicts = []
    for r in roles:
        d = model_to_camel_dict(r)
        d["flag"] = r.role_id in assigned_role_ids
        role_dicts.append(d)

    return {"user": model_to_camel_dict(user, exclude={"password"}), "roles": role_dicts}


def update_auth_role(db: Session, user_id: int, role_ids: list[int]) -> None:
    db.query(SysUserRole).filter(SysUserRole.user_id == user_id).delete()
    for role_id in role_ids:
        db.add(SysUserRole(user_id=user_id, role_id=role_id))
    db.commit()


def dept_tree_select(db: Session) -> list[dict]:
    depts = db.query(SysDept).filter(SysDept.del_flag == DEL_FLAG_NORMAL).order_by(SysDept.parent_id, SysDept.order_num).all()
    nodes = [
        {"id": d.dept_id, "parent_id": d.parent_id or 0, "label": d.dept_name, "disabled": d.status == "1"}
        for d in depts
    ]
    return build_tree_select(nodes)
