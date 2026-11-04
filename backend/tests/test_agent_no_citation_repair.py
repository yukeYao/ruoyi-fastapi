"""
引用修复已移除（阶段 4 评估后决定）：回复没有合法引用时保持 uncited，检索到的分块只作为 references 显示，
服务端不会再为补引用多调用一次模型，接口也不再返回 autoSources / citationRepair。
跨轮引用（追问时沿用上一轮的 chunkId）判为 invalidCitations，不进入 sources。
覆盖普通请求、SSE、历史消息一致。模型用 ScriptedChatModel，Embedding 用 FakeEmbeddingModel。
脚本里多放一条"不应该被调用"的回复：如果服务端多调用了一次模型，len(fake.calls) 会暴露出来。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from main import app
from tests.agent_helpers import MENU_USER, add_role, add_user, call, chat, final, tool_msg
from tests.conftest import auth_headers
from tests.test_agent_knowledge import _status, _stream, kb  # noqa: F401  kb 是夹具

client = TestClient(app)

ANSWER = "需要 agent:chat:use 权限。授权后要重新登录。"
KEYS = ("reply", "sources", "references", "invalidCitations", "suspiciousCitations", "knowledgeStatus")
UNUSED = "不应该被调用"


def _search(query="智能助手菜单权限 重新登录", call_id="s1"):
    return tool_msg(call("search_knowledge", {"query": query}, call_id))


def _run(stream: bool, headers: dict, message: str, conversation_id: str | None = None) -> dict:
    if stream:
        assert conversation_id is None
        events = _stream(headers, message)
        assert events[-1][0] == "final", events
        assert all(d.get("node") != "cite" for e, d in events if e == "step")
        return events[-1][1]
    body = chat(client, headers, message, conversation_id)
    assert body["code"] == 200, body
    return body["data"]


def _assert_history_matches(headers, data):
    history = client.get(f"/agent/conversation/{data['conversationId']}/messages", headers=headers).json()["data"]
    last = [m for m in history if m["type"] == "ai" and m["content"]][-1]
    assert last["content"] == data["reply"]
    knowledge = last.get("knowledge") or {}
    for key in KEYS[1:]:
        api_key = {"knowledgeStatus": "status"}.get(key, key)
        assert knowledge.get(api_key) == data[key], key
    assert "autoSources" not in knowledge and "citationRepair" not in knowledge


@pytest.mark.parametrize("stream", [False, True])
def test_uncited_pure_knowledge_reply_stays_uncited(kb, scripted_model, stream):
    fake = scripted_model(_search(), final(ANSWER), final(UNUSED))
    admin = auth_headers(client)
    data = _run(stream, admin, "智能助手需要什么权限")
    assert data["reply"] == ANSWER  # 正文不被改写，也没有插入任何标记
    assert data["knowledgeStatus"] == "uncited" and data["sources"] == [] and data["references"]
    assert "autoSources" not in data and "citationRepair" not in data
    assert len(fake.calls) == 2
    _assert_history_matches(admin, data)


def test_normal_and_sse_give_identical_result(kb, scripted_model):
    admin = auth_headers(client)
    results = []
    for stream in (False, True):
        scripted_model(_search(), final(ANSWER), final(UNUSED))
        results.append(_run(stream, admin, "智能助手需要什么权限"))
    assert {k: results[0][k] for k in KEYS} == {k: results[1][k] for k in KEYS}


@pytest.mark.parametrize("stream", [False, True])
def test_uncited_after_business_tool(kb, scripted_model, stream):
    fake = scripted_model(tool_msg(call("search_knowledge", {"query": "智能助手菜单权限"}, "s1"),
                                   call("list_roles", {}, "r1")),
                          final(ANSWER), final(UNUSED))
    admin = auth_headers(client)
    data = _run(stream, admin, "智能助手需要什么权限，有哪些角色")
    assert data["knowledgeStatus"] == "uncited" and data["sources"] == [] and data["references"]
    assert len(fake.calls) == 2
    _assert_history_matches(admin, data)


def test_wrong_live_conclusion_never_gets_a_source(kb, scripted_model):
    """评估 P02 / 冒烟 R2-2 的情形：实时结论没写引用，不会被挂上知识库来源"""
    admin = auth_headers(client)
    role_id = add_role(client, admin, "kb_nr_r", [MENU_USER])
    add_user(client, admin, "kb_nr_u", [role_id])
    wrong = "kb_nr_u 缺少 agent:chat:use 权限。可能只是需要重新登录。"
    fake = scripted_model(_search("看不到智能助手"),
                          tool_msg(call("check_user_permission", {"user_name": "kb_nr_u", "perm": "agent:chat:use"}, "c1")),
                          final(wrong), final(UNUSED))
    data = chat(client, admin, "为什么 kb_nr_u 看不到智能助手")["data"]
    assert data["reply"] == wrong
    assert data["knowledgeStatus"] == "uncited" and data["sources"] == []
    assert len(fake.calls) == 3


@pytest.mark.parametrize("stream", [False, True])
def test_uncited_after_approval_in_same_turn(kb, scripted_model, stream):
    """同一轮：检索 -> 写操作 -> 批准 -> 回复没写引用。保持 uncited，写操作只执行一次，模型不被多调用"""
    add_user(client, auth_headers(client), "kb_nr_same", [])
    fake = scripted_model(_search(), tool_msg(call("change_user_status", {"user_name": "kb_nr_same", "status": "1"}, "w1")),
                          final("已停用 kb_nr_same。授权后要重新登录。"), final(UNUSED))
    admin = auth_headers(client)
    action_id = chat(client, admin, "停用 kb_nr_same")["data"]["pendingAction"]["actionId"]
    if stream:
        data = _stream(admin, action_id=action_id)[-1][1]
    else:
        data = client.post(f"/agent/action/{action_id}/approve", headers=admin).json()["data"]
    assert data["action"]["status"] == "SUCCEEDED" and data["pendingAction"] is None
    assert data["knowledgeStatus"] == "uncited" and data["references"] and data["sources"] == []
    assert len(fake.calls) == 3
    assert _status("kb_nr_same") == "1"
    db = SessionLocal()
    try:
        assert db.execute(text("SELECT COUNT(*) FROM sys_oper_log WHERE method = '智能助手:change_user_status' "
                               "AND oper_url = :u"), {"u": f"/agent/action/{action_id}/approve"}).scalar_one() == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 追问：上一轮的 chunkId 不能在本轮使用
# ---------------------------------------------------------------------------

def test_followup_without_search_reusing_old_chunk_is_invalid(kb, scripted_model):
    fake = scripted_model(_search(), final("需要 agent:chat:use [kbaccess#01]。"),
                          final("授权后要重新登录 [kbaccess#01]。"), final(UNUSED))
    admin = auth_headers(client)
    first = chat(client, admin, "智能助手需要什么权限")["data"]
    assert first["knowledgeStatus"] == "cited"
    data = chat(client, admin, "那之后呢", first["conversationId"])["data"]
    assert data["knowledgeStatus"] == "no_search" and data["sources"] == [] and data["invalidCitations"] == 1
    assert len(fake.calls) == 3
    _assert_history_matches(admin, data)


def test_followup_with_new_search_still_rejects_old_chunk(kb, scripted_model):
    """第二轮重新检索了，但检索结果里没有上一轮那个分块：沿用的标记仍是无效引用"""
    fake = scripted_model(_search(), final("需要 agent:chat:use [kbaccess#01]。"),
                          _search("QWERTYUIOP 不存在的话题", "s2"), final("沿用 [kbaccess#01]。"), final(UNUSED))
    admin = auth_headers(client)
    first = chat(client, admin, "智能助手需要什么权限")["data"]
    data = chat(client, admin, "再说一遍", first["conversationId"])["data"]
    assert data["sources"] == [] and data["invalidCitations"] == 1
    assert data["knowledgeStatus"] == "not_found"
    assert len(fake.calls) == 4


# ---------------------------------------------------------------------------
# 展示状态：正常引用、无效引用、疑似伪引用在普通请求、SSE、历史消息里一致
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [False, True])
def test_valid_invalid_and_suspicious_citations_consistent(kb, scripted_model, stream):
    reply = "需要 agent:chat:use [kbaccess#01]。另见 [made-up#01] 和【密码错误锁定规则#01】。"
    fake = scripted_model(_search(), final(reply), final(UNUSED))
    admin = auth_headers(client)
    data = _run(stream, admin, "智能助手需要什么权限")
    assert data["reply"] == reply  # 正文不改
    assert [s["chunkId"] for s in data["sources"]] == ["kbaccess#01"] and data["knowledgeStatus"] == "cited"
    assert data["invalidCitations"] == 1 and data["suspiciousCitations"] == 1
    assert len(fake.calls) == 2
    _assert_history_matches(admin, data)


def test_suspicious_citation_without_search_is_reported(kb, scripted_model):
    scripted_model(final("规则见【权限说明#01】。"))
    data = chat(client, auth_headers(client), "规则是什么")["data"]
    assert data["suspiciousCitations"] == 1 and data["sources"] == [] and data["knowledgeStatus"] == "no_search"
