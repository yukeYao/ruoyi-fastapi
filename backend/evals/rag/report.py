"""
把评估结果渲染成 Markdown 报告、导出人工核对清单。只做格式化，不产生任何数字：所有数字都来自运行目录里的 JSON。
"""
import json
from pathlib import Path

from evals.rag.runner import VARIANTS

CATEGORY_NAMES = {"rule": "规则问答", "live": "结合实时工具", "no_answer": "无答案", "restricted": "权限受限",
                  "injection": "文档注入", "forged": "伪造引用", "followup": "同一会话追问", "sse": "SSE 来源一致",
                  "perm_advice": "权限不足的建议"}
MODE_NAMES = {"bm25": "BM25", "vector": "向量", "hybrid": "混合（RRF）"}


def _pct(m: dict | None) -> str:
    if not m or m.get("value") is None:
        return "—"
    return f"{m['value'] * 100:.1f}%（{m['num']}/{m['den']}）"


def _num(v, fmt="{:.3f}") -> str:
    return "—" if v is None else fmt.format(v)


def _recall(m: dict, k: int):
    """写进 JSON 后整数键会变成字符串"""
    return m["recall"].get(str(k), m["recall"].get(k))


def retrieval_section(r: dict) -> str:
    lines = [f"- 数据集版本 {r['dataset_version']}：dev {r['n_dev']} 条（只用于校准），test {r['n_test']} 条（只用于报告）；"
             f"返回条数 top_k={r['top_k']}；索引 {r['index']['chunks']} 个分块。",
             f"- 当前默认门槛：τv={r['defaults']['min_vector_score']}，τb={r['defaults']['min_bm25_score']}，"
             f"最少命中词数={r['defaults']['min_bm25_terms']}。", "",
             "| 方式 | dev 校准门槛（τv / τb / 最少命中词） | 集合 | Recall@1 | Recall@3 | Recall@5 | MRR | 无答案拒答率 | 有答案却无结果 | 权限泄露 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for mode, m in r["modes"].items():
        t = m["thresholds"]
        thr = " / ".join("—" if v >= 1e8 or v == 99 else str(v) for v in
                         (t["min_vector_score"], t["min_bm25_score"], t["min_bm25_terms"]))
        for label, key in (("dev", "dev"), ("**test**", "test"), ("test（默认门槛）", "test_with_defaults")):
            x = m[key]
            recall = " | ".join(_num(_recall(x, k)) for k in (1, 3, 5))
            lines.append(f"| {MODE_NAMES[mode]} | {thr if key == 'dev' else ''} | {label} | {recall} | {_num(x['mrr'])} | "
                         f"{_num(x['rejection'])} | {x['false_reject']} | {x['leaks']} |")
    return "\n".join(lines)


def e2e_section(e: dict) -> str:
    names = [n for n in VARIANTS if n in e["summary"]]
    head = "| 指标 | " + " | ".join(VARIANTS[n].title for n in names) + " |"
    sep = "|---|" + "---|" * len(names)
    rows = [
        ("用例通过率", lambda m: _pct(m["pass_rate"])),
        ("无答案拒答率", lambda m: _pct(m["no_answer_refusal"])),
        ("幻觉率（规则判定）", lambda m: _pct(m["hallucination"])),
        ("引用准确率", lambda m: _pct(m["citation_accuracy"])),
        ("auto_cited 占有引用回复的比例", lambda m: _pct(m["auto_cited_share"])),
        ("suspiciousCitations 合计", lambda m: str(m["suspicious_citations"])),
        ("invalidCitations 合计", lambda m: str(m["invalid_citations"])),
        ("权限泄露（次）", lambda m: str(m["permission_leaks"])),
        ("注入成功（次）", lambda m: str(m["injection_success"])),
        ("延迟 均值 / 中位数 / 最大（秒）", lambda m: " / ".join(_num(m[k], "{:.2f}") for k in ("latency_mean", "latency_p50", "latency_max"))),
        ("Token 输入 / 输出", lambda m: f"{m['input_tokens']} / {m['output_tokens']}"),
        ("Embedding Token", lambda m: str(m["embedding_tokens"])),
        ("估算成本（美元）", lambda m: f"{m['cost_usd']:.4f}"),
        ("运行出错", lambda m: f"{m['errors']} / {m['runs']}"),
    ]
    body = [f"| {label} | " + " | ".join(fn(e["summary"][n]) for n in names) + " |" for label, fn in rows]
    cat_head = "| 类别 | " + " | ".join(VARIANTS[n].title for n in names) + " |"
    cats = []
    for cat, label in CATEGORY_NAMES.items():
        cells = []
        for n in names:
            m = e["summary"][n]["by_category"].get(cat)
            cells.append(_pct(m) if m else "不适用")
        cats.append(f"| {label} | " + " | ".join(cells) + " |")
    stopped = [f"- **预算保护提前停止**：{e['stopped']}；以下只包含停止前已完成的运行。"] if e.get("stopped") else []
    return "\n".join([*stopped, f"- 门槛来源：{e['thresholds_source']}；每条用例运行 {e['runs']} 次；实际模型 ID：{'、'.join(e['model_ids']) or '未返回'}",
                      "", head, sep, *body, "", "分类通过率（不适用：该方案没有运行这一类，原因见方法说明）：", "",
                      cat_head, sep, *cats])


def render(out_dir: Path) -> str:
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    parts = ["# 知识库（RAG）评估报告（自动生成）", "",
             f"- 代码提交：{meta['commit']}", f"- 对话模型（配置）：{meta['chat_model']}；Embedding：{meta['embedding_model']}",
             f"- 价格（美元/百万 Token，按公开价格估算，需核对）：输入 {meta['prices']['input']}，输出 {meta['prices']['output']}，"
             f"Embedding {meta['prices']['embedding']}", f"- Embedding Token 合计：{meta.get('embedding_tokens_total', 0)}", ""]
    if (out_dir / "retrieval.json").exists():
        parts += ["## 检索评估", "", retrieval_section(json.loads((out_dir / "retrieval.json").read_text(encoding="utf-8"))), ""]
    if (out_dir / "e2e.json").exists():
        parts += ["## 端到端评估", "", e2e_section(json.loads((out_dir / "e2e.json").read_text(encoding="utf-8"))), "",
                  "人工核对清单见同目录 manual_review.md。"]
    return "\n".join(parts) + "\n"


def manual_review(cases: list[dict], traces: list[dict], scored: list[dict]) -> str:
    """规则问答、实时排查、追问、伪造引用：列出问题、回复、来源片段和规则判定，供人工标注事实是否一致"""
    by_id = {c["id"]: c for c in cases}
    verdicts = {(s["case_id"], s["variant"], s["run"]): s for s in scored}
    lines = ["# 人工核对清单", "",
             "规则判定只覆盖 facts_required / facts_forbidden 能表达的事实；下面每条请人工标注"
             "\"事实一致 / 部分一致 / 不一致\"，并核对引用的片段是否支撑对应句子。", ""]
    for t in traces:
        if t["category"] not in ("rule", "live", "followup", "forged"):
            continue
        s = verdicts[(t["case_id"], t["variant"], t["run"])]
        snippets = {row.get("chunkId"): row.get("snippet", "")
                    for r in t["tool_results"] if r["name"] == "search_knowledge"
                    for row in (r["payload"].get("untrusted_data") or {}).get("rows", [])}
        lines += [f"## {t['case_id']} · {t['variant']} · 第 {t['run']} 次", "",
                  f"- 问题：{' → '.join(by_id[t['case_id']]['turns'])}",
                  f"- 工具：{[c['name'] for c in t['tool_calls']]}",
                  f"- 规则判定：{'通过' if s['passed'] else '未通过'}；事实规则：{s.get('facts_ok')}；幻觉（规则）：{s.get('hallucination')}"]
        for i, turn in enumerate(t["turns"], start=1):
            lines += [f"- 第 {i} 轮回复（{turn['knowledgeStatus']}）：{turn['reply']}",
                      f"  - 来源：{[x['chunkId'] for x in turn['sources']]}；自动关联：{[x['chunkId'] for x in turn['autoSources']]}"]
        for chunk_id, text in snippets.items():
            lines.append(f"  - 片段 {chunk_id}：{text}")
        lines += ["- 人工标注：（待填）", ""]
    return "\n".join(lines)
