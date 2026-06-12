"""forge — 统一命令行入口（Phase 0 仅 ask / init / doctor）。"""
from __future__ import annotations

import sys

import click

# Windows 控制台默认 GBK，强制 UTF-8 以便输出 emoji/中文不报错
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

from .infra.anki import Anki
from .infra.config import load_config
from .infra.db import migrate
from .infra.llm import LLM


@click.group()
def cli():
    """interview-forge：本地优先的个人面试知识炼炉。"""


@cli.command()
def init():
    """初始化：建库表 + 建 Anki deck/note type。"""
    click.echo("→ 迁移 Postgres schema …")
    vector_ready = migrate()
    click.echo("  · 向量层(pgvector): "
               + ("已就绪" if vector_ready else "已跳过(Phase 1 接入 pgvector 时再建)"))
    click.echo("→ 创建 Anki deck / InterviewQA note type …")
    anki = Anki()
    anki.ensure_setup()
    anki.close()
    click.echo("✅ init 完成")


@cli.command()
def doctor():
    """健康检查：vLLM / Postgres / AnkiConnect 是否就绪。"""
    cfg = load_config()
    ok = True

    # vLLM
    try:
        model = LLM(cfg).ping()
        click.echo(f"✅ vLLM 可用，模型 = {model}")
    except Exception as e:  # noqa: BLE001
        ok = False
        click.echo(f"❌ vLLM 不可用：{e}")

    # Postgres
    try:
        from .infra.db import connect
        with connect(cfg) as conn:
            v = conn.execute("SELECT version()").fetchone()[0]
        click.echo(f"✅ Postgres 可用：{v.split(',')[0]}")
    except Exception as e:  # noqa: BLE001
        ok = False
        click.echo(f"❌ Postgres 不可用：{e}")

    # Anki
    try:
        v = Anki(cfg).version()
        click.echo(f"✅ AnkiConnect 可用，version={v}")
    except Exception as e:  # noqa: BLE001
        ok = False
        click.echo(f"❌ AnkiConnect 不可用：{e}")

    click.echo(f"vault: {cfg['obsidian']['vault_abs']}")
    raise SystemExit(0 if ok else 1)


@cli.command()
@click.argument("qid", type=int)
def cards(qid):
    """把某题答案拆成原子卡 + cloze 推入 Anki（CardSynth）。"""
    from .pipelines.card_synth import CardSynth
    r = CardSynth().run(qid)
    click.echo(f"✅ QID={qid} 推送 原子卡{r['atomic']} + cloze{r['cloze']}")


@cli.command(name="sync-anki")
def sync_anki():
    """批量为所有未拆卡的题补推原子卡 + cloze。"""
    from .pipelines.card_synth import CardSynth, unsynced_question_ids
    ids = unsynced_question_ids()
    if not ids:
        click.echo("没有待补推的题")
        return
    synth = CardSynth()
    for qid in ids:
        try:
            r = synth.run(qid)
            click.echo(f"  QID={qid}: +{r['atomic']}原子 +{r['cloze']}cloze")
        except Exception as e:  # noqa: BLE001
            click.echo(f"  QID={qid}: 跳过({e})")
    synth.close()
    click.echo(f"✅ 处理 {len(ids)} 题")


@cli.command(name="resume")
@click.argument("file", required=False)
def resume_cmd(file):
    """导入简历（默认取 config.jobs.resume）。"""
    from .infra.config import load_config
    from .pipelines.jobs import import_resume
    file = file or load_config().get("jobs", {}).get("resume")
    res = import_resume(file=file)
    click.echo(f"✅ resume={res['resume_id']} 《{res['source']}》 已抽取画像")


@cli.command(name="jobs-scan")
def jobs_scan_cmd():
    """扫描 config.jobs.folder 下所有 JD xlsx 入库。"""
    from .infra.config import load_config
    from .pipelines.jobs import load_folder
    res = load_folder(load_config()["jobs"]["folder"])
    click.echo(f"✅ {res}")


