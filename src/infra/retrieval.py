"""混合检索 (§6)：dense(pgvector cosine) + BM25(tsvector) → RRF 融合 → reranker 精排。

dense 兜语义、sparse 兜精确术语(模型名/API flag/版本号)，RRF 融合后交给 reranker 取 top-k。
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import load_config
from .db import connect
from .embed import Embedder
from .rerank import Reranker


@dataclass
class Hit:
    chunk_id: int
    content: str
    context: str
    source_title: str
    source_url: str | None
    score: float = 0.0


def _rrf_fuse(rankings: list[list[int]], k: int) -> dict[int, float]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)。"""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return fused


class Retriever:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.r = self.cfg["retrieval"]
        self.embedder = Embedder(self.cfg)
        self.reranker = Reranker(self.cfg)

    def search(self, query: str, *, recall_k: int | None = None,
               top_k: int | None = None) -> list[Hit]:
        recall_k = recall_k or self.r["recall_k"]
        top_k = top_k or self.r["rerank_k"]

        qvec = self.embedder.embed_query(query)

        with connect(self.cfg) as conn:
            # dense：余弦距离升序
            dense_rows = conn.execute(
                "SELECT id FROM chunks ORDER BY embedding <=> %s::vector LIMIT %s",
                (str(qvec), recall_k),
            ).fetchall()
            dense_ids = [r[0] for r in dense_rows]

            # sparse：BM25 风格 ts_rank
            sparse_rows = conn.execute(
                "SELECT id FROM chunks WHERE tsv @@ plainto_tsquery('simple', %s) "
                "ORDER BY ts_rank(tsv, plainto_tsquery('simple', %s)) DESC LIMIT %s",
                (query, query, recall_k),
            ).fetchall()
            sparse_ids = [r[0] for r in sparse_rows]

            fused = _rrf_fuse([dense_ids, sparse_ids], self.r["rrf_k"])
            if not fused:
                return []
            cand_ids = [cid for cid, _ in sorted(fused.items(), key=lambda t: t[1],
                                                 reverse=True)][:recall_k]

            rows = conn.execute(
                "SELECT c.id, c.content, c.context, s.title, s.url "
                "FROM chunks c JOIN sources s ON s.id = c.source_id "
                "WHERE c.id = ANY(%s)",
                (cand_ids,),
            ).fetchall()

        by_id = {r[0]: Hit(r[0], r[1], r[2] or "", r[3] or "", r[4]) for r in rows}
        cands = [by_id[cid] for cid in cand_ids if cid in by_id]
        if not cands:
            return []

        # reranker 精排（拿 context+content 一起，提升判别力）
        docs = [(h.context + "\n" + h.content).strip() for h in cands]
        ranked = self.reranker.rerank(query, docs, top_k)
        out: list[Hit] = []
        for idx, score in ranked:
            h = cands[idx]
            h.score = score
            out.append(h)
        return out

    def close(self):
        self.embedder.close()
        self.reranker.close()
