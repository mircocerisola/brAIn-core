-- v5.35: Task Management System
-- chief_pending_tasks: traccia task per ogni Chief (DA FARE/FATTO/BLOCCATO)
-- coo_project_tasks: TODO list COO per cantieri (P0/P1/P2)

-- 1. chief_pending_tasks — task pendenti per ogni Chief
CREATE TABLE IF NOT EXISTS chief_pending_tasks (
    id SERIAL PRIMARY KEY,
    chief_id TEXT NOT NULL,
    topic_id INTEGER,
    project_slug TEXT DEFAULT '',
    task_description TEXT NOT NULL,
    task_number INTEGER DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'blocked')),
    blocked_reason TEXT DEFAULT '',
    blocked_by TEXT DEFAULT '',
    output_text TEXT DEFAULT '',
    source TEXT DEFAULT 'mirco' CHECK (source IN ('mirco', 'coo', 'inter_agent')),
    created_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);

ALTER TABLE chief_pending_tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_chief_pending_tasks" ON chief_pending_tasks
    FOR ALL USING (true) WITH CHECK (true);

CREATE INDEX IF NOT EXISTS idx_chief_pending_tasks_chief_status
    ON chief_pending_tasks(chief_id, status);
CREATE INDEX IF NOT EXISTS idx_chief_pending_tasks_topic
    ON chief_pending_tasks(topic_id) WHERE topic_id IS NOT NULL;

-- 2. coo_project_tasks — TODO list del COO per cantieri
CREATE TABLE IF NOT EXISTS coo_project_tasks (
    id SERIAL PRIMARY KEY,
    project_slug TEXT NOT NULL,
    project_id INTEGER,
    task_description TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'P1' CHECK (priority IN ('P0', 'P1', 'P2')),
    owner_chief TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'da_fare' CHECK (status IN ('da_fare', 'fatto', 'bloccato')),
    blocked_by TEXT DEFAULT '',
    blocked_reason TEXT DEFAULT '',
    output_text TEXT DEFAULT '',
    mirco_approved BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);

ALTER TABLE coo_project_tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_coo_project_tasks" ON coo_project_tasks
    FOR ALL USING (true) WITH CHECK (true);

CREATE INDEX IF NOT EXISTS idx_coo_project_tasks_project_status
    ON coo_project_tasks(project_slug, status);
CREATE INDEX IF NOT EXISTS idx_coo_project_tasks_owner
    ON coo_project_tasks(owner_chief, status);

NOTIFY pgrst, 'reload schema';
