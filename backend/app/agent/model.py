"""
模型工厂 + 可脚本化的 Fake 模型。

- create_chat_model：按 AGENT_MODEL / AGENT_API_KEY / AGENT_BASE_URL 创建 OpenAI 兼容协议的模型。
  未配置时直接报错，不猜默认模型名，也不在代码里写任何密钥。
- ScriptedChatModel：自动化测试和 CI 用，不需要 API Key。官方的 GenericFakeChatModel 没有实现
  bind_tools（阶段 0 实测会抛 NotImplementedError），无法验证"模型看到了哪些工具"，所以自己实现。
"""
import json
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from app.config import settings
from app.core.constants import AGENT_MODEL_TIMEOUT_SECONDS
from app.core.exceptions import ServiceException

# 脚本项：固定的 AIMessage，或根据"模型这一步看到的消息和工具名"动态生成回复的函数
ScriptItem = AIMessage | Callable[[list[BaseMessage], list[str]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """
    按顺序返回预先写好的回复，可以是包含 tool_calls 的 AIMessage，用来模拟模型"硬调用"
    未绑定或无权限的工具、发出非法参数、一次发出多个调用等情况。

    bind_tools 返回自身（而不是 RunnableBinding），并把工具名记在 bound_tool_names，
    测试据此断言"无权限的工具模型根本看不到"；calls 记录每一步模型收到的消息。
    """

    script: list[Any] = Field(default_factory=list)
    bound_tool_names: list[str] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any) -> "ScriptedChatModel":
        self.bound_tool_names = [convert_to_openai_tool(t)["function"]["name"] for t in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        if self.script:
            item = self.script.pop(0)
            return item(messages, list(self.bound_tool_names)) if callable(item) else item
        return AIMessage(content="（脚本已结束）")

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """
        流式输出（SSE 接口用）：文本按两半拆成两个片段，工具调用作为 tool_call_chunks 一次给出，
        和真实 OpenAI 流式返回的形态一致，便于测试 token 事件和流式下的工具调用。
        """
        message = self._next(messages)
        text = message.content if isinstance(message.content, str) else ""
        half = len(text) // 2
        pieces = [p for p in (text[:half], text[half:]) if p] or [""]
        for i, piece in enumerate(pieces):
            last = i == len(pieces) - 1
            tool_chunks = []
            if last:
                tool_chunks = [{"name": c["name"], "args": json.dumps(c["args"], ensure_ascii=False), "id": c["id"], "index": n}
                               for n, c in enumerate(message.tool_calls)]
                tool_chunks += [{"name": c.get("name"), "args": c.get("args"), "id": c.get("id"), "index": len(tool_chunks) + n}
                                for n, c in enumerate(message.invalid_tool_calls)]
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece, tool_call_chunks=tool_chunks))
            if run_manager and piece:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk


def is_model_configured() -> bool:
    return bool(settings.agent_api_key and settings.agent_model)


def create_chat_model() -> BaseChatModel:
    if not is_model_configured():
        raise ServiceException("智能助手未配置模型，请在 .env 中设置 AGENT_MODEL 和 AGENT_API_KEY")
    # 延迟导入：没配置模型时（包括测试）不需要加载 openai 客户端
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.agent_api_key,
        base_url=settings.agent_base_url or None,
        timeout=AGENT_MODEL_TIMEOUT_SECONDS,
        # 不自动重试：否则一次失败的调用会再叠加若干个超时，运行软时限（graph.agent_node）更难估计；
        # 注意 timeout 是单个网络操作的超时，不是整次调用的总时长。失败交给用户手动重发
        max_retries=0,
        temperature=0,
    )


# 路由通过这个可替换的工厂取模型，测试用 monkeypatch 换成 ScriptedChatModel
def get_chat_model() -> BaseChatModel:
    return create_chat_model()
