"""
引用修复（graph.cite_node）：检索到合法分块但回复没有合法引用时，服务端最多发起一次修复调用。
覆盖：修复成功 / 失败、修复调用不新增工具调用也不碰写操作、审批恢复后的修复边界、sources 与 references 分开，
以及普通请求、SSE、历史消息三处一致。模型用 ScriptedChatModel，Embedding 用 FakeEmbeddingModel。
"""
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.core.database import SessionLocal
from main import app
from tests.agent_helpers import add_user, call, chat, final, tool_msg
from tests.conftest import auth_headers
from tests.test_agent_knowledge import _status, _stream, kb  # noqa: F401  kb 是夹具

client = TestClient(app)

ANSWER = "需要 agent:chat:use 权限。授权后要重新登录。"


def _search(query="智能助手菜单权限 重新登录", call_id="s1"):
    return tool_msg(call("search_knowledge", {"query": query}, call_id))


def _repair(seen: dict, pick=lambda chunks: [chunks[0]["chunkId"]], sentence=1):
    """修复调用的脚本：记录服务端发来的输入，按 pick 选分块"""
    def reply(messages, tools):
        payload = json.loads(messages[-1].content)
        seen["payload"] = payload
        seen["tools"] = list(tools)
        return AIMessage(content=json.dumps({"citations": [{"sentence": sentence, "chunkIds": pick(payload["chunks"])}]}))
    return reply


def _run(stream: bool, headers: dict, message: str) -> dict:
    if stream:
        events = _stream(headers, message)
        assert events[-1][0] == "final", events
        return events[-1][1]
    body = chat(client, headers, message)
    assert body["code"] == 200, body
    return body["data"]


def _history_last_ai(headers, conversation_id):
    history = client.get(f"/agent/conversation/{conversation_id}/messages", headers=headers).json()["data"]
    return [m for m in history if m["type"] == "ai"][-1]


@pytest.mark.parametrize("stream", [False, True])
def test_repair_success_consistent_across_normal_sse_history(kb, scripted_model, stream):
    seen = {}
    fake = scripted_model(_search(), final(ANSWER), _repair(seen))
    admin = auth_headers(client)
    data = _run(stream, admin, "智能助手需要什么权限")

    chunk = seen["payload"]["chunks"][0]["chunkId"]
    # 正文一个字不变，只在第 1 句句末标点前插入 [chunkId]
    assert data["reply"] == ANSWER.replace("权限。", f"权限[{chunk}]。", 1)
    assert data["knowledgeStatus"] == "cited" and data["citationRepair"] == "succeeded"
    assert [s["chunkId"] for s in data["sources"]] == [chunk] and data["invalidCitations"] == 0
    # 其余检索到的分块是"参考资料"，与来源分开
    assert chunk not in {r["chunkId"] for r in data["references"]}
    assert {r["chunkId"] for r in data["references"]} == {c["chunkId"] for c in seen["payload"]["chunks"]} - {chunk}
    # 修复调用看到的句子就是原回复
    assert "".join(s["text"] for s in seen["payload"]["sentences"]) == ANSWER
    assert len(fake.calls) == 3

    # 修复后的回复替换了原回复（同一条消息），历史接口给出同样的来源和参考资料
    last = _history_last_ai(admin, data["conversationId"])
    assert last["content"] == data["reply"]
    for key in ("sources", "references", "status", "citationRepair", "invalidCitations"):
        assert last["knowledge"][key] == data[{"status": "knowledgeStatus"}.get(key, key)]
    history = client.get(f"/agent/conversation/{data['conversationId']}/messages", headers=admin).json()["data"]
    assert sum(1 for m in history if m["type"] == "ai" and m["content"]) == 1


def test_normal_and_sse_give_identical_repair_result(kb, scripted_model):
    admin = auth_headers(client)
    results = []
    for stream in (False, True):
        scripted_model(_search(), final(ANSWER), _repair({}))
        results.append(_run(stream, admin, "智能助手需要什么权限"))
    keys = ("reply", "sources", "references", "invalidCitations", "knowledgeStatus", "citationRepair")
    assert {k: results[0][k] for k in keys} == {k: results[1][k] for k in keys}


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
    assert data["knowledgeStatus"] == "uncited" and data["citationRepair"] == "failed"
    assert data["sources"] == [] and data["references"]  # 检索结果只作为参考资料，不会被标成已引用
    assert _history_last_ai(admin, data["conversationId"])["knowledge"]["citationRepair"] == "failed"


def test_repair_model_error_is_recorded_not_raised(kb, scripted_model):
    def boom(messages, tools):
        raise RuntimeError("upstream timeout sk-REPAIR-CANARY")

    scripted_model(_search(), final(ANSWER), boom)
    body = chat(client, auth_headers(client), "智能助手需要什么权限")
    assert body["code"] == 200
    assert body["data"]["citationRepair"] == "failed" and body["data"]["reply"] == ANSWER
    assert "CANARY" not in json.dumps(body, ensure_ascii=False)


