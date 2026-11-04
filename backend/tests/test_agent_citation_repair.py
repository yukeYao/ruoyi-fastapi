"""
引用修复（graph.cite_node）：只用于纯知识问答（本轮只调用过 search_knowledge）。检索到合法分块但回复没有合法引用时，
服务端最多发起一次修复调用；成功后状态是 auto_cited（自动关联，未核验），补上的分块在 autoSources，不在 sources。
本轮调用过任何业务工具（包括写工具、审批恢复的同一轮）就不修复，保持 uncited。
覆盖普通请求、SSE、历史消息一致。模型用 ScriptedChatModel，Embedding 用 FakeEmbeddingModel。
"""
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import text

from app.core.database import SessionLocal
from main import app
from tests.agent_helpers import MENU_AGENT, MENU_ROLE, MENU_USER, add_role, add_user, call, chat, final, tool_msg
from tests.conftest import auth_headers
from tests.test_agent_knowledge import _status, _stream, kb  # noqa: F401  kb 是夹具

client = TestClient(app)

ANSWER = "需要 agent:chat:use 权限。授权后要重新登录。"
KEYS = ("reply", "sources", "autoSources", "references", "invalidCitations", "suspiciousCitations",
        "knowledgeStatus", "citationRepair")


def _search(query="智能助手菜单权限 重新登录", call_id="s1"):
    return tool_msg(call("search_knowledge", {"query": query}, call_id))


def _repair(seen: dict, pick=lambda chunks: [chunks[0]["chunkId"]], sentence=1):
    """修复调用的脚本：记录服务端发来的输入，按 pick 选分块"""
    def reply(messages, tools):
        seen["payload"] = json.loads(messages[-1].content)
        seen["input"] = list(messages)
        return AIMessage(content=json.dumps({"citations": [{"sentence": sentence, "chunkIds": pick(seen["payload"]["chunks"])}]}))
    return reply


def _run(stream: bool, headers: dict, message: str) -> dict:
    if stream:
        events = _stream(headers, message)
        assert events[-1][0] == "final", events
        return events[-1][1]
    body = chat(client, headers, message)
    assert body["code"] == 200, body
    return body["data"]


def _history_knowledge(headers, conversation_id):
    history = client.get(f"/agent/conversation/{conversation_id}/messages", headers=headers).json()["data"]
    return [m for m in history if m["type"] == "ai" and m["content"]][-1]


def _assert_history_matches(headers, data):
    last = _history_knowledge(headers, data["conversationId"])
    assert last["content"] == data["reply"]
    knowledge = last.get("knowledge") or {}
    for key in KEYS[1:]:
        api_key = {"knowledgeStatus": "status"}.get(key, key)
        assert knowledge.get(api_key) == data[key], key


# ---------------------------------------------------------------------------
# 纯知识问答：修复成功 -> auto_cited，保存原文和映射
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [False, True])
def test_pure_knowledge_repair_is_auto_cited_with_original_and_mapping(kb, scripted_model, stream):
    seen = {}
    fake = scripted_model(_search(), final(ANSWER), _repair(seen))
    admin = auth_headers(client)
    data = _run(stream, admin, "智能助手需要什么权限")

    chunk = seen["payload"]["chunks"][0]["chunkId"]
    repaired = ANSWER.replace("权限。", f"权限[{chunk}]。", 1)
    assert data["reply"] == repaired
    # 自动关联的分块不算普通来源
    assert data["knowledgeStatus"] == "auto_cited" and data["sources"] == []
    assert [s["chunkId"] for s in data["autoSources"]] == [chunk]
    assert chunk not in {r["chunkId"] for r in data["references"]}
    repair = data["citationRepair"]
    assert repair["status"] == "succeeded" and repair["original"] == ANSWER and repair["repaired"] == repaired
    assert repair["mapping"] == [{"sentence": 1, "text": "需要 agent:chat:use 权限。", "chunkIds": [chunk]}]
    assert {"modelName", "inputTokens", "outputTokens"} <= set(repair)
    # 修复调用的输入只有修复提示和一条 JSON：没有对话历史，也就看不到工具定义和其他工具结果
    assert len(seen["input"]) == 2 and seen["input"][0].type == "system"
    assert len(fake.calls) == 3
    _assert_history_matches(admin, data)


