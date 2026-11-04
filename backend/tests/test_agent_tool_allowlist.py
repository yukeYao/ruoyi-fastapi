"""
运行时工具白名单（AgentContext.tool_names）与知识库检索方式（knowledge_retrieval_mode）。
二者都只用于评估对照方案：白名单只会在权限过滤的基础上收窄，不在其中的工具不绑定，硬调用（读或写）按未知工具拒绝。
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.agent import policy
from app.config import settings
from app.core.exceptions import ServiceException
from app.rag import embedding
from app.services import agent_service, knowledge_service
from main import app
from tests.agent_helpers import add_user, call, chat, final, tool_msg, tool_results
from tests.conftest import auth_headers
from tests.test_agent_knowledge import _status, kb  # noqa: F401  kb 是夹具

client = TestClient(app)


@pytest.fixture
def only(monkeypatch):
    """把本次运行的工具白名单设成给定集合（与评估运行器的做法一致）"""
    def _set(names):
        original = agent_service.build_context
        monkeypatch.setattr(agent_service, "build_context",
                            lambda *a, **kw: original(*a, **kw, tool_names=frozenset(names)))
    return _set


def test_allowlist_limits_bound_tools(scripted_model, only):
    only({"search_knowledge"})
    fake = scripted_model(final("好的"))
    chat(client, auth_headers(client), "你好")
    assert fake.bound_tool_names == ["search_knowledge"]


def test_allowlist_never_widens_permissions(scripted_model, only):
    """白名单里有、但没有权限的工具仍然不绑定"""
    from tests.agent_helpers import MENU_AGENT, user_with_menus

    only({"search_knowledge", "search_users"})
    fake = scripted_model(final("好的"))
    _, headers = user_with_menus(client, "allow", [MENU_AGENT])
    chat(client, headers, "你好")
    assert fake.bound_tool_names == ["search_knowledge"]


def test_hard_call_outside_allowlist_rejected(scripted_model, only):
    add_user(client, auth_headers(client), "kb_allow_victim", [])
    only({"search_knowledge"})
    read = call("search_users", {"user_name": "admin"}, "r1")
    scripted_model(tool_msg(read), tool_msg(call("change_user_status", {"user_name": "kb_allow_victim", "status": "1"}, "w1")),
                   final("好的"))
    body = chat(client, auth_headers(client), "查一下再停用")
    results = tool_results(body)
    assert json.loads(results["r1"]["content"]) == {"ok": False, "error": policy.MSG_UNKNOWN_TOOL}
    assert json.loads(results["w1"]["content"]) == {"ok": False, "error": policy.MSG_UNKNOWN_TOOL}
    assert body["data"]["pendingAction"] is None and _status("kb_allow_victim") == "0"


def test_allowlist_excluding_knowledge(scripted_model, only):
    only(set(policy.TOOLS) - {"search_knowledge"})
    fake = scripted_model(tool_msg(call("search_knowledge", {"query": "智能助手"}, "s1")), final("好的"))
    body = chat(client, auth_headers(client), "你好")
    assert "search_knowledge" not in fake.bound_tool_names and "search_users" in fake.bound_tool_names
    assert json.loads(tool_results(body)["s1"]["content"])["error"] == policy.MSG_UNKNOWN_TOOL


# ---------------------------------------------------------------------------
# 检索方式
# ---------------------------------------------------------------------------

ADMIN = {"user_id": 1, "permissions": ["*:*:*"]}


def _search(kb, **kw):
    kb.rollback()
    return knowledge_service.search_knowledge(kb, ADMIN, "智能助手菜单权限 agent:chat:use", **kw)


# 夹具只有几篇小文档，BM25 分数远低于按正式知识库校准的生产门槛（混合 τb=15 / 5）；
# 这里测的是检索方式开关，不是门槛，所以显式给一个夹具尺度的关键词门槛
FIXTURE_BM25 = knowledge_service.Thresholds(1e9, 0.0, 1)


def test_bm25_mode_never_calls_embedding(kb, monkeypatch):
    def boom():
        raise AssertionError("bm25 模式不应调用 Embedding")

    monkeypatch.setattr(embedding, "get_embedding_model", boom)
    result = _search(kb, mode="bm25", thresholds=FIXTURE_BM25)
    assert result.hits and not result.degraded
    assert all(h.vector_score is None for h in result.hits)


def test_vector_mode_skips_bm25(kb, monkeypatch):
    monkeypatch.setattr(knowledge_service, "bm25_search", lambda *a: (_ for _ in ()).throw(AssertionError("不应调用 BM25")))
    result = _search(kb, mode="vector", thresholds=knowledge_service.Thresholds(-1.0, 1e9, 99))
    assert result.hits and all(h.bm25_score == 0 for h in result.hits)


def test_vector_mode_failure_has_no_keyword_fallback(kb, monkeypatch):
    monkeypatch.setattr(embedding, "get_embedding_model", lambda: (_ for _ in ()).throw(embedding.EmbeddingError("x")))
    result = _search(kb, mode="vector")
    assert result.hits == () and result.degraded
    assert result.note == knowledge_service.NOTE_VECTOR_UNAVAILABLE + knowledge_service.NOTE_NO_RESULT


def test_mode_from_settings_and_invalid_mode(kb, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_retrieval_mode", "bm25")
    monkeypatch.setattr(embedding, "get_embedding_model", lambda: (_ for _ in ()).throw(AssertionError("不应调用")))
    assert _search(kb, thresholds=FIXTURE_BM25).hits
    with pytest.raises(ServiceException):
        _search(kb, mode="sql")


def test_default_mode_is_hybrid():
    assert settings.knowledge_retrieval_mode == "hybrid"
