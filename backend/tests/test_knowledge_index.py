"""
知识库索引：构建、增量、发布顺序、重新加载、一致性校验与安全失败、归一化、Embedding 的长度限制与脱敏。
全部使用 FakeEmbeddingModel 和临时索引目录，不调用真实 Embedding，也不碰开发环境的索引。
"""
import json
import logging

import numpy as np
import pytest

from app.config import BACKEND_DIR, settings
from app.core import constants
from app.core.database import SessionLocal
from app.models.agent_knowledge_chunk import AgentKnowledgeChunk
from app.models.agent_knowledge_doc import AgentKnowledgeDoc
from app.models.agent_knowledge_index import AgentKnowledgeIndex
from app.rag import embedding, index_store
from app.rag.documents import load_docs
from app.rag.embedding import EmbeddingError, FakeEmbeddingModel, QueryTooLongError, l2_normalize
from app.services import knowledge_service
from app.services.knowledge_service import KnowledgeIndexUnavailable

DOC_A = """---
doc_id: alpha
title: 公开文档
version: 1
required_perms: []
sources:
  - README.md
---

## 第一节

智能助手需要 agent:chat:use 权限。

## 第二节

停用账号不会让已登录的会话下线。
"""

DOC_B = """---
doc_id: beta
title: 受限文档
version: 1
required_perms: [system:role:edit]
sources:
  - README.md
---

## 授权步骤

在角色管理里勾选智能助手菜单。
"""


def _clear_tables(db):
    for model in (AgentKnowledgeChunk, AgentKnowledgeDoc, AgentKnowledgeIndex):
        db.query(model).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """临时文档目录 + 临时索引目录 + 清空的知识库表"""
    docs_dir, index_dir = tmp_path / "docs", tmp_path / "index"
    docs_dir.mkdir()
    (docs_dir / "alpha.md").write_text(DOC_A, encoding="utf-8")
    (docs_dir / "beta.md").write_text(DOC_B, encoding="utf-8")
    monkeypatch.setattr(settings, "knowledge_index_path", str(index_dir))
    db = SessionLocal()
    _clear_tables(db)
    knowledge_service.clear_cache()
    yield {"docs": docs_dir, "index": index_dir, "db": db}
    knowledge_service.clear_cache()
    _clear_tables(db)
    db.close()


def _build(kb, model=None, force=False):
    model = model or FakeEmbeddingModel()
    report = knowledge_service.build_index(kb["db"], load_docs(kb["docs"]), model, kb["index"], force=force)
    return report, model


def _active(kb):
    kb["db"].rollback()  # 结束当前事务，读到最新提交（REPEATABLE READ）
    return knowledge_service.load_active_index(kb["db"], kb["index"])


def test_index_dir_in_tests_is_not_dev_index():
    """conftest 把索引目录换成了临时目录：测试构建、删除、篡改索引绝不会影响开发环境"""
    assert not settings.knowledge_index_dir.resolve().is_relative_to((BACKEND_DIR / "data").resolve())


def test_first_build_writes_tables_and_files(kb):
    report, model = _build(kb)
    assert report.published and report.chunk_count == 3 and report.embedded == 3 and report.reused == 0
    db = kb["db"]
    meta = db.get(AgentKnowledgeIndex, 1)
    assert meta.build_id == report.build_id and meta.chunk_count == 3
    assert meta.index_version == constants.KNOWLEDGE_INDEX_VERSION and meta.embedding_model == model.name
    rows = db.query(AgentKnowledgeChunk).order_by(AgentKnowledgeChunk.chunk_id).all()
    assert [r.chunk_id for r in rows] == ["alpha#01", "alpha#02", "beta#01"]
    assert json.loads(rows[2].required_perms) == ["system:role:edit"]
    assert all(p.exists() for p in index_store.index_files(kb["index"], report.build_id))
    active = _active(kb)
    assert active.build_id == report.build_id and active.index.ntotal == 3


