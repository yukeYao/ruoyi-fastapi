"""
知识库引用校验：模型可以在回复里写 [chunkId]，但来源列表只由服务端生成。

合法引用的边界是"本轮"：最后一条用户消息之后、这条模型回复之前，search_knowledge 实际成功返回过的分块。
- 一次对话请求就是一轮；
- 人工审批恢复后（批准/拒绝请求）仍是同一轮的继续：中断前的检索结果保存在 checkpoint 里、可以恢复，
  所以算合法；更早轮次的检索结果不算，即使模型在上下文里"记得"那个 chunkId；
- 只认服务端生成的 ToolMessage（名称为 search_knowledge、对应本轮模型发出的 search_knowledge 调用、ok=true）
  里的 rows，来源的标题、章节也取自这里，不取模型回复里的任何文字；
- 回复里出现、但不在合法集合里的 chunkId 计入 invalidCitations，不进入来源列表。
回复正文保持原样（纯文本显示），前端只渲染这里生成的 sources，不解析回复里的引用或链接。
"""
import json
import re

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

SEARCH_TOOL = "search_knowledge"

# 与 documents.py 的 chunk_id 格式一致：doc_id#两位以上序号。也接受中文方括号
CITATION_PATTERN = re.compile(r"[\[【]\s*([a-z0-9][a-z0-9-]{0,63}#\d{2,4})\s*[\]】]")

STATUS_CITED = "cited"            # 至少有一条合法引用
STATUS_UNCITED = "uncited"        # 本轮检索到了内容，但回复没有合法引用
STATUS_NOT_FOUND = "not_found"    # 本轮检索过，知识库没有找到相关内容
STATUS_UNAVAILABLE = "unavailable"  # 本轮检索都失败了（索引不可用等）
STATUS_NO_SEARCH = "no_search"    # 本轮没有检索，回复里却写了引用（全部无效）


def _parse_rows(message: ToolMessage) -> list[dict] | None:
    """成功的检索结果返回 rows；失败（ok=false、截断成预览、不是合法 JSON）返回 None"""
    try:
        payload = json.loads(message.content if isinstance(message.content, str) else "")
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    rows = (payload.get("untrusted_data") or {}).get("rows")
    return rows if isinstance(rows, list) else None


def cited_ids(text: str) -> list[str]:
    """回复里引用的 chunkId，按首次出现的顺序去重"""
    return list(dict.fromkeys(CITATION_PATTERN.findall(text or "")))


def annotate(messages: list[BaseMessage]) -> dict[int, dict]:
    """
    为有内容的模型回复生成引用信息：{消息下标: {"status", "sources", "invalidCitations"}}。
    本轮没有检索、回复里也没有引用的消息不出现在结果里。
    """
    result: dict[int, dict] = {}
    valid: dict[str, dict] = {}
    search_call_ids: set[str] = set()
    had_hits = had_empty = had_error = False

    for index, message in enumerate(messages):
        if message.type == "human":
            # 新的一轮：之前的检索结果全部作废
            valid, search_call_ids = {}, set()
            had_hits = had_empty = had_error = False
        elif isinstance(message, AIMessage):
            search_call_ids |= {c["id"] for c in message.tool_calls if c.get("name") == SEARCH_TOOL and c.get("id")}
            text = message.content if isinstance(message.content, str) else ""
            cited = cited_ids(text)
            if not text or not (cited or had_hits or had_empty or had_error):
                continue
            sources = [valid[c] for c in cited if c in valid]
            if sources:
                status = STATUS_CITED
            elif had_hits:
                status = STATUS_UNCITED
            elif had_empty:
                status = STATUS_NOT_FOUND
            elif had_error:
                status = STATUS_UNAVAILABLE
            else:
                status = STATUS_NO_SEARCH
            result[index] = {"status": status, "sources": sources,
                             "invalidCitations": sum(1 for c in cited if c not in valid)}
        elif (isinstance(message, ToolMessage) and message.name == SEARCH_TOOL
              and message.tool_call_id in search_call_ids):
            rows = _parse_rows(message) if message.status != "error" else None
            if rows is None:
                had_error = True
                continue
            found = False
            for row in rows:
                chunk_id = row.get("chunkId") if isinstance(row, dict) else None
                # 只接受格式正确的 chunkId；全是格式不对的行等同于没有找到
                if isinstance(chunk_id, str) and CITATION_PATTERN.fullmatch(f"[{chunk_id}]"):
                    found = had_hits = True
                    valid.setdefault(chunk_id, {"chunkId": chunk_id, "docId": str(row.get("docId") or ""),
                                                "title": str(row.get("title") or ""),
                                                "section": str(row.get("section") or "")})
            had_empty = had_empty or not found
    return result
