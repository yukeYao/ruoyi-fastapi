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
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.constants import OPER_STATUS_FAIL, OPER_STATUS_SUCCESS
from app.core.database import SessionLocal
from app.core.request_context import current_request
from app.core.request_utils import get_client_ip, get_login_location
from app.models.sys_oper_log import SysOperLog

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="oper-log")
# 尚未写完的异步日志任务：只做登记，写库行为不变；供测试用 flush_oper_log_for_test 确定性地等待落库
_pending: set[Future] = set()
_pending_lock = threading.Lock()


def _submit(*args) -> None:
    future = _executor.submit(_write_log, *args)
    with _pending_lock:
        _pending.add(future)
    future.add_done_callback(_discard)


def _discard(future: Future) -> None:
    with _pending_lock:
        _pending.discard(future)


def flush_oper_log_for_test(timeout: float = 10.0) -> None:
    """
    只供测试使用：等待当前已提交的异步操作日志全部写完（包括等待期间新提交的）。
    替代测试里的"轮询若干秒等日志出现"：轮询在机器忙时会超时，也挡不住"上一个请求的日志晚到"这类竞态。
    生产代码不调用它，日志仍然异步写、不阻塞接口
    """
    deadline = time.monotonic() + timeout
    while True:
        with _pending_lock:
            futures = list(_pending)
        if not futures:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("等待异步操作日志写入超时")
        wait(futures, timeout=remaining)

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
    request_method: str,
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
            method=method[:200],
            request_method=request_method[:10],
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


def record_oper_log(
    title: str,
    business_type: int,
    login_user: dict | None,
    params: dict,
    result: Any,
    status: int,
    error_msg: str,
    cost_time: int,
    method: str = "",
    request_method: str = "",
    url: str = "",
    ip: str = "",
    background: bool = True,
) -> None:
    """
    写一条操作日志：脱敏参数、序列化结果、截断长度后写库。

    从 @log 里拆出来，是为了让不经过路由的写操作（智能助手审批后执行的工具）也能进入
    现有的 sys_oper_log，而不是另建一套日志。@log 的行为保持不变（background=True 异步写）；
    智能助手传 background=False 同步写，保证 agent_action 和操作日志在同一次请求里落库。
    """
    oper_param = _safe_json({k: _redact(v) for k, v in params.items()}, _MAX_PARAM_LENGTH)
    json_result = _safe_json(result, _MAX_PARAM_LENGTH) if result is not None else ""
    oper_name = (login_user or {}).get("user_name", "")
    dept_name = (login_user or {}).get("dept_name") or ""
    args = (title, business_type, method, request_method, url, oper_name, dept_name, ip, oper_param, json_result,
            status, error_msg, cost_time)
    if background:
        _submit(*args)
    else:
        _write_log(*args)


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
                record_oper_log(
                    title,
                    business_type,
                    login_user,
                    {k: v for k, v in kwargs.items() if k not in _SKIP_PARAM_NAMES},
                    result,
                    status,
                    error_msg,
                    int((time.time() - start) * 1000),
                    method=request.method if request else "",
                    request_method=request.method if request else "",
                    url=request.url.path if request else "",
                    ip=get_client_ip(request) if request else "",
                )

        return wrapper

    return decorator
