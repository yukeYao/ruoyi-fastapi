"""
知识库索引的构建、发布与读取。

构建（scripts/build_knowledge_index.py 调用 build_index）：
1. 读取并校验全部文档、分块，给每个分块分配本次构建的 vector_id；
2. 当前生效索引的 index_version、embedding_model 与本次一致时，按 content_hash 复用旧向量，只为新增或变化的分块
   调用 Embedding；不一致（或 --force）时全部重算；内容、模型、版本都没变时什么都不做；
3. 发布：先把新构建的索引文件完整写到磁盘（原子改名），再在一个数据库事务里替换文档、分块并更新
   agent_knowledge_index 这一行，提交即切换。写文件或事务任一步失败，当前生效的仍是旧构建；
4. 切换成功后清理更早的构建文件（保留上一份，读取方可能刚读到旧 build_id）。

读取（load_active_index）：读 agent_knowledge_index 和分块表（同一事务的一致性快照），按 build_id 加载并缓存索引文件，
build_id 变化时重新加载；版本、模型、向量数、摘要任何一项对不上都安全失败，抛 KnowledgeIndexUnavailable，
由调用方转成工具错误，不影响其他业务工具。

检索（search_knowledge）：权限过滤在检索之前——先按当前用户的权限确定可读分块集合，
BM25 只在可读集合上建（IDF 等统计量也只来自可读集合），向量检索用 IDSelectorBatch 只在可读向量里找；
两路结果 RRF 合并后过相关性门槛和限额。Embedding 不可用时降级为只用 BM25，并在结果里附服务端生成的说明。
权限不足和内容不存在得到完全相同的结果：结果只取决于可读集合，空结果的说明也是同一句。

本模块只读写知识库自己的三张表和索引文件，不涉及任何业务数据。
"""
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.core.constants import (
    KNOWLEDGE_CANDIDATES,
    KNOWLEDGE_INDEX_VERSION,
    KNOWLEDGE_KEEP_OLD_BUILDS,
    KNOWLEDGE_MAX_SNIPPET_CHARS,
    KNOWLEDGE_MAX_TOP_K,
    KNOWLEDGE_MAX_TOTAL_CHARS,
    KNOWLEDGE_RRF_K,
    KNOWLEDGE_TOP_K,
)
from app.core.deps import user_has_perm
from app.core.exceptions import ServiceException
from app.models.agent_knowledge_chunk import AgentKnowledgeChunk
from app.models.agent_knowledge_doc import AgentKnowledgeDoc
from app.models.agent_knowledge_index import AgentKnowledgeIndex
from app.rag import embedding, index_store, retriever
from app.rag.bm25 import BM25
from app.rag.documents import KnowledgeChunk, KnowledgeDoc, perms_to_json, sha256
from app.rag.embedding import EmbeddingModel, QueryTooLongError
from app.rag.index_store import IndexFileError, IndexManifest, LoadedIndex
from app.rag.retriever import Candidate, Thresholds
from app.rag.tokenizer import tokenize

logger = logging.getLogger("ruoyi.agent.knowledge")

INDEX_ROW_ID = 1
# 对外（模型、用户）只给这一句；具体原因只写日志，原因里只有版本号、数量这类元数据
MSG_INDEX_UNAVAILABLE = "知识库索引不可用，请联系管理员重新构建索引"


MSG_EMPTY_QUERY = "问题不能为空"
# 下面两句是服务端生成、放在工具结果外层的说明（不在 untrusted_data 里）
NOTE_NO_RESULT = ("知识库中没有找到与问题相关的内容。这不代表系统没有相关规则：可能没有收录，也可能不在你可读的范围内。"
                  "回答时不能编造规则或引用，应如实说明没有找到依据。")
NOTE_DEGRADED = "向量检索暂时不可用，本次只使用关键词检索，结果可能不完整。"
NOTE_VECTOR_UNAVAILABLE = "向量检索暂时不可用，本次没有检索结果。"
RETRIEVAL_MODES = ("hybrid", "bm25", "vector")
MSG_BAD_MODE = "知识库检索方式配置错误"


class KnowledgeIndexUnavailable(ServiceException):
    """索引不可用：对外只有一句固定文案，reason 只写日志"""

    def __init__(self, reason: str):
        super().__init__(MSG_INDEX_UNAVAILABLE)
        self.reason = reason