def test_normal_and_sse_give_identical_repair_result(kb, scripted_model):
    admin = auth_headers(client)
    results = []
    for stream in (False, True):
        scripted_model(_search(), final(ANSWER), _repair({}))
        results.append(_run(stream, admin, "智能助手需要什么权限"))
    assert {k: results[0][k] for k in KEYS} == {k: results[1][k] for k in KEYS}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("bad_output", [
    "这不是 JSON",
    json.dumps({"citations": [{"sentence": 1, "chunkIds": ["made-up#01"]}]}),   # 只给了不合法的 chunkId
    json.dumps({"citations": [{"sentence": 99, "chunkIds": ["kbaccess#01"]}]}),  # 句子编号越界
])
def test_repair_failure_keeps_uncited(kb, scripted_model, stream, bad_output):
    scripted_model(_search(), final(ANSWER), AIMessage(content=bad_output))
    admin = auth_headers(client)
    data = _run(stream, admin, "智能助手需要什么权限")
    assert data["reply"] == ANSWER
    assert data["knowledgeStatus"] == "uncited" and data["citationRepair"]["status"] == "failed"
    assert data["citationRepair"]["original"] == ANSWER and data["citationRepair"]["mapping"] == []
    assert data["sources"] == [] and data["autoSources"] == [] and data["references"]
    _assert_history_matches(admin, data)


def test_repair_model_error_is_recorded_not_raised(kb, scripted_model):
    def boom(messages, tools):
        raise RuntimeError("upstream timeout sk-REPAIR-CANARY")

    scripted_model(_search(), final(ANSWER), boom)
    body = chat(client, auth_headers(client), "智能助手需要什么权限")
    assert body["code"] == 200
    assert body["data"]["citationRepair"]["status"] == "failed" and body["data"]["reply"] == ANSWER
    assert "CANARY" not in json.dumps(body, ensure_ascii=False)


def test_repair_cannot_add_tool_calls_or_writes(kb, scripted_model):
    """修复调用返回工具调用（例如停用账号）：一律忽略，不执行、不生成审批，消息里也不出现新的工具调用"""
    add_user(client, auth_headers(client), "kb_rep_victim", [])
    sneaky = AIMessage(content="", tool_calls=[call("change_user_status", {"user_name": "kb_rep_victim", "status": "1"}, "x1")])
    scripted_model(_search(), final(ANSWER), sneaky)
    data = chat(client, auth_headers(client), "智能助手需要什么权限")["data"]
    assert data["citationRepair"]["status"] == "failed" and data["pendingAction"] is None
    assert [c["name"] for m in data["messages"] if m["type"] == "ai" for c in m["toolCalls"]] == ["search_knowledge"]
    assert not any(m["type"] == "tool" and m["toolCallId"] == "x1" for m in data["messages"])
    assert _status("kb_rep_victim") == "0"
    db = SessionLocal()
    try:
        assert db.execute(text("SELECT COUNT(*) FROM agent_action WHERE conversation_id = :c"),
                          {"c": data["conversationId"]}).scalar_one() == 0
    finally:
        db.close()


def test_no_repair_when_already_cited(kb, scripted_model):
    fake = scripted_model(_search(), final("需要 agent:chat:use [kbaccess#01]。"), final("不应该被调用"))
    data = chat(client, auth_headers(client), "智能助手需要什么权限")["data"]
    assert data["knowledgeStatus"] == "cited" and data["citationRepair"] is None and len(fake.calls) == 2


def test_no_repair_without_hits_or_search(kb, scripted_model):
    fake = scripted_model(final("你好"), final("不应该被调用"))
    data = chat(client, auth_headers(client), "你好")["data"]
    assert data["knowledgeStatus"] is None and len(fake.calls) == 1

    fake = scripted_model(_search("QWERTYUIOP 不存在的话题"), final("知识库中没有找到依据"), final("不应该被调用"))
    data = chat(client, auth_headers(client), "随便问问")["data"]
    assert data["knowledgeStatus"] == "not_found" and data["citationRepair"] is None and len(fake.calls) == 2


# ---------------------------------------------------------------------------
# 用过业务工具就不修复（确定性规则）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [False, True])
def test_no_repair_after_business_tool(kb, scripted_model, stream):
    fake = scripted_model(tool_msg(call("search_knowledge", {"query": "智能助手菜单权限"}, "s1"),
                                   call("list_roles", {}, "r1")),
                          final(ANSWER), final("不应该被调用"))
    admin = auth_headers(client)
    data = _run(stream, admin, "智能助手需要什么权限，有哪些角色")
    assert data["knowledgeStatus"] == "uncited" and data["citationRepair"] is None
    assert data["sources"] == [] and data["autoSources"] == [] and data["references"]
    assert len(fake.calls) == 2
    _assert_history_matches(admin, data)


