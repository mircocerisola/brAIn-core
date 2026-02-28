-- v5.36: Add missing columns to projects table
-- legal_status: used by CLO for legal gate tracking
-- updated_at: used by COO for daily reports and snapshots

ALTER TABLE projects ADD COLUMN IF NOT EXISTS legal_status text DEFAULT 'pending';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone DEFAULT now();

-- Auto-update updated_at on any row change
CREATE OR REPLACE FUNCTION update_projects_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_projects_updated_at ON projects;
CREATE TRIGGER trg_projects_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION update_projects_updated_at();
