"""候选人画像 → 给 prompt 用的「真实人设」字符串。

为什么需要：之前 prompt 把人设硬编成"4年经验+AISALE多智能体"，但真实简历是另一回事，
导致答案让候选人吹自己没做过的经历，一追问就穿帮。这里从真实简历动态生成人设，
并显式给出"真实项目清单"，让 prompt 只在能 cover 的地方提示挂经历、诚实表达。
"""
from __future__ import annotations

from ..infra.config import profile_cfg
from ..pipelines.jobs import latest_resume


def build_persona(cfg: dict | None = None) -> dict:
    """返回 {persona, projects, level}。persona 进 prompt 头，projects 供踩坑钩子点名。"""
    role_title = profile_cfg(cfg)["role_title"]
    default = f"一名{role_title}（无简历画像，按通用中级水平作答，不要杜撰具体项目经历）"
    res = latest_resume(cfg)
    prof = (res or {}).get("profile") or {}
    if not prof:
        return {"persona": default, "projects": [], "level": "中级"}

    years = prof.get("years")
    seniority = prof.get("seniority") or ""
    domains = prof.get("domains") or []
    skills = (prof.get("core_skills") or [])[:8]
    projects = [p.get("name", "") for p in (prof.get("projects") or []) if p.get("name")]

    parts = ["一名"]
    if years:
        parts.append(f" {years} 年经验的")
    parts.append(role_title)
    if seniority:
        parts.append(f"（{seniority}）")
    if domains:
        parts.append("，方向：" + "、".join(domains[:5]))
    if projects:
        parts.append("。真实做过的项目：" + "、".join(projects[:6]))
    if skills:
        parts.append("。技术栈：" + "、".join(skills))
    persona = "".join(parts) + "。"
    return {"persona": persona, "projects": projects,
            "level": seniority or (f"{years}年" if years else "中级")}


def fill(template: str, cfg: dict | None = None) -> str:
    """把 prompt 模板里的占位符替换为真实人设/领域头衔（用 replace 避免 .format 撞花括号）。

    支持占位符：{persona} {projects} {level}（来自简历画像）、
    {coach_title} {interviewer_title} {role_title} {field}（来自 config.profile）。
    """
    p = build_persona(cfg)
    pc = profile_cfg(cfg)
    proj = "、".join(p["projects"]) if p["projects"] else "（简历未提供具体项目，不要杜撰）"
    return (template.replace("{persona}", p["persona"])
                    .replace("{projects}", proj)
                    .replace("{level}", p["level"])
                    .replace("{coach_title}", pc["coach_title"])
                    .replace("{interviewer_title}", pc["interviewer_title"])
                    .replace("{role_title}", pc["role_title"])
                    .replace("{field}", pc["field"]))