def test_r2_style_wrong_conclusion_gets_no_auto_citation(kb, scripted_model):
    """
    复现冒烟 R2-2：查了别的用户和别的角色，回复把它说成目标用户的情况、且没写引用。
    用过业务工具，所以不修复：错误结论不会挂上知识库来源，检索片段只作为参考资料
    """
    admin = auth_headers(client)
    role_id = add_role(client, admin, "kb_r2_r", [MENU_USER])
    add_user(client, admin, "kb_r2_u", [role_id])
    wrong = "kb_r2_u 的角色是普通角色，拥有 agent:chat:use 权限。可能只是需要重新登录。"
    fake = scripted_model(_search("看不到智能助手"),
                          tool_msg(call("get_user_detail", {"user_name": "admin"}, "u1")),
                          tool_msg(call("get_role_permissions", {"role_name": "普通角色", "perm": "agent:chat:use"}, "r1")),
                          final(wrong), AIMessage(content=json.dumps({"citations": [{"sentence": 1, "chunkIds": ["kbaccess#01"]}]})))
    data = chat(client, admin, "为什么 kb_r2_u 看不到智能助手")["data"]
    assert data["reply"] == wrong
    assert data["knowledgeStatus"] == "uncited" and data["citationRepair"] is None
    assert data["sources"] == [] and data["autoSources"] == []
    assert len(fake.calls) == 4  # 第 5 项（修复）没有被调用


@pytest.mark.parametrize("stream", [False, True])
def test_no_repair_after_approval_in_same_turn(kb, scripted_model, stream):
    """同一轮：检索 -> 写操作 -> 批准 -> 回复没写引用。写工具也是业务工具：不修复，写操作只执行一次"""
    add_user(client, auth_headers(client), "kb_rep_same", [])
    fake = scripted_model(_search(), tool_msg(call("change_user_status", {"user_name": "kb_rep_same", "status": "1"}, "w1")),
                          final("已停用 kb_rep_same。授权后要重新登录。"), final("不应该被调用"))
    admin = auth_headers(client)
    action_id = chat(client, admin, "停用 kb_rep_same")["data"]["pendingAction"]["actionId"]
    if stream:
        data = _stream(admin, action_id=action_id)[-1][1]
    else:
        data = client.post(f"/agent/action/{action_id}/approve", headers=admin).json()["data"]
    assert data["action"]["status"] == "SUCCEEDED" and data["pendingAction"] is None
    assert data["knowledgeStatus"] == "uncited" and data["citationRepair"] is None and data["references"]
    assert len(fake.calls) == 3
    assert _status("kb_rep_same") == "1"
    db = SessionLocal()
    try:
        assert db.execute(text("SELECT COUNT(*) FROM sys_oper_log WHERE method = '智能助手:change_user_status' "
                               "AND oper_url = :u"), {"u": f"/agent/action/{action_id}/approve"}).scalar_one() == 1
    finally:
        db.close()


def test_no_repair_with_chunks_from_earlier_turn(kb, scripted_model):
    """上一轮检索到的分块：下一轮的回复不能用它们修复"""
    fake = scripted_model(_search(), final("需要 agent:chat:use [kbaccess#01]。"), final("授权后要重新登录。"),
                          final("不应该被调用"))
    admin = auth_headers(client)
    first = chat(client, admin, "智能助手需要什么权限")["data"]
    data = chat(client, admin, "那之后呢", first["conversationId"])["data"]
    assert data["knowledgeStatus"] is None and data["citationRepair"] is None and data["sources"] == []
    assert len(fake.calls) == 3


# ---------------------------------------------------------------------------
# 展示状态：正常引用、无效引用、疑似伪引用在普通请求、SSE、历史消息里一致
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [False, True])
def test_valid_invalid_and_suspicious_citations_consistent(kb, scripted_model, stream):
    reply = "需要 agent:chat:use [kbaccess#01]。另见 [made-up#01] 和【密码错误锁定规则#01】。"
    fake = scripted_model(_search(), final(reply), final("不应该被调用"))
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