def test_repair_cannot_add_tool_calls_or_writes(kb, scripted_model):
    """修复调用返回工具调用（例如停用账号）：一律忽略，不执行、不生成审批，消息里也不出现新的工具调用"""
    add_user(client, auth_headers(client), "kb_rep_victim", [])
    sneaky = AIMessage(content="", tool_calls=[call("change_user_status", {"user_name": "kb_rep_victim", "status": "1"}, "x1")])
    scripted_model(_search(), final(ANSWER), sneaky)
    admin = auth_headers(client)
    data = chat(client, admin, "智能助手需要什么权限")["data"]
    assert data["citationRepair"] == "failed" and data["pendingAction"] is None
    assert [c["name"] for m in data["messages"] if m["type"] == "ai" for c in m["toolCalls"]] == ["search_knowledge"]
    assert not any(m["type"] == "tool" and m["toolCallId"] == "x1" for m in data["messages"])
    assert _status("kb_rep_victim") == "0"
    db = SessionLocal()
    try:
        from sqlalchemy import text
        assert db.execute(text("SELECT COUNT(*) FROM agent_action WHERE conversation_id = :c"),
                          {"c": data["conversationId"]}).scalar_one() == 0
    finally:
        db.close()


def test_repair_call_input_is_isolated(kb, scripted_model):
    """
    修复调用的输入只有修复提示和一条 JSON（句子 + 本轮分块），没有对话历史和工具定义。
    "不绑定工具"由 cite_node 直接调用 ctx.model.invoke 保证（真实模型的 bind_tools 返回新对象，不改原模型）；
    即使模型仍返回工具调用也会被忽略，见 test_repair_cannot_add_tool_calls_or_writes
    """
    seen = {}
    fake = scripted_model(_search(), final(ANSWER), _repair(seen))
    chat(client, auth_headers(client), "智能助手需要什么权限")
    # 修复调用的输入只有系统提示和一条 JSON：没有对话历史，也就看不到之前的工具结果
    repair_input = fake.calls[-1]
    assert len(repair_input) == 2 and repair_input[0].type == "system" and repair_input[1].type == "human"


@pytest.mark.parametrize("script_tail", [
    [final("需要 agent:chat:use [kbaccess#01]。")],   # 已经有合法引用
])
def test_no_repair_when_already_cited(kb, scripted_model, script_tail):
    fake = scripted_model(_search(), *script_tail, final("不应该被调用"))
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
# 审批恢复后的修复边界
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [False, True])
def test_repair_after_approval_uses_same_turn_chunks_and_executes_once(kb, scripted_model, stream):
    """同一轮：检索 -> 写操作 -> 批准 -> 回复没写引用 -> 修复。修复只用本轮中断前检索到的分块，写操作只执行一次"""
    add_user(client, auth_headers(client), "kb_rep_same", [])
    seen = {}
    scripted_model(_search(), tool_msg(call("change_user_status", {"user_name": "kb_rep_same", "status": "1"}, "w1")),
                   final("已停用 kb_rep_same。授权后要重新登录。"), _repair(seen, sentence=2))
    admin = auth_headers(client)
    first = chat(client, admin, "停用 kb_rep_same")["data"]
    retrieved = {r["chunkId"] for r in json.loads(
        next(m for m in first["messages"] if m["type"] == "tool")["content"])["untrusted_data"]["rows"]}
    action_id = first["pendingAction"]["actionId"]
    if stream:
        data = _stream(admin, action_id=action_id)[-1][1]
    else:
        data = client.post(f"/agent/action/{action_id}/approve", headers=admin).json()["data"]

    assert data["action"]["status"] == "SUCCEEDED" and data["pendingAction"] is None
    assert data["citationRepair"] == "succeeded" and data["knowledgeStatus"] == "cited"
    assert {c["chunkId"] for c in seen["payload"]["chunks"]} == retrieved
    assert _status("kb_rep_same") == "1"
    db = SessionLocal()
    try:
        from sqlalchemy import text
        assert db.execute(text("SELECT COUNT(*) FROM agent_action WHERE conversation_id = :c"),
                          {"c": data["conversationId"]}).scalar_one() == 1
        # 写操作日志只有一条：修复调用不会重新执行 execute
        assert db.execute(text("SELECT COUNT(*) FROM sys_oper_log WHERE method = '智能助手:change_user_status' "
                               "AND oper_url = :u"), {"u": f"/agent/action/{action_id}/approve"}).scalar_one() == 1
    finally:
        db.close()


def test_no_repair_with_chunks_from_earlier_turn(kb, scripted_model):
    """上一轮检索到的分块：下一轮审批恢复后的回复不能用它们修复（本轮没有检索结果就不发起修复）"""
    add_user(client, auth_headers(client), "kb_rep_old", [])
    fake = scripted_model(_search(), final("需要 agent:chat:use [kbaccess#01]。"),
                          tool_msg(call("change_user_status", {"user_name": "kb_rep_old", "status": "1"}, "w1")),
                          final("已停用。授权后要重新登录。"), final("不应该被调用"))
    admin = auth_headers(client)
    first = chat(client, admin, "智能助手需要什么权限")["data"]
    second = chat(client, admin, "停用 kb_rep_old", first["conversationId"])["data"]
    data = client.post(f"/agent/action/{second['pendingAction']['actionId']}/approve", headers=admin).json()["data"]
    assert data["action"]["status"] == "SUCCEEDED"
    assert data["knowledgeStatus"] is None and data["citationRepair"] is None and data["sources"] == []
    assert len(fake.calls) == 4  # 没有第 5 次（修复）调用
