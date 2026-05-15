-- ReAct Agent memory schema expansion
-- Adds turn-based tracking to conversation_messages

ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS turn_id VARCHAR(16);
ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS step_type VARCHAR(20);
ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS tool_name VARCHAR(50);

-- Expand message_type CHECK to include ReAct-specific types
-- First drop the existing constraint (name may vary, use DO block)
DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'conversation_messages'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) LIKE '%message_type%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE conversation_messages DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

ALTER TABLE conversation_messages ADD CONSTRAINT chk_message_type
    CHECK (message_type IN ('咨询', '文档', '文书', '案情', '追问', '其他', '工具调用', '工具结果', '审查', '追问补充'));

-- Index for turn-based queries
CREATE INDEX IF NOT EXISTS idx_messages_turn_id ON conversation_messages(turn_id);
