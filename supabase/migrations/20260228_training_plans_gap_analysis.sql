-- training_plans: aggiungi colonne CPeO v5.25 (tabella preesistente con schema diverso)
ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS chief_name TEXT DEFAULT '';
ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS topic TEXT DEFAULT '';
ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS plan_md TEXT DEFAULT '';
ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS sources_json JSONB DEFAULT '[]'::jsonb;
ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS drive_url TEXT DEFAULT '';
ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- Rendi nullable colonne vecchie per compatibilita
ALTER TABLE training_plans ALTER COLUMN agent_id DROP NOT NULL;
ALTER TABLE training_plans ALTER COLUMN gap_identified DROP NOT NULL;

-- Rimuovi check constraint vecchio che ammetteva solo planned/in_progress/completed/failed
ALTER TABLE training_plans DROP CONSTRAINT IF EXISTS training_plans_status_check;

-- gap_analysis_log: log gap analysis giornaliera CPeO
CREATE TABLE IF NOT EXISTS gap_analysis_log (
    id SERIAL PRIMARY KEY,
    chief_name TEXT NOT NULL,
    gap_score NUMERIC(4,2) DEFAULT 0,
    gap_topics JSONB DEFAULT '[]'::jsonb,
    sources_checked JSONB DEFAULT '{}'::jsonb,
    training_proposed BOOLEAN DEFAULT FALSE,
    training_plan_id INTEGER REFERENCES training_plans(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE gap_analysis_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS "service_role_gap_analysis_log" ON gap_analysis_log FOR ALL USING (true) WITH CHECK (true);

-- Reload schema cache
NOTIFY pgrst, 'reload schema';
