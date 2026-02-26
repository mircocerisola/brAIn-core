-- brAIn v5.4 — Memoria a Tre Livelli
-- L1: Working Memory persistence
-- L2: Episodic Memory
-- L3: Semantic Memory additions

-- L1: Working Memory — storia conversazionale persistita
CREATE TABLE IF NOT EXISTS topic_conversation_history (
  id serial PRIMARY KEY,
  scope_id text NOT NULL,   -- "chat_id:thread_id" o "chat_id:main"
  role text NOT NULL,        -- 'user' | 'bot'
  text text NOT NULL,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tch_scope ON topic_conversation_history(scope_id, created_at DESC);
ALTER TABLE topic_conversation_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_tch" ON topic_conversation_history FOR ALL USING (true) WITH CHECK (true);

-- L2: Episodic Memory — riassunti sessioni
CREATE TABLE IF NOT EXISTS episodic_memory (
  id serial PRIMARY KEY,
  scope_type text NOT NULL,    -- 'topic' | 'project'
  scope_id text NOT NULL,
  summary text NOT NULL,
  messages_covered int,
  importance int DEFAULT 3,
  access_count int DEFAULT 0,
  last_accessed_at timestamptz,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_em_scope ON episodic_memory(scope_type, scope_id, created_at DESC);
ALTER TABLE episodic_memory ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_em" ON episodic_memory FOR ALL USING (true) WITH CHECK (true);

-- L3: Semantic Memory — colonna source per tracciare origine fatti
ALTER TABLE chief_knowledge ADD COLUMN IF NOT EXISTS source text DEFAULT 'manual';
ALTER TABLE org_shared_knowledge ADD COLUMN IF NOT EXISTS source text DEFAULT 'manual';
