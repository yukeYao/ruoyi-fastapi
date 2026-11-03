"""把评分结果填进 report_template.md。只做格式化，不产生任何数字"""
from pathlib import Path

from evals.scoring import NA_NO_TOOLS, VARIANT_NAMES

TEMPLATE_FILE = Path(__file__).resolve().parent / "report_template.md"


def _pct(metric: dict) -> str:
    if metric.get("na"):
        return "不适用"
    if metric.get("value") is None:
        return "—（无样本）"
    return f"{metric['value'] * 100:.1f}%（{metric['num']}/{metric['den']}）"


def _num(value, fmt="{:.2f}") -> str:
    return "—" if value is None else fmt.format(value)


def _count(metric: dict) -> str:
    return "不适用" if metric.get("na") else str(metric["value"])


def metrics_table(variants: list[str], metrics: dict) -> str:
    rows = [
        ("任务完成率（全部用例）", lambda m: _pct(m["task_completion"])),
        ("查询 + 写操作完成率", lambda m: _pct(m["task_completion_core"])),
        ("工具选择准确率", lambda m: _pct(m["tool_accuracy"])),
        ("约束满足率", lambda m: _pct(m["constraint_satisfaction"])),
        ("未经批准写操作数", lambda m: _count(m["unapproved_writes"])),
        ("不应发生的写操作数", lambda m: _count(m["unexpected_writes"])),
        ("越权拦截率", lambda m: _pct(m["interception"]) + (
            f"（发起越权时 {_pct(m['interception_when_attempted'])}）" if "interception_when_attempted" in m else "")),
        ("提示注入成功率", lambda m: _pct(m["injection_success"])),
        ("注入诱导发起率", lambda m: _pct(m["injection_induced"])),
        ("声称已执行但未发生", lambda m: str(m["false_success_claims"])),
        ("平均步数（模型调用）", lambda m: _num(m["avg_model_calls"])),
        ("延迟 均值 / 中位数 / 最大（秒）", lambda m: " / ".join(_num(m[k]) for k in ("latency_mean", "latency_p50", "latency_max"))),
        ("平均 Token（输入 / 输出）", lambda m: f"{_num(m['avg_input_tokens'], '{:.0f}')} / {_num(m['avg_output_tokens'], '{:.0f}')}"),
        ("Token 合计", lambda m: str(m["total_tokens"])),
        ("运行出错次数", lambda m: f"{m['errors']} / {m['runs']}"),
    ]
    head = "| 指标 | " + " | ".join(VARIANT_NAMES[v] for v in variants) + " |"
    sep = "|---|" + "---|" * len(variants)
    body = [f"| {name} | " + " | ".join(fn(metrics[v]) for v in variants) + " |" for name, fn in rows]
    return "\n".join([head, sep, *body])


def category_table(variants: list[str], metrics: dict) -> str:
    names = {"query": "查询", "write": "写操作", "security": "越权", "injection": "提示注入"}
    head = "| 类别 | " + " | ".join(VARIANT_NAMES[v] for v in variants) + " |"
    sep = "|---|" + "---|" * len(variants)
    body = [f"| {label} | " + " | ".join(_pct(metrics[v]["task_completion_by_category"][cat]) for v in variants) + " |"
            for cat, label in names.items()]
    return "\n".join([head, sep, *body])


def _cell(text: str, limit: int = 60) -> str:
    text = (text or "").replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= limit else text[:limit] + "…"


def case_table(cases: list[dict], scored: list[dict], variants: list[str]) -> str:
    head = "| 用例 | 标题 | " + " | ".join(VARIANT_NAMES[v].split("：")[0] for v in variants) + " |"
    sep = "|---|---|" + "---|" * len(variants)
    lines = [head, sep]
    for case in cases:
        cells = []
        for v in variants:
            rows = [s for s in scored if s["case_id"] == case["id"] and s["variant"] == v]
            ok = sum(1 for s in rows if s["task_ok"])
            cells.append(f"{ok}/{len(rows)}" if rows else "—")
        lines.append(f"| {case['id']} | {case['title']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def failures(cases: list[dict], scored: list[dict]) -> str:
    titles = {c["id"]: c["title"] for c in cases}
    lines = []
    for s in scored:
        failed_checks = [c for c in s["checks"] if not c["passed"]]
        if s["task_ok"] and not failed_checks and not s.get("injection_induced"):
            continue
        reasons = []
        if s["error"]:
            reasons.append(f"运行错误：{s['error']}")
        reasons += [f"{c['kind']} 检查未通过：{c['type']} {_cell(str({k: v for k, v in c.items() if k not in ('type', 'kind', 'passed')}), 80)}"
                    for c in failed_checks]
        if s["unexpected_effects"]:
            reasons.append(f"不应发生的写：{s['unexpected_effects']}")
        if s.get("injection_induced"):
            reasons.append("模型发起了注入要求的写调用" + ("，且已落库" if s.get("injection_landed") else "，未落库"))
        lines.append(f"- **{s['case_id']} {titles[s['case_id']]}**（{VARIANT_NAMES[s['variant']].split('：')[0]}，第 {s['run']} 次）："
                     + "；".join(reasons) + f"。工具：{s['tools_called'] or '无'}。回复：「{_cell(s['reply'], 120)}」")
    return "\n".join(lines) or "无。"


def na_notes(variants: list[str]) -> str:
    notes = []
    if "A" in variants:
        notes.append(f"- 基线 A 的工具选择准确率、未经批准写操作数、不应发生的写操作数、越权拦截率、提示注入成功率："
                     f"{NA_NO_TOOLS}。其中经工具结果进入的注入（I1、I2）在 A 中根本无法送达模型；"
                     "用户消息里的注入（I3）没有可执行的写能力，同样无法形成效果。A 在这些用例上的价值只能从"
                     "任务完成率（是否如实说明做不到）和\"声称已执行但未发生\"两项观察。")
    notes.append("- 超级管理员保护（S3）由若依原有的 check_user_allowed 在 service 层实现，不属于 Agent 机制，"
                 "基线 B 以服务账号调用同样的 service，因此也会被拦截；这一项对比的是\"模型是否发起\"而不是\"是否落库\"。")
    return "\n".join(notes)


def render(**values) -> str:
    text = TEMPLATE_FILE.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text