def vectors_digest(entries: list[tuple[int, str, str, str]]) -> str:
    """(vector_id, chunk_id, content_hash, required_perms) 列表的摘要：索引文件和分块表必须对得上"""
    return sha256(json.dumps(sorted(entries), ensure_ascii=False))


def _docs_digest(docs: list[tuple[str, str]]) -> str:
    return sha256(json.dumps(sorted(docs), ensure_ascii=False))


# ---------------------------------------------------------------------------
# 构建与发布
# ---------------------------------------------------------------------------

@dataclass
class BuildReport:
    published: bool
    build_id: str
    chunk_count: int
    embedded: int = 0
    reused: int = 0
    reason: str = ""


@dataclass
class _Plan:
    chunks: list[KnowledgeChunk]
    vector_ids: np.ndarray
    digest: str
    docs_digest: str
    entries: list[tuple[int, str, str, str]] = field(default_factory=list)


def _plan(docs: list[KnowledgeDoc]) -> _Plan:
    chunks = [c for d in docs for c in d.chunks]
    vector_ids = np.arange(1, len(chunks) + 1, dtype="int64")
    entries = [(int(v), c.chunk_id, c.content_hash, perms_to_json(c.required_perms)) for v, c in zip(vector_ids, chunks)]
    return _Plan(chunks, vector_ids, vectors_digest(entries), _docs_digest([(d.doc_id, d.content_hash) for d in docs]),
                 entries)


def _reusable_vectors(db: Session, meta: AgentKnowledgeIndex, index_dir: Path) -> dict[str, np.ndarray]:
    """旧构建里 content_hash -> 向量；旧文件读不出来就返回空（全部重算），不让构建失败"""
    try:
        old = index_store.read_build(index_dir, meta.build_id)
    except IndexFileError:
        logger.warning("旧索引文件不可用，全部重新计算 Embedding：build=%s", meta.build_id)
        return {}
    rows = db.query(AgentKnowledgeChunk.content_hash, AgentKnowledgeChunk.vector_id).all()
    return {h: old.reconstruct(v) for h, v in rows}


def _switch_active(db: Session, docs: list[KnowledgeDoc], plan: _Plan, manifest: IndexManifest, model_name: str) -> None:
    """一个事务内替换文档、分块并更新生效索引；提交即切换"""
    now = datetime.now()
    try:
        old_docs = {d.doc_id: d for d in db.query(AgentKnowledgeDoc).all()}
        db.query(AgentKnowledgeChunk).delete(synchronize_session=False)
        keep = {d.doc_id for d in docs}
        for doc_id, row in old_docs.items():
            if doc_id not in keep:
                db.delete(row)
        for d in docs:
            row = old_docs.get(d.doc_id) or AgentKnowledgeDoc(doc_id=d.doc_id, create_time=now)
            row.title, row.version = d.title, d.version
            row.required_perms, row.sources = perms_to_json(d.required_perms), json.dumps(list(d.sources))
            row.content_hash, row.chunk_count, row.update_time = d.content_hash, len(d.chunks), now
            db.add(row)
        db.add_all([
            AgentKnowledgeChunk(chunk_id=c.chunk_id, doc_id=c.doc_id, title=c.title, section=c.section, seq=c.seq,
                                content=c.content, content_hash=c.content_hash,
                                required_perms=perms_to_json(c.required_perms), vector_id=int(v),
                                embedding_model=model_name, index_version=KNOWLEDGE_INDEX_VERSION,
                                create_time=now, update_time=now)
            for c, v in zip(plan.chunks, plan.vector_ids)
        ])
        meta = db.get(AgentKnowledgeIndex, INDEX_ROW_ID) or AgentKnowledgeIndex(id=INDEX_ROW_ID)
        meta.build_id, meta.index_version, meta.embedding_model = manifest.build_id, manifest.index_version, model_name
        meta.dimension, meta.chunk_count = manifest.dimension, manifest.chunk_count
        meta.vectors_digest, meta.built_time = manifest.vectors_digest, now
        db.add(meta)
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _stored_docs_digest(db: Session) -> str:
    return _docs_digest([(d.doc_id, d.content_hash) for d in db.query(AgentKnowledgeDoc).all()])


