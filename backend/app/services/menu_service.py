"""
菜单树 -> 前端路由，严格复刻若依 SysMenuServiceImpl.buildMenus 及其辅助方法
（getRouteName / getRouterPath / getComponent / isMenuFrame / isInnerLink /
isParentView / innerLinkReplaceEach），对应 getRouters 接口。
"""
from sqlalchemy.orm import Session

from app.core.constants import MENU_TYPE_DIR, MENU_TYPE_MENU, NO_FRAME, STATUS_NORMAL
from app.models.sys_menu import SysMenu
from app.models.sys_role import SysRole
from app.models.sys_role_menu import SysRoleMenu
from app.models.sys_user_role import SysUserRole

MENU_ROOT_ID = 0


def _menu_to_dict(menu: SysMenu) -> dict:
    return {
        "menu_id": menu.menu_id,
        "parent_id": menu.parent_id or 0,
        "menu_name": menu.menu_name,
        "path": menu.path or "",
        "component": menu.component,
        "query": menu.query,
        "route_name": menu.route_name or "",
        "is_frame": menu.is_frame,
        "is_cache": menu.is_cache,
        "menu_type": menu.menu_type,
        "visible": menu.visible,
        "status": menu.status,
        "icon": menu.icon,
        "order_num": menu.order_num or 0,
        "children": [],
    }


def select_menu_tree_by_user_id(db: Session, user_id: int, is_admin: bool) -> list[dict]:
    """
    查询用户可见的菜单树（只取 M目录/C菜单，排除 F按钮）。
    管理员取全部正常菜单；普通用户按角色关联过滤，且角色本身要是正常状态。
    """
    base_filter = [SysMenu.menu_type.in_([MENU_TYPE_DIR, MENU_TYPE_MENU]), SysMenu.status == STATUS_NORMAL]

    if is_admin:
        menus = (
            db.query(SysMenu)
            .filter(*base_filter)
            .order_by(SysMenu.parent_id, SysMenu.order_num)
            .all()
        )
    else:
        menus = (
            db.query(SysMenu)
            .join(SysRoleMenu, SysRoleMenu.menu_id == SysMenu.menu_id)
            .join(SysUserRole, SysUserRole.role_id == SysRoleMenu.role_id)
            .join(SysRole, SysRole.role_id == SysUserRole.role_id)
            .filter(
                SysUserRole.user_id == user_id,
                SysRole.status == STATUS_NORMAL,
                *base_filter,
            )
            .order_by(SysMenu.parent_id, SysMenu.order_num)
            .distinct()
            .all()
        )

    menu_dicts = [_menu_to_dict(m) for m in menus]
    return _get_child_perms(menu_dicts, MENU_ROOT_ID)


def _get_child_perms(menu_list: list[dict], parent_id: int) -> list[dict]:
    result = []
    for menu in menu_list:
        if menu["parent_id"] == parent_id:
            _recursion_fn(menu_list, menu)
            result.append(menu)
    return result


def _recursion_fn(menu_list: list[dict], menu: dict) -> None:
    children = [m for m in menu_list if m["parent_id"] == menu["menu_id"]]
    menu["children"] = children
    for child in children:
        _recursion_fn(menu_list, child)


def _is_http(path: str | None) -> bool:
    return bool(path) and (path.startswith("http://") or path.startswith("https://"))


def _is_menu_frame(menu: dict) -> bool:
    return menu["parent_id"] == MENU_ROOT_ID and menu["menu_type"] == MENU_TYPE_MENU and menu["is_frame"] == NO_FRAME


def _is_inner_link(menu: dict) -> bool:
    return menu["is_frame"] == NO_FRAME and _is_http(menu["path"])


def _is_parent_view(menu: dict) -> bool:
    return menu["parent_id"] != MENU_ROOT_ID and menu["menu_type"] == MENU_TYPE_DIR


def _capitalize(text: str) -> str:
    """对应 Apache Commons StringUtils.capitalize：只把首字母大写，其余不变"""
    return text[:1].upper() + text[1:] if text else text


