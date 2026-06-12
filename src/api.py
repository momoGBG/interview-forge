"""FastAPI 后端：把 pipeline 暴露成本地 Web UI 的接口。

启动： uv run forge serve   （或 uvicorn src.api:app）
路由：
  GET  /                  → 前端单页
  GET  /api/health        → vLLM/Postgres/Anki/vault 健康
  POST /api/ingest        → 摄入 file / url / text
  GET  /api/sources       → 知识源列表
  POST /api/ask/stream    → SSE 流式：检索 → 思考 → 答案 → 落库元数据
  GET  /api/library       → 题库 + 答案列表
  GET  /api/answer/{qid}  → 单题完整答案(口述+深挖+出处)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .infra.anki import Anki
from .infra.config import PROJECT_ROOT, load_config, profile_cfg
from .infra.db import connect
from .infra.llm import LLM
from .pipelines.answer_forge import AnswerForge, build_prompt
from .pipelines.ingest import IngestPipeline

STATIC_DIR = PROJECT_ROOT / "src" / "web"

app = FastAPI(title="interview-forge")


# ---------------- 健康 ----------------
def get_health() -> dict:
    cfg = load_config()
    # profile 一并带回：UI 用 topics 填主题下拉、field 显示领域（见 app.js loadHealth）
    out: dict = {"vault": cfg["obsidian"]["vault_abs"], "profile": profile_cfg(cfg)}
    try:
        out["vllm"] = {"ok": True, "model": LLM(cfg).ping()}
    except Exception as e:  # noqa: BLE001
        out["vllm"] = {"ok": False, "error": str(e)[:200]}
    try:
        with connect(cfg) as conn:
            conn.execute("SELECT 1")
            has_vec = conn.execute(
                "SELECT count(*) FROM pg_extension WHERE extname='vector'").fetchone()[0]
            n_chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        out["postgres"] = {"ok": True, "pgvector": bool(has_vec), "chunks": n_chunks}
    except Exception as e:  # noqa: BLE001
        out["postgres"] = {"ok": False, "error": str(e)[:200]}
    try:
        out["anki"] = {"ok": True, "version": Anki(cfg).version()}
    except Exception as e:  # noqa: BLE001
        out["anki"] = {"ok": False, "error": str(e)[:200]}
    return out


@app.get("/api/health")
def health():
    return get_health()


# ---------------- 摄入 ----------------
class IngestBody(BaseModel):
    url: str | None = None
    text: str | None = None
    contextual: bool = True


@app.post("/api/ingest")
def ingest(body: IngestBody):
    pipe = IngestPipeline()
    try:
        res = pipe.run(url=body.url, text=body.text, contextual=body.contextual)
    finally:
        pipe.close()
    return res


@app.post("/api/ingest/file")
async def ingest_file(file: UploadFile, contextual: bool = True):
    suffix = Path(file.filename or "upload").suffix or ".txt"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    pipe = IngestPipeline()
    try:
        res = pipe.run(file=tmp_path, contextual=contextual)
        res["filename"] = file.filename
    finally:
        pipe.close()
        Path(tmp_path).unlink(missing_ok=True)
    return res


class FrontierBody(BaseModel):
    query: str
    k: int = 3


@app.post("/api/frontier")
def frontier(body: FrontierBody):
    """出网现抓最新论文/模型卡并摄入 KB。"""
    from .pipelines.frontier import Frontier
    fr = Frontier()
    try:
        return fr.ingest(body.query, k=body.k)
    finally:
        fr.close()


@app.get("/api/sources")
def sources():
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.id, s.kind, s.title, s.url, s.fetched_at, "
            "count(c.id) AS n_chunks FROM sources s LEFT JOIN chunks c ON c.source_id=s.id "
            "GROUP BY s.id ORDER BY s.id DESC").fetchall()
    return [{"id": r[0], "kind": r[1], "title": r[2], "url": r[3],
             "fetched_at": str(r[4]), "n_chunks": r[5]} for r in rows]


# ---------------- 提问（SSE 流式） ----------------
class AskBody(BaseModel):
    question: str
    topic: str = "general"
    difficulty: int = 3
    frequency: int = 3
    push_anki: bool = True
    frontier: bool = False      # 出网现抓最新资料再答


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/ask/stream")
def ask_stream(body: AskBody):
    def gen():
        forge = AnswerForge()
        try:
            if body.frontier:
                yield _sse({"type": "frontier", "status": "正在出网抓取最新权威资料…"})
                from .pipelines.frontier import Frontier
                fr = Frontier()
                try:
                    fres = fr.ingest(body.question)
                finally:
                    fr.close()
                got = [{"title": x["title"], "n_chunks": x["n_chunks"]}
                       for x in fres["ingested"]]
                yield _sse({"type": "frontier", "status": "done", "ingested": got})
            hits = forge.retrieve(body.question)
            yield _sse({"type": "retrieval", "hits": [
                {"chunk_id": h.chunk_id, "source_title": h.source_title,
                 "url": h.source_url, "score": round(h.score, 3)} for h in hits]})

            system, user = build_prompt(body.question, hits)
            parts: list[str] = []
            for kind, text in forge.llm.chat_stream(system=system, user=user):
                if kind == "content":
                    parts.append(text)
                yield _sse({"type": kind, "text": text})

            answer_md = "".join(parts).strip()
            meta = forge.finalize(
                question=body.question, answer_md=answer_md, hits=hits,
                topic=body.topic, difficulty=body.difficulty,
                frequency=body.frequency, push_anki=body.push_anki)
            yield _sse({"type": "done", **meta})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(e)[:300]})
        finally:
            forge.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------- 题库 ----------------
@app.get("/api/library")
def library():
    with connect() as conn:
        rows = conn.execute(
            "SELECT q.id, q.text, q.topic, q.difficulty, q.frequency, "
            "a.grounded, a.obsidian_path, a.created_at, "
            "COALESCE(jsonb_array_length(a.citations),0) AS n_cite "
            "FROM questions q LEFT JOIN LATERAL ("
            "  SELECT * FROM answers a2 WHERE a2.question_id=q.id ORDER BY id DESC LIMIT 1"
            ") a ON true ORDER BY q.id DESC").fetchall()
    return [{"qid": r[0], "question": r[1], "topic": r[2], "difficulty": r[3],
             "frequency": r[4], "grounded": r[5], "note_path": r[6],
             "created_at": str(r[7]), "n_citations": r[8]} for r in rows]


@app.post("/api/answer/{qid}/generate")
def answer_generate(qid: int, push_anki: bool = True):
    """给某道还没答案的题，现场用 RAG 生成带出处答案 + 写笔记 +（可选）推卡。"""
    with connect() as conn:
        row = conn.execute("SELECT text, topic FROM questions WHERE id=%s", (qid,)).fetchone()
    if not row:
        return JSONResponse({"error": "题目不存在", "qid": qid}, status_code=404)
    forge = AnswerForge()
    try:
        hits = forge.retrieve(row[0])
        system, user = build_prompt(row[0], hits)
        ans_md = forge.llm.chat(system=system, user=user)
        meta = forge.finalize(question=row[0], answer_md=ans_md, hits=hits,
                              topic=row[1] or "general", difficulty=3, frequency=4,
                              push_anki=push_anki, question_id=qid)
    finally:
        forge.close()
    return {"qid": qid, **meta}


@app.post("/api/library/generate_pending")
def library_generate_pending(workers: int = 16):
    """并发把所有可自动答的待答题(gap/jd_derived/mock_weak)刷成答案+卡片。
    后台线程跑，立即返回；前端轮询 /api/library/progress 看进度。"""
    from .pipelines.bulk import start_bulk
    return start_bulk(workers=workers)


@app.get("/api/library/progress")
def library_progress():
    from .pipelines.bulk import status
    return status()


@app.get("/api/answer/{qid}")
def answer(qid: int):
    """单题详情。LEFT JOIN：没生成答案的题也返回题干 + answered=false，
    避免前端拿不到字段渲染成 undefined。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT q.text, q.origin, a.oral_version, a.deep_version, a.grounded, "
            "a.citations, a.obsidian_path FROM questions q "
            "LEFT JOIN LATERAL (SELECT * FROM answers a2 WHERE a2.question_id=q.id "
            "ORDER BY id DESC LIMIT 1) a ON true WHERE q.id=%s", (qid,)).fetchone()
    if not row:
        return JSONResponse({"error": "题目不存在", "qid": qid}, status_code=404)
    return {"qid": qid, "question": row[0], "origin": row[1],
            "answered": row[2] is not None, "oral": row[2], "deep": row[3],
            "grounded": row[4], "citations": row[5], "note_path": row[6]}


