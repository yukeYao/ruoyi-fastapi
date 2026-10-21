"""唯一性校验，对应若依 checkDeptNameUnique / checkRouteConfigUnique / 个人中心的手机号邮箱校验"""
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models import SysMenu
from app.services.menu_service import check_route_config_unique
from main import app
from tests.conftest import auth_headers

client = TestClient(app)


def _admin_headers():
    return auth_headers(client, "admin", "admin123")


# ---------------------------------------------------------------------------
# 部门名称：同一上级下唯一，排除已删除
# ---------------------------------------------------------------------------

def _add_dept(headers, parent_id, name):
    return client.post(
        "/system/dept", headers=headers, json={"parentId": parent_id, "deptName": name, "orderNum": 1, "status": "0"}
    ).json()


def _find_dept(headers, name, parent_id):
    rows = client.get("/system/dept/list", headers=headers, params={"deptName": name}).json()["data"]
    return next(d["deptId"] for d in rows if d["parentId"] == parent_id)


def test_dept_name_unique_under_same_parent():
    headers = _admin_headers()
    assert _add_dept(headers, 100, "uniq_dept")["code"] == 200
    first_id = _find_dept(headers, "uniq_dept", 100)

    resp = _add_dept(headers, 100, "uniq_dept")
    assert resp["code"] == 500 and resp["msg"] == "新增部门'uniq_dept'失败，部门名称已存在"

    # 不同上级下可以同名
    assert _add_dept(headers, 101, "uniq_dept")["code"] == 200
    second_id = _find_dept(headers, "uniq_dept", 101)

    # 修改：挪到已有同名部门的上级下 -> 冲突；原地保存自己 -> 不冲突
    body = {"deptId": second_id, "parentId": 100, "deptName": "uniq_dept", "orderNum": 1, "status": "0"}
    resp = client.put("/system/dept", headers=headers, json=body).json()
    assert resp["code"] == 500 and resp["msg"] == "修改部门'uniq_dept'失败，部门名称已存在"
    body["parentId"] = 101
    assert client.put("/system/dept", headers=headers, json=body).json()["code"] == 200

    # 已删除的部门不参与唯一校验
    assert client.delete(f"/system/dept/{first_id}", headers=headers).json()["code"] == 200
    assert _add_dept(headers, 100, "uniq_dept")["code"] == 200

    client.delete(f"/system/dept/{_find_dept(headers, 'uniq_dept', 100)}", headers=headers)
    client.delete(f"/system/dept/{second_id}", headers=headers)


# ---------------------------------------------------------------------------
# 菜单路由：同级路由地址唯一、根目录路由地址唯一、路由名称全局唯一（忽略大小写）
# ---------------------------------------------------------------------------

def _menu(name, parent_id, path, route_name="", menu_type="C", menu_id=None):
    body = {"menuName": name, "parentId": parent_id, "orderNum": 1, "path": path, "routeName": route_name, "menuType": menu_type}
    if menu_id is not None:
        body["menuId"] = menu_id
    return body


def _find_menu(headers, name):
    return client.get("/system/menu/list", headers=headers, params={"menuName": name}).json()["data"][0]["menuId"]


def test_menu_route_config_unique():
    headers = _admin_headers()
    created = []
    try:
        assert client.post("/system/menu", headers=headers, json=_menu("uniq_root", 0, "uniqroute", menu_type="M")).json()["code"] == 200
        root_id = _find_menu(headers, "uniq_root")
        created.append(root_id)

        def add(body):
            return client.post("/system/menu", headers=headers, json=body).json()

        # 修改时原样保存自己不冲突
        resp = client.put("/system/menu", headers=headers, json=_menu("uniq_root", 0, "uniqroute", menu_type="M", menu_id=root_id))
        assert resp.json()["code"] == 200

        # 同级（根目录）路由地址重复
        resp = add(_menu("uniq_dup_path", 0, "UniqRoute", route_name="UniqDupName", menu_type="M"))
        assert resp["code"] == 500 and resp["msg"] == "新增菜单'uniq_dup_path'失败，路由名称或地址已存在"

        # 路由名称为空时用路由地址代替，全局唯一：其他上级下同地址也冲突
        assert add(_menu("uniq_child_a", 1, "uniqroute"))["code"] == 500

        # 不同上级、路由名称不同 -> 允许同地址
        assert add(_menu("uniq_child_b", 1, "uniqroute", route_name="UniqChildB"))["code"] == 200
        created.append(_find_menu(headers, "uniq_child_b"))

        # 路由名称冲突（忽略大小写）
        assert add(_menu("uniq_child_c", 1, "uniqc", route_name="UNIQCHILDB"))["code"] == 500

        # 根目录下的路由地址要和所有目录/菜单都不重复（"user" 是系统管理下的用户管理）
        assert add(_menu("uniq_root_user", 0, "user", route_name="UniqRootUser", menu_type="M"))["code"] == 500

        # 与若依一致的不对称规则：上面允许在非根目录下建同地址菜单，但之后根目录菜单再保存时
        # 要和所有目录/菜单比较路由地址，会和 uniq_child_b 冲突
        resp = client.put(
            "/system/menu", headers=headers, json=_menu("uniq_root", 0, "uniqroute", menu_type="M", menu_id=root_id)
        ).json()
        assert resp["code"] == 500 and resp["msg"] == "修改菜单'uniq_root'失败，路由名称或地址已存在"

        # 按钮没有路由地址，不受影响
        assert add(_menu("uniq_button", root_id, "", menu_type="F"))["code"] == 200
        created.append(_find_menu(headers, "uniq_button"))
    finally:
        for menu_id in reversed(created):
            client.delete(f"/system/menu/{menu_id}", headers=headers)


def test_seed_menus_pass_route_config_check():
    """种子数据里的目录/菜单原样保存都不应被路由唯一校验拦下"""
    db = SessionLocal()
    try:
        menus = db.query(SysMenu).filter(SysMenu.menu_type.in_(("M", "C"))).all()
        failed = [m.menu_name for m in menus if not check_route_config_unique(db, m.path, m.route_name, m.parent_id, m.menu_id)]
    finally:
        db.close()
    assert menus and failed == []


# ---------------------------------------------------------------------------
# 个人中心：手机号、邮箱唯一
# ---------------------------------------------------------------------------

def test_profile_phone_and_email_unique():
    admin_user = client.get("/getInfo", headers=_admin_headers()).json()["user"]
    headers = auth_headers(client, "ry", "admin123")

    resp = client.put("/system/user/profile", headers=headers, json={"nickName": "若依", "phonenumber": admin_user["phonenumber"]})
    assert resp.json() == {"code": 500, "msg": "修改用户'ry'失败，手机号码已存在"}

    resp = client.put("/system/user/profile", headers=headers, json={"nickName": "若依", "email": admin_user["email"]})
    assert resp.json() == {"code": 500, "msg": "修改用户'ry'失败，邮箱账号已存在"}
