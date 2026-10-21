"""
`@log` 装饰器，对应若依的 `@Log` 注解 + `LogAspect`（AOP 操作日志）。

设计要点（见 TASK.md 阶段4）：
- 用 functools.wraps 包装路由函数，让 FastAPI 仍能通过 __wrapped__ 正确解析原函数的
  参数签名（inspect.signature 默认 follow_wrapped=True），所以装饰器必须加在
  @router.xxx(...) 之下、离原函数更近的位置。
- 记录模块标题、业务类型、请求方法、URL、操作人、部门、IP、请求参数（排除 db/login_user/
  request 这些依赖对象，脱敏密码字段）、返回结果、状态、错误信息、耗时。
- 用线程池异步写库，不阻塞接口响应；写日志用独立的 Session（不能用请求里那个会被关闭的）。
"""
import functools
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.constants import OPER_STATUS_FAIL, OPER_STATUS_SUCCESS
from app.core.database import SessionLocal
from app.core.request_context import current_request
from app.core.request_utils import get_client_ip, get_login_location
from app.models.sys_oper_log import SysOperLog

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="oper-log")

# 排除敏感属性字段，对应 LogAspect.EXCLUDE_PROPERTIES
_SENSITIVE_KEYS = {"password", "oldPassword", "newPassword", "confirmPassword"}
# 这些参数名是依赖注入进来的对象（数据库会话、登录态、原始请求），不是业务参数，不记录
_SKIP_PARAM_NAMES = {"db", "login_user", "request"}

_MAX_PARAM_LENGTH = 2000


def _redact(value: Any) -> Any:
    if isinstance(value, BaseModel):
        data = value.model_dump(by_alias=True)
        return {k: ("******" if k in _SENSITIVE_KEYS else v) for k, v in data.items()}
    if isinstance(value, dict):
        return {k: ("******" if k in _SENSITIVE_KEYS else v) for k, v in value.items()}
    return value


def _safe_json(value: Any, max_length: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return text[:max_length]


def _write_log(
    title: str,
    business_type: int,
    method: str,
    url: str,
    oper_name: str,
    dept_name: str,
    ip: str,
    oper_param: str,
    json_result: str,
    status: int,
    error_msg: str,
    cost_time: int,
) -> None:
    db = SessionLocal()
    try:
        record = SysOperLog(
            title=title,
            business_type=business_type,
            method=method,
            request_method=method,
            oper_name=oper_name,
            dept_name=dept_name,
            oper_url=url[:255],
            oper_ip=ip,
            oper_location=get_login_location(ip),
            oper_param=oper_param,
            json_result=json_result,
            status=status,
            error_msg=error_msg[:2000],
            oper_time=datetime.now(),
            cost_time=cost_time,
        )
        db.add(record)
        db.commit()
    finally:
        db.close()


def log(title: str, business_type: int):
    """用法：`@log(title="用户管理", business_type=BIZ_INSERT)`，加在 @router.xxx 下面"""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            request = current_request.get()
            login_user = kwargs.get("login_user")

            status = OPER_STATUS_SUCCESS
            error_msg = ""
            result = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as exc:
                status = OPER_STATUS_FAIL
                error_msg = str(exc)
                raise
            finally:
                cost_time = int((time.time() - start) * 1000)
                params = {k: _redact(v) for k, v in kwargs.items() if k not in _SKIP_PARAM_NAMES}
                oper_param = _safe_json(params, _MAX_PARAM_LENGTH)
                json_result = _safe_json(result, _MAX_PARAM_LENGTH) if result is not None else ""
                ip = get_client_ip(request) if request else ""
                oper_name = (login_user or {}).get("user_name", "")
                dept_name = (login_user or {}).get("dept_name") or ""
                url = request.url.path if request else ""
                method = request.method if request else ""
                _executor.submit(
                    _write_log, title, business_type, method, url, oper_name, dept_name, ip, oper_param, json_result, status, error_msg, cost_time
                )

        return wrapper

    return decorator
