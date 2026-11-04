"""
知识库混合检索：分词、BM25、RRF、相关性门槛、限额、权限过滤（检索前）、相同的无结果响应、降级、索引重载。
全部使用 FakeEmbeddingModel 和临时索引目录；门槛用测试夹具显式给出，不依赖配置里的初始值。
"""
import logging
from dataclasses import asdict

import pytest

from app.config import settings
from app.core.constants import KNOWLEDGE_MAX_SNIPPET_CHARS, KNOWLEDGE_MAX_TOP_K, KNOWLEDGE_MAX_TOTAL_CHARS
from app.core.database import SessionLocal
from app.core.exceptions import ServiceException
from app.models.agent_knowledge_chunk import AgentKnowledgeChunk
from app.models.agent_knowledge_doc import AgentKnowledgeDoc
from app.models.agent_knowledge_index import AgentKnowledgeIndex
from app.rag import embedding, retriever
from app.rag.bm25 import BM25
from app.rag.documents import load_docs
from app.rag.embedding import EmbeddingError, FakeEmbeddingModel
from app.rag.index_store import LoadedIndex
from app.rag.retriever import Candidate, Thresholds
from app.rag.tokenizer import tokenize
from app.services import knowledge_service
from app.services.knowledge_service import NOTE_DEGRADED, NOTE_NO_RESULT, KnowledgeIndexUnavailable

RESTRICTED_CANARY = "RESTRICTEDCANARY"

PUBLIC = """---
doc_id: pub
title: 智能助手权限说明
version: 1
required_perms: []
sources:
  - README.md
---

## 菜单权限

智能助手菜单的权限字符是 agent:chat:use，没有这个权限就看不到智能助手菜单。

## 停用与下线

停用账号不会让已登录的会话下线，需要另外执行强制下线。

## 重新登录

给角色授权以后，用户需要重新登录，登录态里的权限才会更新。
"""

ROLE_EDIT = """---
doc_id: roleedit
title: 开通智能助手的管理员步骤
version: 1
required_perms: [system:role:edit]
sources:
  - README.md
---

## 授权步骤

在角色管理里修改角色，勾选智能助手菜单，""" + RESTRICTED_CANARY + """ 保存后让用户重新登录。
"""

TWO_PERMS = """---
doc_id: twoperms
title: 在线会话排查手册
version: 1
required_perms: [monitor:online:list, monitor:online:forceLogout]
sources:
  - README.md
---

## 会话排查

在线会话排查专用步骤：先查询在线会话，再决定是否强制下线。
"""

STRICT = Thresholds(min_vector_score=0.6, min_bm25_score=1.0, min_bm25_terms=2)
LOOSE = Thresholds(min_vector_score=-1.0, min_bm25_score=0.0, min_bm25_terms=0)

PLAIN_USER = {"user_id": 100, "permissions": ["agent:chat:use"]}
ROLE_EDITOR = {"user_id": 101, "permissions": ["agent:chat:use", "system:role:edit"]}
ONLINE_LIST_ONLY = {"user_id": 102, "permissions": ["agent:chat:use", "monitor:online:list"]}
ADMIN = {"user_id": 1, "permissions": ["*:*:*"]}


