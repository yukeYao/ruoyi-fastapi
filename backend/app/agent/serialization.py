"""
工具结果的序列化：字段白名单 -> 脱敏 -> 敏感字段兜底剔除 -> 长度截断。

工具结果会同时进入三个地方：模型上下文、checkpoint、前端。所以这里的原则是"默认不给"：
- 只输出每个工具声明的白名单字段（新增数据库列不会自动泄露给模型）；
- 手机号、邮箱脱敏；
- 再按黑名单递归剔除一遍 password / tokenId 等字段，防止白名单写错；
- 结果统一放在 untrusted_data 里，系统提示词要求模型把它当数据而不是指令
  （登录日志的用户名、操作日志的标题等内容都可能被外部用户控制，是间接提示注入入口）。
"""
import json
from datetime import date, datetime
from typing import Any

from app.core.constants import AGENT_MAX_TOOL_RESULT_CHARS, DATE_FORMAT, DATETIME_FORMAT

# 无论白名单怎么写，这些字段都不能出现在工具结果里（大小写不敏感）
FORBIDDEN_KEYS = {"password", "tokenid", "token", "jwt", "authorization", "operparam", "jsonresult"}

MASK_PHONE = "phone"
MASK_EMAIL = "email"


def mask_phone(value: str | None) -> str:
    """保留前 3 后 4 位，与若依前端脱敏风格一致：138****1234"""
    if not value:
        return ""
    if len(value) < 7:
        return "*" * len(value)
    return value[:3] + "****" + value[-4:]


def mask_email(value: str | None) -> str:
    """只保留用户名首字符和域名：r***@qq.com"""
    if not value:
        return ""
    name, sep, domain = value.partition("@")
    if not sep:
        return "***"
    return (name[:1] or "") + "***@" + domain


_MASKERS = {MASK_PHONE: mask_phone, MASK_EMAIL: mask_email}


def _format(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime(DATETIME_FORMAT)
    if isinstance(value, date):
        return value.strftime(DATE_FORMAT)
    return value


def pick_fields(row: dict, fields: tuple[str, ...], masks: dict[str, str] | None = None) -> dict:
    """按白名单挑字段；支持 "dept.deptName" 这种一层嵌套路径，结果拍平成 deptName"""
    masks = masks or {}
    result = {}
    for field in fields:
        if "." in field:
            parent, child = field.split(".", 1)
            value = (row.get(parent) or {}).get(child)
            key = child
        else:
            value = row.get(field)
            key = field
        if key in masks:
            value = _MASKERS[masks[key]](value)
        result[key] = _format(value)
    return result


def strip_forbidden(value: Any) -> Any:
    """递归剔除敏感字段：白名单之外的第二道防线"""
    if isinstance(value, dict):
        return {k: strip_forbidden(v) for k, v in value.items() if k.lower() not in FORBIDDEN_KEYS}
    if isinstance(value, list):
        return [strip_forbidden(v) for v in value]
    return _format(value)


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def tool_success(data: Any, max_chars: int = AGENT_MAX_TOOL_RESULT_CHARS) -> str:
    """
    成功结果。超长时优先从末尾丢弃 rows 里的记录（保持 JSON 合法、模型仍能读懂），
    并标记 truncated；仍然超长就只给截断后的文本预览。
    """
    data = strip_forbidden(data)
    payload = {"ok": True, "untrusted_data": data}
    text = _dumps(payload)
    if len(text) <= max_chars:
        return text

    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        rows = list(data["rows"])
        while rows:
            rows.pop()
            trimmed = {**data, "rows": rows, "truncated": True}
            text = _dumps({"ok": True, "untrusted_data": trimmed})
            if len(text) <= max_chars:
                return text

    preview = _dumps(data)[: max_chars // 2]
    return _dumps({"ok": True, "truncated": True, "untrusted_preview": preview})


def tool_error(message: str) -> str:
    """失败结果：只返回服务端生成的错误文案，不回显模型传入的参数值"""
    return _dumps({"ok": False, "error": message[:500]})
