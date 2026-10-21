"""登录相关的入参模型，对应若依的 LoginBody"""
from pydantic import BaseModel


class LoginBody(BaseModel):
    username: str
    password: str
    code: str | None = None
    uuid: str | None = None