def build_index(db: Session, docs: list[KnowledgeDoc], model: EmbeddingModel, index_dir: Path | None = None,
                force: bool = False) -> BuildReport:
    index_dir = index_dir or settings.knowledge_index_dir
    plan = _plan(docs)
    meta = db.get(AgentKnowledgeIndex, INDEX_ROW_ID)
    # 先记下旧的 build_id：_switch_active 会原地修改同一个 ORM 对象，切换后再读就成了新值
    previous_build = meta.build_id if meta is not None else None
    compatible = (meta is not None and not force and meta.index_version == KNOWLEDGE_INDEX_VERSION
                  and meta.embedding_model == model.name)

    if compatible and meta.vectors_digest == plan.digest and _stored_docs_digest(db) == plan.docs_digest:
        try:
            index_store.read_build(index_dir, meta.build_id)
            return BuildReport(False, meta.build_id, len(plan.chunks), reason="内容、模型和索引版本都没有变化")
        except IndexFileError:
            logger.warning("当前索引文件不可用，重新发布：build=%s", meta.build_id)

    reusable = _reusable_vectors(db, meta, index_dir) if compatible else {}
    missing = sorted({c.content_hash: c.embedding_text for c in plan.chunks if c.content_hash not in reusable}.items())
    if missing:
        vectors = model.embed_documents([text for _, text in missing])
        if len(vectors) != len(missing):
            raise RuntimeError("Embedding 返回的向量数量与输入不一致")
        reusable.update({h: v for (h, _), v in zip(missing, vectors)})
    matrix = np.stack([reusable[c.content_hash] for c in plan.chunks]).astype("float32")

    build_id = f"{datetime.now():%Y%m%d%H%M%S}-{plan.digest[:8]}-{uuid.uuid4().hex[:6]}"
    manifest = IndexManifest(build_id, KNOWLEDGE_INDEX_VERSION, model.name, int(matrix.shape[1]), len(plan.chunks),
                             plan.digest)
    # 顺序不能反：文件完整落盘之后才切换数据库里的生效版本
    index_store.write_build(index_dir, manifest, plan.vector_ids, matrix)
    _switch_active(db, docs, plan, manifest, model.name)

    keep = {build_id} | ({previous_build} if previous_build and KNOWLEDGE_KEEP_OLD_BUILDS > 0 else set())
    index_store.remove_old_builds(index_dir, keep)
    if meta is None:
        reason = "首次构建"
    elif force:
        reason = "强制重建"
    elif not compatible:
        reason = "索引版本或 Embedding 模型变化，全部重新计算"
    else:
        reason = "内容有变化"
    embedded = {h for h, _ in missing}
    return BuildReport(True, build_id, len(plan.chunks), embedded=len(embedded),
                       reused=sum(1 for c in plan.chunks if c.content_hash not in embedded), reason=reason)


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    content: str
    required_perms: tuple[str, ...]
    vector_id: int


@dataclass(frozen=True)
class ActiveIndex:
    build_id: str
    embedding_model: str
    index: LoadedIndex
    chunks: tuple[ChunkRecord, ...]


_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], LoadedIndex] = {}


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _load_cached(index_dir: Path, build_id: str) -> LoadedIndex:
    key = (str(index_dir), build_id)
    with _cache_lock:
        if key not in _cache:
            loaded = index_store.read_build(index_dir, build_id)
            # 只缓存当前版本：build_id 变了就换掉，旧构建的内存随之释放
            _cache.clear()
            _cache[key] = loaded
        return _cache[key]


