-- brAIn v5.6 — Active Session: contesto progetto persistente tra messaggi Telegram
-- Evita che handle_message perda il contesto dopo che il bot manda SPEC/BOS/build review

CREATE TABLE IF NOT EXISTS active_session (
  id serial PRIMARY KEY,
  telegram_user_id bigint NOT NULL,
  context_type text,        -- 'spec_review' | 'bos_review' | 'build_review' | 'chat'
  project_id int REFERENCES projects(id),
  solution_id int REFERENCES solutions(id),
  last_message text,
  updated_at timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_active_session_user ON active_session(telegram_user_id);
ALTER TABLE active_session ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_active_session" ON active_session FOR ALL USING (true) WITH CHECK (true);
