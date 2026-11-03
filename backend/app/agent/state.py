"""
图状态：会写入 SQLite checkpoint，所以只放可序列化的会话信息。
login_user、db Session、Redis 客户端、模型对象一律不进 state（见 context.py）。
"""
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # add_messages：节点返回的消息追加到历史里，而不是覆盖
    messages: Annotated[list[AnyMessage], add_messages]
    conversation_id: str
    # 本轮用户消息以来模型被调用的次数；每次新消息输入时重置为 0（普通字段，输入直接覆盖）
    step_count: int
