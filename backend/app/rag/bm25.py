"""
BM25（Okapi）关键词打分。没有用 rank-bm25：它从 2022 年起没有发版，而这里需要的只有几十行。

只在调用方传入的语料上计算：语料必须是"当前用户可读的分块"，所以文档频率、平均长度、IDF
都只来自可读子集，受限文档的统计信息不会影响分数（否则分数本身就可能透露受限文档的存在）。
语料只有几百个分块，每次检索现建现算，耗时在毫秒级。
"""
import math
from collections import Counter


class BM25:
    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_freqs = [Counter(doc) for doc in corpus]
        self.doc_lens = [len(doc) for doc in corpus]
        self.avg_len = (sum(self.doc_lens) / len(corpus)) if corpus else 0.0
        df: Counter = Counter()
        for freqs in self.doc_freqs:
            df.update(freqs.keys())
        n = len(corpus)
        # Lucene 的 IDF 写法：恒为正，出现在一半以上文档里的词也不会得到负分
        self.idf = {term: math.log((n - f + 0.5) / (f + 0.5) + 1.0) for term, f in df.items()}

    def score(self, query: list[str]) -> list[tuple[float, int]]:
        """每篇语料的 (BM25 分数, 命中的不同查询词个数)；查询词重复只算一次"""
        terms = [t for t in dict.fromkeys(query) if t in self.idf]
        results = []
        for freqs, length in zip(self.doc_freqs, self.doc_lens):
            total, matched = 0.0, 0
            norm = self.k1 * (1 - self.b + self.b * length / self.avg_len) if self.avg_len else self.k1
            for term in terms:
                tf = freqs.get(term, 0)
                if tf:
                    matched += 1
                    total += self.idf[term] * tf * (self.k1 + 1) / (tf + norm)
            results.append((total, matched))
        return results