def load_active_index(db: Session, index_dir: Path | None = None) -> ActiveIndex:
    index_dir = index_dir or settings.knowledge_index_dir
    meta = db.get(AgentKnowledgeIndex, INDEX_ROW_ID)
    if meta is None:
        raise KnowledgeIndexUnavailable("尚未构建索引")
    if meta.index_version != KNOWLEDGE_INDEX_VERSION:
        raise KnowledgeIndexUnavailable(f"索引版本 {meta.index_version} 与代码要求的 {KNOWLEDGE_INDEX_VERSION} 不一致")

    rows = db.query(AgentKnowledgeChunk).order_by(AgentKnowledgeChunk.vector_id).all()
    entries = [(r.vector_id, r.chunk_id, r.content_hash, r.required_perms) for r in rows]
    if (len(rows) != meta.chunk_count or vectors_digest(entries) != meta.vectors_digest
            or any(r.index_version != meta.index_version or r.embedding_model != meta.embedding_model for r in rows)):
        raise KnowledgeIndexUnavailable("分块表与生效索引的摘要不一致")

    try:
        loaded = _load_cached(index_dir, meta.build_id)
    except IndexFileError as exc:
        raise KnowledgeIndexUnavailable(f"索引文件不可用：{exc}") from None
    m = loaded.manifest
    if (m.index_version, m.embedding_model, m.chunk_count, m.dimension, m.vectors_digest) != (
            meta.index_version, meta.embedding_model, meta.chunk_count, meta.dimension, meta.vectors_digest):
        raise KnowledgeIndexUnavailable("索引文件清单与数据库记录不一致")

    chunks = tuple(ChunkRecord(r.chunk_id, r.doc_id, r.title, r.section, r.content,
                               tuple(json.loads(r.required_perms)), r.vector_id) for r in rows)
    return ActiveIndex(meta.build_id, meta.embedding_model, loaded, chunks)


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KnowledgeHit:
    doc_id: str
    title: str
    section: str
    chunk_id: str
    snippet: str
    score: float                 # RRF 分数（相对排名，不是概率）
    vector_score: float | None   # 余弦相似度；降级或向量检索没召回时为 None
    bm25_score: float


@dataclass(frozen=True)
class KnowledgeSearchResult:
    hits: tuple[KnowledgeHit, ...]
    note: str        # 服务端生成的说明，空字符串表示没有
    degraded: bool   # 本次是否只用了关键词检索


def current_thresholds() -> Thresholds:
    return Thresholds(settings.knowledge_min_vector_score, settings.knowledge_min_bm25_score,
                      settings.knowledge_min_bm25_terms)


def degraded_thresholds() -> Thresholds:
    """向量检索不可用时的纯关键词门槛（向量门槛不起作用：没有向量候选）"""
    return Thresholds(settings.knowledge_min_vector_score, settings.knowledge_degraded_min_bm25_score,
                      settings.knowledge_degraded_min_bm25_terms)


def readable(login_user: dict, chunk: ChunkRecord) -> bool:
    """required_perms 需全部满足；空表示所有智能助手用户可读。超级管理员（*:*:*）全部可读"""
    return all(user_has_perm(login_user, p) for p in chunk.required_perms)


# 分块分词的缓存：按 (build_id, chunk_id) 惰性填充，只在分块进入某次检索的可读集合时才分词；
# build_id 变化时整体清空
_token_lock = threading.Lock()
_token_cache: dict[tuple[str, str], list[str]] = {}


def _chunk_tokens(build_id: str, chunk: ChunkRecord) -> list[str]:
    key = (build_id, chunk.chunk_id)
    with _token_lock:
        if key not in _token_cache:
            if any(k[0] != build_id for k in _token_cache):
                _token_cache.clear()
            # 与 Embedding 输入一致：标题、章节也参与关键词匹配
            _token_cache[key] = tokenize(f"{chunk.title}\n{chunk.section}\n{chunk.content}")
        return _token_cache[key]


def bm25_search(build_id: str, allowed: list[ChunkRecord], query_tokens: list[str]) -> list[tuple[ChunkRecord, float, int]]:
    """只在可读集合上建 BM25；返回得分大于 0 的前 KNOWLEDGE_CANDIDATES 个 (分块, 分数, 命中词数)"""
    if not allowed or not query_tokens:
        return []
    model = BM25([_chunk_tokens(build_id, c) for c in allowed])
    scored = [(c, s, m) for c, (s, m) in zip(allowed, model.score(query_tokens)) if s > 0]
    scored.sort(key=lambda x: (-x[1], x[0].chunk_id))
    return scored[:KNOWLEDGE_CANDIDATES]


