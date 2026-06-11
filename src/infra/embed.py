"""Qwen3-Embedding 调用。

服务端输出 4096 维；这里按 Matryoshka 截断到 config.embedding.dim(默认1024)
并重新 L2 归一化，以便：(a) 适配 pgvector hnsw 索引(≤2000维)，(b) 余弦检索正确。
instruction-aware：检索 query 用 'query' 指令包裹，文档不加（Qwen3-Embedding 惯例）。
"""
from __future__ import annotations

import math

import httpx

from .config import load_config

# Qwen3-Embedding 推荐的检索任务指令
QUERY_INSTRUCT = (
    "Instruct: 给定一个面试知识检索问题，找出能权威回答它的技术文档片段\nQuery: "
)


def _l2_normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Embedder:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or load_config()
        self.c = cfg["embedding"]
        self.dim = self.c["dim"]
        self._client = httpx.Client(timeout=120, trust_env=False)

    def _embed_raw(self, inputs: list[str]) -> list[list[float]]:
        resp = self._client.post(
            self.c["url"],
            json={"model": self.c["model"], "input": inputs},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # 按 index 排序，保证与输入顺序一致
        data.sort(key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def _truncate(self, vecs: list[list[float]]) -> list[list[float]]:
        return [_l2_normalize(v[: self.dim]) for v in vecs]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._truncate(self._embed_raw(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._truncate(self._embed_raw([QUERY_INSTRUCT + text]))[0]

    def close(self):
        self._client.close()
