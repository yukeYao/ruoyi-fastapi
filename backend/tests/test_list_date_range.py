"""
角色列表、字典类型列表按创建时间筛选。

复现的问题：这两个接口没有读取 params[beginTime] / params[endTime]，日期筛选被直接忽略，
选了任何日期范围都会返回全部数据。若依 Java 版（SysRoleMapper / SysDictTypeMapper）按天比较。
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.conftest import auth_headers

client = TestClient(app)

# (列表接口, 详情接口, 只命中初始化数据的筛选条件)
# 初始化 SQL 用 MySQL 的 sysdate() 写 create_time；其他测试新建的数据用 Python 的 datetime.now()，
# 两边时区不一致时可能落在不同日期，所以只看初始化数据，不受其他测试新建数据的影响
CASES = [
    ("/system/role/list", "/system/role/1", {"roleKey": "admin"}),
    ("/system/dict/type/list", "/system/dict/type/1", {"dictType": "sys_user_sex"}),
]


def _range_params(begin: str | None = None, end: str | None = None) -> dict:
    # 前端 addDateRange 的传参格式，这两个页面的日期选择器只传 yyyy-MM-dd；没传的一边不带
    params = {}
    if begin is not None:
        params["params[beginTime]"] = begin
    if end is not None:
        params["params[endTime]"] = end
    return params


def _seed_day(headers, detail_url: str) -> date:
    """用库里的创建时间而不是测试进程的 today()，避免跨零点或时区不一致"""
    return date.fromisoformat(client.get(detail_url, headers=headers).json()["data"]["createTime"][:10])


def _total(headers, list_url: str, seed_filter: dict, **range_params) -> int:
    return client.get(list_url, headers=headers, params={**seed_filter, **range_params}).json()["total"]


@pytest.mark.parametrize("list_url, detail_url, seed_filter", CASES)
def test_same_day_range_includes_seed_data(list_url, detail_url, seed_filter):
    headers = auth_headers(client)
    day = _seed_day(headers, detail_url).isoformat()

    assert _total(headers, list_url, seed_filter, **_range_params(day, day)) > 0


@pytest.mark.parametrize("list_url, detail_url, seed_filter", CASES)
def test_previous_day_range_excludes_seed_data(list_url, detail_url, seed_filter):
    """复现用例：筛选被忽略时会返回全部数据。先确认包含当天时能查到，避免断言空跑"""
    headers = auth_headers(client)
    day = _seed_day(headers, detail_url)
    prev = (day - timedelta(days=1)).isoformat()

    assert _total(headers, list_url, seed_filter, **_range_params(prev, day.isoformat())) > 0
    assert _total(headers, list_url, seed_filter, **_range_params(prev, prev)) == 0


@pytest.mark.parametrize("list_url, detail_url, seed_filter", CASES)
def test_begin_time_only(list_url, detail_url, seed_filter):
    headers = auth_headers(client)
    day = _seed_day(headers, detail_url)

    assert _total(headers, list_url, seed_filter, **_range_params(begin=day.isoformat())) > 0
    assert _total(headers, list_url, seed_filter, **_range_params(begin=(day + timedelta(days=1)).isoformat())) == 0


@pytest.mark.parametrize("list_url, detail_url, seed_filter", CASES)
def test_end_time_only(list_url, detail_url, seed_filter):
    headers = auth_headers(client)
    day = _seed_day(headers, detail_url)

    assert _total(headers, list_url, seed_filter, **_range_params(end=day.isoformat())) > 0
    assert _total(headers, list_url, seed_filter, **_range_params(end=(day - timedelta(days=1)).isoformat())) == 0


@pytest.mark.parametrize("list_url, detail_url, seed_filter", CASES)
def test_empty_strings_mean_no_filter(list_url, detail_url, seed_filter):
    """前端清空日期时会传空字符串，应等同于不筛选"""
    headers = auth_headers(client)

    without = _total(headers, list_url, seed_filter)
    assert _total(headers, list_url, seed_filter, **_range_params("", "")) == without > 0
