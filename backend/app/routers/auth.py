"""
登录认证路由，对应若依的 CaptchaController + SysLoginController。
路由不加前缀（前端 /dev-api 代理会把前缀去掉）。
"""
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.constants import TOKEN_PREFIX
from app.core.database import get_db
from app.core.deps import get_login_user
from app.core.request_utils import get_client_ip, get_login_location, parse_user_agent
from app.core.response import ajax_success
from app.core.security import decode_jwt_token
from app.core.token_service import create_token, delete_login_user, get_login_user as get_cached_login_user
from app.schemas.login import LoginBody
from app.services import captcha_service, login_service, logininfor_service, menu_service

router = APIRouter()


@router.get("/captchaImage")
def captcha_image(db: Session = Depends(get_db)):
    result = captcha_service.generate_captcha(db)
    if not result["captcha_enabled"]:
        return ajax_success(captchaEnabled=False)
    return ajax_success(captchaEnabled=True, uuid=result["uuid"], img=result["img"])


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    browser, os_name = parse_user_agent(request)

    login_service.validate_captcha(db, body.username, body.code, body.uuid, ip, browser, os_name)
    login_service.login_pre_check(db, body.username, body.password, ip, browser, os_name)
    user = login_service.authenticate(db, body.username, body.password, ip, browser, os_name)
    login_service.record_login_success(db, user, ip, browser, os_name)

    login_user = login_service.build_login_user(db, user)
    token = create_token(login_user)
    return ajax_success(token=token)


@router.get("/getInfo")
def get_info(db: Session = Depends(get_db), login_user: dict = Depends(get_login_user)):
    return login_service.get_user_info_response(db, login_user)


@router.get("/getRouters")
def get_routers(db: Session = Depends(get_db), login_user: dict = Depends(get_login_user)):
    menus = menu_service.select_menu_tree_by_user_id(db, login_user["user_id"], login_user["is_admin"])
    return ajax_success(data=menu_service.build_menus(menus))


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith(TOKEN_PREFIX):
        token = authorization[len(TOKEN_PREFIX):]
        try:
            payload = decode_jwt_token(token)
            token_uuid = payload.get("login_user_key")
            cached_user = get_cached_login_user(token_uuid) if token_uuid else None
            if cached_user:
                ip = get_client_ip(request)
                browser, os_name = parse_user_agent(request)
                logininfor_service.record_login_info(
                    db, cached_user["user_name"], "0", "退出成功", ip, get_login_location(ip), browser, os_name
                )
            if token_uuid:
                delete_login_user(token_uuid)
        except Exception:
            pass  # token 本身已经失效，忽略即可，退出接口永远返回成功
    return ajax_success(msg="退出成功")
