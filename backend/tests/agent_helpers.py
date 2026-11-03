"""智能助手测试的公共工具：建角色/用户、构造模型的工具调用、调用对话接口"""
import uuid

from langchain_core.messages import AIMessage

from tests.conftest import auth_headers

# 菜单 ID（见 ry_20260417.sql 与 agent.sql）
MENU_AGENT = 2000
MENU_USER = 100          # system:user:list
MENU_ROLE = 101          # system:role:list
MENU_DEPT = 103          # system:dept:list
MENU_ONLINE = 109        # monitor:online:list
MENU_OPERLOG = 500       # monitor:operlog:list
MENU_LOGININFOR = 501    # monitor:logininfor:list
MENU_USER_QUERY = 1000   # system:user:query

PASSWORD = "Passw0rd!"

# 本用例新建的用户和角色，用例结束后由 conftest 的 cleanup_agent_test_data 统一删除：
# 测试库在整个会话内共享，残留数据会影响其他测试（例如按部门统计可见角色的断言）
CREATED_USER_IDS: list[int] = []
CREATED_ROLE_IDS: list[int] = []


def add_role(client, admin, key, menu_ids, data_scope="1"):
    body = {"roleName": key, "roleKey": key, "roleSort": 60, "status": "0", "menuIds": menu_ids}
    assert client.post("/system/role", headers=admin, json=body).json()["code"] == 200
    # 列表按 LIKE 模糊匹配，必须挑出完全相等的那一行，否则可能拿到别的测试建的同名前缀角色
    rows = client.get("/system/role/list", headers=admin, params={"roleKey": key, "pageSize": 100}).json()["rows"]
    role_id = next(r["roleId"] for r in rows if r["roleKey"] == key)
    CREATED_ROLE_IDS.append(role_id)
    if data_scope != "1":
        resp = client.put("/system/role/dataScope", headers=admin, json={"roleId": role_id, "dataScope": data_scope, "deptIds": []})
        assert resp.json()["code"] == 200
    return role_id


def add_user(client, admin, user_name, role_ids, dept_id=105, **extra):
    body = {"userName": user_name, "nickName": user_name, "password": PASSWORD, "deptId": dept_id,
            "postIds": [], "roleIds": role_ids, **extra}
    resp = client.post("/system/user", headers=admin, json=body).json()
    assert resp["code"] == 200, resp
    rows = client.get("/system/user/list", headers=admin, params={"userName": user_name, "pageSize": 100}).json()["rows"]
    user_id = next(r["userId"] for r in rows if r["userName"] == user_name)
    CREATED_USER_IDS.append(user_id)
    return user_id


def cleanup_created(client) -> None:
    """先删用户再删角色：角色已分配给用户时不允许删除"""
    if not CREATED_USER_IDS and not CREATED_ROLE_IDS:
        return
    admin = auth_headers(client)
    if CREATED_USER_IDS:
        assert client.delete("/system/user/" + ",".join(map(str, CREATED_USER_IDS)), headers=admin).json()["code"] == 200
    if CREATED_ROLE_IDS:
        assert client.delete("/system/role/" + ",".join(map(str, CREATED_ROLE_IDS)), headers=admin).json()["code"] == 200
    CREATED_USER_IDS.clear()
    CREATED_ROLE_IDS.clear()


def user_with_menus(client, prefix, menu_ids, data_scope="1", dept_id=105):
    """
    新建一个只有指定菜单权限的角色和用户，返回 (user_id, 请求头)。
    测试库在整个测试会话内共享，而其他测试按名称 LIKE 查找自己建的数据（如 scope_user），
    所以这里的名称用 ag_ 前缀加 _u/_r 结尾，保证不会包含其他测试的名称，被它们误查到、误删。
    """
    admin = auth_headers(client)
    role_id = add_role(client, admin, f"ag_{prefix}_r", menu_ids, data_scope)
    user_id = add_user(client, admin, f"ag_{prefix}_u", [role_id], dept_id)
    return user_id, auth_headers(client, f"ag_{prefix}_u", PASSWORD)


def call(name, args=None, call_id=None):
    return {"name": name, "args": args or {}, "id": call_id or f"call_{uuid.uuid4().hex[:8]}", "type": "tool_call"}


def tool_msg(*calls):
    """模型回复：发出若干工具调用"""
    return AIMessage(content="", tool_calls=list(calls))


def final(text="完成"):
    return AIMessage(content=text)


def chat(client, headers, message, conversation_id=None):
    body = {"message": message}
    if conversation_id:
        body["conversationId"] = conversation_id
    return client.post("/agent/chat", headers=headers, json=body).json()


def tool_results(body):
    """从对话返回里取出全部 ToolMessage：{tool_call_id: message}"""
    return {m["toolCallId"]: m for m in body["data"]["messages"] if m["type"] == "tool"}
