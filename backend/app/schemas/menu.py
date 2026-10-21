"""菜单相关入参模型"""
from app.schemas.base import CamelModel


class MenuCreate(CamelModel):
    menu_name: str
    parent_id: int = 0
    order_num: int = 0
    path: str | None = ""
    component: str | None = None
    query: str | None = None
    route_name: str | None = ""
    is_frame: int = 1
    is_cache: int = 0
    menu_type: str
    visible: str = "0"
    status: str = "0"
    perms: str | None = None
    icon: str | None = "#"
    remark: str | None = ""


class MenuUpdate(MenuCreate):
    menu_id: int
