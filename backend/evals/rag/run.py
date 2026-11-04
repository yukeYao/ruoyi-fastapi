"""
知识库（RAG）评估入口（手动执行；retrieval / e2e 会调用真实 Embedding 和对话模型并产生费用，不进 pytest/CI）。

    cd backend
    python -m evals.rag.run --plan                         # 只打印数据集规模、调用次数和费用估算，不调用任何 API
    python -m evals.rag.run --stage retrieval              # 检索评估：dev 校准门槛，test 报告
    python -m evals.rag.run --stage e2e --runs 2           # 端到端评估（使用同一目录里检索评估校准出的门槛）
    python -m evals.rag.run --stage all --runs 2

- 只连测试库 ry-vue-test + Redis db 1（导入应用前设置环境变量，启动后再校验），会重建测试库；不要和 pytest 同时运行。
- 索引、checkpoint 都放在本次运行目录 evals/results/<时间>-rag-eval/，不碰开发环境的 data/。
- 测试数据用 smoke_ev_ 前缀，结束时按记录物理删除（rag_smoke.cleanup_smoke_data）。
- 不读取、不打印任何 Key；结果文件里也没有 Key。
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE.parent / "results"
RETRIEVAL_DATASET = HERE / "retrieval_dataset.json"
E2E_DATASET = HERE / "e2e_dataset.json"
FIXTURE_DOCS = HERE / "fixtures"

# 按 OpenAI 公开价格（美元 / 百万 Token），运行前请核对；报告里注明这是估算
PRICES = {"input": 0.15, "output": 0.60, "embedding": 0.02}

# 每类用例预计的模型调用次数（含可能的引用修复），用于 --plan 估算；来自此前冒烟的实测步数
EXPECTED_CALLS = {"rule": 2.5, "live": 4.5, "no_answer": 2.5, "restricted": 2.5, "injection": 2.5, "forged": 2.5,
                  "followup": 5.0, "sse": 2.5, "perm_advice": 2.0}
# 每次工具结果平均增加的输入 Token（知识库片段较长、业务工具结果较短），按冒烟实测反推
RESULT_TOKENS = 900


def _set_test_environment(out_dir: Path) -> None:
    os.environ["MYSQL_DATABASE"] = "ry-vue-test"
    os.environ["REDIS_DB"] = "1"
    os.environ["AGENT_CHECKPOINT_PATH"] = str(out_dir / "checkpoints.sqlite")
    os.environ["KNOWLEDGE_INDEX_PATH"] = str(out_dir / "knowledge_index")


def load_e2e_cases() -> list[dict]:
    return json.loads(E2E_DATASET.read_text(encoding="utf-8"))["cases"]


def plan(runs: int, variant_names: list[str]) -> dict:
    """规模与费用估算：按每个方案实际绑定的工具测量系统提示词和工具定义的大小，再按每类预计步数估算"""
    from app.agent import policy
    from app.agent.tools import TOOLS, tool_schema
    from evals.rag.retrieval import load_cases
    from evals.rag.runner import VARIANTS

    # 字符数换算 Token：中文提示词约 1.4 字符 / Token，工具定义 JSON 以英文为主约 4 字符 / Token。
    # 用这两个比例回算此前冒烟（R1 两步实测 7033、R2 四步实测 16473 输入 Token），估算偏高约 20%，作为保守估计
    def tokens(prompt_chars: int, json_chars: int) -> float:
        return prompt_chars / 1.4 + json_chars / 4

    e2e = load_e2e_cases()
    retrieval = load_cases(RETRIEVAL_DATASET)
    out = {"retrieval_queries": len(retrieval), "e2e_cases": len(e2e), "runs": runs, "variants": {}}
    total_in = total_out = 0.0
    for name in variant_names:
        v = VARIANTS[name]
        specs = [s for s in TOOLS.values() if v.tools is None or s.name in v.tools]
        base = tokens(len(policy.SYSTEM_PROMPT), len(json.dumps([tool_schema(s) for s in specs], ensure_ascii=False)))
        cases = [c for c in e2e if c["category"] in v.categories]
        calls = sum(EXPECTED_CALLS[c["category"]] for c in cases) * runs
        # 第 n 次调用的输入 ≈ 基础提示 + 之前 n-1 次工具结果
        per_case_in = [sum(base + RESULT_TOKENS * i for i in range(int(round(EXPECTED_CALLS[c["category"]]))))
                       for c in cases]
        est_in = sum(per_case_in) * runs
        est_out = calls * 120
        total_in += est_in
        total_out += est_out
        out["variants"][name] = {"cases": len(cases), "runs": len(cases) * runs, "model_calls": round(calls),
                                 "input_tokens": round(est_in), "output_tokens": round(est_out)}
    # Embedding：分块（约 60 段、平均 150 Token）+ 检索评估问题 + 端到端里每次检索的问题
    embed = 60 * 150 + len(retrieval) * 20 + sum(v["runs"] for v in out["variants"].values()) * 40
    out.update(input_tokens=round(total_in), output_tokens=round(total_out), embedding_tokens=embed,
               cost_usd=round(total_in / 1e6 * PRICES["input"] + total_out / 1e6 * PRICES["output"]
                              + embed / 1e6 * PRICES["embedding"], 4))
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="知识库（RAG）评估")
    parser.add_argument("--plan", action="store_true", help="只打印规模与费用估算，不调用任何 API")
    parser.add_argument("--stage", choices=["retrieval", "e2e", "all"])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--variants", nargs="+", default=["no_rag", "bm25", "vector", "hybrid", "hybrid_tools"])
    parser.add_argument("--cases", nargs="+", help="端到端只运行这些用例 id")
    parser.add_argument("--out", type=Path, help="复用已有的运行目录（例如先跑 retrieval、再跑 e2e）")
    args = parser.parse_args(argv)

    out_dir = args.out or RESULTS_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-rag-eval")
    # --plan 也先切到测试库配置：它会导入应用模块（只读取提示词和工具定义，不连库、不调 API）
    _set_test_environment(out_dir)
    if args.plan:
        print(json.dumps(plan(args.runs, args.variants), ensure_ascii=False, indent=1))
        return
    if not args.stage:
        parser.error("需要 --plan 或 --stage")
    out_dir.mkdir(parents=True, exist_ok=True)

    from evals.rag import pipeline

    pipeline.run(out_dir, args.stage, args.runs, args.variants, args.cases)


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parent.parent))
    main()
