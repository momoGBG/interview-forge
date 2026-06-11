"""答案 → 标准 Obsidian .md（frontmatter + 正文）。"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .vault_path import notes_dir, safe_filename


def _link_citations(body_md: str, citations: list[dict] | None) -> str:
    """让 chunk 引用在 Obsidian 里可点可溯源。

    模型答案通常已自带「## 出处」段（`[chunk_N]` 裸文本 + 描述，无链接）。本函数：
      1. 抽出模型出处段里每个 chunk 的描述，并把该段从正文摘除（避免重复）；
      2. 正文内联 [chunk_N] / `[chunk_N]` → Obsidian 脚注 [^chunk_N]（可跳转）；
      3. 文末重建「## 出处」为脚注定义：模型描述 + 来源超链接（citations 带 url 时）。
    幂等性由调用方保证（已含 [^chunk_ 的笔记不再处理）。
    """
    cmap = {int(c["chunk_id"]): c for c in (citations or [])
            if c.get("chunk_id") is not None}

    # 1. 抽描述 + 摘除模型出处段
    desc: dict[int, str] = {}
    m = re.search(r"\n##\s*出处\s*\n(.*?)(?=\n#{1,6}\s|\Z)", body_md, flags=re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            lm = re.match(r"\s*[-*]\s*`?\[chunk[_\s]?(\d+)\]`?[\s:—-]*(.*)", line)
            if lm:
                desc[int(lm.group(1))] = lm.group(2).strip()
        body_md = body_md[:m.start()] + body_md[m.end():]

    ids = set(cmap) | set(desc)
    if not ids:
        return body_md
    body = body_md.rstrip()

    # 2. 内联引用 → 脚注（含反引号包裹的）
    def repl(mm: re.Match) -> str:
        cid = int(mm.group(1))
        return f"[^chunk_{cid}]" if cid in ids else mm.group(0)

    body = re.sub(r"`?\[chunk[_\s]?(\d+)\]`?", repl, body)

    # 3. 重建出处为脚注定义（citations 顺序在前，仅模型提及的在后）
    order = list(cmap) + [cid for cid in desc if cid not in cmap]
    foot = ["", "## 出处", ""]
    for cid in order:
        c = cmap.get(cid, {})
        title = (c.get("source_title") or "").strip()
        url = c.get("url")
        bits = []
        if desc.get(cid):
            bits.append(desc[cid])
        if url:
            bits.append(f"来源：[{title or '原文'}]({url})")
        elif title:
            bits.append(f"来源：{title}")
        foot.append(f"[^chunk_{cid}]: " + (" — ".join(bits) if bits else f"chunk_{cid}"))
    return body + "\n" + "\n".join(foot)


def write_note(*, qid: int, question: str, topic: str, body_md: str,
               difficulty: int | None = None, frequency: int | None = None,
               anki_synced: bool = False,
               citations: list[dict] | None = None) -> Path:
    """写一道题的笔记，返回文件路径。frontmatter 机器可读，正文人读（§4.2）。"""
    today = dt.date.today().isoformat()
    fm_lines = [
        "---",
        f"qid: {qid}",
        f"topic: {topic}",
    ]
    if difficulty is not None:
        fm_lines.append(f"difficulty: {difficulty}")
    if frequency is not None:
        fm_lines.append(f"frequency: {frequency}")
    fm_lines += [
        "tags: []",
        f"anki_synced: {str(anki_synced).lower()}",
        f"last_reviewed: {today}",
        "---",
        "",
        f"# {question}",
        "",
        _link_citations(body_md.strip(), citations),
        "",
    ]
    content = "\n".join(fm_lines)

    path = notes_dir() / f"{safe_filename(question)}.md"
    path.write_text(content, encoding="utf-8")
    return path
