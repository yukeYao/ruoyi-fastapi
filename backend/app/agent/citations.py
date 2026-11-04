"""
知识库引用校验：模型可以在回复里写 [chunkId]，但来源列表只由服务端生成。

合法引用的边界是"本轮"：最后一条用户消息之后、这条模型回复之前，search_knowledge 实际成功返回过的分块。
- 一次对话请求就是一轮；
- 人工审批恢复后（批准/拒绝请求）仍是同一轮的继续：中断前的检索结果保存在 checkpoint 里、可以恢复，
  所以算合法；更早轮次的检索结果不算，即使模型在上下文里"记得"那个 chunkId；
- 只认服务端生成的 ToolMessage（名称为 search_knowledge、对应本轮模型发出的 search_knowledge 调用、ok=true）
  里的 rows，来源的标题、章节也取自这里，不取模型回复里的任何文字；
- 回复里出现、但不在合法集合里的 chunkId 计入 invalidCitations，不进入来源列表；
- 本轮检索到、但回复没有引用的分块列为 references（"检索到但未被引用的参考资料"），与 sources 分开，
  不是回答的依据。
回复正文保持纯文本显示，前端只渲染这里生成的 sources / references，不解析回复里的引用或链接。

引用修复（graph.cite_node）由服务端在回复写进 additional_kwargs[REPAIR_KEY]，这里只读出来展示，不影响任何判断。
"""
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

SEARCH_TOOL = "search_knowledge"
REPAIR_KEY = "citation_repair"
REPAIR_SUCCEEDED = "succeeded"
REPAIR_FAILED = "failed"

# 与 documents.py 的 chunk_id 格式一致：doc_id#两位以上序号。也接受中文方括号
CITATION_PATTERN = re.compile(r"[\[【]\s*([a-z0-9][a-z0-9-]{0,63}#\d{2,4})\s*[\]】]")

STATUS_CITED = "cited"            # 至少有一条合法引用
STATUS_UNCITED = "uncited"        # 本轮检索到了内容，但回复没有合法引用
STATUS_NOT_FOUND = "not_found"    # 本轮检索过，知识库没有找到相关内容
STATUS_UNAVAILABLE = "unavailable"  # 本轮检索都失败了（索引不可用等）
STATUS_NO_SEARCH = "no_search"    # 本轮没有检索，回复里却写了引用（全部无效）


@dataclass
class TurnKnowledge:
    """某条模型回复之前、本轮内的检索情况"""
    valid: dict[str, dict] = field(default_factory=dict)      # chunkId -> 来源信息（服务端数据）
    snippets: dict[str, str] = field(default_factory=dict)    # chunkId -> 片段（引用修复时给模型看）
    had_hits: bool = False
    had_empty: bool = False
    had_error: bool = False

    @property
    def searched(self) -> bool:
        return self.had_hits or self.had_empty or self.had_error


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


def _walk(messages: list[BaseMessage]) -> Iterator[tuple[int, AIMessage, TurnKnowledge]]:
    """逐条遍历，遇到模型回复时给出它之前、本轮内的检索情况"""
    turn = TurnKnowledge()
    search_call_ids: set[str] = set()
    for index, message in enumerate(messages):
        if message.type == "human":
            # 新的一轮：之前的检索结果全部作废
            turn, search_call_ids = TurnKnowledge(), set()
        elif isinstance(message, AIMessage):
            yield index, message, turn
            search_call_ids |= {c["id"] for c in message.tool_calls if c.get("name") == SEARCH_TOOL and c.get("id")}
        elif (isinstance(message, ToolMessage) and message.name == SEARCH_TOOL
              and message.tool_call_id in search_call_ids):
            rows = _parse_rows(message) if message.status != "error" else None
            if rows is None:
                turn.had_error = True
                continue
            found = False
            for row in rows:
                chunk_id = row.get("chunkId") if isinstance(row, dict) else None
                # 只接受格式正确的 chunkId；全是格式不对的行等同于没有找到
                if isinstance(chunk_id, str) and CITATION_PATTERN.fullmatch(f"[{chunk_id}]"):
                    found = turn.had_hits = True
                    turn.valid.setdefault(chunk_id, {"chunkId": chunk_id, "docId": str(row.get("docId") or ""),
                                                     "title": str(row.get("title") or ""),
                                                     "section": str(row.get("section") or "")})
                    turn.snippets.setdefault(chunk_id, str(row.get("snippet") or ""))
            turn.had_empty = turn.had_empty or not found


