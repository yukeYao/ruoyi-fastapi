"""岗位相关入参模型"""
from app.schemas.base import CamelModel


class PostCreate(CamelModel):
    post_code: str
    post_name: str
    post_sort: int
    status: str = "0"
    remark: str | None = None


class PostUpdate(PostCreate):
    post_id: int
