-- Migration: project_members table + build_phase column
-- Data: 2026-02-26

CREATE TABLE IF NOT EXISTS project_members (
  id serial PRIMARY KEY,
  project_id int REFERENCES projects(id),
  telegram_phone text,
  telegram_user_id bigint,
  telegram_username text,
  role text DEFAULT 'manager',
  added_by bigint,
  added_at timestamptz DEFAULT now(),
  active boolean DEFAULT true
);

ALTER TABLE projects ADD COLUMN IF NOT EXISTS build_phase int DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(telegram_user_id);
ALTER TABLE project_members ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON project_members FOR ALL USING (true) WITH CHECK (true);
