"""并发批量补答：把所有可自动答的待答题刷成 grounded 答案 + 卡片。

本地 vLLM 支持上百并发，串行太浪费。这里用线程池，每个 worker 持有独立的
AnswerForge + CardSynth（各自的 LLM/检索/DB 连接，互不干扰），吃满并发。

project_probe（简历项目深挖）不自动答——那是候选人自己的经历，RAG 没有素材，
强答会编造，交给本人或模拟面试处理。
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..infra.anki import Anki
from ..infra.config import load_config
from ..infra.db import connect
from .answer_forge import AnswerForge, build_prompt
from .card_synth import CardSynth

ANSWERABLE = ["gap", "jd_derived", "mock_weak"]

# 检索打的是单卡 embedding/reranker 服务；LLM 生成可高并发，但同时检索数要限流，
# 否则 reranker 被打爆 → ReadTimeout → 答案退化为无出处(grounded=false)。
RETRIEVE_CONCURRENCY = 5
_RETRIEVE_SEM = threading.Semaphore(RETRIEVE_CONCURRENCY)

# 进程内进度（单进程 uvicorn 有效）
_STATE: dict = {"running": False, "total": 0, "done": 0, "grounded": 0,
                "cards": 0, "fail": 0, "started_at": None, "finished_at": None}
_LOCK = threading.Lock()


def pending_answerable(cfg=None) -> list[tuple]:
    with connect(cfg) as conn:
        return conn.execute(
            "SELECT q.id, q.text, q.topic FROM questions q "
            "WHERE q.origin = ANY(%s) AND NOT EXISTS "
            "(SELECT 1 FROM answers a WHERE a.question_id=q.id) ORDER BY q.id",
            (ANSWERABLE,)).fetchall()


def status() -> dict:
    with _LOCK:
        return dict(_STATE)


def _run(items: list[tuple], workers: int):
    cfg = load_config()
    # 预热 Anki，避免多线程首次并发建 deck/model 竞争
    try:
        a = Anki(cfg); a.ensure_setup(); a.ensure_cloze(); a.close()
    except Exception:  # noqa: BLE001
        pass

    tl = threading.local()

    def objs():
        if not hasattr(tl, "forge"):
            tl.forge = AnswerForge(); tl.synth = CardSynth()
        return tl.forge, tl.synth

    def work(item):
        qid, text, topic = item
        forge, synth = objs()
        with _RETRIEVE_SEM:           # 限流：同时检索数 ≤ RETRIEVE_CONCURRENCY
            hits = forge.retrieve(text)
        system, user = build_prompt(text, hits)
        ans_md = forge.llm.chat(system=system, user=user)
        res = forge.finalize(question=text, answer_md=ans_md, hits=hits,
                             topic=topic or "general", difficulty=3, frequency=4,
                             push_anki=True, question_id=qid)
        cards = 0
        try:
            c = synth.run(qid); cards = c["atomic"] + c["cloze"]
        except Exception:  # noqa: BLE001
            pass
        return res["grounded"], cards

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, it) for it in items]
        for f in as_completed(futs):
            try:
                grounded, cards = f.result()
                with _LOCK:
                    _STATE["done"] += 1
                    _STATE["grounded"] += int(grounded)
                    _STATE["cards"] += cards
            except Exception:  # noqa: BLE001
                with _LOCK:
                    _STATE["done"] += 1
                    _STATE["fail"] += 1
    with _LOCK:
        _STATE["running"] = False
        _STATE["finished_at"] = time.time()


def start_bulk(workers: int = 16) -> dict:
    """启动后台并发补答；已在跑则返回当前状态。"""
    with _LOCK:
        if _STATE["running"]:
            return {"already_running": True, **_STATE}
    items = pending_answerable()
    if not items:
        return {"started": 0, "message": "没有可补答的待答题"}
    with _LOCK:
        _STATE.update({"running": True, "total": len(items), "done": 0,
                       "grounded": 0, "cards": 0, "fail": 0,
                       "started_at": time.time(), "finished_at": None})
    threading.Thread(target=_run, args=(items, workers), daemon=True).start()
    return {"started": len(items), "workers": workers}
