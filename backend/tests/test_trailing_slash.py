"""末尾斜杠：路由与前端请求严格一致，不允许 FastAPI 隐式 307 重定向（重定向地址会绕过 vite 代理）"""
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.conftest import auth_headers

client = TestClient(app)

FRONTEND_API_DIR = pathlib.Path(__file__).resolve().parents[2] / "ruoyi-ui" / "src" / "api"


def test_get_user_info_with_trailing_slash_for_add_form():
    """用户管理"新增"请求 GET /system/user/，对应 Java 的 @GetMapping(value = { "/", "/{userId}" })"""
    headers = auth_headers(client, "admin", "admin123")
    resp = client.get("/system/user/", headers=headers, follow_redirects=False)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    # 新增表单不带用户数据，只返回可选角色（排除超级管理员角色）和岗位
    assert "data" not in body and "postIds" not in body and "roleIds" not in body
    assert body["roles"] and all(r["roleId"] != 1 for r in body["roles"])
    assert body["posts"]


def test_redirect_slashes_disabled():
    """路径斜杠不一致时直接 404，而不是 307 重定向"""
    resp = client.get("/system/user/list/", follow_redirects=False)
    assert resp.status_code == 404


def _frontend_requests():
    """解析前端 src/api 下所有 request({...})，得到 (method, path)；拼接的变量用 '1' 代替，parseStrEmpty 额外取空串"""
    calls = []
    for f in sorted(FRONTEND_API_DIR.rglob("*.js")):
        for blk in re.findall(r"request\(\{(.*?)\}\)", f.read_text(encoding="utf-8"), re.S):
            url = re.search(r"url:\s*(.+?),?\s*\n", blk)
            if not url:
                continue
            expr = url.group(1)
            method = re.search(r"method:\s*'(\w+)'", blk)
            method = method.group(1).upper() if method else "GET"
            exprs = [re.sub(r"parseStrEmpty\([^)]*\)", "'1'", expr)]
            if "parseStrEmpty" in expr:
                exprs.append(re.sub(r"parseStrEmpty\([^)]*\)", "''", expr))
            for e in exprs:
                e = re.sub(r"\+\s*[A-Za-z_]\w*", "+ '1'", e)
                calls.append((method, "".join(re.findall(r"'([^']*)'", e))))
    return calls


def _route_exists(method: str, path: str) -> bool:
    # 未登录请求：命中路由会走到鉴权（HTTP 200 + code 401）或参数校验；未命中是 404/405
    status = client.request(method, path, follow_redirects=False).status_code
    assert not 300 <= status < 400, f"{method} {path} 出现重定向"
    return status not in (404, 405)


@pytest.mark.skipif(not FRONTEND_API_DIR.exists(), reason="缺少前端源码 ruoyi-ui/src/api")
def test_frontend_api_paths_have_no_trailing_slash_mismatch():
    """前端请求未命中路由时，如果切换末尾斜杠后能命中，说明是斜杠不一致（未实现的接口不在此列）"""
    calls = _frontend_requests()
    assert ("GET", "/system/user/") in calls
    mismatched = []
    for method, path in calls:
        if _route_exists(method, path):
            continue
        alt = path[:-1] if path.endswith("/") else path + "/"
        if _route_exists(method, alt):
            mismatched.append(f"{method} {path}")
    assert mismatched == []
