"""JobMatcher — AI 语义岗位匹配（单 JD · 并发 · 增量）。

每条 JD 一次 LLM 调用（不再一次塞 5 个、靠序号 i 对齐——那样注意力分散、少返/错位
就丢分）。看可迁移能力 / 项目相关性 / 成长潜力，给 0-100 匹配度、verdict、命中优势、
可补缺口、一句话理由。线程池并发（并发数 config.llm.match_workers，按你 LLM 服务的吞吐
调；worker 只调 LLM、不持 DB 连接，主线程统一写库）。

增量：默认只评**未评过**的 JD（NOT IN job_matches）。新岗位 scan 入库后再 match，
只评新增的 → 秒级；--redo 才全量重评。
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..infra.config import load_config, profile_cfg
from ..infra.db import connect
from ..infra.llm import LLM
from .jobs import latest_resume

# {RECRUITER} 运行时由 config.profile 注入（默认「资深「AI 算法」岗位招聘官」）
_SYS = (
    "你是{RECRUITER} + 职业规划师。给定候选人画像和【一条】JD，评估【真实匹配度】。"
    "不要只看关键词命中，要看：可迁移能力、项目与岗位的实质相关性、成长潜力、技术栈邻近度。"
    "如实评估，不吹捧也不因背景差异一刀切；对更高薪但够一够能拿下的，鼓励标 stretch。\n"
    "ai_score 0-100：90+=极强可直接投；75-89=强匹配(strong)；60-74=够得着需补强(stretch)；"
    "<60=差距过大(weak)。verdict 取 strong/stretch/weak。\n"
    "只输出扁平 JSON："
    '{"ai_score":整数,"verdict":"strong|stretch|weak",'
    '"matched":["结合其真实项目的2-4个硬优势"],"gaps":["2-4个可补齐的关键缺口"],'
    '"reasoning":"一句话理由"}'
)


def _parse_obj(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    try:
        return json.loads(m.group(0) if m else text)
    except (json.JSONDecodeError, AttributeError):
        return {}


def _verdict(score: int, v: str) -> str:
    v = (v or "").lower()
    if v in ("strong", "stretch", "weak"):
        return v
    return "strong" if score >= 75 else ("stretch" if score >= 60 else "weak")


class JobMatcher:
    def __init__(self):
        self.cfg = load_config()
        self._sys = _SYS.replace("{RECRUITER}", profile_cfg(self.cfg)["recruiter_title"])

    def _salary_expect(self, override_max: int | None) -> tuple[int, int]:
        """薪资期望(月薪K)：来自 config.jobs.salary_expect_min/max（非过往薪资）。
        override_max 可临时覆盖期望上限。"""
        j = self.cfg.get("jobs", {})
        emin = int(j.get("salary_expect_min", 25))
        emax = int(override_max or j.get("salary_expect_max", 35))
        return emin, max(emin, emax)

    def _score_one(self, llm: LLM, profile_compact: str, emin: int, emax: int, row) -> dict:
        _id, company, role, salary_max, _annual, experience, raw_text, sim = row
        user = (f"候选人画像：{profile_compact}\n"
                f"薪资期望：{emin}–{emax}K（越高越好；明显低于 {emin}K 视为偏低，请在缺口里点出）\n\n"
                f"目标 JD：\n职位：{role}｜公司：{company}｜月薪上限：{salary_max}K｜"
                f"经验：{experience}\nJD 正文：\n{(raw_text or '')[:1800]}\n\n"
                "请输出匹配评估 JSON：")
        obj = _parse_obj(llm.chat(self._sys, user, max_tokens=1200, temperature=0.3, think=False))
        try:
            score = max(0, min(100, int(obj.get("ai_score", obj.get("score")))))
        except (TypeError, ValueError):
            score = int(round((sim or 0) * 100))  # 解析失败兜底用 dense 相似度
        return {"ai_score": score, "verdict": _verdict(score, obj.get("verdict", "")),
                "matched": obj.get("matched") or [], "gaps": obj.get("gaps") or [],
                "reasoning": str(obj.get("reasoning") or obj.get("reason") or "")[:600]}

    def _candidates(self, conn, remb, sal_floor, scope, dense_top, stretch_top) -> list:
        cols = ("id, company, role, salary_max, annual_max, experience, raw_text, "
                "1-(embedding <=> %s::vector) AS sim")
        if scope == "all":
            return conn.execute(
                f"SELECT {cols} FROM jds WHERE embedding IS NOT NULL "
                "ORDER BY embedding <=> %s::vector", (remb, remb)).fetchall()
        dense = conn.execute(
            f"SELECT {cols} FROM jds WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector LIMIT %s", (remb, remb, dense_top)).fetchall()
        stretch = conn.execute(
            f"SELECT {cols} FROM jds WHERE embedding IS NOT NULL AND salary_max >= %s "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (remb, sal_floor, remb, stretch_top)).fetchall()
        cand = {row[0]: row for row in dense + stretch}
        return list(cand.values())

    def run(self, *, redo: bool = False, workers: int | None = None,
            current_max: int | None = None, scope: str = "all",
            dense_top: int = 40, stretch_top: int = 20) -> dict:
        resume = latest_resume(self.cfg)
        if not resume:
            raise ValueError("还没有导入简历，请先 import_resume")
        rid = resume["resume_id"]
        profile = resume["profile"] or {}
        emin, emax = self._salary_expect(current_max)
        workers = workers or self.cfg.get("llm", {}).get("match_workers", 32)

        with connect(self.cfg) as conn:
            remb = conn.execute("SELECT embedding FROM resume_profile WHERE id=%s",
                                (rid,)).fetchone()[0]
            cands = self._candidates(conn, remb, emax, scope, dense_top, stretch_top)
            scored_ids = {r[0] for r in conn.execute(
                "SELECT jd_id FROM job_matches WHERE resume_id=%s", (rid,)).fetchall()}

        to_score = [row for row in cands if redo or row[0] not in scored_ids]
        profile_compact = json.dumps(
            {k: profile.get(k) for k in
             ("years", "seniority", "core_skills", "domains", "projects", "highlights")},
            ensure_ascii=False)

        _local = threading.local()

        def _llm() -> LLM:
            if not hasattr(_local, "llm"):
                _local.llm = LLM(self.cfg)
            return _local.llm

        def _work(row):
            try:
                return row, self._score_one(_llm(), profile_compact, emin, emax, row)
            except Exception:  # noqa: BLE001 — 单条失败不拖累整体
                return row, None

        n_ok = n_fail = 0
        if to_score:
            with connect(self.cfg) as wconn, ThreadPoolExecutor(max_workers=workers) as ex:
                for fut in as_completed([ex.submit(_work, row) for row in to_score]):
                    row, s = fut.result()
                    if s is None:
                        n_fail += 1
                        continue
                    wconn.execute(
                        "INSERT INTO job_matches (jd_id, resume_id, dense_score, ai_score, "
                        "verdict, matched, gaps, reasoning) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (jd_id, resume_id) DO UPDATE SET "
                        "dense_score=EXCLUDED.dense_score, ai_score=EXCLUDED.ai_score, "
                        "verdict=EXCLUDED.verdict, matched=EXCLUDED.matched, "
                        "gaps=EXCLUDED.gaps, reasoning=EXCLUDED.reasoning, created_at=now()",
                        (row[0], rid, float(row[7] or 0), s["ai_score"], s["verdict"],
                         json.dumps(s["matched"], ensure_ascii=False),
                         json.dumps(s["gaps"], ensure_ascii=False), s["reasoning"]))
                    n_ok += 1

        res = stored_matches(self.cfg)
        res.update({"resume_id": rid, "candidates": len(cands), "scored_now": n_ok,
                    "failed": n_fail, "skipped_existing": len(cands) - len(to_score)})
        return res

    def close(self):
        pass


def stored_matches(cfg=None) -> dict:
    """读取已存的匹配结果（不重新算）。薪资基准用 config 的薪资期望，非过往薪资。"""
    cfg = cfg or load_config()
    res = latest_resume(cfg)
    j = (cfg.get("jobs", {}) if cfg else {})
    emin = int(j.get("salary_expect_min", 25))
    emax = int(j.get("salary_expect_max", 35))
    if not res:
        return {"best": [], "stretch": [], "all": [],
                "expect_min": emin, "expect_max": emax, "n_scored": 0}
    rid = res["resume_id"]
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT m.jd_id, j.company, j.role, j.salary_max, j.annual_max, j.experience, "
            "m.dense_score, m.ai_score, m.verdict, m.matched, m.gaps, m.reasoning, "
            "m.prep_built, j.url "
            "FROM job_matches m JOIN jds j ON j.id=m.jd_id WHERE m.resume_id=%s",
            (rid,)).fetchall()
    results = [{"jd_id": r[0], "company": r[1], "role": r[2], "salary_max": r[3],
                "annual_max": r[4], "experience": r[5], "dense_score": r[6],
                "ai_score": r[7], "verdict": r[8], "matched": r[9], "gaps": r[10],
                "reasoning": r[11], "prep_built": r[12], "url": r[13]} for r in rows]
    best = sorted([r for r in results if r["verdict"] != "weak"],
                  key=lambda r: r["ai_score"], reverse=True)[:20]
    # 冲高薪：达到/超过期望上限(emax)的高薪好岗，按薪资优先
    stretch_list = sorted(
        [r for r in results if (r["salary_max"] or 0) >= emax and r["ai_score"] >= 55],
        key=lambda r: ((r["salary_max"] or 0), r["ai_score"]), reverse=True)[:15]
    all_sorted = sorted(results, key=lambda r: (r["ai_score"] or 0), reverse=True)
    return {"best": best, "stretch": stretch_list, "all": all_sorted,
            "expect_min": emin, "expect_max": emax, "n_scored": len(results)}