def test_vectors_are_l2_normalized_and_inner_product_is_cosine(kb):
    _build(kb)
    active = _active(kb)
    for chunk in active.chunks:
        assert np.linalg.norm(active.index.reconstruct(chunk.vector_id)) == pytest.approx(1.0, abs=1e-5)
    # 未归一化的查询向量：检索得分必须等于与原始向量的余弦相似度
    raw = np.random.default_rng(0).standard_normal(FakeEmbeddingModel().dim).astype("float32") * 7
    stored = {c.vector_id: active.index.reconstruct(c.vector_id) for c in active.chunks}
    for vector_id, score in active.index.search(raw, 3, list(stored)):
        v = stored[vector_id]
        cosine = float(raw @ v / (np.linalg.norm(raw) * np.linalg.norm(v)))
        assert score == pytest.approx(cosine, abs=1e-5)


def test_write_build_normalizes_even_if_caller_did_not(tmp_path):
    vectors = np.array([[3.0, 4.0], [0.0, 2.0]], dtype="float32")
    manifest = index_store.IndexManifest("b1", "v1", "m", 2, 2, "d")
    index_store.write_build(tmp_path, manifest, np.array([1, 2]), vectors)
    loaded = index_store.read_build(tmp_path, "b1")
    assert np.allclose(loaded.reconstruct(1), [0.6, 0.8]) and np.allclose(loaded.reconstruct(2), [0.0, 1.0])


def test_l2_normalize_keeps_zero_vector():
    out = l2_normalize(np.zeros((1, 4)))
    assert not np.isnan(out).any() and out.dtype == np.float32


def test_search_only_inside_allowed_ids(kb):
    _build(kb)
    active = _active(kb)
    target = next(c for c in active.chunks if c.doc_id == "beta")
    query = active.index.reconstruct(target.vector_id)
    allowed = [c.vector_id for c in active.chunks if c.doc_id == "alpha"]
    hits = active.index.search(query, 5, allowed)
    # 受限分块与查询完全相同也不会出现：只在允许集合里检索
    assert hits and {v for v, _ in hits} <= set(allowed)
    assert active.index.search(query, 5, []) == []


def test_rebuild_without_changes_does_nothing(kb):
    first, _ = _build(kb)
    model = FakeEmbeddingModel()
    second, _ = _build(kb, model)
    assert not second.published and second.build_id == first.build_id
    assert model.calls == []  # 没有重新计算 Embedding


def test_incremental_build_only_embeds_changed_chunks(kb):
    first, _ = _build(kb)
    path = kb["docs"] / "alpha.md"
    path.write_text(path.read_text(encoding="utf-8").replace("不会让已登录的会话下线", "不会让会话下线，需要另外强制下线"),
                    encoding="utf-8")
    model = FakeEmbeddingModel()
    second, _ = _build(kb, model)
    assert second.published and second.build_id != first.build_id
    assert model.calls == [1] and second.embedded == 1 and second.reused == 2
    assert _active(kb).build_id == second.build_id


def test_doc_metadata_change_republishes_without_embedding(kb):
    _build(kb)
    path = kb["docs"] / "alpha.md"
    path.write_text(path.read_text(encoding="utf-8").replace("version: 1", "version: 2"), encoding="utf-8")
    model = FakeEmbeddingModel()
    report, _ = _build(kb, model)
    assert report.published and model.calls == []
    assert kb["db"].get(AgentKnowledgeDoc, "alpha").version == 2


def test_removed_doc_is_deleted(kb):
    _build(kb)
    (kb["docs"] / "beta.md").unlink()
    _build(kb)
    db = kb["db"]
    assert db.get(AgentKnowledgeDoc, "beta") is None
    assert db.query(AgentKnowledgeChunk).filter(AgentKnowledgeChunk.doc_id == "beta").count() == 0


def test_model_change_forces_full_rebuild(kb):
    _build(kb)
    model = FakeEmbeddingModel(name="fake-other")
    report, _ = _build(kb, model)
    assert report.published and report.embedded == 3 and report.reused == 0
    assert _active(kb).embedding_model == "fake-other"


def test_index_version_change_forces_full_rebuild(kb, monkeypatch):
    _build(kb)
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_INDEX_VERSION", "v-next")
    report, model = _build(kb)
    assert report.published and model.calls == [3]


def test_force_rebuild(kb):
    _build(kb)
    report, model = _build(kb, force=True)
    assert report.published and model.calls == [3] and report.reason == "强制重建"


