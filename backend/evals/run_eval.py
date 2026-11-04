"""
运行评估（手动执行，会调用真实模型并产生费用；不进 pytest/CI）。

    cd backend
    python -m evals.run_eval                      # 三种方案、全部用例、各跑 1 次
    python -m evals.run_eval --runs 3 --variants full B --cases W1 S1
    python -m evals.run_eval --runs 3 --variants B --cases S1 S2 S6 --note "补充运行：……"

结果写到 evals/results/<时间>/：traces.json（原始轨迹）、scored.json（逐条评分）、report.md（报告）。
results 目录不提交：轨迹里有模型原文，体积大，且每次运行都不同。
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _set_test_environment(out_dir: Path) -> None:
    # 必须在导入 app 之前设置：settings 在导入时读取环境变量（环境变量优先于 .env）
    os.environ["MYSQL_DATABASE"] = "ry-vue-test"
    os.environ["REDIS_DB"] = "1"
    os.environ["AGENT_CHECKPOINT_PATH"] = str(out_dir / "checkpoints.sqlite")


def _git_commit() -> str:
    """报告写明代码版本；有未提交的已跟踪改动时标注，避免报告对应不上代码"""
    root = Path(__file__).resolve().parent.parent.parent
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "未知（无法读取 git）"
    return head + ("（工作区有未提交的改动）" if dirty else "")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="智能助手评估")
    parser.add_argument("--variants", nargs="+", default=["A", "B", "full"], choices=["A", "B", "full"])
    parser.add_argument("--cases", nargs="+", help="只运行这些用例 id")
    parser.add_argument("--runs", type=int, default=1)
    # 部分用例 / 部分方案的补充运行必须在报告里写明，避免被当成一次完整评估
    parser.add_argument("--note", default="", help="写进报告的运行说明，例如补充运行的原因和范围")
    args = parser.parse_args(argv)

    out_dir = RESULTS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    _set_test_environment(out_dir)

    from app.agent.model import is_model_configured
    from app.config import settings
    from evals import environment as env
    from evals import report, runners
    from evals.dataset import dataset_version, load_cases
    from evals.scoring import VARIANTS, aggregate, score_case

    env.ensure_test_environment()
    if not is_model_configured():
        raise SystemExit("未配置模型：请在 backend/.env 中设置 AGENT_MODEL 和 AGENT_API_KEY")

    cases = load_cases(args.cases)
    variants = [v for v in VARIANTS if v in args.variants]
    env.prepare()

    commit = _git_commit()
    traces, scored = [], []
    total = len(variants) * len(cases) * args.runs
    done = 0
    for variant in variants:
        for case in cases:
            for run in range(1, args.runs + 1):
                headers = env.reset()
                trace = runners.run_case(variant, case, run, headers)
                result = score_case(case, trace)
                traces.append(trace)
                scored.append(result)
                done += 1
                status = "通过" if result["task_ok"] else "未通过"
                print(f"[{done}/{total}] {variant:<4} {case['id']} 第{run}次：{status}  {trace['latency']}s"
                      + (f"  错误：{trace['error']}" if trace["error"] else ""), flush=True)

    metrics = {v: aggregate(v, scored) for v in variants}
    (out_dir / "traces.json").write_text(json.dumps(traces, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out_dir / "scored.json").write_text(json.dumps({"metrics": metrics, "cases": scored}, ensure_ascii=False,
                                                    indent=1, default=str), encoding="utf-8")
    text = report.render(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        model=settings.agent_model,
        model_ids="、".join(sorted({n for t in traces for n in t.get("model_names", [])})) or "未返回",
        commit=commit, dataset_version=dataset_version(),
        run_note=args.note or ("完整评估" if not args.cases and len(variants) == len(VARIANTS)
                               else "部分运行：只包含上面列出的方案和用例"),
        sample_size=f"每个方案 {len(cases)} 条 × {args.runs} 次 = {len(cases) * args.runs} 次，共 {len(traces)} 次",
        case_count=len(cases), runs=args.runs,
        variants="、".join(report.VARIANT_NAMES[v] for v in variants),
        metrics_table=report.metrics_table(variants, metrics),
        category_table=report.category_table(variants, metrics),
        case_table=report.case_table(cases, scored, variants),
        failures=report.failures(cases, scored),
        na_notes=report.na_notes(variants),
    )
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    print(f"报告：{out_dir / 'report.md'}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
