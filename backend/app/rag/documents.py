"""
知识文档的解析与分块。

文档格式：开头是 frontmatter（--- 包围），正文是 Markdown，按二级标题（## ）分章节。
frontmatter 只允许下面几个字段，格式写死，不引入 YAML 库：

    ---
    doc_id: agent-access              # 与文件名一致，小写字母、数字、短横线
    title: 看不到智能助手菜单怎么排查
    version: 1                        # 正整数，文档内容有实质变化时 +1
    required_perms: [system:role:edit]  # 需全部满足；[] 表示所有智能助手用户可读
    sources:                          # 内容来源文件，相对仓库根目录，至少一个
      - backend/sql/agent.sql
    ---

校验从严：未知字段、格式不对直接报错，而不是悄悄忽略——权限字段写错（例如拼成 required_perm）
如果被忽略，受限文档就会变成所有人可读。
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.core.constants import KNOWLEDGE_CHUNK_MAX_CHARS

DOC_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# 与若依的权限字符格式一致：模块:功能:操作
PERM_PATTERN = re.compile(r"^[a-zA-Z]+:[a-zA-Z]+:[a-zA-Z]+$")
_REQUIRED_KEYS = ("doc_id", "title", "version", "required_perms", "sources")
_DEFAULT_SECTION = "概述"


class KnowledgeDocError(ValueError):
    """文档格式错误：构建索引时直接中止，不发布任何半成品"""


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    seq: int
    content: str
    required_perms: tuple[str, ...]

    @property
    def embedding_text(self) -> str:
        # 标题和章节一起送去 Embedding：很多章节正文里不重复主题词（例如"排查顺序"），只靠正文检索不到
        return f"{self.title}\n{self.section}\n{self.content}"

    @property
    def content_hash(self) -> str:
        return sha256(self.embedding_text)


@dataclass(frozen=True)
class KnowledgeDoc:
    doc_id: str
    title: str
    version: int
    required_perms: tuple[str, ...]
    sources: tuple[str, ...]
    body: str
    path: Path
    chunks: tuple[KnowledgeChunk, ...] = field(default=())

    @property
    def content_hash(self) -> str:
        meta = json.dumps([self.doc_id, self.title, self.version, list(self.required_perms), list(self.sources)],
                          ensure_ascii=False)
        return sha256(meta + "\n" + self.body)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def perms_to_json(perms: tuple[str, ...] | list[str]) -> str:
    # 排序后存：同一组权限只有一种写法，摘要校验才稳定
    return json.dumps(sorted(perms), ensure_ascii=False)


# ---------------------------------------------------------------------------
# frontmatter
# ---------------------------------------------------------------------------

def _parse_inline_list(value: str, where: str) -> list[str]:
    if not (value.startswith("[") and value.endswith("]")):
        raise KnowledgeDocError(f"{where}：必须写成 [a, b] 形式的列表")
    inner = value[1:-1].strip()
    return [item.strip() for item in inner.split(",")] if inner else []


def _split_frontmatter(text: str, where: str) -> tuple[list[str], str]:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise KnowledgeDocError(f"{where}：缺少 frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise KnowledgeDocError(f"{where}：frontmatter 没有结束标记 ---") from None
    return lines[1:end], "\n".join(lines[end + 1:]).strip()


def parse_frontmatter(lines: list[str], where: str) -> dict:
    data: dict = {}
    current_list: list[str] | None = None
    for raw in lines:
        if not raw.strip():
            continue
        if raw.startswith(("  - ", "- ")):
            if current_list is None:
                raise KnowledgeDocError(f"{where}：列表项前面没有字段名")
            current_list.append(raw.strip()[2:].strip())
            continue
        key, sep, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or key not in _REQUIRED_KEYS:
            raise KnowledgeDocError(f"{where}：不支持的字段 {key!r}")
        if key in data:
            raise KnowledgeDocError(f"{where}：字段 {key} 重复")
        if key in ("required_perms", "sources") and not value:
            current_list = data[key] = []
        elif key in ("required_perms", "sources"):
            data[key] = _parse_inline_list(value, f"{where} {key}")
            current_list = None
        else:
            data[key] = value
            current_list = None
    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        raise KnowledgeDocError(f"{where}：缺少字段 {', '.join(missing)}")
    return data


def _validate(data: dict, path: Path) -> tuple[str, str, int, tuple[str, ...], tuple[str, ...]]:
    where = path.name
    doc_id, title = data["doc_id"], data["title"]
    if not DOC_ID_PATTERN.match(doc_id) or doc_id != path.stem:
        raise KnowledgeDocError(f"{where}：doc_id 必须是小写字母、数字、短横线，且与文件名一致")
    if not title or len(title) > 100:
        raise KnowledgeDocError(f"{where}：title 不能为空且不超过 100 字")
    if not data["version"].isdigit() or int(data["version"]) < 1:
        raise KnowledgeDocError(f"{where}：version 必须是正整数")
    perms = data["required_perms"]
    bad = [p for p in perms if not PERM_PATTERN.match(p)]
    if bad or len(set(perms)) != len(perms):
        raise KnowledgeDocError(f"{where}：required_perms 格式不正确或重复：{bad}")
    sources = data["sources"]
    if not sources:
        raise KnowledgeDocError(f"{where}：sources 至少要有一个来源文件")
    for source in sources:
        p = PurePosixPath(source)
        # 只允许仓库内的相对路径：绝对路径、盘符、.. 都会把开发机目录结构写进知识库
        if "\\" in source or ":" in source or p.is_absolute() or ".." in p.parts:
            raise KnowledgeDocError(f"{where}：sources 只能是相对仓库根目录的路径：{source}")
    return doc_id, title, int(data["version"]), tuple(perms), tuple(sources)


# ---------------------------------------------------------------------------
# 分块
# ---------------------------------------------------------------------------

def _sections(body: str) -> list[tuple[str, str]]:
    """按二级标题切章节；第一个二级标题之前的内容归入"概述"。更深的标题留在章节正文里"""
    sections: list[tuple[str, list[str]]] = [(_DEFAULT_SECTION, [])]
    for line in body.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        else:
            sections[-1][1].append(line)
    return [(name, "\n".join(lines).strip()) for name, lines in sections if "\n".join(lines).strip()]


def _pack(pieces: list[str], limit: int, sep: str) -> list[str]:
    """贪心合并：相邻片段拼到不超过 limit；单个片段本身超长时按字符硬切"""
    result: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > limit:
            if current:
                result.append(current)
                current = ""
            result.append(piece[:limit])
            piece = piece[limit:]
        candidate = current + sep + piece if current else piece
        if len(candidate) <= limit:
            current = candidate
        else:
            result.append(current)
            current = piece
    if current:
        result.append(current)
    return result


def split_section(text: str, limit: int = KNOWLEDGE_CHUNK_MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        # 段落本身超长（例如一张长表格）再按行切，尽量不把一行表格拆成两半
        parts += [paragraph] if len(paragraph) <= limit else _pack(paragraph.splitlines(), limit, "\n")
    return _pack(parts, limit, "\n\n")


def chunk_doc(doc_id: str, title: str, body: str, required_perms: tuple[str, ...],
              limit: int = KNOWLEDGE_CHUNK_MAX_CHARS) -> tuple[KnowledgeChunk, ...]:
    chunks = []
    for section, text in _sections(body):
        for piece in split_section(text, limit):
            seq = len(chunks) + 1
            chunks.append(KnowledgeChunk(f"{doc_id}#{seq:02d}", doc_id, title, section, seq, piece, required_perms))
    if not chunks:
        raise KnowledgeDocError(f"{doc_id}：正文为空")
    return tuple(chunks)


def load_doc(path: Path) -> KnowledgeDoc:
    lines, body = _split_frontmatter(path.read_text(encoding="utf-8"), path.name)
    doc_id, title, version, perms, sources = _validate(parse_frontmatter(lines, path.name), path)
    return KnowledgeDoc(doc_id, title, version, perms, sources, body, path, chunk_doc(doc_id, title, body, perms))


def load_docs(docs_dir: Path) -> list[KnowledgeDoc]:
    docs = [load_doc(p) for p in sorted(docs_dir.glob("*.md"))]
    if not docs:
        raise KnowledgeDocError(f"{docs_dir.name}：没有找到知识文档")
    return docs
