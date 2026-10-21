"""
启动入口：uvicorn，监听 8080（与前端 vite.config.js 代理目标一致，路由不加前缀）。
"""
import uvicorn
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.request_context import current_request
from app.routers import auth
from app.routers.monitor import logininfor as monitor_logininfor
from app.routers.monitor import online as monitor_online
from app.routers.monitor import operlog as monitor_operlog
from app.routers.system import config as system_config
from app.routers.system import dept as system_dept
from app.routers.system import dict as system_dict
from app.routers.system import menu as system_menu
from app.routers.system import notice as system_notice
from app.routers.system import post as system_post
from app.routers.system import role as system_role
from app.routers.system import user as system_user

app = FastAPI(title="RuoYi FastAPI 后端", description="用 FastAPI 复现若依后端（精简学习版）")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """把当前 Request 存进 ContextVar，供 @log 操作日志装饰器读取（对应 ServletUtils.getRequest()）"""

    async def dispatch(self, request: Request, call_next):
        token = current_request.set(request)
        try:
            return await call_next(request)
        finally:
            current_request.reset(token)


app.add_middleware(RequestContextMiddleware)

register_exception_handlers(app)
app.include_router(auth.router)
app.include_router(system_user.router)
app.include_router(system_role.router)
app.include_router(system_menu.router)
app.include_router(system_dept.router)
app.include_router(system_post.router)
app.include_router(system_dict.router)
app.include_router(system_config.router)
app.include_router(system_notice.router)
app.include_router(monitor_operlog.router)
app.include_router(monitor_logininfor.router)
app.include_router(monitor_online.router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.app_port, reload=True)