def _clear(db):
    for model in (AgentKnowledgeChunk, AgentKnowledgeDoc, AgentKnowledgeIndex):
        db.query(model).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def kb(tmp_path, monkeypatch):
    docs_dir, index_dir = tmp_path / "docs", tmp_path / "index"
    docs_dir.mkdir()
    for name, text in (("pub", PUBLIC), ("roleedit", ROLE_EDIT), ("twoperms", TWO_PERMS)):
        (docs_dir / f"{name}.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(settings, "knowledge_index_path", str(index_dir))
    model = FakeEmbeddingModel()
    monkeypatch.setattr(embedding, "get_embedding_model", lambda: model)
    db = SessionLocal()
    _clear(db)
    knowledge_service.clear_cache()
    knowledge_service.build_index(db, load_docs(docs_dir), model, index_dir)
    yield {"db": db, "docs": docs_dir, "index": index_dir, "model": model}
    knowledge_service.clear_cache()
    _clear(db)
    db.close()


def _search(kb, user, query, thresholds=STRICT, top_k=4):
    kb["db"].rollback()  # 新事务，读到最新提交
    return knowledge_service.search_knowledge(kb["db"], user, query, top_k=top_k, thresholds=thresholds)


# ---------------------------------------------------------------------------
# 分词
# ---------------------------------------------------------------------------

def test_tokenizer_keeps_permission_strings_whole():
    tokens = tokenize("agent:chat:use 和 monitor:online:forceLogout 有什么区别")
    assert "agent:chat:use" in tokens and "monitor:online:forcelogout" in tokens
    # 不能拆出 use、chat 这类碎片：否则所有含权限字符的分块都会被命中
    assert not {"agent", "chat", "use", "monitor", "online"} & set(tokens)


def test_tokenizer_chinese_words_ascii_ids_and_stopwords():
    tokens = tokenize("为什么 test01 看不到智能助手？")
    assert "test01" in tokens and "智能助手" in tokens
    assert "为什么" not in tokens and "？" not in tokens
    # 搜索引擎模式同时给出长词和短词，短问题也能匹配长词
    assert {"强制", "下线", "强制下线"} <= set(tokenize("强制下线"))


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

def test_bm25_scores_and_matched_terms():
    corpus = [tokenize("停用账号不会让会话下线"), tokenize("强制下线会让会话失效"), tokenize("验证码有效期两分钟")]
    scores = BM25(corpus).score(tokenize("强制下线"))
    assert scores[1][0] > scores[0][0] > 0 and scores[2] == (0.0, 0)
    assert scores[1][1] >= 2


def test_bm25_statistics_depend_only_on_given_corpus():
    """同一篇文档，语料里多一篇（受限）文档时分数会变：所以 BM25 必须只在可读子集上建"""
    public = [tokenize("智能助手菜单需要权限"), tokenize("停用账号")]
    restricted = [tokenize("智能助手授权步骤")]
    query = tokenize("智能助手")
    assert BM25(public).score(query)[0] != BM25(public + restricted).score(query)[0]


def test_search_bm25_uses_only_readable_subset(kb, monkeypatch):
    """检索里建 BM25 时传入的语料恰好是可读分块（数量与内容），受限分块的词频、IDF 都不参与"""
    seen = []

    class SpyBM25(BM25):
        def __init__(self, corpus, *args, **kwargs):
            seen.append(corpus)
            super().__init__(corpus, *args, **kwargs)

    monkeypatch.setattr(knowledge_service, "BM25", SpyBM25)
    _search(kb, PLAIN_USER, "智能助手菜单")
    assert len(seen) == 1 and len(seen[0]) == 3  # pub 的 3 个分块
    assert all(RESTRICTED_CANARY.lower() not in tokens for tokens in seen[0])


# ---------------------------------------------------------------------------
# RRF、门槛、限额
# ---------------------------------------------------------------------------

def test_rrf_fuse():
    scores = retriever.rrf_fuse([["a", "b"], ["b", "c"]], k=60)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61) and scores["c"] == pytest.approx(1 / 62)
    assert scores["b"] > scores["a"] > scores["c"]


def test_rank_applies_thresholds_after_fusion():
    candidates = {
        "top": Candidate("top", vector_score=0.2, bm25_score=0.5, bm25_terms=1),   # 两路都排第一，但都没过门槛
        "vec": Candidate("vec", vector_score=0.7),                                  # 向量相似度过门槛
        "kw": Candidate("kw", vector_score=0.1, bm25_score=3.0, bm25_terms=2),     # 关键词过门槛
        "kw1": Candidate("kw1", bm25_score=3.0, bm25_terms=1),                      # 分数够但只命中 1 个词
    }
    ranked = retriever.rank(candidates, ["top", "vec", "kw"], ["top", "kw", "kw1"], STRICT, rrf_k=60, top_k=5)
    assert [r.chunk_id for r in ranked] == ["kw", "vec"]
    assert retriever.rank(candidates, ["top", "vec", "kw"], ["top", "kw"], STRICT, 60, top_k=1)[0].chunk_id == "kw"