# ---------------------------------------------------------------------------
# 发布顺序：任何一步失败，读取方看到的仍是旧的完整构建
# ---------------------------------------------------------------------------

def _change_doc(kb):
    path = kb["docs"] / "alpha.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n补充一句。\n", encoding="utf-8")


def test_failure_while_writing_files_keeps_old_build(kb, monkeypatch):
    first, _ = _build(kb)
    _change_doc(kb)

    def broken(*args, **kwargs):
        raise OSError("磁盘已满")

    monkeypatch.setattr(index_store.os, "replace", broken)
    with pytest.raises(OSError):
        _build(kb)
    monkeypatch.undo()
    monkeypatch.setattr(settings, "knowledge_index_path", str(kb["index"]))
    active = _active(kb)
    assert active.build_id == first.build_id and active.index.ntotal == 3


def test_failure_while_switching_keeps_old_build(kb, monkeypatch):
    first, _ = _build(kb)
    _change_doc(kb)
    original = knowledge_service._switch_active

    def fail_after_writes(db, *args, **kwargs):
        # 让事务里的删除、插入都执行，再在提交前失败：回滚后不能留下任何半成品
        def boom():
            raise RuntimeError("数据库连接中断")
        monkeypatch.setattr(db, "commit", boom)
        original(db, *args, **kwargs)

    monkeypatch.setattr(knowledge_service, "_switch_active", fail_after_writes)
    with pytest.raises(RuntimeError):
        _build(kb)
    monkeypatch.undo()
    monkeypatch.setattr(settings, "knowledge_index_path", str(kb["index"]))
    active = _active(kb)
    assert active.build_id == first.build_id and len(active.chunks) == 3


def test_files_are_complete_before_switch(kb, monkeypatch):
    """切换数据库里的生效版本时，新构建的文件必须已经完整可读"""
    _build(kb)
    _change_doc(kb)
    seen = {}
    original = knowledge_service._switch_active

    def check_then_switch(db, docs, plan, manifest, model_name):
        seen["loaded"] = index_store.read_build(kb["index"], manifest.build_id).ntotal
        original(db, docs, plan, manifest, model_name)

    monkeypatch.setattr(knowledge_service, "_switch_active", check_then_switch)
    _build(kb)
    assert seen["loaded"] == 3


def test_reader_reloads_when_build_changes(kb):
    first, _ = _build(kb)
    assert _active(kb).build_id == first.build_id
    _change_doc(kb)
    second, _ = _build(kb)
    assert _active(kb).build_id == second.build_id


def test_old_builds_are_cleaned_but_previous_kept(kb):
    builds = []
    for i in range(3):
        if i:
            _change_doc(kb)
        builds.append(_build(kb)[0].build_id)
    names = {p.name for p in kb["index"].iterdir()}
    assert not any(builds[0] in n for n in names)
    assert all(any(b in n for n in names) for b in builds[1:])


# ---------------------------------------------------------------------------
# 一致性校验：对不上就安全失败
# ---------------------------------------------------------------------------

def test_unavailable_before_first_build(kb):
    with pytest.raises(KnowledgeIndexUnavailable) as exc:
        _active(kb)
    assert str(exc.value) == knowledge_service.MSG_INDEX_UNAVAILABLE


def test_missing_index_files_fail_safely(kb):
    report, _ = _build(kb)
    knowledge_service.clear_cache()
    for path in index_store.index_files(kb["index"], report.build_id):
        path.unlink()
    with pytest.raises(KnowledgeIndexUnavailable):
        _active(kb)


def test_corrupted_index_file_fails_safely(kb):
    report, _ = _build(kb)
    knowledge_service.clear_cache()
    index_store.index_files(kb["index"], report.build_id)[0].write_bytes(b"not a faiss index")
    with pytest.raises(KnowledgeIndexUnavailable):
        _active(kb)


def test_code_index_version_mismatch_fails_safely(kb, monkeypatch):
    _build(kb)
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_INDEX_VERSION", "v-next")
    with pytest.raises(KnowledgeIndexUnavailable) as exc:
        _active(kb)
    assert "版本" in exc.value.reason


