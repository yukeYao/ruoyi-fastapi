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
from app.core.constants import KNOWLEDGE_INDEX_VERSION, KNOWLEDGE_KEEP_OLD_BUILDS
from app.models.agent_knowledge_chunk import AgentKnowledgeChunk
from app.models.agent_knowledge_doc import AgentKnowledgeDoc
from app.models.agent_knowledge_index import AgentKnowledgeIndex
from app.rag import index_store
from app.rag.documents import KnowledgeChunk, KnowledgeDoc, perms_to_json, sha256
from app.rag.embedding import EmbeddingModel
from app.rag.index_store import IndexFileError, IndexManifest, LoadedIndex

logger = logging.getLogger("ruoyi.agent.knowledge")

INDEX_ROW_ID = 1
# 对外（模型、用户）只给这一句；具体原因只写日志，原因里只有版本号、数量这类元数据
MSG_INDEX_UNAVAILABLE = "知识库索引不可用，请联系管理员重新构建索引"


class KnowledgeIndexUnavailable(RuntimeError):
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
