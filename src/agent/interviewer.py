"""MockInterviewer —— 工具增强型面试官 Agent（Phase 4）。

Claude-Code 式架构：外层是确定性状态机（出题→作答→评分→决策→下一题），
内层 LLM 通过工具循环做窄任务。工具：
  · search_kb   —— 检索本地权威 KB，给题目接地气、给评分核对数字（grounding）
  · get_profile —— 拿候选人简历真实项目，让题贴脸
靠"结构化输出 + 自动修复 + 检索接地气 + 自适应难度"把弱模型撑成靠谱面试官。
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

from ..infra.anki import Anki
from ..infra.config import load_config, profile_cfg
from ..infra.db import connect
from ..infra.llm import LLM
from ..infra.retrieval import Retriever
from ..pipelines.jobs import latest_resume
from .runtime import Tool, agent_loop, call_json

DIMS = ["结构性", "准确性", "数字", "选型意识", "踩坑"]


def oral_coach(text: str, cfg: dict | None = None) -> dict:
    """口语教练（语音模式）：评流畅度/口头禅/啰嗦度，给一版更接地气的说法。"""
    cfg = cfg or load_config()
    llm = LLM(cfg)
    sys = ("你是面试口语教练。根据候选人这段【口头作答转写】评估口语表达，只输出 JSON："
           '{"fluency":1-5整数(流畅度),"verbosity":1-5整数(啰嗦度,5=很啰嗦),'
           '"fillers":["口头禅/废话词,如 嗯/那个/然后,没有则空"],'
           '"polished":"把它改写成更接地气、更精炼、能脱口而出的一版(保留技术要点)"}')
    try:
        out = call_json(llm, sys, f"口头作答转写：\n{text}", max_tokens=1500)
    finally:
        llm.close()
    if not isinstance(out, dict):
        out = {}
    return {"fluency": int(out.get("fluency", 3) or 3),
            "verbosity": int(out.get("verbosity", 3) or 3),
            "fillers": out.get("fillers", []) or [],
            "polished": out.get("polished", "")}


@dataclass
class Session:
    id: int
    topic: str
    jd_id: int | None
    focus_areas: list[str]
    target_q: int
    difficulty: int = 3
    focus_idx: int = 0
    qno: int = 0
    is_followup: bool = False
    followups_here: int = 0
    cur_question: str = ""
    cur_focus: str = ""
    cur_refpoints: list = field(default_factory=list)
    cur_citations: list = field(default_factory=list)
    turns: list = field(default_factory=list)   # 每回合的 scores
    weak_qids: list = field(default_factory=list)
    done: bool = False


SESSIONS: dict[int, Session] = {}


class MockInterviewer:
    def __init__(self):
        self.cfg = load_config()
        self.llm = LLM(self.cfg)
        self._retriever: Retriever | None = None
        self._profile = (latest_resume(self.cfg) or {}).get("profile") or {}
        self._iv_title = profile_cfg(self.cfg)["interviewer_title"]  # 面试官头衔（按 config.profile）

    # ---------- 工具 ----------
    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever(self.cfg)
        return self._retriever

    def _tools(self, sink: dict) -> dict[str, Tool]:
        def search_kb(inp: dict) -> str:
            q = inp.get("query") or inp.get("q") or ""
            try:
                hits = self.retriever.search(q, top_k=4)
            except Exception:  # noqa: BLE001
                return "（KB 检索不可用，凭专业知识继续）"
            if not hits:
                return "（KB 无相关片段，凭专业知识继续）"
            sink.setdefault("hits", []).extend(
                {"chunk_id": h.chunk_id, "source": h.source_title} for h in hits)
            return "\n".join(f"[chunk_{h.chunk_id}] {h.source_title}: {h.content[:300]}"
                             for h in hits)

        def get_profile(_inp: dict) -> str:
            return json.dumps(self._profile, ensure_ascii=False)[:1200]

        return {
            "search_kb": Tool("search_kb",
                              "检索本地权威知识库。输入{\"query\":\"检索词\"}。"
                              "用于让题目有出处、或核对候选人答案里的数字/论断是否准确。",
                              search_kb),
            "get_profile": Tool("get_profile",
                                "获取候选人简历画像(真实项目/技能)。输入{}。用于让题目贴合其经历。",
                                get_profile),
        }

    # ---------- 开场：规划大纲 ----------
    def start(self, *, topic: str = "inference", jd_id: int | None = None,
              n_questions: int = 5) -> dict:
        jd_ctx = ""
        if jd_id:
            with connect(self.cfg) as conn:
                row = conn.execute("SELECT role, company, raw_text FROM jds WHERE id=%s",
                                   (jd_id,)).fetchone()
            if row:
                jd_ctx = f"\n目标岗位：{row[0]}@{row[1]}\n{row[2][:800]}"
        sys = (f"你是{self._iv_title}，规划一场面试的考察大纲。结合主题、候选人画像、"
               "目标岗位，给出 4-6 个由浅入深的考察方向。只输出 JSON："
               '{"focus_areas":["方向1",...],"opening":"一句开场白"}')
        user = (f"面试主题：{topic}\n候选人画像：{json.dumps(self._profile, ensure_ascii=False)[:1000]}"
                f"{jd_ctx}")
        plan = call_json(self.llm, sys, user, max_tokens=1200)
        focus = (plan.get("focus_areas") or [topic])[:n_questions]   # n_questions 控制广度
        opening = plan.get("opening", "我们开始吧，放轻松。")

        with connect(self.cfg) as conn:
            sid = conn.execute(
                "INSERT INTO interview_sessions (topic, jd_id, plan, difficulty) "
                "VALUES (%s,%s,%s,3) RETURNING id",
                (topic, jd_id, json.dumps(plan, ensure_ascii=False))).fetchone()[0]
        s = Session(id=sid, topic=topic, jd_id=jd_id, focus_areas=focus,
                    target_q=len(focus))
        SESSIONS[sid] = s
        q = self._next_question(s)
        return {"session_id": sid, "opening": opening, "focus_areas": focus,
                "question": q, "qno": s.qno, "difficulty": s.difficulty,
                "total": s.target_q}

    # ---------- 出题（工具循环：检索 + 简历） ----------
    def _next_question(self, s: Session, dig: str = "") -> str:
        s.cur_focus = s.focus_areas[min(s.focus_idx, len(s.focus_areas) - 1)]
        sink: dict = {}
        diff_word = {1: "热身", 2: "基础", 3: "进阶", 4: "深入", 5: "专家"}[s.difficulty]
        task = (f"针对考察方向「{s.cur_focus}」出一道【{diff_word}】难度的面试题。"
                f"主题={s.topic}。" + (f"重点追问这个没答好的点：{dig}。" if dig else "")
                + "可调用 search_kb 让题有出处、get_profile 贴候选人真实项目。"
                "final 时输出 {\"question\":\"题目(口语化,面试官口吻)\","
                "\"reference_points\":[\"2-4个采分要点,稍后给答案打分用\"]}")
        sys = f"你是{self._iv_title}，只问一道题，犀利、接地气、能往深挖。"
        out = agent_loop(self.llm, sys, task, self._tools(sink), max_steps=2)
        question = (out.get("question") if isinstance(out, dict) else None) or \
            f"请讲讲 {s.cur_focus}。"
        s.cur_question = question
        s.cur_refpoints = out.get("reference_points", []) if isinstance(out, dict) else []
        s.cur_citations = sink.get("hits", [])
        s.qno += 1
        return question

    # ---------- 评分（工具循环：检索核对） ----------
    def answer(self, session_id: int, user_answer: str) -> dict:
        s = SESSIONS.get(session_id)
        if not s:
            raise ValueError("会话不存在或已过期")
        if s.done:
            return {"done": True}

        sink: dict = {}
        sys = (f"你是严格而建设性的{self._iv_title}，给候选人的回答打分。"
               "可调用 search_kb 核对回答里的数字/论断是否属实。\n"
               "五维各 1-5 分(1差5优)：结构性(有无逻辑骨架)/准确性(概念是否正确)/"
               "数字(有无量化且正确)/选型意识(权衡/场景判断)/踩坑(工程细节与真实坑)。\n"
               "评分准则（重要）：① 候选人坦诚'某处没做过/不确定'但能讲清思路或可迁移经验的，"
               "不要因诚实而过度扣分，准确性可给中上；② 但若明显在编造数字/经历、且与检索事实"
               "不符，准确性/数字直接给低分，并在 factual_issues 点名错误——面试最忌不懂装懂。\n"
               "final 输出 JSON："
               '{"scores":{"结构性":n,"准确性":n,"数字":n,"选型意识":n,"踩坑":n},'
               '"feedback":"3-5句犀利但建设性的点评,口语化,先肯定再指问题给方向",'
               '"missing":["漏掉的关键点"],'
               '"factual_issues":["事实/数字错误或疑似编造,没有则空"],"verdict":"strong|ok|weak"}')
        task = (f"题目：{s.cur_question}\n采分要点：{json.dumps(s.cur_refpoints, ensure_ascii=False)}\n"
                f"候选人回答：\n{user_answer}\n\n请核对并打分。")
        ev = agent_loop(self.llm, sys, task, self._tools(sink), max_steps=2)
        if not isinstance(ev, dict):
            ev = {}
        scores = {d: int((ev.get("scores") or {}).get(d, 3) or 3) for d in DIMS}
        avg = statistics.mean(scores.values())
        total = sum(scores.values())
        feedback = ev.get("feedback", "")
        verdict = ev.get("verdict") or ("weak" if avg < 3 else "strong" if avg >= 4 else "ok")
        citations = (s.cur_citations or []) + sink.get("hits", [])

        # 落回合
        with connect(self.cfg) as conn:
            conn.execute(
                "INSERT INTO interview_turns (session_id, qno, focus, question, answer, "
                "scores, feedback, is_followup, citations) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (s.id, s.qno, s.cur_focus, s.cur_question, user_answer,
                 json.dumps({**scores, "total": total}, ensure_ascii=False), feedback,
                 s.is_followup, json.dumps(citations, ensure_ascii=False)))
            conn.execute("INSERT INTO study_log (question_id, event, score, notes) "
                         "VALUES (NULL,'mock_answered',%s,%s)", (total, s.cur_focus))
            # 薄弱题回流
            if avg < 3:
                qid = conn.execute(
                    "INSERT INTO questions (text, topic, origin) VALUES (%s,%s,'mock_weak') "
                    "RETURNING id", (s.cur_question, s.topic)).fetchone()[0]
                s.weak_qids.append(qid)

        s.turns.append({"focus": s.cur_focus, "scores": scores, "avg": avg})

        # ---------- 确定性决策 ----------
        if avg < 3 and not s.is_followup and s.followups_here < 1:
            dig = "；".join(ev.get("missing", [])[:2]) or s.cur_focus
            s.is_followup = True
            s.followups_here += 1
            nxt = self._next_question(s, dig=dig)
        else:
            s.is_followup = False
            s.followups_here = 0
            s.focus_idx += 1
            if avg >= 4:
                s.difficulty = min(5, s.difficulty + 1)
            elif avg < 2.5:
                s.difficulty = max(1, s.difficulty - 1)
            if s.qno >= s.target_q and s.focus_idx >= len(s.focus_areas):
                s.done = True
                nxt = None
            else:
                nxt = self._next_question(s)

        return {"session_id": s.id, "scores": scores, "avg": round(avg, 2),
                "feedback": feedback, "missing": ev.get("missing", []),
                "factual_issues": ev.get("factual_issues", []), "verdict": verdict,
                "citations": citations, "is_followup": s.is_followup,
                "next_question": nxt, "qno": s.qno, "difficulty": s.difficulty,
                "done": s.done}

    # ---------- 收尾报告 ----------
    def finish(self, session_id: int) -> dict:
        s = SESSIONS.get(session_id)
        if not s:
            with connect(self.cfg) as conn:
                row = conn.execute("SELECT summary FROM interview_sessions WHERE id=%s",
                                   (session_id,)).fetchone()
            return row[0] if row and row[0] else {"error": "会话不存在"}

        # 逐维均分
        dim_avg = {}
        for d in DIMS:
            vals = [t["scores"][d] for t in s.turns if d in t["scores"]]
            dim_avg[d] = round(statistics.mean(vals), 2) if vals else 0
        overall = round(statistics.mean(list(dim_avg.values())), 2) if dim_avg else 0
        weak_focus = sorted({t["focus"] for t in s.turns if t["avg"] < 3})

        sys = ("你是面试官，根据各维度得分和薄弱方向，给候选人一段总评(2-4句,犀利建设性)"
               "和 3 条最该补的行动建议。只输出 JSON："
               '{"summary":"总评","actions":["建议1","建议2","建议3"]}')
        user = (f"主题：{s.topic}\n逐维均分(满分5)：{json.dumps(dim_avg, ensure_ascii=False)}\n"
                f"总分：{overall}\n薄弱方向：{weak_focus}\n回合数：{len(s.turns)}")
        rep = call_json(self.llm, sys, user, max_tokens=1000)

        report = {"session_id": s.id, "topic": s.topic, "n_turns": len(s.turns),
                  "dim_avg": dim_avg, "overall": overall, "weak_focus": weak_focus,
                  "summary": rep.get("summary", ""), "actions": rep.get("actions", []),
                  "weak_qids": s.weak_qids}
        with connect(self.cfg) as conn:
            conn.execute("UPDATE interview_sessions SET status='done', ended_at=now(), "
                         "summary=%s WHERE id=%s",
                         (json.dumps(report, ensure_ascii=False), s.id))
        s.done = True
        return report

    def reinforce_weak(self, session_id: int, max_cards: int = 3) -> dict:
        """对薄弱题生成 grounded 答案 + 原子卡（回流强化）。"""
        from ..pipelines.answer_forge import AnswerForge
        from ..pipelines.card_synth import CardSynth
        s = SESSIONS.get(session_id)
        qids = (s.weak_qids if s else [])[:max_cards]
        if not qids:
            return {"reinforced": 0}
        forge = AnswerForge()
        synth = CardSynth()
        done = 0
        with connect(self.cfg) as conn:
            for qid in qids:
                qtext = conn.execute("SELECT text, topic FROM questions WHERE id=%s",
                                     (qid,)).fetchone()
                if not qtext:
                    continue
                hits = forge.retrieve(qtext[0])
                from ..pipelines.answer_forge import build_prompt
                system, user = build_prompt(qtext[0], hits)
                ans = forge.llm.chat(system=system, user=user)
                forge.finalize(question=qtext[0], answer_md=ans, hits=hits,
                               topic=qtext[1] or s.topic, difficulty=3, frequency=4,
                               push_anki=True)
                done += 1
        # CardSynth 对这些题补原子卡
        for qid in qids:
            try:
                synth.run(qid)
            except Exception:  # noqa: BLE001
                pass
        forge.close()
        synth.close()
        return {"reinforced": done}

    def close(self):
        self.llm.close()
        if self._retriever is not None:
            self._retriever.close()
