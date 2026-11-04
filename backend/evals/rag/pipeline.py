"""
评估流程（由 run.py 在设置好测试库环境变量之后导入）：

1. 重建测试库（若依 + 智能助手 + 知识库表），关闭验证码；
2. 检索评估：用配置的 Embedding 建索引（只含正式文档），每个问题算一次向量并缓存，
   收集可读集合内的两路候选，在 dev 上按方式分别校准门槛，在 test 上报告（同时给出当前默认门槛在 test 上的结果作对照）；
3. 端到端评估：索引加入评估专用的注入文档，门槛使用第 2 步在 dev 上校准的值（没有就用当前配置，并在报告里写明），
   按方案逐条运行、评分、汇总，导出人工核对清单；
4. 无论成功失败，按记录物理删除 smoke_ev_ 数据、会话、审批记录、知识库行、登录态和频率限制计数。
"""
import json
import shutil
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.agent.model import is_model_configured
from app.config import BACKEND_DIR, settings
from app.core.constants import KNOWLEDGE_RRF_K, KNOWLEDGE_TOP_K
from app.core.database import SessionLocal
from app.rag import embedding
from app.rag.documents import load_docs
from app.rag.retriever import Thresholds
from app.services import knowledge_service
from evals import environment as env
from evals.rag import metrics, report, retrieval, runner
from evals.rag import run as run_config
from evals.rag.run import E2E_DATASET, FIXTURE_DOCS, PRICES, RETRIEVAL_DATASET, load_e2e_cases
from evals.rag_smoke import SmokeRecords, cleanup_smoke_data
from evals.run_eval import _git_commit
from scripts.init_db import AGENT_SQL_FILE, KNOWLEDGE_SQL_FILE, create_database_if_not_exists, import_sql_file

DOCS_DIR = BACKEND_DIR.parent / "docs" / "knowledge"


def _check_environment(out_dir: Path, stage: str) -> None:
    env.ensure_test_environment()
    if not settings.knowledge_index_dir.resolve().is_relative_to(out_dir.resolve()):
        raise SystemExit("索引目录不在本次运行目录里，停止运行")
    if not embedding.is_embedding_configured():
        raise SystemExit("未配置 Embedding（AGENT_EMBEDDING_MODEL、AGENT_EMBEDDING_API_KEY）")
    if stage in ("e2e", "all") and not is_model_configured():
        raise SystemExit("未配置对话模型（AGENT_MODEL、AGENT_API_KEY）")


def _rebuild_test_db() -> None:
    create_database_if_not_exists()
    import_sql_file()
    import_sql_file(AGENT_SQL_FILE)
    import_sql_file(KNOWLEDGE_SQL_FILE)
    env.redis_client.flushdb()
    env.sql("UPDATE sys_config SET config_value = 'false' WHERE config_key = 'sys.account.captchaEnabled'")


def _build(docs_dir: Path, model, records: SmokeRecords) -> dict:
    db = SessionLocal()
    try:
        docs = load_docs(docs_dir)
        built = knowledge_service.build_index(db, docs, model, settings.knowledge_index_dir)
    finally:
        db.close()
    records.knowledge_doc_ids = sorted(set(records.knowledge_doc_ids) | {d.doc_id for d in docs})
    records.build_id = built.build_id
    knowledge_service.clear_cache()
    return {"build_id": built.build_id, "chunks": built.chunk_count, "embedded": built.embedded}


def spent(input_tokens: int, output_tokens: int, embedding_tokens: int) -> float:
    return (input_tokens / 1e6 * PRICES["input"] + output_tokens / 1e6 * PRICES["output"]
            + embedding_tokens / 1e6 * PRICES["embedding"])


def over_budget(input_tokens: int, output_tokens: int, embedding_tokens: int) -> str:
    """超出预算时返回原因，否则返回空字符串"""
    cost = spent(input_tokens, output_tokens, embedding_tokens)
    if cost > run_config.BUDGET_USD:
        return f"累计估算费用 {cost:.4f} 美元超过上限 {run_config.BUDGET_USD} 美元"
    if input_tokens > run_config.BUDGET_INPUT_TOKENS:
        return f"累计输入 Token {input_tokens} 超过上限 {run_config.BUDGET_INPUT_TOKENS}"
    return ""


def _thresholds_dict(t: Thresholds) -> dict:
    return asdict(t)


