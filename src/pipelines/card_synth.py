"""CardSynth (§5.4)：一条答案 → 3-6 张原子卡 + 关键数字 cloze，推入 Anki。

口述卡在 AnswerForge 已推，这里负责把"深挖版"原子化沉淀为抗遗忘卡片。
"""
from __future__ import annotations

import json
import re

from ..infra.anki import Anki
from ..infra.config import load_config
from ..infra.db import connect
from ..infra.llm import LLM

_SYS = (
    "你是 Anki 卡片设计专家，遵循'最小信息原则'。把面试答案拆成抗遗忘卡片。只输出 JSON：\n"
    '{"atomic":[{"front":"一个微观知识点的问题","back":"简短精准的答案(1-3句)",'
    '"key":"一句话要点"}],'
    '"cloze":["把关键数字/术语挖空的句子，用 {{c1::挖空内容}} 语法，如 '
    '\\"Llama-2 7B 在 4K 上下文 KV Cache 约 {{c1::2GB}}\\""]}。'
    "atomic 出 3-6 张，每张只考一个点(如'√d_k 为什么除'单独一张)；"
    "cloze 出 2-4 句，只挖最该背的数字/术语。不要寒暄。"
)


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    try:
        return json.loads(m.group(0) if m else text)
    except (json.JSONDecodeError, AttributeError):
        return {}


class CardSynth:
    def __init__(self):
        self.cfg = load_config()
        self.llm = LLM(self.cfg)

    def run(self, question_id: int) -> dict:
        with connect(self.cfg) as conn:
            row = conn.execute(
                "SELECT q.text, q.topic, a.oral_version, a.deep_version "
                "FROM questions q JOIN answers a ON a.question_id=q.id "
                "WHERE q.id=%s ORDER BY a.id DESC LIMIT 1", (question_id,)).fetchone()
        if not row:
            raise ValueError(f"题 {question_id} 没有答案，先 ask 生成")
        qtext, topic, oral, deep = row
        topic = topic or "general"

        data = _parse_json(self.llm.chat(
            _SYS, f"题目：{qtext}\n\n答案（深挖版）：\n{deep or oral}",
            max_tokens=3000, temperature=0.3, think=False))
        atomic = data.get("atomic", []) or []
        cloze = data.get("cloze", []) or []

        anki = Anki(self.cfg)
        anki.ensure_setup()
        pushed = {"atomic": 0, "cloze": 0}
        with connect(self.cfg) as conn:
            for c in atomic:
                front, back = c.get("front", ""), c.get("back", "")
                if not front:
                    continue
                nid = anki.add_note(
                    fields={"Question": front, "OralAnswer": back,
                            "KeyPoints": c.get("key", ""), "QID": str(question_id),
                            "Topic": topic},
                    tags=["interview-forge", topic, "atomic"])
                conn.execute(
                    "INSERT INTO cards (question_id, anki_note_id, front, back, card_type, "
                    "pushed_at) VALUES (%s,%s,%s,%s,'atomic', now())",
                    (question_id, nid, front, back))
                pushed["atomic"] += 1
            for sent in cloze:
                if "{{c" not in sent:
                    continue
                nid = anki.add_cloze(sent, qid=str(question_id), topic=topic,
                                     tags=["interview-forge", topic, "cloze"])
                conn.execute(
                    "INSERT INTO cards (question_id, anki_note_id, front, back, card_type, "
                    "pushed_at) VALUES (%s,%s,%s,%s,'cloze', now())",
                    (question_id, nid, sent, ""))
                pushed["cloze"] += 1
        anki.close()
        # 注意：不要在 run() 里关 self.llm —— materials 等会复用同一个 CardSynth
        # 实例循环调用 run()，关了之后续题就拿不到 LLM 连接（曾导致只成功 1 张卡）。
        return {"question_id": question_id, **pushed}

    def close(self):
        self.llm.close()


def unsynced_question_ids(cfg=None) -> list[int]:
    """有答案但还没拆过原子卡(只有 oral_prompt 卡)的题。"""
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT DISTINCT a.question_id FROM answers a "
            "WHERE NOT EXISTS (SELECT 1 FROM cards c WHERE c.question_id=a.question_id "
            "AND c.card_type IN ('atomic','cloze')) ORDER BY a.question_id").fetchall()
    return [r[0] for r in rows]
