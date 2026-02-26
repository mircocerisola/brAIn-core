-- Migration: pipeline_locked + code_tasks + sandbox fields
-- Applied: 2026-02-27

-- TASK 2: pipeline lock anti-duplicazione
ALTER TABLE projects ADD COLUMN IF NOT EXISTS pipeline_locked boolean DEFAULT false;

-- Indici per pipeline_locked
CREATE INDEX IF NOT EXISTS idx_projects_pipeline_locked ON projects(pipeline_locked) WHERE pipeline_locked = true;
CREATE INDEX IF NOT EXISTS idx_projects_bos_id ON projects(bos_id);

-- code_tasks: tabella per task da eseguire dai Chief via Code Agent
CREATE TABLE IF NOT EXISTS code_tasks (
    id serial PRIMARY KEY,
    title text NOT NULL,
    prompt text,
    requested_by text,
    status text DEFAULT 'pending_approval',
    sandbox_check jsonb,
    sandbox_passed boolean DEFAULT false,
    override_by text,
    triggered_by_message text,
    routing_chain jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- Se code_tasks esiste già, aggiungi le colonne mancanti
ALTER TABLE code_tasks ADD COLUMN IF NOT EXISTS sandbox_check jsonb;
ALTER TABLE code_tasks ADD COLUMN IF NOT EXISTS sandbox_passed boolean DEFAULT false;
ALTER TABLE code_tasks ADD COLUMN IF NOT EXISTS override_by text;
ALTER TABLE code_tasks ADD COLUMN IF NOT EXISTS triggered_by_message text;
ALTER TABLE code_tasks ADD COLUMN IF NOT EXISTS routing_chain jsonb;

-- RLS e indici code_tasks
ALTER TABLE code_tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS "service_role_all_code_tasks" ON code_tasks FOR ALL USING (true) WITH CHECK (true);
CREATE INDEX IF NOT EXISTS idx_code_tasks_status ON code_tasks(status);
CREATE INDEX IF NOT EXISTS idx_code_tasks_requested_by ON code_tasks(requested_by);
