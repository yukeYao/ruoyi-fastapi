"""参数配置只读查询，对应若依的 ISysConfigService（本项目不实现参数的增删改）"""
from sqlalchemy.orm import Session

from app.models.sys_config import SysConfig


def get_config_value(db: Session, config_key: str, default: str = "") -> str:
    config = db.query(SysConfig).filter(SysConfig.config_key == config_key).first()
    return config.config_value if config else default
