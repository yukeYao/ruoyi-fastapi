"""
统一返回结构，对应若依 Java 版的 AjaxResult / TableDataInfo。

约定（见 TASK.md 第5节）：
- HTTP 状态码一律 200，业务状态放在 JSON 的 code 字段里；
- 普通返回 {"code":200,"msg":"操作成功","data":...}，
  部分接口（登录、获取用户信息、验证码等）把字段平铺在顶层，用 extra 传入；
- 分页返回 {"code":200,"msg":"查询成功","total":N,"rows":[...]}；
- 未登录 401，无权限 403，业务错误 500，警告 601。
"""
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.inspection import inspect as sa_inspect

from app.core.constants import DATE_FORMAT, DATETIME_FORMAT

_CAMEL_RE = re.compile(r"_([a-z0-9])")


def to_camel(snake: str) -> str:
    """下划线转小驼峰：user_name -> userName"""
    return _CAMEL_RE.sub(lambda m: m.group(1).upper(), snake)


def _format_value(value: Any) -> Any:
    """datetime/date 统一格式化为若依前端约定的字符串格式"""
    if isinstance(value, datetime):
        return value.strftime(DATETIME_FORMAT)
    if isinstance(value, date):
        return value.strftime(DATE_FORMAT)
    return value


def model_to_camel_dict(obj: Any, exclude: set[str] | None = None) -> dict:
    """
    把一个 SQLAlchemy ORM 对象转换成 camelCase 的字典，只转换表字段（column），
    不包含关联对象（relationship），关联数据由各 service 自行拼装。
    """
    exclude = exclude or set()
    mapper = sa_inspect(obj).mapper
    result = {}
    for column in mapper.columns:
        field_name = column.key
        if field_name in exclude:
            continue
        result[to_camel(field_name)] = _format_value(getattr(obj, field_name))
    return result


def ajax_success(data: Any = None, msg: str = "操作成功", **extra) -> dict:
    """成功返回。extra 用于需要把字段平铺在顶层的接口（如 token、user、roles）"""
    body = {"code": 200, "msg": msg}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return body


def ajax_error(msg: str = "操作失败", code: int = 500) -> dict:
    """业务错误返回，默认 500"""
    return {"code": code, "msg": msg}


def ajax_warn(msg: str) -> dict:
    """警告返回，对应若依的 601"""
    return {"code": 601, "msg": msg}


def table_data_info(rows: list, total: int, msg: str = "查询成功") -> dict:
    """分页列表返回"""
    return {"code": 200, "msg": msg, "total": total, "rows": rows}
