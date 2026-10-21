"""岗位管理业务逻辑，对应若依 SysPostController + SysPostServiceImpl（sys_post 没有 del_flag，是真删除）"""
from sqlalchemy.orm import Session

from app.core.exceptions import ServiceException
from app.core.response import model_to_camel_dict
from app.models.sys_post import SysPost
from app.models.sys_user_post import SysUserPost


def list_posts(
    db: Session, post_code: str | None, post_name: str | None, status: str | None, page_num: int, page_size: int
) -> tuple[list[dict], int]:
    query = db.query(SysPost)
    if post_code:
        query = query.filter(SysPost.post_code.like(f"%{post_code}%"))
    if post_name:
        query = query.filter(SysPost.post_name.like(f"%{post_name}%"))
    if status:
        query = query.filter(SysPost.status == status)

    total = query.count()
    rows = query.order_by(SysPost.post_sort).offset((page_num - 1) * page_size).limit(page_size).all()
    return [model_to_camel_dict(p) for p in rows], total


def list_all_posts(db: Session) -> list[dict]:
    return [model_to_camel_dict(p) for p in db.query(SysPost).order_by(SysPost.post_sort).all()]


def get_post_detail(db: Session, post_id: int) -> dict:
    post = db.query(SysPost).filter(SysPost.post_id == post_id).first()
    if post is None:
        raise ServiceException("岗位不存在")
    return model_to_camel_dict(post)


def check_post_name_unique(db: Session, post_name: str, post_id: int | None = None) -> bool:
    existing = db.query(SysPost).filter(SysPost.post_name == post_name).first()
    return existing is None or existing.post_id == post_id


def check_post_code_unique(db: Session, post_code: str, post_id: int | None = None) -> bool:
    existing = db.query(SysPost).filter(SysPost.post_code == post_code).first()
    return existing is None or existing.post_id == post_id


def create_post(db: Session, body, operator_name: str) -> None:
    if not check_post_name_unique(db, body.post_name):
        raise ServiceException(f"新增岗位'{body.post_name}'失败，岗位名称已存在")
    if not check_post_code_unique(db, body.post_code):
        raise ServiceException(f"新增岗位'{body.post_name}'失败，岗位编码已存在")
    post = SysPost(
        post_code=body.post_code,
        post_name=body.post_name,
        post_sort=body.post_sort,
        status=body.status,
        remark=body.remark,
        create_by=operator_name,
    )
    db.add(post)
    db.commit()


def update_post(db: Session, body, operator_name: str) -> None:
    if not check_post_name_unique(db, body.post_name, body.post_id):
        raise ServiceException(f"修改岗位'{body.post_name}'失败，岗位名称已存在")
    if not check_post_code_unique(db, body.post_code, body.post_id):
        raise ServiceException(f"修改岗位'{body.post_name}'失败，岗位编码已存在")

    post = db.query(SysPost).filter(SysPost.post_id == body.post_id).first()
    if post is None:
        raise ServiceException("岗位不存在")
    post.post_code = body.post_code
    post.post_name = body.post_name
    post.post_sort = body.post_sort
    post.status = body.status
    post.remark = body.remark
    post.update_by = operator_name
    db.commit()


def delete_posts(db: Session, post_ids: list[int]) -> None:
    for post_id in post_ids:
        post = db.query(SysPost).filter(SysPost.post_id == post_id).first()
        if post is None:
            continue
        assigned_count = db.query(SysUserPost).filter(SysUserPost.post_id == post_id).count()
        if assigned_count > 0:
            raise ServiceException(f"{post.post_name}已分配,不能删除")

    db.query(SysPost).filter(SysPost.post_id.in_(post_ids)).delete(synchronize_session=False)
    db.commit()
