"""部门相关入参模型"""
from app.schemas.base import CamelModel


class DeptCreate(CamelModel):
    parent_id: int
    dept_name: str
    order_num: int = 0
    leader: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str = "0"


class DeptUpdate(DeptCreate):
    dept_id: int
