CREATE TABLE IF NOT EXISTS conversation_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    message_role VARCHAR(10) NOT NULL CHECK (message_role IN ('user', 'ai')),
    message_content TEXT NOT NULL,
    token_count INT NOT NULL DEFAULT 0,
    create_time TIMESTAMP NOT NULL DEFAULT NOW(),
    message_type VARCHAR(20) NOT NULL DEFAULT '咨询'
        CHECK (message_type IN ('咨询', '文档', '文书', '案情', '追问'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON conversation_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_create_time ON conversation_messages(create_time);