def test_clip_limits_each_and_total():
    texts = ["甲" * 700, "乙" * 500, "丙" * 500, "丁" * 500]
    out = retriever.clip(texts, max_each=600, max_total=1200)
    assert out[0] == "甲" * 600 + "…" and out[1] == "乙" * 500 and len(out) == 2


def test_search_limits_top_k_and_total(kb):
    result = _search(kb, ADMIN, "智能助手 权限 登录 下线 会话", thresholds=LOOSE, top_k=99)
    assert 0 < len(result.hits) <= KNOWLEDGE_MAX_TOP_K
    assert sum(len(h.snippet) for h in result.hits) <= KNOWLEDGE_MAX_TOTAL_CHARS
    assert all(len(h.snippet) <= KNOWLEDGE_MAX_SNIPPET_CHARS + 1 for h in result.hits)


def test_search_thresholds_filter_irrelevant_query(kb):
    assert _search(kb, ADMIN, "今天天气怎么样", thresholds=STRICT).hits == ()


def test_thresholds_come_from_settings_by_default(kb, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_min_vector_score", 2.0)
    monkeypatch.setattr(settings, "knowledge_min_bm25_score", 1e9)
    kb["db"].rollback()
    assert knowledge_service.search_knowledge(kb["db"], ADMIN, "智能助手菜单 agent:chat:use").hits == ()


# ---------------------------------------------------------------------------
# 权限过滤（检索前）
# ---------------------------------------------------------------------------

def test_public_doc_found_by_plain_user(kb):
    result = _search(kb, PLAIN_USER, "看不到智能助手菜单 agent:chat:use")
    assert result.hits and result.hits[0].doc_id == "pub" and not result.degraded
    hit = result.hits[0]
    assert hit.chunk_id.startswith("pub#") and hit.title == "智能助手权限说明" and hit.section and hit.snippet


def test_restricted_doc_hidden_even_for_exact_query(kb):
    result = _search(kb, PLAIN_USER, f"勾选智能助手菜单 {RESTRICTED_CANARY}", thresholds=LOOSE)
    assert all(h.doc_id == "pub" for h in result.hits)
    assert RESTRICTED_CANARY not in str(asdict(result))


def test_restricted_doc_visible_with_permission(kb):
    for user in (ROLE_EDITOR, ADMIN):
        result = _search(kb, user, f"勾选智能助手菜单 {RESTRICTED_CANARY}")
        assert result.hits[0].doc_id == "roleedit"


def test_required_perms_must_all_be_satisfied(kb):
    query = "在线会话排查专用步骤"
    assert all(h.doc_id != "twoperms" for h in _search(kb, ONLINE_LIST_ONLY, query, thresholds=LOOSE).hits)
    both = {"user_id": 103, "permissions": ["monitor:online:list", "monitor:online:forceLogout"]}
    assert _search(kb, both, query).hits[0].doc_id == "twoperms"


def test_vector_and_keyword_search_never_touch_restricted_chunks(kb, monkeypatch):
    """Fake 检索器：记录向量检索拿到的允许集合、被分词的分块；受限分块一个都不能出现"""
    db = kb["db"]
    restricted_ids = {r.chunk_id: r.vector_id for r in db.query(AgentKnowledgeChunk).all() if r.doc_id != "pub"}
    allowed_seen, tokenized, reconstructed = [], [], []
    original_search, original_tokens = LoadedIndex.search, knowledge_service._chunk_tokens

    def spy_search(self, query, k, allowed_ids):
        allowed_seen.append(set(allowed_ids))
        return original_search(self, query, k, allowed_ids)

    def spy_tokens(build_id, chunk):
        tokenized.append(chunk.chunk_id)
        return original_tokens(build_id, chunk)

    monkeypatch.setattr(LoadedIndex, "search", spy_search)
    monkeypatch.setattr(LoadedIndex, "reconstruct", lambda self, v: reconstructed.append(v))
    monkeypatch.setattr(knowledge_service, "_chunk_tokens", spy_tokens)
    knowledge_service._token_cache.clear()

    result = _search(kb, PLAIN_USER, f"勾选智能助手菜单 {RESTRICTED_CANARY} 在线会话排查", thresholds=LOOSE)
    assert allowed_seen and not (allowed_seen[0] & set(restricted_ids.values()))
    assert tokenized and not (set(tokenized) & set(restricted_ids))
    assert reconstructed == []
    assert all(h.chunk_id not in restricted_ids for h in result.hits)


def test_no_permission_and_nonexistent_return_identical_result(kb):
    """受限文档的内容 vs 根本不存在的内容：对无权用户返回完全相同的结果（包括说明）"""
    restricted = _search(kb, PLAIN_USER, f"{RESTRICTED_CANARY} 授权步骤")
    missing = _search(kb, PLAIN_USER, "QWERTYUIOP 不存在的话题")
    assert restricted == missing
    assert restricted.hits == () and restricted.note == NOTE_NO_RESULT and not restricted.degraded


def test_identical_result_also_when_degraded(kb, monkeypatch):
    def broken():
        raise EmbeddingError("Embedding 调用失败：APITimeoutError")

    monkeypatch.setattr(embedding, "get_embedding_model", broken)
    restricted = _search(kb, PLAIN_USER, f"{RESTRICTED_CANARY} 授权步骤")
    missing = _search(kb, PLAIN_USER, "QWERTYUIOP 不存在的话题")
    assert restricted == missing and restricted.degraded


def test_user_without_any_readable_chunk_still_embeds_query(kb, monkeypatch):
    """可读集合为空时也计算问题向量：是否降级不能随权限变化，否则结果会因权限而不同"""
    for path in kb["docs"].glob("pub.md"):
        path.unlink()
    knowledge_service.build_index(kb["db"], load_docs(kb["docs"]), kb["model"], kb["index"])
    before = kb["model"].query_calls
    result = _search(kb, PLAIN_USER, "授权步骤")
    assert result.hits == () and kb["model"].query_calls == before + 1


# ---------------------------------------------------------------------------
# 降级与异常
# ---------------------------------------------------------------------------

def test_embedding_failure_degrades_to_bm25_without_details(kb, monkeypatch, caplog):
    class Broken(FakeEmbeddingModel):
        def embed_query(self, text):
            raise RuntimeError("upstream said: key sk-LEAK-CANARY-1 rejected")

    monkeypatch.setattr(embedding, "get_embedding_model", lambda: Broken())
    caplog.set_level(logging.WARNING)
    result = _search(kb, PLAIN_USER, "停用账号 强制下线 QUERY-CANARY")
    assert result.degraded and result.note == NOTE_DEGRADED
    assert result.hits and result.hits[0].vector_score is None and result.hits[0].bm25_score > 0
    # 外层说明是固定文案，日志只有异常类型：不出现上游异常原文，也不记录问题原文
    assert "LEAK" not in caplog.text and "QUERY-CANARY" not in caplog.text and "RuntimeError" in caplog.text
    assert "LEAK" not in str(asdict(result))


def test_embedding_not_configured_degrades(kb, monkeypatch):
    monkeypatch.setattr(embedding, "get_embedding_model", embedding.create_embedding_model)
    result = _search(kb, PLAIN_USER, "停用账号 强制下线")
    assert result.degraded and result.hits


def test_embedding_model_mismatch_degrades(kb, monkeypatch):
    monkeypatch.setattr(embedding, "get_embedding_model", lambda: FakeEmbeddingModel(name="another-model"))
    result = _search(kb, PLAIN_USER, "停用账号 强制下线")
    assert result.degraded and result.note == NOTE_DEGRADED


def test_degraded_search_uses_degraded_thresholds(kb, monkeypatch):
    """
    混合检索降级为纯关键词时，用 BM25 单独校准的降级门槛，而不是按"有向量路"校准的混合门槛；
    调用方显式传入门槛（评估、测试）时照用传入的
    """
    monkeypatch.setattr(embedding, "get_embedding_model", embedding.create_embedding_model)  # 未配置 -> 降级
    query = "停用账号 强制下线"
    monkeypatch.setattr(settings, "knowledge_min_bm25_score", 0.0)
    monkeypatch.setattr(settings, "knowledge_min_bm25_terms", 0)
    monkeypatch.setattr(settings, "knowledge_degraded_min_bm25_score", 1e9)
    kb["db"].rollback()
    blocked = knowledge_service.search_knowledge(kb["db"], PLAIN_USER, query)
    assert blocked.degraded and blocked.hits == ()
    monkeypatch.setattr(settings, "knowledge_degraded_min_bm25_score", 0.0)
    monkeypatch.setattr(settings, "knowledge_degraded_min_bm25_terms", 1)
    monkeypatch.setattr(settings, "knowledge_min_bm25_score", 1e9)
    allowed = knowledge_service.search_knowledge(kb["db"], PLAIN_USER, query)
    assert allowed.degraded and allowed.hits
    # 显式门槛优先：即使降级门槛放得很宽，传入严格门槛也照用
    strict = knowledge_service.search_knowledge(kb["db"], PLAIN_USER, query, thresholds=Thresholds(2.0, 1e9, 99))
    assert strict.degraded and strict.hits == ()


def test_production_defaults_are_v3_dev_calibration():
    """生产默认门槛 = v3 在 dev 上的校准值（frozen_v3.json）：混合检索用 hybrid 校准值，降级用 BM25 校准值"""
    import json

    from app.config import BACKEND_DIR, Settings

    frozen = json.loads((BACKEND_DIR / "evals" / "rag" / "frozen_v3.json").read_text(encoding="utf-8"))["calibration"]
    fields = Settings.model_fields
    assert (fields["knowledge_min_vector_score"].default, fields["knowledge_min_bm25_score"].default,
            fields["knowledge_min_bm25_terms"].default) == (frozen["hybrid"]["min_vector_score"],
                                                            frozen["hybrid"]["min_bm25_score"],
                                                            frozen["hybrid"]["min_bm25_terms"])
    assert (fields["knowledge_degraded_min_bm25_score"].default,
            fields["knowledge_degraded_min_bm25_terms"].default) == (frozen["bm25"]["min_bm25_score"],
                                                                     frozen["bm25"]["min_bm25_terms"])


def test_degraded_and_empty_notes_combined(kb, monkeypatch):
    monkeypatch.setattr(embedding, "get_embedding_model", embedding.create_embedding_model)
    result = _search(kb, PLAIN_USER, "QWERTYUIOP")
    assert result.hits == () and result.note == NOTE_DEGRADED + NOTE_NO_RESULT


def test_index_unavailable_raises_service_exception(kb, caplog):
    _clear(kb["db"])
    knowledge_service.clear_cache()
    caplog.set_level(logging.WARNING)
    with pytest.raises(ServiceException) as exc:
        _search(kb, PLAIN_USER, "智能助手")
    assert isinstance(exc.value, KnowledgeIndexUnavailable)
    assert exc.value.message == knowledge_service.MSG_INDEX_UNAVAILABLE and "智能助手" not in caplog.text


def test_query_length_and_empty_checked_before_embedding(kb):
    with pytest.raises(ServiceException):
        _search(kb, PLAIN_USER, "字" * 201)
    with pytest.raises(ServiceException):
        _search(kb, PLAIN_USER, "   ")
    assert kb["model"].query_calls == 0


def test_search_sees_new_build_after_reindex(kb):
    assert _search(kb, PLAIN_USER, "NEWTOPICMARKER 新增章节").hits == ()
    path = kb["docs"] / "pub.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n## 新增章节\n\nNEWTOPICMARKER 这是新加入的规则说明。\n",
                    encoding="utf-8")
    knowledge_service.build_index(kb["db"], load_docs(kb["docs"]), kb["model"], kb["index"])
    result = _search(kb, PLAIN_USER, "NEWTOPICMARKER 新增章节")
    assert result.hits and result.hits[0].section == "新增章节"
