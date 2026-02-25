-- Migration: Layer 3 — tabelle projects e project_metrics
-- brAIn v4.3 — Execution Pipeline (BOS → MVP)
-- Ricrea projects con schema Layer 3 (era vuota con schema legacy)

DROP TABLE IF EXISTS project_metrics CASCADE;
DROP TABLE IF EXISTS projects CASCADE;

CREATE TABLE projects (
  id serial PRIMARY KEY,
  name text NOT NULL,
  slug text UNIQUE NOT NULL,
  bos_id int REFERENCES solutions(id),
  bos_score float,
  status text DEFAULT 'init',
  github_repo text,
  topic_id bigint,            -- Telegram Forum Topic thread ID
  landing_page_url text,      -- null finche' non si deploya
  landing_page_html text,     -- HTML generato (deploy separato)
  spec_md text,
  build_prompt text,          -- Prompt Claude Code pronto
  stack jsonb,
  kpis jsonb,
  gtm_script text,
  prospect_list jsonb,
  created_at timestamptz DEFAULT now(),
  launched_at timestamptz,
  notes text
);

CREATE TABLE project_metrics (
  id serial PRIMARY KEY,
  project_id int REFERENCES projects(id),
  week int,
  customers_count int DEFAULT 0,
  revenue_eur float DEFAULT 0,
  key_metric_name text,
  key_metric_value float,
  recorded_at timestamptz DEFAULT now()
);

CREATE INDEX idx_projects_slug ON projects(slug);
CREATE INDEX idx_projects_status ON projects(status);
CREATE INDEX idx_project_metrics_project_week ON project_metrics(project_id, week);

-- RLS
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_metrics ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON projects
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "service_role_all" ON project_metrics
  FOR ALL USING (true) WITH CHECK (true);
