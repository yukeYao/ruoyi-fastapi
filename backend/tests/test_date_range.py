"""
日期范围参数的统一校验与标准化。

复现的问题：直接调接口传非法日期（如 abc、2026-13-01）时，MySQL 报 1525，
被兜底异常处理接住返回"服务器内部错误"。现在统一在 core/date_range.py 校验，返回明确的业务错误。
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.date_range import normalize_date_range
from app.core.exceptions import ServiceException
from main import app
from tests.conftest import auth_headers

client = TestClient(app)

LIST_URLS = [
    "/system/user/list",
    "/system/role/list",
    "/system/dict/type/list",
    "/monitor/operlog/list",
    "/monitor/logininfor/list",
]

INVALID_VALUES = ["abc", "2026-13-01", "2026-10", "2026/10/30", "2026-10-30 12:00", " 2026-10-30"]


# ---------- normalize_date_range 纯函数 ----------

def test_normalize_keeps_full_datetime():
    begin, end = normalize_date_range("2026-10-30 08:00:00", "2026-10-30 18:30:00", by_day=False)
    assert begin == datetime(2026, 10, 30, 8, 0, 0)
    assert end == datetime(2026, 10, 30, 18, 30, 0)


def test_normalize_completes_date_only():
    begin, end = normalize_date_range("2026-10-30", "2026-10-30", by_day=False)
    assert begin == datetime(2026, 10, 30, 0, 0, 0)
    assert end == datetime(2026, 10, 30, 23, 59, 59)


def test_normalize_by_day_drops_time():
    """按天比较的列表（对应 Java 的 date_format(...,'%Y%m%d')）忽略时间部分"""
    begin, end = normalize_date_range("2026-10-30 08:00:00", "2026-10-30 18:30:00", by_day=True)
    assert begin == datetime(2026, 10, 30, 0, 0, 0)
    assert end == datetime(2026, 10, 30, 23, 59, 59)


@pytest.mark.parametrize("value", [None, ""])
def test_normalize_empty_is_ignored(value):
    assert normalize_date_range(value, value, by_day=False) == (None, None)


def test_normalize_one_side_only():
    assert normalize_date_range("2026-10-30", None, by_day=False) == (datetime(2026, 10, 30, 0, 0, 0), None)
    assert normalize_date_range(None, "2026-10-30", by_day=False) == (None, datetime(2026, 10, 30, 23, 59, 59))


@pytest.mark.parametrize("value", INVALID_VALUES)
@pytest.mark.parametrize("by_day", [True, False])
def test_normalize_invalid_raises(value, by_day):
    with pytest.raises(ServiceException, match="日期格式不正确"):
        normalize_date_range(value, None, by_day=by_day)
    with pytest.raises(ServiceException, match="日期格式不正确"):
        normalize_date_range(None, value, by_day=by_day)


# ---------- 五个列表接口 ----------

@pytest.mark.parametrize("url", LIST_URLS)
@pytest.mark.parametrize("key", ["params[beginTime]", "params[endTime]"])
def test_list_invalid_date_returns_business_error(url, key):
    headers = auth_headers(client)
    body = client.get(url, headers=headers, params={key: "abc"}).json()
    assert body["code"] == 500
    assert body["msg"] == "日期格式不正确"