@app.get("/api/chunk/{chunk_id}")
def chunk(chunk_id: int):
    """按 id 取某个检索片段的原文 + 来源（供前端溯源弹层）。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT c.id, c.content, c.context, c.ord, s.title, s.url, s.kind "
            "FROM chunks c JOIN sources s ON s.id=c.source_id WHERE c.id=%s",
            (chunk_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "chunk 不存在", "chunk_id": chunk_id},
                            status_code=404)
    return {"chunk_id": row[0], "content": row[1], "context": row[2], "ord": row[3],
            "source_title": row[4], "url": row[5], "kind": row[6]}


# ---------------- 岗位匹配 ----------------
@app.get("/api/resume")
def resume_get():
    from .pipelines.jobs import latest_resume
    r = latest_resume()
    return r or {}


@app.post("/api/resume/import")
async def resume_import(file: UploadFile):
    from .pipelines.jobs import import_resume
    suffix = Path(file.filename or "resume").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        return import_resume(file=tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/resume/import_default")
def resume_import_default():
    """从 config.jobs.resume 导入默认简历（免上传）。"""
    from .pipelines.jobs import import_resume
    path = load_config().get("jobs", {}).get("resume")
    if not path or not Path(path).exists():
        return JSONResponse({"error": f"默认简历不存在：{path}"}, status_code=404)
    return import_resume(file=path)


@app.get("/api/jobs/stats")
def jobs_stats():
    with connect() as conn:
        n = conn.execute("SELECT count(*) FROM jds").fetchone()[0]
        nm = conn.execute("SELECT count(*) FROM job_matches").fetchone()[0]
    return {"jds": n, "matched": nm}


@app.post("/api/jobs/scan")
def jobs_scan():
    """扫描 config.jobs 配置的全部 JD 数据源（xlsx folder + json，都配了就都扫）。"""
    from .pipelines.jobs import load_folder, load_json
    j = load_config().get("jobs", {})
    folder, json_src = j.get("folder"), j.get("json")
    out = {"files": 0, "rows": 0, "inserted": 0, "skipped_dup": 0, "sources": []}
    if folder and Path(folder).exists():
        r = load_folder(folder)
        out["sources"].append("xlsx")
        for k in ("files", "rows", "inserted", "skipped_dup"):
            out[k] += r.get(k, 0)
    if json_src and Path(json_src).exists():
        r = load_json(json_src)
        out["sources"].append("json")
        for k in ("files", "rows", "inserted", "skipped_dup"):
            out[k] += r.get(k, 0)
    if not out["sources"]:
        return JSONResponse(
            {"error": f"未找到 JD 数据源：folder={folder} / json={json_src}，"
                      "请在 config.yaml 的 jobs 块配置至少一个"}, status_code=404)
    return out


class MatchBody(BaseModel):
    current_max: int | None = None
    redo: bool = False           # 默认增量：只评未评过的 JD（新岗位秒级）
    scope: str = "all"           # all=评全部 JD（完整排名）；pool=dense+高薪候选
    workers: int | None = None


@app.post("/api/jobs/match")
def jobs_match(body: MatchBody):
    from .pipelines.job_matcher import JobMatcher
    m = JobMatcher()
    try:
        return m.run(current_max=body.current_max, redo=body.redo,
                     scope=body.scope, workers=body.workers)
    finally:
        m.close()


@app.get("/api/jobs/matches")
def jobs_matches():
    from .pipelines.job_matcher import stored_matches
    return stored_matches()


@app.post("/api/jobs/{jd_id}/prep")
def jobs_prep(jd_id: int):
    from .pipelines.gap_analyzer import GapAnalyzer
    g = GapAnalyzer()
    try:
        return g.run(jd_id)
    finally:
        g.close()


@app.post("/api/jobs/{jd_id}/materials")
def jobs_materials(jd_id: int, max_q: int = 6):
    """对该 JD 的备考题批量生成 grounded 答案 + Anki 卡。"""
    from .pipelines.materials import build_jd_materials
    return build_jd_materials(jd_id, max_q=max_q)


@app.post("/api/jobs/{jd_id}/frontier")
def jobs_frontier(jd_id: int, k_per_query: int = 2, n_queries: int = 4):
    """按该 JD 自动提炼检索词，出网抓最新论文/模型卡入库（让答案接地气在 2026 前沿）。"""
    from .pipelines.frontier import Frontier
    fr = Frontier()
    try:
        return fr.ingest_for_jd(jd_id, k_per_query=k_per_query, n_queries=n_queries)
    finally:
        fr.close()


# ---------------- 模拟面试 Agent ----------------
class MockStartBody(BaseModel):
    topic: str = "general"
    jd_id: int | None = None
    n_questions: int = 4


class MockAnswerBody(BaseModel):
    session_id: int
    answer: str


@app.post("/api/mock/start")
def mock_start(body: MockStartBody):
    from .agent.interviewer import MockInterviewer
    iv = MockInterviewer()
    try:
        return iv.start(topic=body.topic, jd_id=body.jd_id, n_questions=body.n_questions)
    finally:
        iv.close()


@app.post("/api/mock/answer")
def mock_answer(body: MockAnswerBody):
    from .agent.interviewer import MockInterviewer
    iv = MockInterviewer()
    try:
        return iv.answer(body.session_id, body.answer)
    finally:
        iv.close()


@app.post("/api/mock/{session_id}/finish")
def mock_finish(session_id: int):
    from .agent.interviewer import MockInterviewer
    iv = MockInterviewer()
    try:
        return iv.finish(session_id)
    finally:
        iv.close()


@app.post("/api/mock/{session_id}/reinforce")
def mock_reinforce(session_id: int):
    from .agent.interviewer import MockInterviewer
    iv = MockInterviewer()
    try:
        return iv.reinforce_weak(session_id)
    finally:
        iv.close()


# ---------------- 语音（ASR + 口语教练） ----------------
@app.post("/api/asr")
async def asr(file: UploadFile):
    from .infra.asr import transcribe
    suffix = Path(file.filename or "audio").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        return transcribe(tmp_path)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:300]}, status_code=500)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class OralBody(BaseModel):
    text: str


@app.post("/api/mock/oral")
def mock_oral(body: OralBody):
    from .agent.interviewer import oral_coach
    return oral_coach(body.text)


# ---------------- 前端静态页（放最后，避免吃掉 /api） ----------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
