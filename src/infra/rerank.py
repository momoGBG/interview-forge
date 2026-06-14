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
        self._model = self.c["model"]
        self._client = httpx.Client(timeout=120, trust_env=False)

    def _discover_model(self) -> str | None:
        """服务端重新部署后 served-model-name 常与配置不一致(404)。
        从 /v1/models 自动发现真实模型名。"""
        try:
            base = self.c["url"].split("/v1/")[0]
            r = self._client.get(base + "/v1/models", timeout=10)
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            for mid in ids:
                if "rerank" in mid.lower():
                    return mid
            return ids[0] if ids else None
        except Exception:  # noqa: BLE001
            return None

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        """返回 [(原始文档下标, 相关性分数)]，按分数降序，截断 top_k。"""
        if not documents:
            return []
        body = {"model": self._model, "query": query, "documents": documents}
        resp = self._client.post(self.c["url"], json=body)
        if resp.status_code == 404:  # vLLM 对未知 model 名返回 404 → 自动发现后重试一次
            real = self._discover_model()
            if real and real != self._model:
                self._model = real
                body["model"] = real
                resp = self._client.post(self.c["url"], json=body)
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
