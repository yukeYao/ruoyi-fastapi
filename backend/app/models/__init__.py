"""
统一导入所有模型，确保 Base.metadata 能收集到全部表（主要用于测试里建表等场景）。
"""
from app.models.sys_config import SysConfig
from app.models.sys_dept import SysDept
from app.models.sys_dict_data import SysDictData
from app.models.sys_dict_type import SysDictType
from app.models.sys_logininfor import SysLogininfor
from app.models.sys_menu import SysMenu
from app.models.sys_oper_log import SysOperLog
from app.models.sys_post import SysPost
from app.models.sys_role import SysRole
from app.models.sys_role_dept import SysRoleDept
from app.models.sys_role_menu import SysRoleMenu
from app.models.sys_user import SysUser
from app.models.sys_user_post import SysUserPost
from app.models.sys_user_role import SysUserRole

__all__ = [
    "SysConfig",
    "SysDept",
    "SysDictData",
    "SysDictType",
    "SysLogininfor",
    "SysMenu",
    "SysOperLog",
    "SysPost",
    "SysRole",
    "SysRoleDept",
    "SysRoleMenu",
    "SysUser",
    "SysUserPost",
    "SysUserRole",
]
