"""角色-用户授权相关的小入参模型"""
from app.schemas.base import CamelModel


class AuthUserCancelBody(CamelModel):
    user_id: int
    role_id: int
