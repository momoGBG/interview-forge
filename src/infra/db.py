"""Postgres 连接 + schema 迁移。"""
from __future__ import annotations

from pathlib import Path

import psycopg

from .config import load_config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
VECTOR_SCHEMA_PATH = Path(__file__).resolve().parent / "schema_vector.sql"
JOBS_SCHEMA_PATH = Path(__file__).resolve().parent / "schema_jobs.sql"
AGENT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema_agent.sql"


def dsn(cfg: dict | None = None) -> str:
    cfg = (cfg or load_config())["postgres"]
    return (
        f"host={cfg['host']} port={cfg['port']} dbname={cfg['db']} "
        f"user={cfg['user']} password={cfg['password']}"
    )


def connect(cfg: dict | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn(cfg), autocommit=True)


def migrate(cfg: dict | None = None) -> bool:
    """执行核心 schema（幂等）。若 pgvector 可用则一并建向量层。

    返回 True 表示向量层已就绪（Phase 1 ready），False 表示当前 Postgres
    无 pgvector，向量层已跳过（Phase 0 用 postgres:15 时的预期情况）。
    """
    with connect(cfg) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        try:
            conn.execute(VECTOR_SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute(JOBS_SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute(AGENT_SCHEMA_PATH.read_text(encoding="utf-8"))
            return True
        except psycopg.Error:
            # 无 pgvector 扩展：Phase 0 不需要，安静跳过
            return False
