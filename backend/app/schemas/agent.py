"""智能助手入参模型"""
from pydantic import Field

from app.core.constants import AGENT_MAX_INPUT_CHARS
from app.schemas.base import CamelModel


class AgentChatRequest(CamelModel):
    # 不传表示新建会话（ID 由服务端生成）；传了必须是当前用户已有的会话
    conversation_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=AGENT_MAX_INPUT_CHARS)
