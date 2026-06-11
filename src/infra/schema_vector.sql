-- interview-forge — 向量层（Phase 1，需要 pgvector 扩展）。
-- migrate() 仅在 pgvector 可用时执行；Phase 0 用 postgres:15 时会被跳过。
CREATE EXTENSION IF NOT EXISTS vector;

-- 检索分块（RAG 的最小检索单元）
CREATE TABLE IF NOT EXISTS chunks (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT REFERENCES sources(id) ON DELETE CASCADE,
    ord           INT,
    content       TEXT NOT NULL,
    context       TEXT,
    embedding     VECTOR(1024),
    tsv           TSVECTOR,
    token_count   INT
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);

CREATE TABLE IF NOT EXISTS resume_chunks (
    id            BIGSERIAL PRIMARY KEY,
    section       TEXT,
    content       TEXT,
    embedding     VECTOR(1024)
);
