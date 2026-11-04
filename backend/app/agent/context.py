"""
运行时上下文：通过 graph.invoke(..., context=AgentContext(...)) 传入，节点用 runtime.context 读取。

为什么不放进 state 或 config["configurable"]：
- state 会写入 checkpoint；
- configurable 里的字符串值会被 LangGraph 合并进 checkpoint 元数据（阶段 0 实测在 SQLite 文件里能搜到）；
- 官方 Runtime context 只在本次运行内存里存在，不落盘。
登录态必须每次请求重新解析：审批恢复执行时用的是"这一次"请求的权限，而不是会话开始时的权限。
"""
import time
from dataclasses import dataclass
from typing import Any

from app.core.constants import AGENT_RUN_DEADLINE_SECONDS

# 登录态里与鉴权凭据相关的字段：工具和模型都用不到，从 login_user 里剔除，缩小泄露面
_CREDENTIAL_KEYS = {"token"}


@dataclass(frozen=True)
class AgentContext:
    login_user: dict
    model: Any  # BaseChatModel；用 Any 避免 dataclass 对 pydantic 模型做类型检查
    # 操作者自己的会话 tokenId：只用于"强制下线"时排除操作者自己的会话，不传给工具的 login_user
    session_token: str | None = None
    client_ip: str = ""
    # time.monotonic() 的软截止时间：每次调用模型前检查，降低运行超过运行锁时长的可能（不是硬上限）
    deadline: float = 0.0
    # 本次运行允许使用的工具名（None 表示不额外限制）。只会在权限过滤的基础上再收窄：
    # 不在其中的工具不绑定，硬调用也按未知工具拒绝。评估用它构造"无知识库""只有知识库"等对照方案
    tool_names: frozenset[str] | None = None


def build_context(login_user: dict, model: Any, client_ip: str = "", tool_names: frozenset[str] | None = None) -> AgentContext:
    safe_login_user = {k: v for k, v in login_user.items() if k not in _CREDENTIAL_KEYS}
    return AgentContext(
        login_user=safe_login_user,
        model=model,
        session_token=login_user.get("token"),
        client_ip=client_ip,
        deadline=time.monotonic() + AGENT_RUN_DEADLINE_SECONDS,
        tool_names=tool_names,
    )
