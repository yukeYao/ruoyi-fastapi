"""
所有入参模型的基类：camelCase 别名，前端传 camelCase，这里用 snake_case 写字段。
"""
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
