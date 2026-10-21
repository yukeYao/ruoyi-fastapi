"""菜单管理路由，对应 SysMenuController，路径前缀 /system/menu"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.constants import SUPER_ADMIN_ID
from app.core.database import get_db
from app.core.deps import has_perm
from app.core.response import ajax_success
from app.schemas.menu import MenuCreate, MenuUpdate
from app.services import menu_service, role_service

router = APIRouter(prefix="/system/menu")


def _is_admin(login_user: dict) -> bool:
    return login_user.get("user_id") == SUPER_ADMIN_ID


@router.get("/list")
def list_menus(
    menuName: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    login_user: dict = Depends(has_perm("system:menu:list")),
):
    data = menu_service.list_menus(db, _is_admin(login_user), login_user["user_id"], menuName, status)
    return ajax_success(data=data)


@router.get("/treeselect")
def treeselect(db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:menu:list"))):
    return ajax_success(data=menu_service.menu_treeselect(db, _is_admin(login_user), login_user["user_id"]))


@router.get("/roleMenuTreeselect/{role_id}")
def role_menu_treeselect(
    role_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:menu:query"))
):
    role = role_service.get_role_detail(db, role_id)
    result = menu_service.role_menu_treeselect(
        db, _is_admin(login_user), login_user["user_id"], role_id, role["menuCheckStrictly"] in (1, True)
    )
    return ajax_success(**result)


@router.get("/{menu_id}")
def get_menu(menu_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:menu:query"))):
    return ajax_success(data=menu_service.get_menu_detail(db, menu_id))


@router.post("")
def add_menu(body: MenuCreate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:menu:add"))):
    menu_service.create_menu(db, body, login_user["user_name"])
    return ajax_success()


@router.put("")
def edit_menu(
    body: MenuUpdate, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:menu:edit"))
):
    menu_service.update_menu(db, body, login_user["user_name"])
    return ajax_success()


@router.delete("/{menu_id}")
def remove_menu(
    menu_id: int, db: Session = Depends(get_db), login_user: dict = Depends(has_perm("system:menu:remove"))
):
    menu_service.delete_menu(db, menu_id)
    return ajax_success()
