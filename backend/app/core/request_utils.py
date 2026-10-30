"""
从 Request 中提取 IP、浏览器/操作系统信息，用于登录日志、操作日志。

若依 Java 版用 ip2region/第三方接口解析真实地理位置（AddressUtils.getRealAddressByIP），
这里做了简化：只区分"内网IP"和"未知地点"，不接入外部地理位置服务（见 README 的简化说明）。
"""
from datetime import datetime

from fastapi import Request
from user_agents import parse

from app.core.date_range import normalize_date_range

_PRIVATE_PREFIXES = ("127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.")


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def get_login_location(ip: str) -> str:
    if not ip or ip in ("localhost",) or ip.startswith(_PRIVATE_PREFIXES):
        return "内网IP"
    return "未知地点"


def get_date_range_params(request: Request, by_day: bool) -> tuple[datetime | None, datetime | None]:
    """
    读取形如 `params[beginTime]=xxx&params[endTime]=xxx` 的时间范围查询参数。
    前端用这种"方括号"写法传参，FastAPI 的 Query() 无法直接声明，只能从 query_params 里手动取。
    取到后立即交给 normalize_date_range 校验，非法值在进入 service 之前就报"日期格式不正确"。
    """
    begin = request.query_params.get("params[beginTime]")
    end = request.query_params.get("params[endTime]")
    return normalize_date_range(begin, end, by_day)


def parse_user_agent(request: Request) -> tuple[str, str]:
    """返回 (浏览器, 操作系统)，解析失败时给出兜底文案"""
    ua = parse(request.headers.get("user-agent", ""))

    browser = ua.browser.family or "未知浏览器"
    if ua.browser.version_string:
        browser += " " + ua.browser.version_string.split(".")[0]

    os_name = ua.os.family or "未知操作系统"
    if ua.os.version_string:
        os_name += " " + ua.os.version_string.split(".")[0]

    return browser, os_name
