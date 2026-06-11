"""Qwen3-Reranker 调用（vLLM /v1/rerank，Cohere/Jina 风格）。

请求: {model, query, documents:[str]}
响应: {results:[{index, relevance_score, document}]}
"""
from __future__ import annotations

import httpx

from .config import load_config


class Reranker:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or load_config()
        self.c = cfg["reranker"]
        self._client = httpx.Client(timeout=120, trust_env=False)

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        """返回 [(原始文档下标, 相关性分数)]，按分数降序，截断 top_k。"""
        if not documents:
            return []
        resp = self._client.post(
            self.c["url"],
            json={"model": self.c["model"], "query": query, "documents": documents},
        )
        resp.raise_for_status()
        results = resp.json()["results"]
        ranked = sorted(
            ((r["index"], r["relevance_score"]) for r in results),
            key=lambda t: t[1],
            reverse=True,
        )
        return ranked[:top_k]

    def close(self):
        self._client.close()
