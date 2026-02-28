-- conversation_state: traccia quale Chief e' attivo per topic Telegram
CREATE TABLE IF NOT EXISTS conversation_state (
    topic_id BIGINT PRIMARY KEY,
    active_chief TEXT,
    last_message_at TIMESTAMPTZ DEFAULT NOW(),
    project_slug TEXT,
    context TEXT
);

ALTER TABLE conversation_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_conversation_state" ON conversation_state FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