def turn_knowledge(messages: list[BaseMessage], index: int) -> TurnKnowledge:
    """第 index 条（必须是模型回复）之前、本轮内的检索情况；引用修复只能使用这里的分块"""
    for i, _, turn in _walk(messages):
        if i == index:
            return turn
    return TurnKnowledge()


def _repair_status(message: AIMessage) -> str | None:
    value = (message.additional_kwargs or {}).get(REPAIR_KEY)
    status = value.get("status") if isinstance(value, dict) else None
    return status if status in (REPAIR_SUCCEEDED, REPAIR_FAILED) else None


def annotate(messages: list[BaseMessage]) -> dict[int, dict]:
    """
    为有内容的模型回复生成引用信息：
    {消息下标: {"status", "sources", "references", "invalidCitations", "citationRepair"}}。
    本轮没有检索、回复里也没有引用的消息不出现在结果里。
    """
    result: dict[int, dict] = {}
    for index, message, turn in _walk(messages):
        text = message.content if isinstance(message.content, str) else ""
        cited = cited_ids(text)
        if not text or not (cited or turn.searched):
            continue
        sources = [turn.valid[c] for c in cited if c in turn.valid]
        if sources:
            status = STATUS_CITED
        elif turn.had_hits:
            status = STATUS_UNCITED
        elif turn.had_empty:
            status = STATUS_NOT_FOUND
        elif turn.had_error:
            status = STATUS_UNAVAILABLE
        else:
            status = STATUS_NO_SEARCH
        result[index] = {"status": status, "sources": sources,
                         "references": [v for k, v in turn.valid.items() if k not in cited],
                         "invalidCitations": sum(1 for c in cited if c not in turn.valid),
                         "citationRepair": _repair_status(message)}
    return result


# ---------------------------------------------------------------------------
# 引用修复：模型只给出"第几句引用哪个分块"，由服务端把 [chunkId] 插进原文，正文一个字都不改
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[。！？!?；;\n])")
_TRAILING_PUNCT = "。！？!?；;，,\n "


def split_sentences(text: str) -> list[str]:
    """
    按句末标点和换行切分，拼回去与原文完全相同。只有空白的片段（例如"！"后面的换行）并到前一句，
    否则模型看到的句子编号里会夹着空行，标注容易错位
    """
    sentences: list[str] = []
    for piece in _SENTENCE_END.split(text):
        if not piece:
            continue
        if sentences and not piece.strip():
            sentences[-1] += piece
        else:
            sentences.append(piece)
    return sentences


def insert_citations(text: str, mapping: dict[int, list[str]]) -> str:
    """把 [chunkId] 插到对应句子的末尾标点之前；mapping 的句子编号从 1 开始"""
    sentences = split_sentences(text)
    out = []
    for number, sentence in enumerate(sentences, start=1):
        ids = mapping.get(number)
        if ids:
            body = sentence.rstrip(_TRAILING_PUNCT)
            tail = sentence[len(body):]
            sentence = body + "".join(f"[{c}]" for c in ids) + tail
        out.append(sentence)
    return "".join(out)


def parse_repair(raw: str, sentence_count: int, valid: set[str]) -> dict[int, list[str]]:
    """
    解析修复调用的输出 {"citations": [{"sentence": 1, "chunkIds": ["a#01"]}]}。
    句子编号越界、chunkId 不在本轮合法集合里的一律丢弃；格式不对返回空字典（修复失败）
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        items = json.loads(text).get("citations")
    except (ValueError, AttributeError):
        return {}
    mapping: dict[int, list[str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        number, ids = item.get("sentence"), item.get("chunkIds")
        if not isinstance(number, int) or not 1 <= number <= sentence_count or not isinstance(ids, list):
            continue
        good = [c for c in ids if isinstance(c, str) and c in valid]
        if good:
            mapping.setdefault(number, [])
            mapping[number] += [c for c in good if c not in mapping[number]]
    return mapping
