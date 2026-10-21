"""
部门管理业务逻辑，对应若依 SysDeptController + SysDeptServiceImpl。

重点：ancestors 维护。
- 新增：ancestors = 父部门.ancestors + "," + 父部门.deptId；父部门停用时不允许新增。
- 修改且父部门变化时：新 ancestors = 新父部门.ancestors + "," + 新父部门.deptId；
  所有子孙部门（FIND_IN_SET(deptId, ancestors)）的 ancestors 字符串里，
  原来以"旧 ancestors"开头的那一段要替换成"新 ancestors"（前缀替换，部门自身的 id 不变）。
- 修改后如果把部门改成"正常"，且 ancestors 不是根："0"，要把 ancestors 链上所有祖先的状态也顺带改成正常
  （对应 Java 的 updateParentDeptStatusNormal：你启用一个部门，它的上级肯定也得是启用的）。
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit import fill_create_fields, fill_update_fields
from app.core.constants import DEL_FLAG_DELETED, DEL_FLAG_NORMAL, STATUS_DISABLE, STATUS_NORMAL, SUPER_ADMIN_ID
from app.core.data_scope import build_data_scope_filter
from app.core.exceptions import ServiceException
from app.core.response import model_to_camel_dict
from app.core.tree_utils import build_tree_select
from app.models.sys_dept import SysDept
from app.models.sys_role_dept import SysRoleDept
from app.models.sys_user import SysUser


def list_depts(
    db: Session, login_user: dict, dept_name: str | None, status: str | None, exclude_dept_id: int | None = None
) -> list[dict]:
    query = db.query(SysDept).filter(SysDept.del_flag == DEL_FLAG_NORMAL)
    if dept_name:
        query = query.filter(SysDept.dept_name.like(f"%{dept_name}%"))
    if status:
        query = query.filter(SysDept.status == status)

    # 部门列表查询没有 join sys_user，所以"仅本人"规则退化成"看不到"（user_alias=None）
    scope_expr = build_data_scope_filter(login_user, dept_alias="sys_dept", user_alias=None)
    if scope_expr:
        query = query.filter(text(scope_expr))

    depts = query.order_by(SysDept.parent_id, SysDept.order_num).all()
    if exclude_dept_id is not None:
        excluded = {d.dept_id for d in depts if d.dept_id == exclude_dept_id}
        excluded |= {d.dept_id for d in depts if d.ancestors and str(exclude_dept_id) in d.ancestors.split(",")}
        depts = [d for d in depts if d.dept_id not in excluded]

    return [model_to_camel_dict(d) for d in depts]


def get_dept_detail(db: Session, dept_id: int) -> dict:
    dept = db.query(SysDept).filter(SysDept.dept_id == dept_id).first()
    if dept is None:
        raise ServiceException("部门不存在")
    return model_to_camel_dict(dept)


def check_dept_data_scope(db: Session, login_user: dict, dept_id: int) -> None:
    if login_user.get("user_id") == SUPER_ADMIN_ID:
        return
    rows = list_depts(db, login_user, None, None)
    if not any(d["deptId"] == dept_id for d in rows):
        raise ServiceException("没有权限访问部门数据！")


def create_dept(db: Session, body, operator_name: str) -> None:
    parent = db.query(SysDept).filter(SysDept.dept_id == body.parent_id).first()
    if parent is None:
        raise ServiceException("父部门不存在")
    if parent.status == STATUS_DISABLE:
        raise ServiceException("部门停用，不允许新增")

    dept = SysDept(
        parent_id=body.parent_id,
        ancestors=f"{parent.ancestors},{parent.dept_id}",
        dept_name=body.dept_name,
        order_num=body.order_num,
        leader=body.leader,
        phone=body.phone,
        email=body.email,
        status=body.status,
    )
    fill_create_fields(dept, operator_name)
    db.add(dept)
    db.commit()


def update_dept(db: Session, body, operator_name: str) -> None:
    if body.parent_id == body.dept_id:
        raise ServiceException(f"修改部门'{body.dept_name}'失败，上级部门不能是自己")

    if body.status == STATUS_DISABLE:
        normal_children = (
            db.query(SysDept)
            .filter(SysDept.status == STATUS_NORMAL, text(f"FIND_IN_SET({int(body.dept_id)}, ancestors)"))
            .count()
        )
        if normal_children > 0:
            raise ServiceException("该部门包含未停用的子部门！")

    dept = db.query(SysDept).filter(SysDept.dept_id == body.dept_id).first()
    if dept is None:
        raise ServiceException("部门不存在")

    old_ancestors = dept.ancestors or ""
    new_ancestors = old_ancestors
    if body.parent_id != dept.parent_id:
        new_parent = db.query(SysDept).filter(SysDept.dept_id == body.parent_id).first()
        if new_parent is None:
            raise ServiceException("父部门不存在")
        new_ancestors = f"{new_parent.ancestors},{new_parent.dept_id}"

        children = db.query(SysDept).filter(text(f"FIND_IN_SET({int(body.dept_id)}, ancestors)")).all()
        for child in children:
            if child.ancestors and child.ancestors.startswith(old_ancestors):
                child.ancestors = new_ancestors + child.ancestors[len(old_ancestors):]

    dept.dept_name = body.dept_name
    dept.parent_id = body.parent_id
    dept.ancestors = new_ancestors
    dept.order_num = body.order_num
    dept.leader = body.leader
    dept.phone = body.phone
    dept.email = body.email
    dept.status = body.status
    fill_update_fields(dept, operator_name)
    db.commit()

    # 启用部门时，顺带把它所有祖先都改成正常状态（上级都不正常，自己正常没有意义）
    if body.status == STATUS_NORMAL and new_ancestors and new_ancestors != "0":
        ancestor_ids = [int(x) for x in new_ancestors.split(",") if x and x != "0"]
        if ancestor_ids:
            db.query(SysDept).filter(SysDept.dept_id.in_(ancestor_ids)).update(
                {SysDept.status: STATUS_NORMAL}, synchronize_session=False
            )
            db.commit()


def delete_dept(db: Session, dept_id: int) -> None:
    if db.query(SysDept).filter(SysDept.parent_id == dept_id, SysDept.del_flag == DEL_FLAG_NORMAL).count() > 0:
        raise ServiceException("存在下级部门,不允许删除")
    if db.query(SysUser).filter(SysUser.dept_id == dept_id, SysUser.del_flag == DEL_FLAG_NORMAL).count() > 0:
        raise ServiceException("部门存在用户,不允许删除")

    dept = db.query(SysDept).filter(SysDept.dept_id == dept_id).first()
    if dept is None:
        raise ServiceException("部门不存在")
    dept.del_flag = DEL_FLAG_DELETED
    db.commit()


def dept_tree_select(db: Session, login_user: dict) -> list[dict]:
    depts = list_depts(db, login_user, None, None)
    nodes = [
        {"id": d["deptId"], "parent_id": d["parentId"] or 0, "label": d["deptName"], "disabled": d["status"] == "1"}
        for d in depts
    ]
    return build_tree_select(nodes)


def dept_list_by_role_id(db: Session, role_id: int, dept_check_strictly: bool) -> list[int]:
    """给角色"数据权限"弹窗用：该角色已勾选的部门 id 列表（严格模式下只给叶子节点，父节点由前端表现为半选）"""
    checked_ids = {row.dept_id for row in db.query(SysRoleDept).filter(SysRoleDept.role_id == role_id).all()}
    if not dept_check_strictly or not checked_ids:
        return list(checked_ids)

    all_depts = {d.dept_id: d for d in db.query(SysDept).all()}
    parent_ids_of_checked = {all_depts[i].parent_id for i in checked_ids if i in all_depts}
    return [i for i in checked_ids if i not in parent_ids_of_checked]
