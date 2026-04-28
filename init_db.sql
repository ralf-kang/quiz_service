-- Quiz Service schema. Idempotent; safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     TEXT REFERENCES users(id) ON DELETE CASCADE,
    test_type    TEXT,                       -- 사용자 안 카테고리 (예: "정보처리기사")
    filename     TEXT NOT NULL,
    mime         TEXT,
    content_hash TEXT,
    uploaded_at  TIMESTAMPTZ DEFAULT NOW()
);
-- 기존 DB 호환
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS test_type    TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS topics       JSONB;
-- 기존 데이터의 test_type 결측을 '기본' 으로 채워 무손실 유지
UPDATE documents SET test_type = '기본' WHERE test_type IS NULL;

-- 기존 (owner_id, filename) 유니크 인덱스가 있다면 (test_type 분리 전 버전) 제거
DROP INDEX IF EXISTS documents_owner_filename_uniq;
DROP INDEX IF EXISTS documents_owner_hash_uniq;

-- (사용자, 테스트 타입) 안에서만 중복 방지
CREATE UNIQUE INDEX IF NOT EXISTS documents_test_filename_uniq
    ON documents (owner_id, test_type, filename);
CREATE UNIQUE INDEX IF NOT EXISTS documents_test_hash_uniq
    ON documents (owner_id, test_type, content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS documents_test_type_idx
    ON documents (owner_id, test_type);

CREATE TABLE IF NOT EXISTS chunks (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    seq          INT NOT NULL,
    content      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_trgm_idx ON chunks USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS chunks_doc_idx  ON chunks (document_id);

CREATE TABLE IF NOT EXISTS quiz_sessions (
    id            BIGSERIAL PRIMARY KEY,
    owner_id      TEXT REFERENCES users(id) ON DELETE CASCADE,
    document_id   BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    test_type     TEXT,
    title         TEXT,
    n_questions   INT DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);
ALTER TABLE quiz_sessions ADD COLUMN IF NOT EXISTS test_type    TEXT;
ALTER TABLE quiz_sessions ADD COLUMN IF NOT EXISTS document_ids JSONB;
ALTER TABLE quiz_sessions ADD COLUMN IF NOT EXISTS options      JSONB;
UPDATE quiz_sessions SET test_type = '기본' WHERE test_type IS NULL;
CREATE INDEX IF NOT EXISTS quiz_sessions_owner_idx ON quiz_sessions (owner_id);
CREATE INDEX IF NOT EXISTS quiz_sessions_test_type_idx ON quiz_sessions (owner_id, test_type);

CREATE TABLE IF NOT EXISTS questions (
    id            BIGSERIAL PRIMARY KEY,
    owner_id      TEXT REFERENCES users(id) ON DELETE CASCADE,
    document_id   BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    session_id    BIGINT REFERENCES quiz_sessions(id) ON DELETE CASCADE,
    type          TEXT NOT NULL,           -- 'mcq' | 'short'
    style         TEXT,
    stem          TEXT NOT NULL,
    choices       JSONB,
    answer        JSONB NOT NULL,
    rationale     TEXT,
    refs          JSONB,                   -- [{document_id, chunk_id, snippet}]
    topic_tag     TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
-- 기존 DB 호환: 컬럼이 없으면 추가
ALTER TABLE questions ADD COLUMN IF NOT EXISTS session_id BIGINT REFERENCES quiz_sessions(id) ON DELETE CASCADE;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS excluded BOOLEAN DEFAULT FALSE;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS passage  TEXT;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS group_id TEXT;
CREATE INDEX IF NOT EXISTS questions_owner_idx    ON questions (owner_id);
CREATE INDEX IF NOT EXISTS questions_topic_idx    ON questions (topic_tag);
CREATE INDEX IF NOT EXISTS questions_session_idx  ON questions (session_id);
CREATE INDEX IF NOT EXISTS questions_excluded_idx ON questions (owner_id, excluded);

CREATE TABLE IF NOT EXISTS attempts (
    id            BIGSERIAL PRIMARY KEY,
    owner_id      TEXT REFERENCES users(id) ON DELETE CASCADE,
    question_id   BIGINT REFERENCES questions(id) ON DELETE CASCADE,
    user_answer   JSONB,
    correct       BOOLEAN NOT NULL,
    feedback      TEXT,
    attempted_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS attempts_owner_idx ON attempts (owner_id);

CREATE TABLE IF NOT EXISTS mastery (
    owner_id        TEXT REFERENCES users(id) ON DELETE CASCADE,
    topic_tag       TEXT NOT NULL,
    correct_streak  INT DEFAULT 0,
    wrong_count     INT DEFAULT 0,
    level           INT DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (owner_id, topic_tag)
);

CREATE TABLE IF NOT EXISTS exam_scopes (
    test_type    TEXT PRIMARY KEY,
    source       TEXT,                  -- 'file' | 'llm'
    topics       JSONB,                 -- [{"name":"...", "section":"...", "ref_url":"..."}]
    fetched_at   TIMESTAMPTZ DEFAULT NOW()
);
