"""Phase 0 冒烟测试：验证三大依赖就绪 + 章节解析 + 端到端 ask。

运行：uv run pytest -s   （需要 Postgres/vLLM/Anki 均在线）
仅离线单元用例可：uv run pytest -k offline
"""
from __future__ import annotations

import pytest

from src.infra.anki import Anki
from src.infra.config import load_config
from src.infra.db import connect, migrate
from src.infra.llm import LLM
from src.pipelines.answer_forge import _extract_section


def test_extract_section_offline():
    md = "## 口述版\n本质是空间换时间。\n\n## 深挖版\n### 本质\n细节。"
    assert "空间换时间" in _extract_section(md, "口述版")
    assert "细节" in _extract_section(md, "深挖版")
    assert _extract_section(md, "不存在") == ""


def test_parse_salary_offline():
    from src.pipelines.jobs import parse_salary
    assert parse_salary("30-60K·15薪") == {"min": 30, "max": 60, "months": 15,
                                           "annual_max": 900}
    assert parse_salary("20-35K")["max"] == 35
    assert parse_salary("8千-1.2万")["max"] == 12   # 万→K 换算
    assert parse_salary("面议") == {"months": 12}
    assert parse_salary("") == {}


def test_vllm_online():
    assert LLM().ping()  # 返回模型名即可


def test_postgres_online():
    migrate()
    with connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_anki_online():
    assert Anki().version() == 6


@pytest.mark.e2e
def test_ask_end_to_end():
    """完整切片：调用一次真实 ask，确认落库 + 笔记 + 卡片。"""
    from pathlib import Path

    from src.pipelines.answer_forge import AnswerForge

    res = AnswerForge().run("自注意力里为什么要除以 sqrt(d_k)?", topic="transformer")
    assert res["qid"] > 0
    assert res["anki_note_id"]
    assert Path(res["note_path"]).exists()
    with connect() as conn:
        n = conn.execute("SELECT count(*) FROM cards WHERE question_id=%s",
                         (res["qid"],)).fetchone()[0]
    assert n == 1
