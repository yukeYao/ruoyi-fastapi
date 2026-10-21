"""
算术验证码，对应若依的 CaptchaController（这里只实现 math 类型，不做字符验证码）。
"""
import base64
import io
import random
import uuid

from PIL import Image, ImageDraw
from sqlalchemy.orm import Session

from app.core.constants import CAPTCHA_CODE_KEY_PREFIX, CAPTCHA_EXPIRE_MINUTES
from app.core.redis_client import redis_client
from app.services.config_service import get_config_value

_WIDTH, _HEIGHT = 120, 40


def is_captcha_enabled(db: Session) -> bool:
    return get_config_value(db, "sys.account.captchaEnabled", "true").lower() == "true"


def _draw_captcha_image(text: str) -> bytes:
    image = Image.new("RGB", (_WIDTH, _HEIGHT), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    # 干扰线，提高识别难度（纯装饰，不影响答案校验）
    for _ in range(6):
        start = (random.randint(0, _WIDTH), random.randint(0, _HEIGHT))
        end = (random.randint(0, _WIDTH), random.randint(0, _HEIGHT))
        draw.line([start, end], fill=tuple(random.randint(120, 200) for _ in range(3)))
    draw.text((12, 10), text, fill=(0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="GIF")
    return buffer.getvalue()


def generate_captcha(db: Session) -> dict:
    """
    返回字典包含 captcha_enabled，以及（开启时）uuid / img。
    答案存 Redis，有效期2分钟，校验一次后立即删除（一次性）。
    """
    if not is_captcha_enabled(db):
        return {"captcha_enabled": False}

    num1, num2 = random.randint(1, 9), random.randint(1, 9)
    answer = num1 + num2
    display_text = f"{num1}+{num2}=?"

    captcha_uuid = uuid.uuid4().hex
    redis_client.set(CAPTCHA_CODE_KEY_PREFIX + captcha_uuid, str(answer), ex=CAPTCHA_EXPIRE_MINUTES * 60)

    image_bytes = _draw_captcha_image(display_text)
    return {
        "captcha_enabled": True,
        "uuid": captcha_uuid,
        "img": base64.b64encode(image_bytes).decode("ascii"),
    }
