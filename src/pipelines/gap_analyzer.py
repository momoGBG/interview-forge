"""GapAnalyzer (§5.2)：选定 JD × 简历 → 缺口/联想题 + 《备考清单》。

把简历当"已知证据库"、JD 当"目标考纲"，两者差集落成三类产物：
  (a) 知识缺口题   origin='gap'
  (b) 项目深挖追问 origin='project_probe'（挖简历外的真实经历）
  (c) 联想考点题   origin='jd_derived'
并产出一份《该 JD 备考清单》Markdown 写进 Obsidian。
"""
from __future__ import annotations

import datetime as dt
import json
import re

from ..infra.config import load_config
from ..infra.db import connect
from ..infra.llm import LLM
from ..obsidian.vault_path import notes_dir, safe_filename
from .jobs import latest_resume

# {coach_title} 由 persona.fill 在运行时按 config.profile 注入（默认「资深 AI 算法面试教练」）
_SYS = (
    "你是{coach_title}。给定【目标 JD】和【候选人简历画像】，做缺口分析，"
    "把简历当已知证据库、JD 当目标考纲，输出该 JD 的备考产物。只输出 JSON：\n"
    '{"summary":"这个JD的备考重点(2-3句)",'
    '"gap_questions":["我可能不会但JD要考的知识题，6-10道，具体到能直接拿去准备"],'
    '"project_probes":["针对候选人真实项目的深挖追问，引导把简历没写透的经历讲成故事，4-6个"],'
    '"associated_topics":["JD暗示但未明说的高频考题(顺着某条岗位要求挖出它背后的高频考点)，'
    '6-10道"]}。题目要接地气、是面试真的会问的形式。'
)


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    try:
        return json.loads(m.group(0) if m else text)
    except (json.JSONDecodeError, AttributeError):
        return {}


class GapAnalyzer:
    def __init__(self):
        self.cfg = load_config()
        self.llm = LLM(self.cfg)

    def run(self, jd_id: int, *, topic: str = "jd") -> dict:
        resume = latest_resume(self.cfg)
        profile = (resume or {}).get("profile") or {}
        with connect(self.cfg) as conn:
            jd = conn.execute(
                "SELECT company, role, raw_text, requirements, salary_max FROM jds WHERE id=%s",
                (jd_id,)).fetchone()
            match = conn.execute(
                "SELECT matched, gaps, reasoning, ai_score FROM job_matches "
                "WHERE jd_id=%s ORDER BY id DESC LIMIT 1", (jd_id,)).fetchone()
        if not jd:
            raise ValueError(f"JD {jd_id} 不存在")
        company, role, raw_text, _req, salary_max = jd

        match_ctx = ""
        if match:
            match_ctx = (f"\n已知匹配分析：优势={json.dumps(match[0], ensure_ascii=False)}；"
                         f"缺口={json.dumps(match[1], ensure_ascii=False)}；"
                         f"匹配度={match[3]}")
        user = (f"【目标 JD】{role} @ {company}（月薪上限 {salary_max}K）\n{raw_text[:2500]}\n\n"
                f"【候选人简历画像】{json.dumps(profile, ensure_ascii=False)}{match_ctx}\n\n"
                f"请做缺口分析并输出 JSON：")
        from ..infra.persona import fill
        raw = self.llm.chat(fill(_SYS, self.cfg), user, max_tokens=4000,
                            temperature=0.4, think=False)
        data = _parse_json(raw)
        if not data:
            raise ValueError(f"缺口分析 JSON 解析失败，原始输出片段：{raw[:300]}")

        gap_q = data.get("gap_questions", []) or []
        proj_q = data.get("project_probes", []) or []
        assoc_q = data.get("associated_topics", []) or []

        # 落 questions
        ids = {"gap": [], "project_probe": [], "jd_derived": []}
        with connect(self.cfg) as conn:
            for q in gap_q:
                qid = self._add_q(conn, q, topic, "gap", jd_id)
                ids["gap"].append((qid, q))
            for q in proj_q:
                qid = self._add_q(conn, q, topic, "project_probe", jd_id)
                ids["project_probe"].append((qid, q))
            for q in assoc_q:
                qid = self._add_q(conn, q, topic, "jd_derived", jd_id)
                ids["jd_derived"].append((qid, q))
            conn.execute("UPDATE job_matches SET prep_built=true WHERE jd_id=%s", (jd_id,))

        note_path = self._write_checklist(company, role, salary_max,
                                           data.get("summary", ""), ids, match)
        self.llm.close()
        return {"jd_id": jd_id, "company": company, "role": role,
                "n_gap": len(gap_q), "n_project": len(proj_q), "n_assoc": len(assoc_q),
                "note_path": str(note_path), "summary": data.get("summary", "")}

    def _add_q(self, conn, text, topic, origin, jd_id) -> int:
        return conn.execute(
            "INSERT INTO questions (text, topic, origin, jd_id) VALUES (%s,%s,%s,%s) "
            "RETURNING id", (text, topic, origin, jd_id)).fetchone()[0]

    def _write_checklist(self, company, role, salary_max, summary, ids, match) -> "Path":  # noqa
        today = dt.date.today().isoformat()
        lines = [
            "---", f"type: 备考清单", f"company: {company}", f"role: {role}",
            f"salary_max: {salary_max}", f"created: {today}", "tags: [备考清单, JD]", "---",
            "", f"# 备考清单 · {role} @ {company}（{salary_max}K）", "",
        ]
        if match:
            lines += [f"> AI 匹配度 **{match[3]}**　{match[2] or ''}", ""]
            if match[0]:
                lines += ["**我的优势**：" + "、".join(match[0]), ""]
            if match[1]:
                lines += ["**待补缺口**：" + "、".join(match[1]), ""]
        if summary:
            lines += ["## 备考重点", summary, ""]
        lines += ["## (a) 知识缺口题（我不会但 JD 要考）"]
        lines += [f"- [ ] [[{safe_filename(q)}]] `qid:{qid}`" for qid, q in ids["gap"]]
        lines += ["", "## (b) 项目深挖追问（把简历外真实经历讲成故事）"]
        lines += [f"- [ ] {q} `qid:{qid}`" for qid, q in ids["project_probe"]]
        lines += ["", "## (c) 联想考点（JD 暗示的高频题）"]
        lines += [f"- [ ] [[{safe_filename(q)}]] `qid:{qid}`" for qid, q in ids["jd_derived"]]
        lines += ["", "---", "用 `forge ask` 或 UI 提问，逐条把上面的题变成有出处的笔记+卡片。"]

        path = notes_dir() / f"备考清单_{safe_filename(company)}_{safe_filename(role)}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def close(self):
        self.llm.close()
