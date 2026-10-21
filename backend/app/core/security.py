"""
密码哈希校验 + JWT 编解码。

若依 Java 库里密码是 Spring Security BCryptPasswordEncoder 生成的 `$2a$` 格式，
Python 的 bcrypt 库可以直接校验，不需要重新生成哈希。
"""
import bcrypt
import jwt

from app.config import settings


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码与数据库里的 bcrypt 哈希是否匹配"""
    if not hashed_password:
        return False
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def hash_password(plain_password: str) -> str:
    """生成 bcrypt 哈希（重置密码等场景使用）"""
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_jwt_token(token_uuid: str, username: str) -> str:
    """
    JWT 里只放 token 的 uuid（login_user_key）和用户名，不放任何权限/敏感信息，
    完整的登录态数据存在 Redis 里，JWT 只是指向 Redis 的"钥匙"。
    """
    payload = {"login_user_key": token_uuid, "sub": username}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> dict:
    """解析 JWT，签名或格式不对会抛出 jwt.PyJWTError"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
