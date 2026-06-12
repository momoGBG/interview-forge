"""Frontier —— 实时前沿增强（Phase 5）。

当题目涉及最新技术(本月新模型/新论文)时，出网现抓权威资料摄入 KB，再让 AnswerForge
带出处作答。后端：
  · firecrawl  —— 若配了 firecrawl.api_key，用 /v2/search（research/github 类目）
  · hf(默认)   —— 免 key：HuggingFace papers 搜索→arXiv PDF + HF 模型卡（均经代理）
"""
from __future__ import annotations

import httpx

from ..infra.config import load_config
from ..infra.db import connect
from .ingest import IngestPipeline


class Frontier:
    def __init__(self):
        self.cfg = load_config()
        self.proxy = self.cfg.get("proxy")
        self.fc = self.cfg.get("firecrawl", {}) or {}
        self.fr = self.cfg.get("frontier", {}) or {}

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=40, trust_env=False, follow_redirects=True,
                            proxy=self.proxy,
                            headers={"User-Agent": "interview-forge"})

    # ---------- 各后端搜索 ----------
    def search_papers(self, query: str, k: int) -> list[dict]:
        with self._client() as c:
            r = c.get("https://huggingface.co/api/papers/search", params={"q": query})
            r.raise_for_status()
            items = r.json()
        out = []
        for it in items[: k * 2]:
            p = it.get("paper", it)
            aid = p.get("id")
            if not aid:
                continue
            out.append({"title": p.get("title", aid),
                        "url": f"https://arxiv.org/pdf/{aid}", "kind": "arxiv"})
            if len(out) >= k:
                break
        return out

    def search_models(self, query: str, k: int) -> list[dict]:
        with self._client() as c:
            r = c.get("https://huggingface.co/api/models",
                      params={"search": query, "limit": k, "sort": "trendingScore"})
            r.raise_for_status()
            models = r.json()
        return [{"title": m.get("id"), "url": f"https://huggingface.co/{m['id']}/raw/main/README.md",
                 "kind": "hf_card"} for m in models if m.get("id")]

    def search_web(self, query: str, k: int) -> list[dict]:
        """DuckDuckGo 免 key 全网搜索（HTML 端点，经代理）。

        能抓到官方博客/发布公告这类**非论文**网页（如 qwen.ai/blog），
        弥补 arXiv/HF 后端搜不到「新模型发布」的盲区。
        """
        # 首选 ddgs 库：自动处理 vqd token/端点轮换，比裸爬稳（裸爬常被 202 反爬拦）
        try:
            from ddgs import DDGS
            with DDGS(proxy=self.proxy, timeout=20) as d:
                rs = list(d.text(query, max_results=k))
            out = []
            seen = set()
            for r in rs:
                u = r.get("href") or r.get("url")
                if not u or u in seen:
                    continue
                seen.add(u)
                out.append({"title": (r.get("title") or u)[:120], "url": u, "kind": "web"})
            if out:
                return out[:k]
        except Exception:  # noqa: BLE001 — ddgs 不可用则退回裸爬
            pass

        from urllib.parse import parse_qs, unquote, urlparse

        from bs4 import BeautifulSoup
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        out: list[dict] = []
        for endpoint, sel in (("https://html.duckduckgo.com/html/", "a.result__a"),
                              ("https://lite.duckduckgo.com/lite/", "a.result-link")):
            try:
                with httpx.Client(timeout=30, trust_env=False, follow_redirects=True,
                                  proxy=self.proxy, headers={"User-Agent": ua}) as c:
                    r = c.post(endpoint, data={"q": query, "kl": "us-en"})
                    r.raise_for_status()
                    html = r.text
            except Exception:  # noqa: BLE001 — 端点不通就换下一个
                continue
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select(sel):
                href = a.get("href") or ""
                if "uddg=" in href:  # DDG 跳转链接 //duckduckgo.com/l/?uddg=<encoded>
                    href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
                if not href.startswith("http") or "duckduckgo.com" in urlparse(href).netloc:
                    continue
                out.append({"title": (a.get_text(" ", strip=True) or href)[:120],
                            "url": href, "kind": "web"})
            if out:
                break
        seen, dedup = set(), []
        for h in out:
            if h["url"] in seen:
                continue
            seen.add(h["url"])
            dedup.append(h)
        return dedup[:k]

    def search_firecrawl(self, query: str, k: int) -> list[dict]:
        key = self.fc.get("api_key")
        if not key:
            return []
        with self._client() as c:
            r = c.post(f"{self.fc.get('base_url','https://api.firecrawl.dev')}/v2/search",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"query": query, "limit": k,
                             "categories": ["research", "github"],
                             "scrapeOptions": {"formats": ["markdown"]}})
            r.raise_for_status()
            data = r.json()
        out = []
        for it in (data.get("data") or data.get("web") or [])[:k]:
            out.append({"title": it.get("title") or it.get("url"),
                        "url": it.get("url"), "kind": "web",
                        "markdown": it.get("markdown")})
        return out

    def search(self, query: str, k: int,
               kinds=("web", "papers", "models")) -> list[dict]:
        backend = self.fr.get("backend", "auto")
        if (backend == "firecrawl" or
                (backend == "auto" and self.fc.get("api_key"))):
            hits = self.search_firecrawl(query, k)
            if hits:
                return hits
        # 免 key：全网(DDG) + 论文 + 模型卡，按来源轮流取，保证多样性
        buckets = []
        if "web" in kinds:
            buckets.append(self.search_web(query, k))
        if "papers" in kinds:
            buckets.append(self.search_papers(query, k))
        if "models" in kinds:
            buckets.append(self.search_models(query, k))
        from itertools import zip_longest
        merged, seen = [], set()
        for group in zip_longest(*buckets):
            for h in group:
                if not h or h["url"] in seen:
                    continue
                seen.add(h["url"])
                merged.append(h)
        return merged[:k]

    # ---------- 检索 + 摄入 ----------
    def _seen(self, url: str) -> bool:
        with connect(self.cfg) as conn:
            return conn.execute("SELECT 1 FROM sources WHERE url=%s LIMIT 1",
                                (url,)).fetchone() is not None

    def ingest(self, query: str, *, k: int | None = None,
               kinds=("web", "papers", "models"), contextual: bool = False) -> dict:
        k = k or self.fr.get("k", 3)
        cands = self.search(query, k, kinds=kinds)
        pipe = IngestPipeline()
        ingested, skipped, errors = [], 0, []
        try:
            for cd in cands:
                url = cd.get("url")
                if not url or self._seen(url):
                    skipped += 1
                    continue
                try:
                    if cd.get("markdown"):  # firecrawl 直接给了正文
                        res = pipe.run(text=cd["markdown"], contextual=contextual)
                    else:
                        res = pipe.run(url=url, contextual=contextual)
                    res["url"] = url
                    res["from"] = cd["kind"]
                    ingested.append(res)
                except Exception as e:  # noqa: BLE001
                    errors.append({"url": url, "error": str(e)[:120]})
        finally:
            pipe.close()
        return {"query": query, "candidates": len(cands), "ingested": ingested,
                "skipped": skipped, "errors": errors}

    # ---------- 按 JD 抽检索词 → 抓该岗位最新论文 ----------
    def jd_queries(self, jd_id: int, n: int = 4) -> list[str]:
        """让 LLM 从 JD 正文里提炼 n 个适合 arXiv/HF 检索的英文技术关键词。"""
        from ..infra.llm import LLM
        with connect(self.cfg) as conn:
            jd = conn.execute("SELECT company, role, raw_text FROM jds WHERE id=%s",
                              (jd_id,)).fetchone()
        if not jd:
            raise ValueError(f"JD {jd_id} 不存在")
        from ..infra.config import profile_cfg
        field = profile_cfg(self.cfg)["field"]
        sys = (f"你是「{field}」领域的技术情报检索专家。读岗位 JD，提炼出最值得去 "
               "arXiv/HuggingFace 搜**最新论文/技术资料**的英文技术检索词"
               "（具体到方法/模型/框架名，而非宽泛大词）。"
               f'只输出 JSON：{{"queries":["...", 共 {n} 个]}}')
        user = f"岗位：{jd[1]} @ {jd[0]}\nJD 正文：\n{jd[2][:2200]}"
        llm = LLM(self.cfg)
        try:
            raw = llm.chat(sys, user, max_tokens=800, temperature=0.3, think=False)
        finally:
            llm.close()
        import json as _json
        import re as _re
        m = _re.search(r"\{.*\}", raw, flags=_re.DOTALL)
        try:
            qs = _json.loads(m.group(0)).get("queries", []) if m else []
        except Exception:  # noqa: BLE001
            qs = []
        return [q for q in qs if isinstance(q, str) and q.strip()][:n]

    def ingest_for_jd(self, jd_id: int, *, k_per_query: int = 2,
                      n_queries: int = 4) -> dict:
        """对某 JD 自动检索词→逐词抓最新论文/模型卡入库。返回每个词的命中。"""
        queries = self.jd_queries(jd_id, n=n_queries)
        per_query, total_ingested = [], []
        for q in queries:
            res = self.ingest(q, k=k_per_query)
            per_query.append({"query": q, "ingested": len(res["ingested"]),
                              "skipped": res["skipped"]})
            total_ingested += res["ingested"]
        return {"jd_id": jd_id, "queries": queries, "per_query": per_query,
                "n_new_sources": len(total_ingested),
                "ingested": [{"title": x["title"], "url": x.get("url"),
                              "n_chunks": x["n_chunks"]} for x in total_ingested]}

    def close(self):
        pass