def test_chunk_table_tampering_fails_safely(kb):
    """分块表被改（例如手工把受限文档的权限清空）：摘要对不上，拒绝使用，不能按被改过的权限检索"""
    _build(kb)
    db = kb["db"]
    db.query(AgentKnowledgeChunk).filter(AgentKnowledgeChunk.doc_id == "beta").update({"required_perms": "[]"})
    db.commit()
    with pytest.raises(KnowledgeIndexUnavailable):
        _active(kb)


def test_manifest_mismatch_fails_safely(kb):
    report, _ = _build(kb)
    knowledge_service.clear_cache()
    manifest_path = index_store.index_files(kb["index"], report.build_id)[1]
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["embedding_model"] = "something-else"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(KnowledgeIndexUnavailable):
        _active(kb)


# ---------------------------------------------------------------------------
# Embedding：长度限制、未配置、脱敏、不泄露 Key
# ---------------------------------------------------------------------------

def test_query_length_is_limited_before_sending():
    model = FakeEmbeddingModel()
    with pytest.raises(QueryTooLongError):
        model.embed_query("字" * (constants.KNOWLEDGE_MAX_QUERY_CHARS + 1))
    assert model.embed_query("字" * constants.KNOWLEDGE_MAX_QUERY_CHARS).shape == (model.dim,)


def test_real_embedding_requires_configuration():
    assert not embedding.is_embedding_configured()  # conftest 清空了 Key：pytest 不会读取真实 Key
    with pytest.raises(EmbeddingError):
        embedding.create_embedding_model()


class _LeakyClient:
    """模拟 SDK 异常信息里带着 Key 和请求原文"""

    class embeddings:
        @staticmethod
        def create(**kwargs):
            raise RuntimeError(f"401 invalid key sk-CANARY-KEY-123456 for input {kwargs['input']}")


def test_embedding_error_is_desensitized():
    model = embedding.OpenAICompatibleEmbedding.__new__(embedding.OpenAICompatibleEmbedding)
    model.name, model._client = "m", _LeakyClient()
    with pytest.raises(EmbeddingError) as exc:
        model.embed_query("为什么看不到智能助手 QUESTION-CANARY")
    text = str(exc.value) + repr(exc.value.__cause__) + repr(exc.value.__context__)
    assert "CANARY" not in text and exc.value.__cause__ is None and exc.value.__context__ is None


def test_embedding_key_and_query_not_in_index_or_logs(kb, monkeypatch, caplog):
    canary = "sk-EMBEDDING-KEY-CANARY-7f3a"
    monkeypatch.setattr(settings, "agent_embedding_api_key", canary)
    caplog.set_level(logging.DEBUG)
    report, model = _build(kb)
    model.embed_query("QUERY-TEXT-CANARY")
    db = kb["db"]
    stored = json.dumps([[getattr(r, c.name) for c in r.__table__.columns]
                         for m in (AgentKnowledgeIndex, AgentKnowledgeChunk, AgentKnowledgeDoc) for r in db.query(m).all()],
                        default=str, ensure_ascii=False)
    files = b"".join(p.read_bytes() for p in kb["index"].iterdir())
    for marker in (canary, "QUERY-TEXT-CANARY"):
        assert marker not in stored and marker.encode() not in files and marker not in caplog.text


def test_build_script_on_real_docs_with_fake_embedding(kb, capsys):
    """命令行脚本：用真实的 docs/knowledge、假 Embedding、临时索引目录；第二次运行不重建"""
    from scripts import build_knowledge_index

    build_knowledge_index.main(["--fake"])
    first = capsys.readouterr().out
    assert "已发布" in first
    build_knowledge_index.main(["--fake"])
    assert "无需重建" in capsys.readouterr().out
    active = _active(kb)
    assert {c.doc_id for c in active.chunks} == {d.doc_id for d in load_docs(build_knowledge_index.DOCS_DIR)}


def test_build_script_refuses_without_embedding_config(kb):
    """不加 --fake 且没有配置 Embedding：直接退出，不会悄悄改用假模型"""
    from scripts import build_knowledge_index

    with pytest.raises(SystemExit):
        build_knowledge_index.main([])
