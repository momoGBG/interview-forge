"""JD 备考资料一键备齐：把某 JD 的缺口/联想题批量做成 grounded 答案 + Anki 卡。

复用 AnswerForge(RAG 带出处) + CardSynth(原子卡+cloze)，让备考清单上的题
直接变成可学的 Obsidian 笔记 + 抗遗忘卡片。
"""
from __future__ import annotations

from ..infra.config import load_config
from ..infra.db import connect
from ..pipelines.answer_forge import AnswerForge, build_prompt
from ..pipelines.card_synth import CardSynth

# 哪些题值得自动生成权威答案（知识题 + 联想考点；项目深挖是个人故事，不自动答）
ANSWERABLE_ORIGINS = ("gap", "jd_derived")


def regenerate_answer(forge, question_id: int, cfg=None) -> bool:
    """用当前 prompt 重新生成某题答案并覆盖笔记（复用题，不推重复卡）。"""
    with connect(cfg) as conn:
        row = conn.execute("SELECT text, topic FROM questions WHERE id=%s",
                           (question_id,)).fetchone()
    if not row:
        return False
    hits = forge.retrieve(row[0])
    system, user = build_prompt(row[0], hits)
    ans_md = forge.llm.chat(system=system, user=user)
    forge.finalize(question=row[0], answer_md=ans_md, hits=hits,
                   topic=row[1] or "general", difficulty=3, frequency=4,
                   push_anki=False, question_id=question_id)
    return True


def polluted_question_ids(cfg=None) -> list[int]:
    """最新答案里含旧人设(AISALE)等需要重生成的题。"""
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (a.question_id) a.question_id FROM answers a "
            "WHERE a.deep_version LIKE '%AISALE%' ORDER BY a.question_id, a.id DESC"
        ).fetchall()
    return [r[0] for r in rows]


def pending_question_ids(jd_id: int, cfg=None) -> list[int]:
    """该 JD 下、还没有答案的知识/联想题。"""
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT q.id FROM questions q WHERE q.jd_id=%s AND q.origin = ANY(%s) "
            "AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.question_id=q.id) "
            "ORDER BY q.id", (jd_id, list(ANSWERABLE_ORIGINS))).fetchall()
    return [r[0] for r in rows]


def build_jd_materials(jd_id: int, *, max_q: int = 6, with_cards: bool = True) -> dict:
    cfg = load_config()
    ids = pending_question_ids(jd_id, cfg)[:max_q]
    if not ids:
        return {"jd_id": jd_id, "answered": 0, "carded": 0, "remaining": 0}

    with connect(cfg) as conn:
        meta = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT id, text, topic FROM questions WHERE id = ANY(%s)", (ids,)).fetchall()}

    forge = AnswerForge()
    synth = CardSynth() if with_cards else None
    answered = carded = 0
    card_errors: list[str] = []
    try:
        for qid in ids:
            text, topic = meta[qid]
            hits = forge.retrieve(text)
            system, user = build_prompt(text, hits)
            ans_md = forge.llm.chat(system=system, user=user)
            # 复用 finalize：写库 + 笔记 + 口述卡。这里复用已有 question 行，不另建。
            res = forge.finalize(question=text, answer_md=ans_md, hits=hits,
                                 topic=topic or "jd", difficulty=3, frequency=4,
                                 push_anki=True, question_id=qid)
            answered += 1
            if synth:
                try:
                    synth.run(res["qid"])
                    carded += 1
                except Exception as e:  # noqa: BLE001
                    card_errors.append(f"qid={res['qid']}: {str(e)[:120]}")
    finally:
        forge.close()
        if synth:
            synth.close()

    remaining = len(pending_question_ids(jd_id, cfg))
    out = {"jd_id": jd_id, "answered": answered, "carded": carded,
           "remaining": remaining}
    if card_errors:
        out["card_errors"] = card_errors
    return out
