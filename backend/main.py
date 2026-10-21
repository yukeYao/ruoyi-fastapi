"""
启动入口：uvicorn，监听 8080（与前端 vite.config.js 代理目标一致，路由不加前缀）。
"""
import uvicorn
from fastapi import FastAPI

from app.config import settings
from app.core.exceptions import register_exception_handlers

app = FastAPI(title="RuoYi FastAPI 后端", description="用 FastAPI 复现若依后端（精简学习版）")

register_exception_handlers(app)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.app_port, reload=True)
