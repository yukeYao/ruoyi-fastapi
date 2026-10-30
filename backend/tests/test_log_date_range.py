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

LOGININFOR_URL = "/monitor/logininfor/list"


def _range_params(begin: str | None = None, end: str | None = None, **extra) -> dict:
    # 前端 addDateRange 的传参格式：params[beginTime] / params[endTime]，没传的一边不带
    params = dict(extra)
    if begin is not None:
        params["params[beginTime]"] = begin
    if end is not None:
        params["params[endTime]"] = end
    return params


def _admin_logins(headers, **params) -> dict:
    return client.get(LOGININFOR_URL, headers=headers, params={"userName": "admin", "pageSize": 100, **params}).json()


def _latest_login_day(headers) -> str:
    """取最新一条登录日志的日期，用库里的时间而不是测试进程的 today()，避免两边时钟/时区不一致"""
    return _admin_logins(headers, pageSize=1)["rows"][0]["loginTime"][:10]


def _prev_day(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def _has_day(rows: list[dict], day: str) -> bool:
    return any(r["loginTime"].startswith(day) for r in rows)


def _wait_for_oper_log(headers, title: str, timeout: float = 3.0) -> dict:
    """@log 是异步写库，轮询等它落库"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get("/monitor/operlog/list", headers=headers, params={"title": title, "pageSize": 1}).json()
        if body["total"] > 0:
            return body["rows"][0]
        time.sleep(0.1)
    raise AssertionError(f"等待操作日志 '{title}' 落库超时")


# ---------- 复现原问题：结束日期当天要整天算进去 ----------

def test_logininfor_end_date_includes_whole_day():
    headers = auth_headers(client)
    day = _latest_login_day(headers)

    body = _admin_logins(headers, **_range_params(f"{day} 00:00:00", f"{day} 23:59:59"))
    assert _has_day(body["rows"], day)


def test_operlog_end_date_includes_whole_day():
    headers = auth_headers(client)
    resp = client.post(
        "/system/post",
        headers=headers,
        json={"postCode": "date_range_post", "postName": "日期筛选测试岗位", "postSort": 1, "status": "0"},
    )
    # 先断言新增成功：如果这里失败，就不用再查日志，也不会在清理时出现额外的报错掩盖真实原因
    assert resp.json()["code"] == 200

    day = _wait_for_oper_log(headers, "岗位管理")["operTime"][:10]
    resp = client.get(
        "/monitor/operlog/list",
        headers=headers,
        params=_range_params(f"{day} 00:00:00", f"{day} 23:59:59", title="岗位管理"),
    )
    found = resp.json()["total"]

    # 清理放在断言之前做，并且只在确实查到岗位时才删，清理本身不会抛出与本用例无关的异常
    rows = client.get("/system/post/list", headers=headers, params={"postCode": "date_range_post"}).json()["rows"]
    if rows:
        client.delete(f"/system/post/{rows[0]['postId']}", headers=headers)

    assert found > 0


def test_log_end_time_only_date():
    """只传结束时间、且只有日期（直接调接口时可能这么传），结束日期当天也要整天算进去"""
    headers = auth_headers(client)
    day = _latest_login_day(headers)

    assert _has_day(_admin_logins(headers, **_range_params(end=day))["rows"], day)


def test_log_both_date_only():
    """开始、结束都只传日期时，按整天补全"""
    headers = auth_headers(client)
    day = _latest_login_day(headers)

    assert _has_day(_admin_logins(headers, **_range_params(day, day))["rows"], day)


# ---------- 边界：只传一边、空字符串、范围不能放宽 ----------

def test_log_begin_time_only():
    headers = auth_headers(client)
    day = _latest_login_day(headers)

    assert _has_day(_admin_logins(headers, **_range_params(begin=day))["rows"], day)


def test_log_end_time_only_previous_day_excludes_today():
    headers = auth_headers(client)
    day = _latest_login_day(headers)

    assert not _has_day(_admin_logins(headers, **_range_params(end=_prev_day(day)))["rows"], day)


def test_log_previous_day_range_excludes_today():
    """
    保护性用例：防止修复时把范围放得过宽。
    先确认把今天包含进来时确实能查到今天的记录，再断言只查前一天时查不到，避免结果为空时断言空跑。
    """
    headers = auth_headers(client)
    day = _latest_login_day(headers)
    prev = _prev_day(day)

    assert _has_day(_admin_logins(headers, **_range_params(f"{prev} 00:00:00", f"{day} 23:59:59"))["rows"], day)
    assert not _has_day(_admin_logins(headers, **_range_params(f"{prev} 00:00:00", f"{prev} 23:59:59"))["rows"], day)


def test_log_empty_strings_mean_no_filter():
    """前端清空日期时会传空字符串，应等同于不筛选（对应 Java mapper 的 != '' 判断）"""
    headers = auth_headers(client)

    without = _admin_logins(headers)["total"]
    with_empty = _admin_logins(headers, **_range_params("", ""))["total"]
    assert with_empty == without > 0