@cli.command(name="jobs-import")
@click.argument("target", required=False)
def jobs_import_cmd(target):
    """从 JSON 摄入 JD（通用数据接口）。TARGET 为 .json 文件或目录，
    缺省取 config.jobs.json。Schema 见 docs/DATA_SCHEMA.md，示例 data/jds.sample.json。"""
    from .infra.config import load_config
    from .pipelines.jobs import load_json
    target = target or load_config().get("jobs", {}).get("json", "data/jds.sample.json")
    res = load_json(target)
    click.echo(f"✅ {res}")


@cli.command()
@click.option("--current-max", type=int, default=None, help="当前月薪上限K(默认从简历推断)")
@click.option("--redo", is_flag=True, help="全量重评(默认增量：只评未评过的)")
@click.option("--scope", type=click.Choice(["all", "pool"]), default="all",
              help="all=评全部JD；pool=dense+高薪候选")
@click.option("--workers", type=int, default=None, help="并发数(默认 config.llm.match_workers 或 32)")
def match(current_max, redo, scope, workers):
    """AI 岗位匹配，打印 最佳匹配 + 冲高薪 两榜。"""
    from .pipelines.job_matcher import JobMatcher
    r = JobMatcher().run(current_max=current_max, redo=redo, scope=scope, workers=workers)
    click.echo(f"薪资期望 {r.get('expect_min','?')}–{r.get('expect_max','?')}K · "
               f"本次新评 {r.get('scored_now',0)} · 失败 {r.get('failed',0)} · "
               f"跳过已评 {r.get('skipped_existing',0)} · 累计 {r['n_scored']} 个岗位")
    for title, lst in (("最佳匹配", r["best"]), ("冲高薪", r["stretch"])):
        click.echo(f"\n=== {title} ===")
        for x in lst[:8]:
            click.echo(f"  {x['ai_score']:>3} [{x['verdict']}] {x['role']} @ "
                       f"{x['company']} ({x['salary_max']}K)")


@cli.command()
@click.argument("jd_id", type=int)
def gap(jd_id):
    """对某 JD 生成备考清单（缺口/项目深挖/联想考点）。"""
    from .pipelines.gap_analyzer import GapAnalyzer
    r = GapAnalyzer().run(jd_id)
    click.echo(f"✅ 《{r['role']} @ {r['company']}》 缺口{r['n_gap']}/项目{r['n_project']}/"
               f"联想{r['n_assoc']}\n📝 {r['note_path']}")


@cli.command(name="jd-materials")
@click.argument("jd_id", type=int)
@click.option("--max-q", type=int, default=6)
def jd_materials(jd_id, max_q):
    """对某 JD 的备考题批量生成 grounded 答案 + Anki 卡。"""
    from .pipelines.materials import build_jd_materials
    r = build_jd_materials(jd_id, max_q=max_q)
    click.echo(f"✅ JD={jd_id} 生成答案 {r['answered']} 题 / 拆卡 {r['carded']} 题，"
               f"剩余 {r['remaining']} 题待备")


@cli.command()
@click.argument("audio")
def transcribe(audio):
    """本地 ASR 转写一个音频文件。"""
    from .infra.asr import transcribe as tr
    r = tr(audio)
    click.echo(f"[{r['device']} · {r['duration']}s · {r['language']}] {r['text']}")


@cli.command()
@click.option("--topic", default="general", help="主题，建议取 config.profile.topics 之一")
@click.option("--jd-id", type=int, default=None)
@click.option("-n", "--questions", type=int, default=4)
def mock(topic, jd_id, questions):
    """开一场模拟面试（命令行交互版）。"""
    from .agent.interviewer import MockInterviewer
    iv = MockInterviewer()
    s = iv.start(topic=topic, jd_id=jd_id, n_questions=questions)
    click.echo(f"\n🎤 {s['opening']}\n考察方向：{ '、'.join(s['focus_areas']) }\n")
    sid = s["session_id"]
    q = s["question"]
    while q:
        click.echo(click.style(f"[Q{s.get('qno','')} · 难度{s.get('difficulty','')}] ", fg="cyan") + q)
        ans = click.prompt("你的回答", default="", show_default=False)
        r = iv.answer(sid, ans)
        click.echo(click.style(f"  评分 {r['avg']}/5 ", fg="yellow") + str(r["scores"]))
        click.echo("  " + (r["feedback"] or ""))
        s = r
        q = r.get("next_question")
    rep = iv.finish(sid)
    click.echo(click.style(f"\n=== 报告 总分 {rep['overall']}/5 ===", fg="green"))
    click.echo(f"逐维：{rep['dim_avg']}")
    click.echo(f"薄弱：{'、'.join(rep['weak_focus']) or '无'}")
    click.echo(f"总评：{rep['summary']}")
    for a in rep.get("actions", []):
        click.echo(f"  · {a}")
    iv.close()


