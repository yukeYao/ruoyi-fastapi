"""
用一个 ContextVar 存当前请求的 Request 对象。

`@log` 装饰器包装的是路由函数本身，不是所有被装饰的函数都显式声明了 `request: Request`
参数，所以没法直接从函数参数里拿到 Request —— 用中间件在请求进入时把 Request 存进这个
ContextVar，装饰器需要时直接从这里读，对应若依用 ServletUtils.getRequest() 拿当前请求的做法。
"""
from contextvars import ContextVar

from starlette.requests import Request

current_request: ContextVar[Request | None] = ContextVar("current_request", default=None)
