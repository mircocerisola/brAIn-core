-- v5.28: project_assets table for storing landing pages, brand files, etc.
CREATE TABLE IF NOT EXISTS project_assets (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id),
    asset_type TEXT NOT NULL,
    content TEXT DEFAULT '',
    filename TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(project_id, asset_type)
);

ALTER TABLE project_assets ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_project_assets" ON project_assets FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
