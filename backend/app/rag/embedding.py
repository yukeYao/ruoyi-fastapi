"""
Embedding 接口。

- OpenAICompatibleEmbedding：按 AGENT_EMBEDDING_MODEL / AGENT_EMBEDDING_API_KEY / AGENT_EMBEDDING_BASE_URL 调用
  OpenAI 兼容的 /embeddings。直接用 openai SDK 而不是 langchain 的 OpenAIEmbeddings：后者默认
  check_embedding_ctx_length=True，会用 tiktoken 按 OpenAI 模型名切分（可能联网下载编码文件，非 OpenAI 模型名会出错），
  而且默认自动重试 2 次。
- FakeEmbeddingModel：pytest / CI 和开发用，不联网、不需要 Key，按字符 n-gram 哈希出确定性的向量，
  字面相近的文本向量也相近，足够测试检索流程。
- 所有向量在写入 faiss 前、查询前都做 L2 归一化：索引用内积（IndexFlatIP），归一化后内积就等于余弦相似度。

用户问题会发送给配置的 Embedding 服务：embed_query 先检查长度，超过 KNOWLEDGE_MAX_QUERY_CHARS 直接拒绝，
不截断后发送；这里不记录问题原文，异常信息也只保留类型和状态码。
"""
import hashlib
from typing import Protocol

import numpy as np

from app.config import settings
from app.core.constants import KNOWLEDGE_EMBED_BATCH_SIZE, KNOWLEDGE_EMBED_TIMEOUT_SECONDS, KNOWLEDGE_MAX_QUERY_CHARS

MSG_QUERY_TOO_LONG = f"问题过长：知识库检索最多 {KNOWLEDGE_MAX_QUERY_CHARS} 个字符，请精简后重试"
MSG_NOT_CONFIGURED = "知识库未配置 Embedding 模型（AGENT_EMBEDDING_MODEL、AGENT_EMBEDDING_API_KEY）"


class EmbeddingError(RuntimeError):
    """
    Embedding 调用失败的脱敏异常：第三方 SDK 的异常信息可能带有部分 Key、请求头或上游原文，
    只保留异常类型和 HTTP 状态码，并用 from None 切断异常链（与 graph.ModelCallError 同样的理由）
    """


class QueryTooLongError(ValueError):
    pass


class EmbeddingModel(Protocol):
    # 模型名写进索引元数据：模型变了，旧向量和新查询不在同一个空间，必须重建
    name: str

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """按行归一化成单位向量（float32、C 连续，faiss 要求）；全零行保持为零，不产生 NaN"""
    vectors = np.ascontiguousarray(np.asarray(vectors, dtype="float32"))
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.ascontiguousarray(vectors / np.where(norms == 0, 1.0, norms), dtype="float32")


def check_query(text: str) -> str:
    text = (text or "").strip()
    if len(text) > KNOWLEDGE_MAX_QUERY_CHARS:
        raise QueryTooLongError(MSG_QUERY_TOO_LONG)
    return text


def _describe_error(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    suffix = f"（HTTP {int(status_code)}）" if isinstance(status_code, int) else ""
    return f"Embedding 调用失败：{type(exc).__name__}{suffix}"


class FakeEmbeddingModel:
    """
    确定性的假 Embedding：字符 1-gram、2-gram 各自哈希到 dim 个桶（带符号），再 L2 归一化。
    calls 记录每次调用送入的文本条数，测试据此断言"内容未变化时没有重新计算 Embedding"。
    """

    def __init__(self, name: str = "fake-ngram-v1", dim: int = 256):
        self.name = name
        self.dim = dim
        self.calls: list[int] = []
        self.query_calls = 0

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype="float32")
        text = text.lower()
        grams = list(text) + [text[i:i + 2] for i in range(len(text) - 1)]
        for gram in grams:
            if gram.isspace():
                continue
            digest = hashlib.md5(gram.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            vec[bucket] += 1.0 if digest[4] & 1 else -1.0
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        self.calls.append(len(texts))
        return l2_normalize(np.stack([self._vector(t) for t in texts])) if texts else np.zeros((0, self.dim), "float32")

    def embed_query(self, text: str) -> np.ndarray:
        self.query_calls += 1
        return l2_normalize(self._vector(check_query(text)))[0]


class OpenAICompatibleEmbedding:
    def __init__(self, model: str, api_key: str, base_url: str = ""):
        # 延迟导入：只用 Fake 时（包括测试）不加载 openai 客户端
        from openai import OpenAI

        self.name = model
        # max_retries=0：失败直接返回，由调用方安全降级；不要让一次检索叠加多个超时
        self._client = OpenAI(api_key=api_key, base_url=base_url or None,
                              timeout=KNOWLEDGE_EMBED_TIMEOUT_SECONDS, max_retries=0)

    def _embed(self, texts: list[str]) -> np.ndarray:
        rows: list[list[float]] = []
        for start in range(0, len(texts), KNOWLEDGE_EMBED_BATCH_SIZE):
            batch = texts[start:start + KNOWLEDGE_EMBED_BATCH_SIZE]
            error = ""
            try:
                response = self._client.embeddings.create(model=self.name, input=batch)
            except Exception as exc:
                error = _describe_error(exc)
            # 在 except 块外抛出：from None 只是不显示原异常，__context__ 里仍然留着它（带 Key 和输入原文）
            if error:
                raise EmbeddingError(error)
            rows += [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        if len(rows) != len(texts):
            raise EmbeddingError("Embedding 调用失败：返回的向量数量与输入不一致")
        return l2_normalize(np.array(rows, dtype="float32"))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts) if texts else np.zeros((0, 0), "float32")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([check_query(text)])[0]


def is_embedding_configured() -> bool:
    return bool(settings.agent_embedding_model and settings.agent_embedding_api_key)


def create_embedding_model() -> EmbeddingModel:
    if not is_embedding_configured():
        raise EmbeddingError(MSG_NOT_CONFIGURED)
    return OpenAICompatibleEmbedding(settings.agent_embedding_model, settings.agent_embedding_api_key,
                                     settings.agent_embedding_base_url)


# 检索和构建都通过这个可替换的工厂取模型，测试用 monkeypatch 换成 FakeEmbeddingModel
def get_embedding_model() -> EmbeddingModel:
    return create_embedding_model()
