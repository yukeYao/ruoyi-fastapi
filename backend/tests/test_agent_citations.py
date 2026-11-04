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
    assert ann == {"status": "cited", "invalidCitations": 0,
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
    assert citations.annotate(empty)[3] == {"status": "not_found", "sources": [], "invalidCitations": 0}
    uncited = [HumanMessage(content="问"), _search_call("c1"), _result("c1", [ROW]), AIMessage(content="不写引用")]
    assert citations.annotate(uncited)[3]["status"] == "uncited"


def test_plain_reply_without_search_is_not_annotated():
    assert citations.annotate([HumanMessage(content="你好"), AIMessage(content="你好")]) == {}


def test_malformed_chunk_ids_in_rows_are_ignored():
    bad = {**ROW, "chunkId": "<script>#1"}
    messages = [HumanMessage(content="问"), _search_call("c1"), _result("c1", [bad]), AIMessage(content="x")]
    assert citations.annotate(messages)[3]["status"] == "not_found"
