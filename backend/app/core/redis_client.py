"""
Redis 客户端。

注意：本机 Redis 是 5.0.14（较旧版本），不支持 RESP3（HELLO 命令），
而新版 redis-py 默认会尝试用 RESP3 握手，导致连接报错
`unknown command 'HELLO'`。这里显式指定 protocol=2 强制使用 RESP2 协议。
"""
from redis import Redis

from app.config import settings

redis_client = Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    password=settings.redis_password or None,
    db=settings.redis_db,
    decode_responses=True,
    protocol=2,
)
