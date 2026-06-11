-- interview-forge — Phase 0 核心 schema（不依赖 pgvector）。
-- 向量相关表/列在 schema_vector.sql（Phase 1，需要 pgvector 扩展）。

-- 知识源（权威材料的原始记录）
CREATE TABLE IF NOT EXISTS sources (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    fetched_at    TIMESTAMPTZ DEFAULT now(),
    raw_markdown  TEXT,
    meta          JSONB
);

-- 题库
CREATE TABLE IF NOT EXISTS questions (
    id            BIGSERIAL PRIMARY KEY,
    text          TEXT NOT NULL,
    topic         TEXT,
    difficulty    SMALLINT,
    frequency     SMALLINT,
    origin        TEXT,
    jd_id         BIGINT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 答案
CREATE TABLE IF NOT EXISTS answers (
    id            BIGSERIAL PRIMARY KEY,
    question_id   BIGINT REFERENCES questions(id) ON DELETE CASCADE,
    oral_version  TEXT,
    deep_version  TEXT,
    citations     JSONB,
    grounded      BOOLEAN DEFAULT false,
    obsidian_path TEXT,
    model         TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Anki 卡片映射
CREATE TABLE IF NOT EXISTS cards (
    id            BIGSERIAL PRIMARY KEY,
    question_id   BIGINT REFERENCES questions(id),
    anki_note_id  BIGINT,
    front         TEXT,
    back          TEXT,
    card_type     TEXT,
    pushed_at     TIMESTAMPTZ
);

-- JD（requirements 等）
CREATE TABLE IF NOT EXISTS jds (
    id            BIGSERIAL PRIMARY KEY,
    company       TEXT, role TEXT, url TEXT,
    raw_text      TEXT,
    requirements  JSONB,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 学习与模拟面试日志
CREATE TABLE IF NOT EXISTS study_log (
    id            BIGSERIAL PRIMARY KEY,
    question_id   BIGINT,
    event         TEXT,
    score         SMALLINT,
    notes         TEXT,
    ts            TIMESTAMPTZ DEFAULT now()
);
