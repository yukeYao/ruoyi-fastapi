"""
关键词检索的分词：中文用 jieba 搜索引擎模式，权限字符和英文/数字标识整体保留。

- 权限字符（如 agent:chat:use、monitor:online:forceLogout）先用正则整体抽出，作为一个词：
  jieba 会把它拆成 agent / : / chat / : / use，"use" 这种碎片会让所有含权限字符的分块都命中。
- 英文和数字（test01、tokenId、del_flag）整体保留并转小写。
- 中文片段用 lcut_for_search：既给出"强制下线"，也给出"强制""下线"，短问题也能匹配到长词。
- 加一份领域词典，避免"智能助手""数据权限"被切成不相关的碎片。
- 去掉标点和少量没有区分度的虚词。

用独立的 jieba.Tokenizer 实例，不修改 jieba 的全局默认分词器。
"""
import logging
import re
import threading

import jieba

_PERM = re.compile(r"[A-Za-z]+:[A-Za-z*]+:[A-Za-z*]+")
_ASCII = re.compile(r"[A-Za-z0-9_]+")
_HAN = re.compile(r"[一-鿿]+")

# 领域词：都来自 docs/knowledge 和菜单名称
DOMAIN_WORDS = (
    "智能助手", "数据权限", "数据范围", "权限字符", "超级管理员", "强制下线", "在线用户", "在线会话", "登录日志",
    "操作日志", "审批卡片", "待审批", "逻辑删除", "菜单权限", "角色管理", "用户管理", "重新登录", "登录态",
    "账号锁定", "解除锁定", "密码错误", "验证码", "停用账号", "启用账号", "分配角色", "本部门", "仅本人",
)

STOPWORDS = frozenset(
    "的 了 是 在 和 与 及 或 吗 呢 吧 啊 呀 么 我 你 他 她 它 我们 你们 他们 这 那 这个 那个 一个 什么 怎么 怎么办 "
    "为什么 如何 哪些 哪个 是否 可以 能 能否 会 要 需要 请 请问 一下 时 时候 后 前 里 中 上 下 对 把 被 让 给 "
    "就 都 也 还 又 再 但 而 并 等 等等 之 其 该 此 及其 以及 有 为 没有".split()
)

_lock = threading.Lock()
_tokenizer: jieba.Tokenizer | None = None


def _get_tokenizer() -> jieba.Tokenizer:
    global _tokenizer
    with _lock:
        if _tokenizer is None:
            # jieba 首次加载词典会往日志打一大段说明，静音；加载约 0.4 秒，只做一次
            jieba.setLogLevel(logging.WARNING)
            tok = jieba.Tokenizer()
            for word in DOMAIN_WORDS:
                tok.add_word(word, freq=20000)
            _tokenizer = tok
        return _tokenizer


def tokenize(text: str) -> list[str]:
    """返回检索用的词列表（保留重复，BM25 需要词频）"""
    text = text or ""
    tokens: list[str] = [p.lower() for p in _PERM.findall(text)]
    rest = _PERM.sub(" ", text)
    tokens += [w.lower() for w in _ASCII.findall(rest)]
    tok = _get_tokenizer()
    for segment in _HAN.findall(rest):
        tokens += [w for w in tok.lcut_for_search(segment) if w not in STOPWORDS]
    return tokens
