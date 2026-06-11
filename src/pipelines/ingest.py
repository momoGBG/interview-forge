"""IngestPipeline (§5.1)：权威源 → 知识库。

输入：本地文件(pdf/md/txt) / 粘贴文本 / URL(出网经代理抓取)
流程：取清洗后文本 → 写 sources → 语义分块 → contextual retrieval 定位句
      → Qwen3-Embedding 向量化 + tsvector → 写 chunks。
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from ..infra.config import load_config
from ..infra.db import connect
from ..infra.embed import Embedder
from ..infra.llm import LLM

# —— token 粗估（无 tiktoken）：CJK 字符约 1 token，英文约 4 字符/token ——
def est_tokens(text: str) -> int:
    cjk = len(re.findall(r"[一-鿿]", text))
    other = len(text) - cjk
    return cjk + other // 4


# ---------- 取文本 ----------
def load_text(*, file: str | None = None, text: str | None = None,
              url: str | None = None) -> tuple[str, str, str]:
    """返回 (kind, title, markdown_text)。"""
    if text:
        title = text.strip().splitlines()[0][:80] if text.strip() else "manual"
        return "manual", title, text
    if file:
        p = Path(file)
        if p.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            content = "\n\n".join(page.extract_text() or "" for page in reader.pages)
            return "doc", p.stem, content
        return "doc", p.stem, p.read_text(encoding="utf-8", errors="ignore")
    if url:
        cfg = load_config()
        proxy = cfg.get("proxy")  # 出网走代理
        with httpx.Client(timeout=90, trust_env=False, follow_redirects=True,
                          proxy=proxy) as c:
            r = c.get(url, headers={"User-Agent": "Mozilla/5.0 interview-forge"})
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
            # PDF（如 arxiv.org/pdf/<id>，无 .pdf 后缀）→ pypdf 解析
            if "application/pdf" in ctype or url.lower().endswith(".pdf"):
                import tempfile

                from pypdf import PdfReader
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(r.content)
                    tmp_path = tmp.name
                try:
                    content = "\n\n".join(pg.extract_text() or ""
                                          for pg in PdfReader(tmp_path).pages)
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
                kind = "arxiv" if "arxiv.org" in url else "doc"
                title = url.rstrip("/").split("/")[-1]
                return kind, f"{kind}:{title}", content
            html = r.text
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        title = (soup.title.string if soup.title else url) or url
        body = soup.get_text("\n")
        body = re.sub(r"\n{3,}", "\n\n", body)
        kind = "arxiv" if "arxiv.org" in url else ("hf_card" if "huggingface" in url else "web")
        return kind, title.strip()[:120], body
    raise ValueError("需提供 file / text / url 之一")


# ---------- 分块 ----------
def chunk_text(text: str, target: int, overlap: int) -> list[str]:
    """按段落语义边界打包到 target token，块间留 overlap。不切断代码块/公式行。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for p in paras:
        pt = est_tokens(p)
        if cur and cur_tok + pt > target:
            chunks.append("\n\n".join(cur))
            # overlap：保留尾部若干段直到凑够 overlap token
            keep, kt = [], 0
            for seg in reversed(cur):
                kt += est_tokens(seg)
                keep.insert(0, seg)
                if kt >= overlap:
                    break
            cur, cur_tok = keep[:], sum(est_tokens(s) for s in keep)
        cur.append(p)
        cur_tok += pt
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


# ---------- contextual retrieval 定位句 ----------
_CTX_SYS = (
    "你为一段文档片段写一句话定位说明，便于检索时理解它在全文中的位置与主题。"
    "只输出这一句话，不要引号、不要前缀。"
)


def make_context(llm: LLM, doc_title: str, doc_head: str, chunk: str) -> str:
    user = (f"文档标题：{doc_title}\n文档开头节选：\n{doc_head[:1500]}\n\n"
            f"片段：\n{chunk[:1200]}\n\n请输出该片段的一句话定位说明：")
    try:
        ctx = llm.chat(_CTX_SYS, user, max_tokens=256, temperature=0.2, think=False)
        return ctx.splitlines()[0].strip()[:200]
    except Exception:  # noqa: BLE001 — 兜底，绝不让定位句失败阻断摄入
        return f"本段出自《{doc_title}》。"


class IngestPipeline:
    def __init__(self):
        self.cfg = load_config()
        self.r = self.cfg["retrieval"]
        self.embedder = Embedder(self.cfg)
        self.llm = LLM(self.cfg)

    def run(self, *, file: str | None = None, text: str | None = None,
            url: str | None = None, contextual: bool = True) -> dict:
        kind, title, content = load_text(file=file, text=text, url=url)
        if not content.strip():
            raise ValueError("源内容为空")

        raw_chunks = chunk_text(content, self.r["chunk_target_tokens"],
                                self.r["chunk_overlap_tokens"])

        # contextual retrieval：定位句 + 原文
        contexts = []
        for ch in raw_chunks:
            ctx = make_context(self.llm, title, content[:1500], ch) if contextual else ""
            contexts.append(ctx)
        embed_inputs = [(c + "\n\n" + ch).strip() for c, ch in zip(contexts, raw_chunks)]
        vectors = self.embedder.embed_documents(embed_inputs)

        with connect(self.cfg) as conn:
            source_id = conn.execute(
                "INSERT INTO sources (kind, title, url, raw_markdown) "
                "VALUES (%s,%s,%s,%s) RETURNING id",
                (kind, title, url, content),
            ).fetchone()[0]
            for ord_, (ch, ctx, vec) in enumerate(zip(raw_chunks, contexts, vectors)):
                conn.execute(
                    "INSERT INTO chunks (source_id, ord, content, context, embedding, "
                    "tsv, token_count) VALUES (%s,%s,%s,%s,%s::vector, "
                    "to_tsvector('simple', %s), %s)",
                    (source_id, ord_, ch, ctx, str(vec), ch, est_tokens(ch)),
                )
        return {"source_id": source_id, "title": title, "kind": kind,
                "n_chunks": len(raw_chunks)}

    def close(self):
        self.embedder.close()
        self.llm.close()
