"""
列表查询的日期范围参数（params[beginTime] / params[endTime]）统一在这里校验和标准化。

若依 Java 版把原始字符串直接交给 MySQL：用户/角色/字典类型用 date_format 按天比较，非法值静默查不到数据；
操作日志/登录日志直接比较，非法值会报 SQL 错误。这里统一改为先在 Python 里严格解析：
- 合法值的查询结果与 Java 一致；
- 非法值返回明确的业务错误"日期格式不正确"，而不是静默返回空列表（让人误以为没数据）或"服务器内部错误"。
解析成 datetime 后，service 直接用"列 >= / <= 参数"比较，列上不套函数，日志表的时间索引可以继续使用。
"""
from datetime import datetime

from app.core.constants import DATE_FORMAT, DATETIME_FORMAT, DAY_BEGIN_TIME, DAY_END_TIME
from app.core.exceptions import ServiceException

_DATE_LENGTH = len("2026-01-01")


def _parse(value: str, fill_time: str, by_day: bool) -> datetime:
    # 只接受 yyyy-MM-dd 和 yyyy-MM-dd HH:mm:ss，strptime 不允许多余字符，所以带空格、斜杠、缺秒的都会被拒绝
    date_only = len(value) == _DATE_LENGTH
    try:
        parsed = datetime.strptime(value, DATE_FORMAT if date_only else DATETIME_FORMAT)
    except ValueError:
        raise ServiceException("日期格式不正确")
    if by_day or date_only:
        # 只保留日期，开始补当天最早、结束补当天最晚，结束日期当天的记录才能整天查到
        parsed = datetime.strptime(f"{parsed.strftime(DATE_FORMAT)} {fill_time}", DATETIME_FORMAT)
    return parsed


def normalize_date_range(
    begin: str | None, end: str | None, by_day: bool
) -> tuple[datetime | None, datetime | None]:
    """
    by_day=True：用户、角色、字典类型列表，对应 Java 的 date_format(create_time,'%Y%m%d') 按天比较，传了时间也忽略；
    by_day=False：操作日志、登录日志，前端传完整时间，按原值比较，只有日期时才补全时分秒。
    空值或空字符串表示不按这一边筛选，与 Java mapper 的 `!= ''` 判断一致。
    """
    begin_dt = _parse(begin, DAY_BEGIN_TIME, by_day) if begin else None
    end_dt = _parse(end, DAY_END_TIME, by_day) if end else None
    return begin_dt, end_dt
