"""角色相关入参模型"""
from app.schemas.base import CamelModel


class RoleCreate(CamelModel):
    role_name: str
    role_key: str
    role_sort: int
    status: str = "0"
    menu_check_strictly: bool = True
    dept_check_strictly: bool = True
    remark: str | None = None
    menu_ids: list[int] = []


class RoleUpdate(RoleCreate):
    role_id: int


class RoleDataScope(CamelModel):
    role_id: int
    data_scope: str
    dept_check_strictly: bool = True
    dept_ids: list[int] = []


class RoleChangeStatus(CamelModel):
    role_id: int
    status: str
