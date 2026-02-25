-- Migration: source_ids su problems, tabella source_thresholds
-- brAIn v4.2

-- ============================================================
-- STEP 1: source_ids su problems (array di IDs fonti)
-- ============================================================
ALTER TABLE problems ADD COLUMN IF NOT EXISTS source_ids jsonb DEFAULT '[]';

-- Migra dati esistenti: se source_id è presente, lo mette in source_ids
UPDATE problems
SET source_ids = jsonb_build_array(source_id)
WHERE source_id IS NOT NULL
  AND (source_ids IS NULL OR source_ids = '[]'::jsonb);

-- ============================================================
-- STEP 2: tabella source_thresholds (soglie dinamiche fonti)
-- ============================================================
CREATE TABLE IF NOT EXISTS source_thresholds (
  id serial PRIMARY KEY,
  dynamic_threshold float,
  absolute_threshold float DEFAULT 0.25,
  active_sources_count int,
  archived_this_week int,
  target_active_pct float DEFAULT 0.80,
  updated_at timestamptz DEFAULT now(),
  update_reason text
);

-- RLS
ALTER TABLE source_thresholds ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='source_thresholds' AND policyname='service_role_all') THEN
    CREATE POLICY "service_role_all" ON source_thresholds FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- Riga iniziale di default
INSERT INTO source_thresholds (dynamic_threshold, absolute_threshold, target_active_pct, update_reason)
SELECT 0.35, 0.25, 0.80, 'valore iniziale'
WHERE NOT EXISTS (SELECT 1 FROM source_thresholds);
