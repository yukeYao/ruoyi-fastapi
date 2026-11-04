"""
混合检索的纯计算部分（不连库、不调模型）：RRF 合并、相关性门槛、条数与内容总量限额。

- RRF（Reciprocal Rank Fusion）：score = Σ 1 / (k + 名次)。只用名次，不用原始分数，
  所以 BM25 分数和余弦相似度量纲不同也能直接合并。
- RRF 分数只表示"相对排名"，没有绝对含义：问题与知识库完全无关时，排第一的仍会有 RRF 分数。
  所以另加相关性门槛，候选至少满足一项才返回：余弦相似度 >= τv，或（BM25 >= τb 且命中的不同查询词 >= 最少命中数）。
  门槛来自配置（settings.knowledge_min_*），阶段 4 再用开发集校准。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    min_vector_score: float
    min_bm25_score: float
    min_bm25_terms: int


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    vector_score: float | None = None   # 余弦相似度；向量检索没返回它（或已降级）时为 None
    bm25_score: float = 0.0
    bm25_terms: int = 0


@dataclass(frozen=True)
class Ranked:
    chunk_id: str
    score: float          # RRF 分数
    vector_score: float | None
    bm25_score: float


def passes(candidate: Candidate, t: Thresholds) -> bool:
    by_vector = candidate.vector_score is not None and candidate.vector_score >= t.min_vector_score
    by_keyword = candidate.bm25_score >= t.min_bm25_score and candidate.bm25_terms >= t.min_bm25_terms
    return by_vector or by_keyword


def rrf_fuse(rankings: list[list[str]], k: int) -> dict[str, float]:
    """rankings：每路检索按相关性降序的 chunk_id 列表（名次从 1 开始）"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def rank(candidates: dict[str, Candidate], vector_ranking: list[str], bm25_ranking: list[str], thresholds: Thresholds,
         rrf_k: int, top_k: int) -> list[Ranked]:
    """先 RRF 合并两路名次，再按门槛过滤，最后取前 top_k；同分按 chunk_id 排序，结果稳定"""
    fused = rrf_fuse([vector_ranking, bm25_ranking], rrf_k)
    kept = [Ranked(cid, s, candidates[cid].vector_score, candidates[cid].bm25_score)
            for cid, s in fused.items() if passes(candidates[cid], thresholds)]
    kept.sort(key=lambda r: (-r.score, r.chunk_id))
    return kept[:top_k]


def clip(texts: list[str], max_each: int, max_total: int) -> list[str]:
    """
    按顺序截断片段：单个不超过 max_each，累计不超过 max_total。总量用完后后面的片段整条丢弃，
    而不是给每条留一点点——半句话的引用比没有更容易误导。返回的列表可能比输入短
    """
    result, used = [], 0
    for text in texts:
        piece = text if len(text) <= max_each else text[:max_each] + "…"
        if used + len(piece) > max_total:
            break
        result.append(piece)
        used += len(piece)
    return result
