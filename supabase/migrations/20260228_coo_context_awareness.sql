-- v5.33: COO context awareness — pending actions + project state

-- Tabella 1: azioni promesse dal COO (traccia cosa ha detto che avrebbe fatto)
CREATE TABLE IF NOT EXISTS coo_pending_actions (
    id SERIAL PRIMARY KEY,
    topic_id BIGINT,
    project_slug TEXT DEFAULT '',
    action_description TEXT NOT NULL,
    target_chief TEXT DEFAULT '',
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    created_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ,
    context_summary TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_coo_pending_status ON coo_pending_actions(status, topic_id);
ALTER TABLE coo_pending_actions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_coo_pending" ON coo_pending_actions FOR ALL USING (true) WITH CHECK (true);

-- Tabella 2: stato pipeline per progetto (il COO aggiorna ad ogni evento)
CREATE TABLE IF NOT EXISTS coo_project_state (
    project_slug TEXT PRIMARY KEY,
    project_id INTEGER,
    current_step TEXT DEFAULT '',
    blocking_chief TEXT DEFAULT '',
    blocking_reason TEXT DEFAULT '',
    parallel_tasks JSONB DEFAULT '[]',
    last_update TIMESTAMPTZ DEFAULT now(),
    next_action TEXT DEFAULT '',
    next_action_owner TEXT DEFAULT ''
);
ALTER TABLE coo_project_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_coo_state" ON coo_project_state FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
