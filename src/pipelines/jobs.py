"""JD / 简历 数据层：解析 xlsx 入 jds、薪资解析、简历 PDF → 结构化画像 + 向量。"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from ..infra.config import load_config, profile_cfg
from ..infra.db import connect
from ..infra.embed import Embedder
from ..infra.llm import LLM

# 列名 → 标准字段（兼容多种 xlsx 表头：岗位/职位 两种叫法混用）
COL_MAP = {
    "公司": "company",
    "岗位": "role", "职位": "role", "岗位名称": "role", "职位名称": "role",
    "薪资": "salary", "薪资范围": "salary",
    "经验要求": "experience", "工作经验": "experience",
    "学历要求": "education", "学历": "education",
    "岗位描述": "desc", "职位描述": "desc", "岗位职责": "desc",
    "位置": "location", "地点": "location", "工作地点": "location",
    "公司规模": "size", "加分项目": "bonus", "加分项": "bonus",
    "岗位链接": "url", "职位链接": "url", "链接": "url",
    "所属行业": "industry", "行业": "industry",
}


# ---------- 薪资解析 ----------
def _to_k(num: float, unit: str) -> float:
    if unit == "万":
        return num * 10
    if unit == "千":
        return num
    return num  # K 或裸数字按 K


def parse_salary(s: str | None) -> dict:
    """'30-60K·15薪' → {min:30,max:60,months:15,annual_max:900}。无法解析返回空。"""
    if not s:
        return {}
    months = 12
    m = re.search(r"[·\*xX×](\d+)\s*薪", s) or re.search(r"(\d+)\s*薪", s)
    if m:
        months = int(m.group(1))
    nums = re.findall(r"(\d+(?:\.\d+)?)\s*([KkＫ万千]?)", s)
    vals = []
    for n, u in nums:
        if n == str(months) and u == "":
            continue  # 跳过"薪"数字
        if float(n) > 1000:  # 像年份/无关大数，跳过
            continue
        vals.append(_to_k(float(n), u))
    vals = [v for v in vals if v > 0][:2]
    if not vals:
        return {"months": months}
    lo = int(min(vals))
    hi = int(max(vals))
    return {"min": lo, "max": hi, "months": months, "annual_max": hi * months}


# ---------- xlsx → jds ----------
def _read_sheet(path: str) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []
    header = [str(h).strip() if h else "" for h in rows[0]]
    idx = {COL_MAP[h]: i for i, h in enumerate(header) if h in COL_MAP}
    out = []
    for r in rows[1:]:
        if not r or not r[idx.get("company", 0)]:
            continue
        rec = {k: (str(r[i]).strip() if i < len(r) and r[i] is not None else "")
               for k, i in idx.items()}
        if rec.get("company") or rec.get("role"):
            out.append(rec)
    return out


# 标准字段（无论来自 xlsx 还是 JSON，归一化后都是这一组英文键）：
#   company role salary experience education desc location size bonus url industry
def _normalize_record(rec: dict) -> dict:
    """把一条 JD 记录归一化为标准英文字段。中文键名按 COL_MAP 翻译，英文键名原样保留。"""
    out: dict = {}
    for k, v in rec.items():
        std = COL_MAP.get(str(k).strip(), str(k).strip())
        out[std] = "" if v is None else str(v).strip() if not isinstance(v, (dict, list)) else v
    out.setdefault("dedup_key", f"{out.get('company','')}|{out.get('role','')}|"
                                f"{out.get('salary','')}")
    return out


def _persist_records(recs: list[dict], cfg: dict) -> tuple[int, int]:
    """去重 + 批量嵌入 + 入库（仅新行）。返回 (inserted, skipped)。

    增量：嵌入是单卡服务的瓶颈，故先按 dedup_key 过滤掉库里已有的行，**只嵌入+插入
    新行**。加 1 条 JD 只嵌 1 条，不再每次重嵌全部 ~千条。
    """
    total = len(recs)
    with connect(cfg) as conn:
        existing = {r[0] for r in conn.execute("SELECT dedup_key FROM jds").fetchall()}
    seen: set[str] = set()
    new_recs = []
    for r in recs:
        dk = r["dedup_key"]
        if dk in existing or dk in seen:   # 跨文件/库内都去重
            continue
        seen.add(dk)
        new_recs.append(r)
    recs = new_recs

    embedder = Embedder(cfg)
    inserted = 0
    skipped = total - len(recs)
    with connect(cfg) as conn:
        for batch_start in range(0, len(recs), 32):
            batch = recs[batch_start:batch_start + 32]
            texts = [
                f"职位：{r.get('role','')}｜公司：{r.get('company','')}｜"
                f"经验：{r.get('experience','')}｜学历：{r.get('education','')}｜"
                f"加分：{r.get('bonus','')}｜描述：{str(r.get('desc',''))[:1800]}"
                for r in batch
            ]
            vecs = embedder.embed_documents(texts)
            for r, vec in zip(batch, vecs):
                sal = parse_salary(r.get("salary"))
                dedup = r["dedup_key"]
                raw = (f"# {r.get('role','')} @ {r.get('company','')}\n"
                       f"薪资：{r.get('salary','')}  经验：{r.get('experience','')}  "
                       f"学历：{r.get('education','')}  地点：{r.get('location','')}\n"
                       f"加分项：{r.get('bonus','')}\n\n{r.get('desc','')}")
                req = {"salary_raw": r.get("salary"), "bonus": r.get("bonus"),
                       "industry": r.get("industry"), "size": r.get("size"),
                       "location": r.get("location"), **sal}
                res = conn.execute(
                    "INSERT INTO jds (company, role, url, raw_text, requirements, "
                    "embedding, salary_min, salary_max, salary_months, annual_max, "
                    "experience, education, source_file, dedup_key) "
                    "VALUES (%s,%s,%s,%s,%s,%s::vector,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (dedup_key) DO NOTHING RETURNING id",
                    (r.get("company"), r.get("role"), r.get("url"), raw,
                     json.dumps(req, ensure_ascii=False), str(vec),
                     sal.get("min"), sal.get("max"), sal.get("months"), sal.get("annual_max"),
                     r.get("experience"), r.get("education"), r.get("source_file"), dedup),
                ).fetchone()
                if res:
                    inserted += 1
                else:
                    skipped += 1
    embedder.close()
    return inserted, skipped


def load_folder(folder: str) -> dict:
    """读取目录下所有 xlsx → 去重入 jds（含薪资解析 + 向量）。"""
    cfg = load_config()
    # 跳过 Excel 打开时生成的 ~$ 锁/临时文件，否则 openpyxl 读它会 PermissionError
    files = sorted(f for f in glob.glob(str(Path(folder) / "*.xlsx"))
                   if not Path(f).name.startswith("~$"))
    recs: list[dict] = []
    for f in files:
        try:
            sheet_recs = _read_sheet(f)
        except (PermissionError, OSError):
            continue  # 文件被占用/损坏，跳过这一个不中断整体
        for rec in sheet_recs:
            rec["source_file"] = Path(f).name
            rec["dedup_key"] = (f"{rec.get('company','')}|{rec.get('role','')}|"
                                f"{rec.get('salary','')}")
            recs.append(rec)
    inserted, skipped = _persist_records(recs, cfg)
    return {"files": len(files), "rows": len(recs), "inserted": inserted,
            "skipped_dup": skipped}


def load_json(target: str) -> dict:
    """从 JSON 摄入 JD（推荐的通用数据接口）。

    target 可以是单个 .json 文件，或一个装着多个 .json 的目录。
    每个 JSON 顶层是一个数组，或一个含 "jobs" 数组的对象。每条记录字段（均可选，
    缺啥就空）：company / role / salary / experience / education / desc / location /
    size / bonus / url / industry。也兼容对应的中文键名（见 COL_MAP）。

    salary 写原始字符串即可（如 "25-35K·15薪" / "$120k-$160k"），由 parse_salary 解析。
    详见 docs/DATA_SCHEMA.md，示例见 data/jds.sample.json。
    """
    cfg = load_config()
    p = Path(target)
    files = (sorted(glob.glob(str(p / "*.json"))) if p.is_dir() else [str(p)])
    recs: list[dict] = []
    for f in files:
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = data.get("jobs", []) if isinstance(data, dict) else data
        for rec in (rows or []):
            if not isinstance(rec, dict):
                continue
            norm = _normalize_record(rec)
            norm["source_file"] = Path(f).name
            if norm.get("company") or norm.get("role"):
                recs.append(norm)
    inserted, skipped = _persist_records(recs, cfg)
    return {"files": len(files), "rows": len(recs), "inserted": inserted,
            "skipped_dup": skipped}


# ---------- 简历 → 画像 ----------
# {RECRUITER} 运行时由 config.profile 注入（默认「资深「AI 算法」岗位招聘官」）
_RESUME_SYS = (
    "你是{RECRUITER}招聘专家。把候选人简历抽取成结构化 JSON，只输出 JSON，字段："
    '{"years":数字(工作年限),"seniority":"如 P5/P6 或 初级/中级/高级",'
    '"core_skills":["..."],"domains":["候选人所在领域的细分方向"],'
    '"projects":[{"name":"...","desc":"一句话"}],'
    '"current_salary_band":"(若简历可推断,否则空)",'
    '"highlights":["最能打的 3-5 个亮点"]}'
)


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    try:
        return json.loads(m.group(0) if m else text)
    except (json.JSONDecodeError, AttributeError):
        return {}


def import_resume(*, file: str | None = None, text: str | None = None) -> dict:
    cfg = load_config()
    if file:
        p = Path(file)
        suf = p.suffix.lower()
        if suf == ".pdf":
            from pypdf import PdfReader
            raw = "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)
        elif suf in (".html", ".htm"):
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(p.read_text(encoding="utf-8", errors="ignore"),
                                 "html.parser")
            for t in soup(["script", "style"]):
                t.decompose()
            import re as _re
            raw = _re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))
        elif suf in (".docx",):
            raw = p.read_text(encoding="utf-8", errors="ignore")  # 退化处理
        else:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        source = p.name
    elif text:
        raw, source = text, "pasted"
    else:
        raise ValueError("需提供 file 或 text")

    llm = LLM(cfg)
    embedder = Embedder(cfg)
    resume_sys = _RESUME_SYS.replace("{RECRUITER}", profile_cfg(cfg)["recruiter_title"])
    profile = _parse_json(llm.chat(resume_sys, f"简历全文：\n{raw[:8000]}",
                                   max_tokens=2000, temperature=0.2, think=False))
    vec = embedder.embed_documents([raw[:6000]])[0]
    llm.close()
    embedder.close()

    with connect(cfg) as conn:
        rid = conn.execute(
            "INSERT INTO resume_profile (source_file, raw_text, profile, embedding) "
            "VALUES (%s,%s,%s,%s::vector) RETURNING id",
            (source, raw, json.dumps(profile, ensure_ascii=False), str(vec)),
        ).fetchone()[0]
    return {"resume_id": rid, "source": source, "profile": profile}


def latest_resume(cfg=None) -> dict | None:
    with connect(cfg) as conn:
        row = conn.execute(
            "SELECT id, source_file, profile FROM resume_profile ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return {"resume_id": row[0], "source": row[1], "profile": row[2]}
