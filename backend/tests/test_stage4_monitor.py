"""阶段4验收测试：操作日志 / 登录日志 / 在线用户"""
import time

from fastapi.testclient import TestClient

from app.core.redis_client import redis_client
from main import app
from tests.conftest import auth_headers

client = TestClient(app)


def _admin_headers():
    return auth_headers(client, "admin", "admin123")


def _wait_for_oper_log(headers, title: str, timeout: float = 3.0) -> dict:
    """@log 装饰器用线程池异步写库，这里轮询等它落库，避免测试里出现竞态"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get("/monitor/operlog/list", headers=headers, params={"title": title, "pageSize": 1})
        body = resp.json()
        if body["total"] > 0:
            return body["rows"][0]
        time.sleep(0.1)
    raise AssertionError(f"等待操作日志 '{title}' 落库超时")


def test_write_operation_creates_oper_log_asynchronously():
    headers = _admin_headers()
    resp = client.post(
        "/system/post",
        headers=headers,
        json={"postCode": "stage4_post", "postName": "阶段4测试岗位", "postSort": 1, "status": "0"},
    )
    assert resp.json()["code"] == 200

    row = _wait_for_oper_log(headers, "岗位管理")
    assert row["businessType"] == 1  # BIZ_INSERT
    assert row["operName"] == "admin"
    assert row["status"] == 0
    assert "stage4_post" in row["operParam"]
    assert "password" not in row["operParam"]

    # 清理
    post_id = client.get("/system/post/list", headers=headers, params={"postCode": "stage4_post"}).json()["rows"][0]["postId"]
    client.delete(f"/system/post/{post_id}", headers=headers)


def test_failed_operation_logs_error_status():
    headers = _admin_headers()
    # 删除已被分配的岗位(postId=1 "董事长")会抛 ServiceException，日志应该记为异常状态
    resp = client.delete("/system/post/1", headers=headers)
    assert resp.json()["code"] == 500

    row = _wait_for_oper_log(headers, "岗位管理")
    # 可能命中上一条新增日志，多取几条找删除操作那条
    deadline = time.time() + 3
    found = None
    while time.time() < deadline:
        rows = client.get("/monitor/operlog/list", headers=headers, params={"title": "岗位管理", "pageSize": 20}).json()["rows"]
        for r in rows:
            if r["businessType"] == 3 and r["status"] == 1:
                found = r
                break
        if found:
            break
        time.sleep(0.1)
    assert found is not None
    assert "不能删除" in found["errorMsg"] or "已分配" in found["errorMsg"]


def test_operlog_delete_and_clean():
    headers = _admin_headers()
    row = _wait_for_oper_log(headers, "岗位管理")
    oper_id = row["operId"]

    resp = client.delete(f"/monitor/operlog/{oper_id}", headers=headers)
    assert resp.json()["code"] == 200

    resp = client.delete("/monitor/operlog/clean", headers=headers)
    assert resp.json()["code"] == 200

    # clean 这个操作本身也会异步产生一条"清空数据"的新日志，所以清空后只剩这一条，不是 0 条
    row = _wait_for_oper_log(headers, "操作日志")
    assert row["businessType"] == 9  # BIZ_CLEAN
    assert client.get("/monitor/operlog/list", headers=headers).json()["total"] == 1


def test_logininfor_list_and_unlock():
    headers = _admin_headers()
    # 刚才的登录本身就会产生登录日志
    resp = client.get("/monitor/logininfor/list", headers=headers, params={"userName": "admin"})
    body = resp.json()
    assert body["code"] == 200
    assert body["total"] > 0

    # 手动设一个锁定计数，调用解锁接口后应该被清掉
    redis_client.set("pwd_err_cnt:someone", 5, ex=600)
    resp = client.get("/monitor/logininfor/unlock/someone", headers=headers)
    assert resp.json()["code"] == 200
    assert redis_client.get("pwd_err_cnt:someone") is None


def test_online_users_list_and_force_logout():
    headers = _admin_headers()
    token = headers["Authorization"].split(" ")[1]

    resp = client.get("/monitor/online/list", headers=headers)
    body = resp.json()
    assert body["code"] == 200
    admin_sessions = [row for row in body["rows"] if row["userName"] == "admin"]
    assert len(admin_sessions) >= 1

    import jwt as pyjwt

    from app.config import settings

    payload = pyjwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    token_id = payload["login_user_key"]

    resp = client.delete(f"/monitor/online/{token_id}", headers=headers)
    assert resp.json()["code"] == 200

    # 被强退后，原 token 应该立刻失效（401）
    resp = client.get("/getInfo", headers=headers)
    assert resp.json()["code"] == 401