def _get_route_name_by(name: str | None, path: str) -> str:
    router_name = name if name else path
    return _capitalize(router_name)


def _get_route_name(menu: dict) -> str:
    if _is_menu_frame(menu):
        return ""
    return _get_route_name_by(menu["route_name"], menu["path"])


def _get_router_path(menu: dict) -> str:
    router_path = menu["path"]
    if menu["parent_id"] != MENU_ROOT_ID and _is_inner_link(menu):
        router_path = _inner_link_replace_each(router_path)
    if menu["parent_id"] == MENU_ROOT_ID and menu["menu_type"] == MENU_TYPE_DIR and menu["is_frame"] == NO_FRAME:
        router_path = "/" + menu["path"]
    elif _is_menu_frame(menu):
        router_path = "/"
    return router_path


def _get_component(menu: dict) -> str:
    component = "Layout"
    if menu["component"] and not _is_menu_frame(menu):
        component = menu["component"]
    elif not menu["component"] and menu["parent_id"] != MENU_ROOT_ID and _is_inner_link(menu):
        component = "InnerLink"
    elif not menu["component"] and _is_parent_view(menu):
        component = "ParentView"
    return component


def _inner_link_replace_each(path: str) -> str:
    for old, new in (("http://", ""), ("https://", ""), ("www.", ""), (".", "/"), (":", "/")):
        path = path.replace(old, new)
    return path


def _meta(title: str, icon: str | None, no_cache: bool = False, link: str | None = None, check_http: bool = True) -> dict:
    """
    对应 MetaVo 的几个构造函数：
    - check_http=True（4参构造）：link 只有在是 http(s) 链接时才保留；
    - check_http=False（3参构造）：不检查，直接把传入值当作 link。
    """
    return {
        "title": title,
        "icon": icon,
        "noCache": no_cache,
        "link": link if (not check_http or _is_http(link)) else None,
    }


def build_menus(menus: list[dict]) -> list[dict]:
    """构建前端路由所需要的菜单（RouterVo 列表）"""
    routers = []
    for menu in menus:
        router: dict = {
            "hidden": menu["visible"] == "1",
            "name": _get_route_name(menu),
            "path": _get_router_path(menu),
            "component": _get_component(menu),
        }
        if menu.get("query"):
            router["query"] = menu["query"]
        router["meta"] = _meta(menu["menu_name"], menu["icon"], menu["is_cache"] == 1, menu["path"])

        children = menu.get("children") or []
        if children and menu["menu_type"] == MENU_TYPE_DIR:
            router["alwaysShow"] = True
            router["redirect"] = "noRedirect"
            router["children"] = build_menus(children)
        elif _is_menu_frame(menu):
            router["meta"] = None
            child = {
                "path": menu["path"],
                "component": menu["component"],
                "name": _get_route_name_by(menu["route_name"], menu["path"]),
                "meta": _meta(menu["menu_name"], menu["icon"], menu["is_cache"] == 1, menu["path"]),
            }
            if menu.get("query"):
                child["query"] = menu["query"]
            router["children"] = [child]
        elif menu["parent_id"] == MENU_ROOT_ID and _is_inner_link(menu):
            router["meta"] = _meta(menu["menu_name"], menu["icon"])
            router["path"] = "/"
            inner_path = _inner_link_replace_each(menu["path"])
            router["children"] = [
                {
                    "path": inner_path,
                    "component": "InnerLink",
                    "name": _get_route_name_by(menu["route_name"], inner_path),
                    "meta": _meta(menu["menu_name"], menu["icon"], link=menu["path"], check_http=False),
                }
            ]

        # 对应 Java RouterVo 上的 @JsonInclude(NON_EMPTY)：空字符串/None/空列表都不出现在返回结果里
        if not router.get("name"):
            router.pop("name", None)
        if router.get("meta") is None:
            router.pop("meta", None)
        if not router.get("children"):
            router.pop("children", None)
        routers.append(router)
    return routers
