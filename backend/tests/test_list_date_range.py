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

# (列表接口, 用来取初始化数据创建日期的详情接口)：初始化 SQL 用 sysdate() 写入 create_time，就是建测试库的那天
CASES = [
    ("/system/role/list", "/system/role/1"),
    ("/system/dict/type/list", "/system/dict/type/1"),
]


def _range_params(begin: str, end: str) -> dict:
    # 前端 addDateRange 的传参格式，这两个页面的日期选择器只传 yyyy-MM-dd
    return {"params[beginTime]": begin, "params[endTime]": end}


def _seed_day(headers, detail_url: str) -> date:
    """用库里的创建时间而不是测试进程的 today()，避免跨零点或时区不一致"""
    return date.fromisoformat(client.get(detail_url, headers=headers).json()["data"]["createTime"][:10])


@pytest.mark.parametrize("list_url, detail_url", CASES)
def test_same_day_range_includes_seed_data(list_url, detail_url):
    headers = auth_headers(client)
    day = _seed_day(headers, detail_url).isoformat()

    resp = client.get(list_url, headers=headers, params=_range_params(day, day))
    assert resp.json()["total"] > 0


@pytest.mark.parametrize("list_url, detail_url", CASES)
def test_previous_day_range_excludes_seed_data(list_url, detail_url):
    headers = auth_headers(client)
    prev = (_seed_day(headers, detail_url) - timedelta(days=1)).isoformat()

    resp = client.get(list_url, headers=headers, params=_range_params(prev, prev))
    assert resp.json()["total"] == 0
