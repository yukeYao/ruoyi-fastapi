"""
从 Request 中提取 IP、浏览器/操作系统信息，用于登录日志、操作日志。

若依 Java 版用 ip2region/第三方接口解析真实地理位置（AddressUtils.getRealAddressByIP），
这里做了简化：只区分"内网IP"和"未知地点"，不接入外部地理位置服务（见 README 的简化说明）。
"""
from fastapi import Request
from user_agents import parse

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


def get_date_range_params(request: Request) -> tuple[str | None, str | None]:
    """
    读取形如 `params[beginTime]=xxx&params[endTime]=xxx` 的时间范围查询参数。
    前端用这种"方括号"写法传参，FastAPI 的 Query() 无法直接声明，只能从 query_params 里手动取。
    """
    begin = request.query_params.get("params[beginTime]")
    end = request.query_params.get("params[endTime]")
    return begin, end


def normalize_datetime_range(begin: str | None, end: str | None) -> tuple[str | None, str | None]:
    """
    把日期范围补全成可以直接和 datetime 列比较的完整时间。

    操作日志、登录日志页面传的是 `yyyy-MM-dd HH:mm:ss`（结束时间是 23:59:59），若依 Java 版直接拿它和列比较。
    如果只传了 `yyyy-MM-dd`，MySQL 会把它当成当天 00:00:00，结束日期当天的记录就全被漏掉，
    所以只有日期时：开始补 00:00:00，结束补 23:59:59。
    不在列上套 DATE() 来比较，是为了让 oper_time / login_time 上的索引继续生效。
    """
    if begin and len(begin) == 10:
        begin = f"{begin} 00:00:00"
    if end and len(end) == 10:
        end = f"{end} 23:59:59"
    return begin, end


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
