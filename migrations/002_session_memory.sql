CREATE TABLE IF NOT EXISTS session_memory (
    session_id VARCHAR(64) PRIMARY KEY,
    short_term_memory JSONB NOT NULL DEFAULT '[]',
    summary_list JSONB NOT NULL DEFAULT '[]',
    window_token_count INT NOT NULL DEFAULT 0,
    state VARCHAR(20) NOT NULL DEFAULT 'idle',
    has_document BOOLEAN NOT NULL DEFAULT FALSE,
    document_name VARCHAR(256),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_memory_updated_at ON session_memory(updated_at);
