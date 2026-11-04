"""
知识文档的一致性测试：frontmatter 格式、来源文件存在、权限字符真实存在、不含密钥/默认密码/本地路径。
文档内容会进入检索结果、模型上下文和 checkpoint，所以这里按"发布到外部"的标准检查。
"""
import re
from pathlib import Path

import pytest

from app.agent.tools import TOOLS
from app.config import BACKEND_DIR
from app.rag.documents import KnowledgeDocError, chunk_doc, load_doc, load_docs, split_section
from app.core.constants import KNOWLEDGE_CHUNK_MAX_CHARS

REPO_ROOT = BACKEND_DIR.parent
DOCS_DIR = REPO_ROOT / "docs" / "knowledge"
PERM_IN_TEXT = re.compile(r"\b[a-z]+:[a-zA-Z]+:[a-zA-Z]+\b")

# 不允许出现在知识文档里的内容：默认密码、Key、JWT、bcrypt 哈希、开发机路径
FORBIDDEN_PATTERNS = {
    "默认密码": re.compile(r"admin123", re.I),
    "API Key": re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}"),
    "密码哈希": re.compile(r"\$2[aby]\$\d{2}\$"),
    "Windows 路径": re.compile(r"\b[A-Za-z]:[\\/]"),
    "用户目录": re.compile(r"(/Users/|/home/|\\Users\\)", re.I),
    "Bearer 令牌": re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}"),
}


def _known_perms() -> set[str]:
    """若依菜单 + 智能助手菜单里的全部权限字符，再加工具注册表声明的权限"""
    perms = set()
    for sql in ("ry_20260417.sql", "agent.sql"):
        perms |= set(re.findall(r"'([a-z]+:[a-zA-Z]+:[a-zA-Z]+)'", (BACKEND_DIR / "sql" / sql).read_text(encoding="utf-8")))
    return perms | {spec.perm for spec in TOOLS.values()}


@pytest.fixture(scope="module")
def docs():
    return load_docs(DOCS_DIR)


def test_doc_count_in_range(docs):
    assert 20 <= len(docs) <= 30


def test_sources_exist(docs):
    for doc in docs:
        for source in doc.sources:
            assert (REPO_ROOT / source).is_file(), f"{doc.doc_id} 的来源文件不存在：{source}"


def test_perms_exist(docs):
    known = _known_perms()
    for doc in docs:
        mentioned = set(doc.required_perms) | set(PERM_IN_TEXT.findall(doc.body))
        unknown = mentioned - known
        assert not unknown, f"{doc.doc_id} 引用了不存在的权限字符：{unknown}"


def test_no_secrets_or_local_paths():
    for path in DOCS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for name, pattern in FORBIDDEN_PATTERNS.items():
            assert not pattern.search(text), f"{path.name} 含有{name}"


def test_both_public_and_restricted_docs_exist(docs):
    """权限过滤的测试和评估需要两类文档都有"""
    assert any(not d.required_perms for d in docs) and any(d.required_perms for d in docs)


def test_chunks_respect_size_limit_and_ids_are_unique(docs):
    chunks = [c for d in docs for c in d.chunks]
    assert all(len(c.content) <= KNOWLEDGE_CHUNK_MAX_CHARS for c in chunks)
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    # 分块继承文档的权限：检索前按分块过滤，不能有分块漏掉文档的权限
    for doc in docs:
        assert all(c.required_perms == doc.required_perms for c in doc.chunks)


# ---------------------------------------------------------------------------
# 解析与分块的单元测试
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, name: str, front: str, body: str = "## 章节\n\n正文内容。") -> Path:
    path = tmp_path / f"{name}.md"
    path.write_text(f"---\n{front}\n---\n\n{body}\n", encoding="utf-8")
    return path


GOOD = "doc_id: demo\ntitle: 示例\nversion: 1\nrequired_perms: []\nsources:\n  - README.md"


def test_parse_good_doc(tmp_path):
    doc = load_doc(_write(tmp_path, "demo", GOOD))
    assert doc.required_perms == () and doc.sources == ("README.md",)
    assert [c.chunk_id for c in doc.chunks] == ["demo#01"] and doc.chunks[0].section == "章节"


@pytest.mark.parametrize("front", [
    GOOD.replace("required_perms: []", "required_perm: [system:role:edit]"),   # 字段名拼错不能被忽略
    GOOD.replace("required_perms: []", "required_perms: [system-role-edit]"),
    GOOD.replace("required_perms: []", "required_perms: [a:b:c, a:b:c]"),
    GOOD.replace("  - README.md", "  - D:/work/README.md"),
    GOOD.replace("  - README.md", "  - ../outside.md"),
    GOOD.replace("  - README.md", ""),
    GOOD.replace("version: 1", "version: 0"),
    GOOD.replace("doc_id: demo", "doc_id: other"),                              # 与文件名不一致
    GOOD + "\nextra: 1",
])
def test_parse_rejects_bad_frontmatter(tmp_path, front):
    with pytest.raises(KnowledgeDocError):
        load_doc(_write(tmp_path, "demo", front))


def test_long_section_is_split_on_paragraphs():
    paragraphs = [f"第{i}段" + "字" * 250 for i in range(5)]
    pieces = split_section("\n\n".join(paragraphs), limit=600)
    assert len(pieces) == 3 and all(len(p) <= 600 for p in pieces)
    assert "".join(pieces).replace("\n", "") == "".join(paragraphs)


def test_content_hash_covers_title_and_section():
    a = chunk_doc("d", "标题", "## 甲\n内容", ())[0]
    b = chunk_doc("d", "标题", "## 乙\n内容", ())[0]
    c = chunk_doc("d", "新标题", "## 甲\n内容", ())[0]
    assert len({a.content_hash, b.content_hash, c.content_hash}) == 3
