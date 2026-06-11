-- 岗位匹配层（需要 pgvector）。在 schema_vector.sql 之后执行。

-- 简历画像（结构化 + 向量）
CREATE TABLE IF NOT EXISTS resume_profile (
    id            BIGSERIAL PRIMARY KEY,
    source_file   TEXT,
    raw_text      TEXT,
    profile       JSONB,             -- {years, seniority, core_skills[], domains[], projects[], salary_band, highlights[]}
    embedding     VECTOR(1024),
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- jds 扩展：向量 + 解析后的薪资 + 来源文件
ALTER TABLE jds ADD COLUMN IF NOT EXISTS embedding     VECTOR(1024);
ALTER TABLE jds ADD COLUMN IF NOT EXISTS salary_min    INT;     -- 月薪下限(K)
ALTER TABLE jds ADD COLUMN IF NOT EXISTS salary_max    INT;     -- 月薪上限(K)
ALTER TABLE jds ADD COLUMN IF NOT EXISTS salary_months INT;     -- 几薪
ALTER TABLE jds ADD COLUMN IF NOT EXISTS annual_max    INT;     -- 年薪上限(K) = salary_max*months
ALTER TABLE jds ADD COLUMN IF NOT EXISTS experience    TEXT;
ALTER TABLE jds ADD COLUMN IF NOT EXISTS education      TEXT;
ALTER TABLE jds ADD COLUMN IF NOT EXISTS source_file   TEXT;
ALTER TABLE jds ADD COLUMN IF NOT EXISTS dedup_key     TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS jds_dedup_idx ON jds(dedup_key);

-- AI 匹配结果
CREATE TABLE IF NOT EXISTS job_matches (
    id            BIGSERIAL PRIMARY KEY,
    jd_id         BIGINT REFERENCES jds(id) ON DELETE CASCADE,
    resume_id     BIGINT REFERENCES resume_profile(id) ON DELETE CASCADE,
    dense_score   REAL,              -- resume×JD 向量余弦
    ai_score      INT,               -- LLM 综合匹配度 0-100
    verdict       TEXT,              -- 'strong' | 'stretch' | 'weak'
    matched       JSONB,             -- 命中的优势点
    gaps          JSONB,             -- 可补齐的缺口
    reasoning     TEXT,
    prep_built    BOOLEAN DEFAULT false,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS job_matches_uniq ON job_matches(jd_id, resume_id);
