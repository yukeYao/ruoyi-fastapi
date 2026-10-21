"""
审计字段填充，对应若依 BaseEntity 的 createBy/createTime/updateBy/updateTime。

若依里 createBy/updateBy 由 Controller 用 getUsername() 设置，createTime/updateTime 由 Mapper XML 里的
sysdate() 写入；这里统一在 service 里调用下面两个方法，新增填 create_*，修改填 update_*。
"""
from datetime import datetime
from typing import Any


def fill_create_fields(entity: Any, operator_name: str) -> None:
    """新增：填写创建者和创建时间"""
    entity.create_by = operator_name
    entity.create_time = datetime.now()


def fill_update_fields(entity: Any, operator_name: str) -> None:
    """修改：填写更新者和更新时间"""
    entity.update_by = operator_name
    entity.update_time = datetime.now()
