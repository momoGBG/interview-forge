-- 模拟面试 Agent 的会话与回合（用于复盘、薄弱点回流）。
CREATE TABLE IF NOT EXISTS interview_sessions (
    id          BIGSERIAL PRIMARY KEY,
    topic       TEXT,
    jd_id       BIGINT,
    plan        JSONB,          -- 面试大纲(focus areas)
    difficulty  SMALLINT DEFAULT 3,
    status      TEXT DEFAULT 'active',   -- 'active' | 'done'
    summary     JSONB,
    created_at  TIMESTAMPTZ DEFAULT now(),
    ended_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS interview_turns (
    id          BIGSERIAL PRIMARY KEY,
    session_id  BIGINT REFERENCES interview_sessions(id) ON DELETE CASCADE,
    qno         INT,
    focus       TEXT,
    question    TEXT,
    answer      TEXT,
    scores      JSONB,          -- {结构性,准确性,数字,选型意识,踩坑, total}
    feedback    TEXT,
    is_followup BOOLEAN DEFAULT false,
    citations   JSONB,
    created_at  TIMESTAMPTZ DEFAULT now()
);
