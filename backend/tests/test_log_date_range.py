"""
操作日志 / 登录日志按日期范围筛选。

复现的问题：前端日期选择器传的是 `D 00:00:00 ~ D 23:59:59`，
后端曾经把结束时间截成 `D`，MySQL 按 `D 00:00:00` 比较，导致结束日期当天的记录全部查不到。
"""
import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from main import app
from tests.conftest import auth_headers

client = TestClient(app)


def _range_params(begin: str, end: str, **extra) -> dict:
    # 前端 addDateRange 的传参格式：params[beginTime] / params[endTime]
    return {"params[beginTime]": begin, "params[endTime]": end, **extra}


def _latest_login_day(headers) -> str:
    """取最新一条登录日志的日期，用库里的时间而不是测试进程的 today()，避免两边时钟/时区不一致"""
    rows = client.get("/monitor/logininfor/list", headers=headers, params={"userName": "admin", "pageSize": 1}).json()["rows"]
    return rows[0]["loginTime"][:10]


def _wait_for_oper_log(headers, title: str, timeout: float = 3.0) -> dict:
    """@log 是异步写库，轮询等它落库"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get("/monitor/operlog/list", headers=headers, params={"title": title, "pageSize": 1}).json()
        if body["total"] > 0:
            return body["rows"][0]
        time.sleep(0.1)
    raise AssertionError(f"等待操作日志 '{title}' 落库超时")


def test_logininfor_end_date_includes_whole_day():
    headers = auth_headers(client)
    day = _latest_login_day(headers)

    resp = client.get(
        "/monitor/logininfor/list",
        headers=headers,
        params=_range_params(f"{day} 00:00:00", f"{day} 23:59:59", userName="admin"),
    )
    assert resp.json()["total"] > 0


def test_operlog_end_date_includes_whole_day():
    headers = auth_headers(client)
    resp = client.post(
        "/system/post",
        headers=headers,
        json={"postCode": "date_range_post", "postName": "日期筛选测试岗位", "postSort": 1, "status": "0"},
    )
    assert resp.json()["code"] == 200
    try:
        day = _wait_for_oper_log(headers, "岗位管理")["operTime"][:10]
        resp = client.get(
            "/monitor/operlog/list",
            headers=headers,
            params=_range_params(f"{day} 00:00:00", f"{day} 23:59:59", title="岗位管理"),
        )
        assert resp.json()["total"] > 0
    finally:
        post_id = client.get("/system/post/list", headers=headers, params={"postCode": "date_range_post"}).json()["rows"][0]["postId"]
        client.delete(f"/system/post/{post_id}", headers=headers)


def test_log_date_only_end_time():
    """直接调接口只传日期时，也应该把结束日期当天整天算进去"""
    headers = auth_headers(client)
    day = _latest_login_day(headers)

    resp = client.get("/monitor/logininfor/list", headers=headers, params=_range_params(day, day, userName="admin"))
    assert resp.json()["total"] > 0


def test_log_previous_day_range_excludes_today():
    """范围只到前一天时，不能把今天的记录也查出来（防止修复时把范围放得过宽）"""
    headers = auth_headers(client)
    day = _latest_login_day(headers)
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()

    rows = client.get(
        "/monitor/logininfor/list",
        headers=headers,
        params=_range_params(f"{prev} 00:00:00", f"{prev} 23:59:59", userName="admin", pageSize=100),
    ).json()["rows"]
    assert all(not r["loginTime"].startswith(day) for r in rows)
