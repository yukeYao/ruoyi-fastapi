"""用户相关入参模型，对应若依 SysUser 在各接口里收到的 JSON"""
from app.schemas.base import CamelModel


class UserCreate(CamelModel):
    dept_id: int | None = None
    user_name: str
    nick_name: str
    password: str
    email: str | None = ""
    phonenumber: str | None = ""
    sex: str | None = "0"
    status: str | None = "0"
    remark: str | None = None
    post_ids: list[int] = []
    role_ids: list[int] = []


class UserUpdate(CamelModel):
    user_id: int
    dept_id: int | None = None
    nick_name: str
    email: str | None = ""
    phonenumber: str | None = ""
    sex: str | None = "0"
    status: str | None = "0"
    remark: str | None = None
    post_ids: list[int] = []
    role_ids: list[int] = []


class UserResetPwd(CamelModel):
    user_id: int
    password: str


class UserChangeStatus(CamelModel):
    user_id: int
    status: str


class UserProfileUpdate(CamelModel):
    nick_name: str
    email: str | None = ""
    phonenumber: str | None = ""
    sex: str | None = "0"


class UserPwdUpdate(CamelModel):
    old_password: str
    new_password: str
