"""
检索评估的候选收集：对每个问题，在该用户的可读集合内分别取 BM25 和向量两路候选（各最多 KNOWLEDGE_CANDIDATES 个），
与线上 knowledge_service.search_knowledge 使用同一套函数（readable、bm25_search、索引 search）。
之后不同检索方式、不同门槛的结果都由 metrics.ranked_for 离线计算，不需要重复调用 Embedding。
"""
import json
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from app.core.constants import KNOWLEDGE_CANDIDATES
from app.rag.tokenizer import tokenize
from app.services import knowledge_service

ADMIN = {"user_id": 1, "permissions": ["*:*:*"]}


def login_user_for(perms) -> dict:
    """数据集里的 perms：admin 或权限字符列表（user_id 用 0，保证不会被当成超级管理员）"""
    return ADMIN if perms == "admin" else {"user_id": 0, "permissions": list(perms)}


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def embed_queries(cases: list[dict], model, cache_file: Path) -> dict[str, list[float]]:
    """问题向量缓存在本次运行目录：同一次评估里校准、各方式对比都复用，不重复调用 Embedding"""
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    vectors = {c["id"]: model.embed_query(c["query"]).tolist() for c in cases}
    cache_file.write_text(json.dumps(vectors), encoding="utf-8")
    return vectors


def collect_candidates(db: Session, cases: list[dict], vectors: dict[str, list[float]]) -> dict[str, dict]:
    active = knowledge_service.load_active_index(db)
    by_vector = {c.vector_id: c for c in active.chunks}
    result = {}
    for case in cases:
        user = login_user_for(case["perms"])
        allowed = [c for c in active.chunks if knowledge_service.readable(user, c)]
        bm25 = knowledge_service.bm25_search(active.build_id, allowed, tokenize(case["query"]))
        hits = active.index.search(np.array(vectors[case["id"]], dtype="float32"), KNOWLEDGE_CANDIDATES,
                                   [c.vector_id for c in allowed])
        result[case["id"]] = {
            "bm25": [(c.chunk_id, round(score, 6), matched) for c, score, matched in bm25],
            "vector": [(by_vector[v].chunk_id, round(sim, 6)) for v, sim in hits],
        }
    return result
