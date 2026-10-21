"""参数配置路由（只读），对应 SysConfigController.getConfigKey"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_login_user
from app.core.response import ajax_success
from app.services.config_service import get_config_value

router = APIRouter(prefix="/system/config")


@router.get("/configKey/{config_key}")
def get_config_key(config_key: str, db: Session = Depends(get_db), login_user: dict = Depends(get_login_user)):
    return ajax_success(data=get_config_value(db, config_key))