def run_retrieval(out_dir: Path, model, records: SmokeRecords) -> dict:
    index = _build(DOCS_DIR, model, records)
    cases = retrieval.load_cases(RETRIEVAL_DATASET)
    vectors = retrieval.embed_queries(cases, model, out_dir / "query_vectors.json")
    db = SessionLocal()
    try:
        candidates = retrieval.collect_candidates(db, cases, vectors)
    finally:
        db.close()
    dev = [c for c in cases if c["split"] == "dev"]
    test = [c for c in cases if c["split"] == "test"]
    defaults = knowledge_service.current_thresholds()
    result = {"index": index, "dataset_version": json.loads(RETRIEVAL_DATASET.read_text(encoding="utf-8"))["version"],
              "n_dev": len(dev), "n_test": len(test), "top_k": KNOWLEDGE_TOP_K, "defaults": _thresholds_dict(defaults),
              "modes": {}}
    for mode in metrics.MODES:
        # 校准只看 dev；test 只用来报告校准后的门槛和默认门槛的效果
        tuned, dev_m = metrics.calibrate(dev, candidates, mode, KNOWLEDGE_TOP_K, KNOWLEDGE_RRF_K)
        test_m = metrics.retrieval_metrics(test, candidates, mode, tuned, KNOWLEDGE_TOP_K, KNOWLEDGE_RRF_K)
        default_m = metrics.retrieval_metrics(test, candidates, mode, defaults, KNOWLEDGE_TOP_K, KNOWLEDGE_RRF_K)
        result["modes"][mode] = {"thresholds": _thresholds_dict(tuned), "dev": dev_m, "test": test_m,
                                 "test_with_defaults": default_m}
    (out_dir / "candidates.json").write_text(json.dumps(candidates, ensure_ascii=False), encoding="utf-8")
    (out_dir / "retrieval.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def _save_traces(out_dir: Path, traces: list[dict], scored: list[dict]) -> None:
    (out_dir / "traces.json").write_text(json.dumps(traces, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out_dir / "scored.json").write_text(json.dumps(scored, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def run_e2e(out_dir: Path, model, records: SmokeRecords, runs: int, variant_names: list[str],
            case_ids: list[str] | None, prior_embedding_tokens: int = 0, resume: bool = False,
            max_minutes: float | None = None) -> dict:
    """
    resume：读取本目录已有的 traces/scored，跳过已完成的（方案, 用例, 第几次），接着运行；
    max_minutes：本次调用运行到这个时长就停下保存（不是预算停止，下次 --resume 继续）。
    两者配合把很长的评估拆成多次前台运行，避免评估在后台运行时与 pytest 抢测试库
    """
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(exist_ok=True)
    for path in [*DOCS_DIR.glob("*.md"), *FIXTURE_DOCS.glob("*.md")]:
        shutil.copy(path, docs_dir / path.name)
    index = _build(docs_dir, model, records)

    retrieval_file = out_dir / "retrieval.json"
    tuned = json.loads(retrieval_file.read_text(encoding="utf-8"))["modes"] if retrieval_file.exists() else {}
    thresholds_source = "dev 校准" if tuned else "当前配置（本目录没有检索评估结果）"
    defaults = knowledge_service.current_thresholds()

    headers = runner.prepare_data(records)
    cases = [c for c in load_e2e_cases() if not case_ids or c["id"] in case_ids]
    traces, scored = [], []
    if resume and (out_dir / "traces.json").exists():
        traces = json.loads((out_dir / "traces.json").read_text(encoding="utf-8"))
        scored = json.loads((out_dir / "scored.json").read_text(encoding="utf-8"))
    done = {(t["variant"], t["case_id"], t["run"]) for t in traces}
    deadline = time.monotonic() + max_minutes * 60 if max_minutes is not None else None
    stopped = paused = ""
    for name in variant_names:
        variant = runner.VARIANTS[name]
        t = Thresholds(**tuned[variant.mode]["thresholds"]) if variant.mode in tuned else defaults
        for case in [c for c in cases if c["category"] in variant.categories]:
            for run in range(1, runs + 1):
                if stopped or paused:
                    break
                if (name, case["id"], run) in done:
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    paused = f"本次运行达到 {max_minutes} 分钟，已保存，用 --resume 继续"
                    print(paused, flush=True)
                    break
                trace = runner.run_case(case, variant, run, headers, t, model, records)
                result = metrics.score_e2e(case, trace)
                traces.append(trace)
                scored.append({**result, "variant": name, "run": run})
                verdict = "通过" if result["passed"] else "未通过：" + ",".join(k for k, v in result["checks"].items() if v is False)
                print(f"[{name}] {case['id']} 第{run}次 {verdict}  {trace['latency']}s  "
                      f"Token={trace['input_tokens']}/{trace['output_tokens']}" + (f"  错误：{trace['error']}" if trace["error"] else ""),
                      flush=True)
                # 每条用例后落盘：中途停止或出错时已有结果不丢
                _save_traces(out_dir, traces, scored)
                # 预算按实际 usage 累计（含检索评估和索引构建用掉的 Embedding Token）
                stopped = over_budget(sum(x["input_tokens"] for x in traces), sum(x["output_tokens"] for x in traces),
                                      getattr(model, "total_tokens", 0) + prior_embedding_tokens)
                if stopped:
                    print(f"预算保护：{stopped}，停止运行并保存已有结果", flush=True)
    ran = [name for name in variant_names if any(t["variant"] == name for t in traces)]
    summary = {name: metrics.aggregate_e2e([s for s in scored if s["variant"] == name],
                                           [t for t in traces if t["variant"] == name], PRICES)
               for name in ran}
    remaining = sum(1 for name in variant_names for c in cases if c["category"] in runner.VARIANTS[name].categories
                    for r in range(1, runs + 1) if (name, c["id"], r) not in {(t["variant"], t["case_id"], t["run"]) for t in traces})
    result = {"index": index, "thresholds_source": thresholds_source, "runs": runs, "stopped": stopped,
              "paused": paused, "remaining": remaining,
              "dataset_version": json.loads(E2E_DATASET.read_text(encoding="utf-8"))["version"],
              "model_ids": sorted({n for t in traces for n in t["model_names"]}), "summary": summary}
    _save_traces(out_dir, traces, scored)
    (out_dir / "e2e.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out_dir / "manual_review.md").write_text(report.manual_review(cases, traces, scored), encoding="utf-8")
    return result


def run(out_dir: Path, stage: str, runs: int, variant_names: list[str], case_ids: list[str] | None,
        resume: bool = False, max_minutes: float | None = None) -> None:
    _check_environment(out_dir, stage)
    model = embedding.get_embedding_model()
    # 整次评估共用一个 Embedding 客户端：它累计接口返回的 Token，用于统计成本
    embedding.get_embedding_model = lambda: model
    records = SmokeRecords()
    # 两步运行（先 retrieval、后 e2e，同一目录）时 meta 累计，不互相覆盖
    meta_file = out_dir / "meta.json"
    previous = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    prior_embedding = int(previous.get("embedding_tokens_total", 0))
    meta = {"commit": _git_commit(), "chat_model": settings.agent_model, "embedding_model": model.name,
            "prices": PRICES, "budget": {"usd": run_config.BUDGET_USD, "input_tokens": run_config.BUDGET_INPUT_TOKENS},
            "stages": previous.get("stages", [])}
    started = datetime.now()
    try:
        _rebuild_test_db()
        if stage in ("retrieval", "all"):
            r = run_retrieval(out_dir, model, records)
            print(f"检索评估完成：dev {r['n_dev']} 条、test {r['n_test']} 条")
        if stage in ("e2e", "all"):
            e = run_e2e(out_dir, model, records, runs, variant_names, case_ids, prior_embedding, resume, max_minutes)
            print(f"端到端评估：剩余 {e['remaining']} 次；模型 ID {e['model_ids']}"
                  + (f"；提前停止：{e['stopped']}" if e["stopped"] else ""))
        meta["embedding_tokens_total"] = prior_embedding + getattr(model, "total_tokens", 0)
        meta["stages"].append({"stage": stage, "commit": meta["commit"], "started": started.strftime("%Y-%m-%d %H:%M:%S"),
                               "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               "embedding_tokens": getattr(model, "total_tokens", 0)})
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / "report.md").write_text(report.render(out_dir), encoding="utf-8")
        print(f"报告：{out_dir / 'report.md'}")
    finally:
        try:
            cleanup_smoke_data(env, records)
        finally:
            from app.agent.checkpointer import close_checkpointer

            knowledge_service.clear_cache()
            close_checkpointer()
            for path in out_dir.glob("checkpoints.sqlite*"):
                path.unlink(missing_ok=True)