@cli.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8077, type=int)
def serve(host, port):
    """启动本地 Web UI（FastAPI）。"""
    import uvicorn
    click.echo(f"→ UI: http://{host}:{port}")
    uvicorn.run("src.api:app", host=host, port=port, reload=False)


@cli.command()
@click.argument("target")
@click.option("--no-context", is_flag=True, help="跳过 contextual retrieval 定位句（更快）")
def ingest(target, no_context):
    """摄入权威源到知识库：URL / 本地文件(pdf/md/txt)。"""
    from pathlib import Path

    from .pipelines.ingest import IngestPipeline

    pipe = IngestPipeline()
    is_url = target.startswith("http://") or target.startswith("https://")
    kwargs = {"url": target} if is_url else {"file": str(Path(target))}
    click.echo(f"→ 摄入：{target}")
    res = pipe.run(contextual=not no_context, **kwargs)
    pipe.close()
    click.echo(f"✅ source={res['source_id']} 《{res['title']}》 切成 {res['n_chunks']} 块")


@cli.command()
@click.argument("query")
def search(query):
    """只检索看片段（调试用），不生成答案。"""
    from .infra.retrieval import Retriever

    r = Retriever()
    hits = r.search(query)
    r.close()
    for h in hits:
        click.echo(f"[chunk_{h.chunk_id}] score={h.score:.3f} 《{h.source_title}》")
        click.echo("  " + h.content[:120].replace("\n", " "))


@cli.command()
@click.argument("query")
@click.option("-k", default=3, type=int, help="抓取并摄入的源数量")
def frontier(query, k):
    """出网现抓最新论文/模型卡摄入 KB（Phase 5 前沿增强）。"""
    from .pipelines.frontier import Frontier
    fr = Frontier()
    r = fr.ingest(query, k=k)
    fr.close()
    for x in r["ingested"]:
        click.echo(f"  + [{x['from']}] {x['title']} → {x['n_chunks']} 块")
    click.echo(f"✅ 摄入 {len(r['ingested'])} 源 / 跳过{r['skipped']} / 失败{len(r['errors'])}")


@cli.command()
@click.argument("question")
@click.option("--topic", default="general", help="主题，如 inference/transformer/infra")
@click.option("--difficulty", default=3, type=int)
@click.option("--frequency", default=3, type=int)
@click.option("--frontier", "use_frontier", is_flag=True, help="先出网抓最新资料再答")
def ask(question, topic, difficulty, frequency, use_frontier):
    """生成答案 + Obsidian 笔记 + Anki 口述卡。"""
    from .pipelines.answer_forge import AnswerForge

    if use_frontier:
        from .pipelines.frontier import Frontier
        click.echo("→ 出网抓取最新资料…")
        fr = Frontier()
        fres = fr.ingest(question)
        fr.close()
        for x in fres["ingested"]:
            click.echo(f"  + {x['title']} → {x['n_chunks']} 块")
    click.echo(f"→ 生成答案：{question}")
    res = AnswerForge().run(question, topic=topic, difficulty=difficulty,
                            frequency=frequency)
    click.echo(f"✅ QID={res['qid']}  Anki note={res['anki_note_id']}")
    click.echo(f"📝 笔记：{res['note_path']}")


if __name__ == "__main__":
    cli()
