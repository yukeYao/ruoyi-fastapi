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

没有合法引用的回复保持 uncited，检索到的分块只作为 references 显示；服务端不替模型补引用。
（曾经有过的"引用修复"已移除：阶段 4 评估发现它会把规则片段挂到"你缺少该权限"这类实时结论上。
旧会话的 checkpoint 里如果还有修复记录 additional_kwargs[LEGACY_REPAIR_KEY]，一律按修复前的原文显示和校验，
服务端插入的标记不算引用，见 message_text。）

来源只表示"这段内容来自本轮知识库检索"，不代表内容已被验证为真：服务端只能校验引用是否来自检索结果，
不能校验回复里的句子是否忠实于片段。

形似引用但不符合 chunkId 格式的标记（例如【密码错误锁定规则#01】）计入 suspiciousCitations：
既不是合法来源，也不算 invalidCitations，正文不改，前端提示"存在无法核实的引用格式"。
"""
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

SEARCH_TOOL = "search_knowledge"
# 已移除的引用修复写在旧回复上的记录键：只用于读取旧 checkpoint
LEGACY_REPAIR_KEY = "citation_repair"

# 与 documents.py 的 chunk_id 格式一致：doc_id#两位以上序号。也接受中文方括号
CITATION_PATTERN = re.compile(r"[\[【]\s*([a-z0-9][a-z0-9-]{0,63}#\d{2,4})\s*[\]】]")

STATUS_CITED = "cited"            # 至少有一条合法引用
STATUS_UNCITED = "uncited"        # 本轮检索到了内容，但回复没有合法引用
STATUS_NOT_FOUND = "not_found"    # 本轮检索过，知识库没有找到相关内容
STATUS_UNAVAILABLE = "unavailable"  # 本轮检索都失败了（索引不可用等）
STATUS_NO_SEARCH = "no_search"    # 本轮没有检索，回复里却写了引用（全部无效）

# 形似引用的标记：方括号里有 #数字。符合 CITATION_PATTERN 的是正式引用，其余计为疑似伪引用
_CITATION_LIKE = re.compile(r"[\[【]([^\[\]【】\n]{1,80}?#\s*\d{1,4})\s*[\]】]")


@dataclass
class TurnKnowledge:
    """某条模型回复之前、本轮内的检索情况"""
    valid: dict[str, dict] = field(default_factory=dict)      # chunkId -> 来源信息（服务端数据）
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


def suspicious_count(text: str) -> int:
    """形似引用、但不符合 chunkId 格式的标记个数（按出现次数）"""
    return sum(1 for m in _CITATION_LIKE.finditer(text or "") if not CITATION_PATTERN.fullmatch(m.group(0)))


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
            turn.had_empty = turn.had_empty or not found


def message_text(message: AIMessage) -> str:
    """
    回复正文。旧会话里被引用修复改写过的回复，返回修复前的原文：服务端插入的 [chunkId] 不是模型写的引用，
    不能在修复功能移除后变成普通来源
    """
    record = (message.additional_kwargs or {}).get(LEGACY_REPAIR_KEY)
    if isinstance(record, dict) and isinstance(record.get("original"), str):
        return record["original"]
    return message.content if isinstance(message.content, str) else ""


def annotate(messages: list[BaseMessage]) -> dict[int, dict]:
    """
    为有内容的模型回复生成引用信息：
    {消息下标: {"status", "sources", "references", "invalidCitations", "suspiciousCitations"}}。
    本轮没有检索、回复里也没有引用或疑似引用的消息不出现在结果里。
    """
    result: dict[int, dict] = {}
    for index, message, turn in _walk(messages):
        text = message_text(message)
        cited = cited_ids(text)
        suspicious = suspicious_count(text)
        if not text or not (cited or suspicious or turn.searched):
            continue
        # 只认本轮合法集合里的 chunkId：上一轮的 chunkId 即使模型"记得"，也计入 invalidCitations
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
                         "suspiciousCitations": suspicious}
    return result
