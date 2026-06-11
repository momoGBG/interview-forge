"""AnswerForge — Phase 1：题目 → 混合检索 → 接地气、带出处的答案 → Obsidian → Anki → 落库。

设计为可复用步骤，方便 FastAPI 流式接口共享：
  retrieve() → build_prompt() → (LLM 生成，可流式) → finalize()
KB 为空时自动回退到 Phase 0 的无检索 prompt（grounded=false）。
"""
from __future__ import annotations

import re
from pathlib import Path

from ..infra.anki import Anki
from ..infra.config import PROJECT_ROOT, load_config
from ..infra.db import connect
from ..infra.llm import LLM
from ..infra.retrieval import Hit, Retriever
from ..obsidian.writer import write_note

PROMPT_RAG = PROJECT_ROOT / "src" / "prompts" / "answer_forge_rag.md"
PROMPT_PLAIN = PROJECT_ROOT / "src" / "prompts" / "answer_forge.md"


def _extract_section(md: str, header: str) -> str:
    pat = rf"^##\s*{re.escape(header)}\s*$(.*?)(?=^##\s|\Z)"
    m = re.search(pat, md, flags=re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _cited_ids(md: str) -> set[int]:
    return {int(x) for x in re.findall(r"\[chunk[_\s]?(\d+)\]", md, flags=re.IGNORECASE)}


def build_prompt(question: str, hits: list[Hit]) -> tuple[str, str]:
    """返回 (system, user)。有检索片段走 RAG prompt，否则走 plain。
    system 里的 {persona}/{projects} 用真实简历画像填充（避免人设幻觉）。"""
    from ..infra.persona import fill
    if hits:
        system = fill(PROMPT_RAG.read_text(encoding="utf-8"))
        frags = []
        for h in hits:
            head = f"[chunk_{h.chunk_id}] 来源：{h.source_title}"
            ctx = f"（定位：{h.context}）" if h.context else ""
            frags.append(f"{head}{ctx}\n{h.content}")
        user = f"<题目>\n{question}\n\n<检索片段>\n" + "\n\n---\n\n".join(frags)
    else:
        system = fill(PROMPT_PLAIN.read_text(encoding="utf-8"))
        user = f"题目：{question}"
    return system, user


class AnswerForge:
    def __init__(self):
        self.cfg = load_config()
        self.llm = LLM(self.cfg)
        self._retriever: Retriever | None = None

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever(self.cfg)
        return self._retriever

    def retrieve(self, question: str) -> list[Hit]:
        # 检索打的是单卡 embedding/reranker 服务，高并发下偶发 ReadTimeout。
        # 重试一次自愈；两次都失败才退化为无检索（grounded=false）。
        last = None
        for attempt in range(2):
            try:
                return self.retriever.search(question)
            except Exception as e:  # noqa: BLE001
                last = e
        import sys
        print(f"[retrieve] 检索失败(退化无检索): {type(last).__name__}: {str(last)[:120]}",
              file=sys.stderr, flush=True)
        return []

    def finalize(self, *, question: str, answer_md: str, hits: list[Hit],
                 topic: str, difficulty: int, frequency: int,
                 push_anki: bool = True, question_id: int | None = None) -> dict:
        """落库 + 写笔记 + 推 Anki 卡。供同步 run 与流式接口共用。
        question_id 给定时复用已有题(如 JD 备考题)，不另建。"""
        oral = _extract_section(answer_md, "口述版") or answer_md
        cited = _cited_ids(answer_md)
        valid_ids = {h.chunk_id for h in hits}
        used = cited & valid_ids
        grounded = bool(used) if hits else False
        citations = [
            {"chunk_id": h.chunk_id, "source_title": h.source_title, "url": h.source_url}
            for h in hits if h.chunk_id in used
        ]

        qid, answer_id = self._persist_answer(
            question, topic, difficulty, frequency, answer_md, oral, grounded, citations,
            question_id=question_id)

        note_path = write_note(
            qid=qid, question=question, topic=topic, body_md=answer_md,
            difficulty=difficulty, frequency=frequency, anki_synced=False,
            citations=citations)
        with connect(self.cfg) as conn:
            conn.execute("UPDATE answers SET obsidian_path=%s WHERE id=%s",
                         (str(note_path), answer_id))

        anki_note_id = None
        if push_anki:
            anki = Anki(self.cfg)
            anki.ensure_setup()
            anki_note_id = anki.add_note(
                fields={"Question": question, "OralAnswer": oral, "KeyPoints": "",
                        "QID": str(qid), "Topic": topic},
                tags=["interview-forge", topic] + (["grounded"] if grounded else []))
            self._persist_card(qid, anki_note_id, question, oral)
            anki.close()

        with connect(self.cfg) as conn:
            conn.execute("INSERT INTO study_log (question_id, event) VALUES (%s,'generated')",
                         (qid,))

        return {"qid": qid, "answer_id": answer_id, "anki_note_id": anki_note_id,
                "note_path": str(note_path), "grounded": grounded,
                "n_citations": len(citations), "n_hits": len(hits)}

    def run(self, question: str, *, topic: str = "general", difficulty: int = 3,
            frequency: int = 3) -> dict:
        hits = self.retrieve(question)
        system, user = build_prompt(question, hits)
        answer_md = self.llm.chat(system=system, user=user)
        res = self.finalize(question=question, answer_md=answer_md, hits=hits,
                            topic=topic, difficulty=difficulty, frequency=frequency)
        res["answer_md"] = answer_md
        self.close()
        return res

    # —— 持久化辅助 ——
    def _persist_answer(self, question, topic, difficulty, frequency, deep, oral,
                        grounded, citations, question_id=None):
        import json
        with connect(self.cfg) as conn:
            if question_id is not None:
                qid = question_id
            else:
                qid = conn.execute(
                    "INSERT INTO questions (text, topic, difficulty, frequency, origin) "
                    "VALUES (%s,%s,%s,%s,'manual') RETURNING id",
                    (question, topic, difficulty, frequency)).fetchone()[0]
            answer_id = conn.execute(
                "INSERT INTO answers (question_id, oral_version, deep_version, grounded, "
                "citations, model) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (qid, oral, deep, grounded, json.dumps(citations, ensure_ascii=False),
                 self.cfg["llm"]["model"])).fetchone()[0]
        return qid, answer_id

    def _persist_card(self, qid, note_id, front, back):
        with connect(self.cfg) as conn:
            conn.execute(
                "INSERT INTO cards (question_id, anki_note_id, front, back, card_type, "
                "pushed_at) VALUES (%s,%s,%s,%s,'oral_prompt', now())",
                (qid, note_id, front, back))

    def close(self):
        self.llm.close()
        if self._retriever is not None:
            self._retriever.close()
