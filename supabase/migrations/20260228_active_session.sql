-- brAIn v5.6 — Active Session: contesto progetto persistente tra messaggi Telegram
-- Evita che handle_message perda il contesto dopo che il bot manda SPEC/BOS/build review

CREATE TABLE IF NOT EXISTS active_session (
  id serial PRIMARY KEY,
  telegram_user_id bigint NOT NULL,
  context_type text,
  project_id int,
  solution_id int,
  last_message text,
  updated_at timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_active_session_user ON active_session(telegram_user_id);
