"""
ServiceException + 全局异常处理器。

前端 request.js 的响应拦截器只看 HTTP 200 响应里的 body.code，
非 200 的 HTTP 状态会被 axios 当成网络错误处理（走 catch 分支，提示"系统接口xxx异常"），
所以这里所有异常处理器都必须返回 status_code=200，把错误信息放进 body 的 code/msg 里。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.response import ajax_error

logger = logging.getLogger("ruoyi")


class ServiceException(Exception):
    """业务异常：service 层主动抛出（对应若依的 ServiceException），默认按 500 处理"""

    def __init__(self, message: str, code: int = 500):
        self.message = message
        self.code = code
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceException)
    async def handle_service_exception(request: Request, exc: ServiceException):
        return JSONResponse(status_code=200, content=ajax_error(exc.message, exc.code))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(request: Request, exc: RequestValidationError):
        logger.warning("参数校验失败：%s %s %s", request.method, request.url.path, exc.errors())
        return JSONResponse(status_code=200, content=ajax_error("请求参数不正确"))

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception):
        logger.exception("未捕获的异常：%s %s", request.method, request.url.path)
        return JSONResponse(status_code=200, content=ajax_error("服务器内部错误"))
