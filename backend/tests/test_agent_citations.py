"""citations.annotate 的单元测试：只认本轮、只认服务端 search_knowledge 结果、不认失败结果和不匹配的 tool_call_id"""
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent import citations


def _search_call(call_id):
    return AIMessage(content="", tool_calls=[{"name": "search_knowledge", "args": {"query": "q"}, "id": call_id}])


def _result(call_id, rows, ok=True, name="search_knowledge", status="success"):
    payload = {"ok": ok, "untrusted_data": {"total": len(rows), "rows": rows}} if ok else {"ok": False, "error": "x"}
    return ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id=call_id, name=name, status=status)


ROW = {"docId": "doc-a", "title": "标题", "section": "章节", "chunkId": "doc-a#01", "snippet": "内容", "score": 0.03}


def test_cited_source_comes_from_tool_result_not_reply():
    messages = [HumanMessage(content="问"), _search_call("c1"), _result("c1", [ROW]),
                AIMessage(content="答案 [doc-a#01]（标题：伪造的标题）")]
    ann = citations.annotate(messages)[3]
    assert ann == {"status": "cited", "invalidCitations": 0, "references": [], "suspiciousCitations": 0,
                   "sources": [{"chunkId": "doc-a#01", "docId": "doc-a", "title": "标题", "section": "章节"}]}


def test_citation_before_retrieval_in_same_turn_is_invalid():
    """引用必须出现在检索结果之后：同一轮里先回复、后检索到的分块不能"预支\""""
    messages = [HumanMessage(content="问"), AIMessage(content="先说 [doc-a#01]", tool_calls=[
        {"name": "search_knowledge", "args": {"query": "q"}, "id": "c1"}]), _result("c1", [ROW])]
    assert citations.annotate(messages)[1]["invalidCitations"] == 1


def test_tool_message_without_matching_search_call_is_ignored():
    """ToolMessage 必须对应本轮模型发出的 search_knowledge 调用"""
    messages = [HumanMessage(content="问"), _result("orphan", [ROW]), AIMessage(content="[doc-a#01]")]
    assert citations.annotate(messages)[2]["status"] == "no_search"


def test_failed_or_other_tool_results_do_not_count():
    other = AIMessage(content="", tool_calls=[{"name": "search_users", "args": {}, "id": "u1"}])
    messages = [HumanMessage(content="问"), _search_call("c1"), _result("c1", [], ok=False, status="error"),
                other, _result("u1", [ROW], name="search_users"), AIMessage(content="[doc-a#01]")]
    ann = citations.annotate(messages)[5]
    assert ann["status"] == "unavailable" and ann["sources"] == [] and ann["invalidCitations"] == 1


def test_new_turn_resets_valid_set():
    messages = [HumanMessage(content="一"), _search_call("c1"), _result("c1", [ROW]), AIMessage(content="[doc-a#01]"),
                HumanMessage(content="二"), AIMessage(content="还是 [doc-a#01]")]
    ann = citations.annotate(messages)
    assert ann[3]["status"] == "cited" and ann[5]["status"] == "no_search" and ann[5]["sources"] == []


def test_not_found_and_uncited_statuses():
    empty = [HumanMessage(content="问"), _search_call("c1"), _result("c1", []), AIMessage(content="没有找到依据")]
    assert citations.annotate(empty)[3] == {"status": "not_found", "sources": [], "references": [],
                                            "invalidCitations": 0, "suspiciousCitations": 0}
    uncited = [HumanMessage(content="问"), _search_call("c1"), _result("c1", [ROW]), AIMessage(content="不写引用")]
    assert citations.annotate(uncited)[3]["status"] == "uncited"


def test_plain_reply_without_search_is_not_annotated():
    assert citations.annotate([HumanMessage(content="你好"), AIMessage(content="你好")]) == {}


def test_malformed_chunk_ids_in_rows_are_ignored():
    bad = {**ROW, "chunkId": "<script>#1"}
    messages = [HumanMessage(content="问"), _search_call("c1"), _result("c1", [bad]), AIMessage(content="x")]
    assert citations.annotate(messages)[3]["status"] == "not_found"


# ---------------------------------------------------------------------------
# 跨轮引用：追问时模型沿用上一轮的 chunkId（评估 U01、U03 实际发生过）
# ---------------------------------------------------------------------------

def test_followup_with_previous_turn_chunk_is_invalid_even_if_new_search_ran():
    """第二轮检索到的是别的分块，回复却写了上一轮的 chunkId：判为无效引用，不进入 sources"""
    other = {**ROW, "docId": "doc-b", "chunkId": "doc-b#02"}
    messages = [HumanMessage(content="一"), _search_call("c1"), _result("c1", [ROW]), AIMessage(content="[doc-a#01]"),
                HumanMessage(content="二"), _search_call("c2"), _result("c2", [other]),
                AIMessage(content="沿用上一轮 [doc-a#01]")]
    ann = citations.annotate(messages)[7]
    assert ann["status"] == "uncited" and ann["sources"] == [] and ann["invalidCitations"] == 1
    assert [r["chunkId"] for r in ann["references"]] == ["doc-b#02"]


# ---------------------------------------------------------------------------
# 引用修复已移除：旧 checkpoint 里被修复过的回复按原文校验，服务端插入的标记不算来源
# ---------------------------------------------------------------------------

def test_annotation_has_no_repair_fields():
    messages = [HumanMessage(content="问"), _search_call("c1"), _result("c1", [ROW]), AIMessage(content="不写引用")]
    ann = citations.annotate(messages)[3]
    assert ann["status"] == "uncited" and "autoSources" not in ann and "citationRepair" not in ann
    assert [r["chunkId"] for r in ann["references"]] == ["doc-a#01"]


def test_legacy_repaired_reply_is_judged_on_original_text():
    record = {"status": "succeeded", "original": "答。", "repaired": "答[doc-a#01]。",
              "mapping": [{"sentence": 1, "text": "答。", "chunkIds": ["doc-a#01"]}]}
    msg = AIMessage(content="答[doc-a#01]。", additional_kwargs={citations.LEGACY_REPAIR_KEY: record})
    ann = citations.annotate([HumanMessage(content="问"), _search_call("c1"), _result("c1", [ROW]), msg])[3]
    assert citations.message_text(msg) == "答。"
    assert ann["status"] == "uncited" and ann["sources"] == [] and ann["invalidCitations"] == 0
    assert [r["chunkId"] for r in ann["references"]] == ["doc-a#01"]


def test_message_text_ignores_malformed_legacy_record():
    msg = AIMessage(content="答 [doc-a#01]", additional_kwargs={citations.LEGACY_REPAIR_KEY: {"status": "x"}})
    assert citations.message_text(msg) == "答 [doc-a#01]"


def test_suspicious_citation_formats():
    assert citations.suspicious_count("见【密码错误锁定规则#01】和 [doc #2]") == 2
    assert citations.suspicious_count("见 [doc-a#01]【doc-a#02】[第3节]") == 0