def _vector_search(active: ActiveIndex, query: str, allowed: list[ChunkRecord]) -> tuple[list[tuple[int, float]], bool]:
    """
    返回 ([(vector_id, 余弦相似度)], 是否降级)。即使可读集合为空也先计算问题的向量：
    否则"有没有可读文档"会体现在是否降级上，权限不足和内容不存在的结果就不完全相同了
    """
    try:
        model = embedding.get_embedding_model()
        if model.name != active.embedding_model:
            # 查询和索引不在同一个向量空间，相似度没有意义
            raise embedding.EmbeddingError("查询所用 Embedding 模型与索引不一致")
        query_vector = model.embed_query(query)
        return active.index.search(query_vector, KNOWLEDGE_CANDIDATES, [c.vector_id for c in allowed]), False
    except Exception as exc:
        # 只记录异常类型和脱敏后的文案，不记录问题原文
        detail = str(exc) if isinstance(exc, (embedding.EmbeddingError, IndexFileError)) else ""
        logger.warning("知识库向量检索不可用，降级为关键词检索：%s %s", type(exc).__name__, detail)
        return [], True


def search_knowledge(db: Session, login_user: dict, query: str, top_k: int = KNOWLEDGE_TOP_K,
                     thresholds: Thresholds | None = None, mode: str | None = None) -> KnowledgeSearchResult:
    try:
        query = embedding.check_query(query)
    except QueryTooLongError as exc:
        raise ServiceException(str(exc)) from None
    if not query:
        raise ServiceException(MSG_EMPTY_QUERY)
    top_k = max(1, min(int(top_k), KNOWLEDGE_MAX_TOP_K))
    explicit = thresholds is not None
    thresholds = thresholds or current_thresholds()
    mode = mode or settings.knowledge_retrieval_mode
    if mode not in RETRIEVAL_MODES:
        raise ServiceException(MSG_BAD_MODE)

    try:
        active = load_active_index(db)
    except KnowledgeIndexUnavailable as exc:
        logger.warning("知识库索引不可用：%s", exc.reason)
        raise

    # 权限过滤在检索之前：后面两路检索都只接触这个集合
    allowed = [c for c in active.chunks if readable(login_user, c)]
    by_id = {c.chunk_id: c for c in allowed}
    by_vector = {c.vector_id: c for c in allowed}

    # 单一检索方式（评估对照）只跑对应的一路；hybrid 两路都跑
    bm25_hits = bm25_search(active.build_id, allowed, tokenize(query)) if mode != "vector" else []
    vector_hits, degraded = _vector_search(active, query, allowed) if mode != "bm25" else ([], False)
    # 防御：向量检索返回了可读集合以外的 ID（不应发生）就丢弃，而不是去分块表里查
    vector_hits = [(v, s) for v, s in vector_hits if v in by_vector]
    if degraded and not explicit:
        # 混合检索降级为纯关键词：换用 BM25 单独校准的门槛。调用方显式传了门槛（评估、测试）时照用
        thresholds = degraded_thresholds()

    candidates: dict[str, Candidate] = {}
    for c, score, matched in bm25_hits:
        candidates[c.chunk_id] = Candidate(c.chunk_id, bm25_score=score, bm25_terms=matched)
    for vector_id, sim in vector_hits:
        cid = by_vector[vector_id].chunk_id
        old = candidates.get(cid, Candidate(cid))
        candidates[cid] = Candidate(cid, sim, old.bm25_score, old.bm25_terms)

    ranked = retriever.rank(candidates, [by_vector[v].chunk_id for v, _ in vector_hits],
                            [c.chunk_id for c, _, _ in bm25_hits], thresholds, KNOWLEDGE_RRF_K, top_k)
    snippets = retriever.clip([by_id[r.chunk_id].content for r in ranked], KNOWLEDGE_MAX_SNIPPET_CHARS,
                              KNOWLEDGE_MAX_TOTAL_CHARS)
    hits = tuple(
        KnowledgeHit(by_id[r.chunk_id].doc_id, by_id[r.chunk_id].title, by_id[r.chunk_id].section, r.chunk_id, text,
                     round(r.score, 6), None if r.vector_score is None else round(r.vector_score, 4),
                     round(r.bm25_score, 4))
        for r, text in zip(ranked, snippets)
    )
    degraded_note = NOTE_VECTOR_UNAVAILABLE if mode == "vector" else NOTE_DEGRADED
    notes = ([degraded_note] if degraded else []) + ([] if hits else [NOTE_NO_RESULT])
    return KnowledgeSearchResult(hits, "".join(notes), degraded)
