-- Migration: tabelle archivio, status_detail, scan_schedule, exchange_rates
-- brAIn v4.1

-- ============================================================
-- STEP 1: TABELLE ARCHIVIO (prima di truncare)
-- ============================================================
CREATE TABLE IF NOT EXISTS problems_archive AS SELECT * FROM problems;
CREATE TABLE IF NOT EXISTS solutions_archive AS SELECT * FROM solutions;
CREATE TABLE IF NOT EXISTS solution_scores_archive AS SELECT * FROM solution_scores;
CREATE TABLE IF NOT EXISTS bos_archive AS SELECT * FROM solutions WHERE bos_score IS NOT NULL;

-- ============================================================
-- STEP 2: COLONNE status_detail PRIMA DI TRUNCARE
-- ============================================================
ALTER TABLE problems ADD COLUMN IF NOT EXISTS status_detail text DEFAULT 'active';
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS status_detail text DEFAULT 'active';

-- Check constraint valori validi
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'problems_status_detail_check' AND table_name = 'problems'
  ) THEN
    ALTER TABLE problems ADD CONSTRAINT problems_status_detail_check
      CHECK (status_detail IN ('active', 'archived', 'rejected'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'solutions_status_detail_check' AND table_name = 'solutions'
  ) THEN
    ALTER TABLE solutions ADD CONSTRAINT solutions_status_detail_check
      CHECK (status_detail IN ('active', 'archived', 'rejected'));
  END IF;
END $$;

-- Indice per query normali (solo active)
CREATE INDEX IF NOT EXISTS idx_problems_status_detail ON problems (status_detail);
CREATE INDEX IF NOT EXISTS idx_solutions_status_detail ON solutions (status_detail);

-- ============================================================
-- STEP 3: TRUNCATE (svuota le tabelle originali)
-- ============================================================
TRUNCATE problems CASCADE;
TRUNCATE solutions CASCADE;

-- ============================================================
-- STEP 4: scan_schedule — 12 slot da 2 ore
-- ============================================================
CREATE TABLE IF NOT EXISTS scan_schedule (
  id serial PRIMARY KEY,
  hour int NOT NULL,
  strategy text NOT NULL,
  source_category text,
  last_used timestamptz,
  notes text
);

-- Svuota se esiste già per re-popolarlo correttamente
TRUNCATE scan_schedule;

INSERT INTO scan_schedule (hour, strategy, source_category, notes) VALUES
  (0,  'top_sources',           'high_relevance',   'mezzanotte - fonti top ranked'),
  (2,  'sector_rotation',       NULL,                'settore con meno problemi nel DB'),
  (4,  'low_ranking_exploration','low_relevance',   'fonti mai o poco usate - scoperta gemme'),
  (6,  'top_sources',           'high_relevance',   'primo mattino'),
  (8,  'trend_emergenti',       'trending',         'segnali deboli, futuro'),
  (10, 'sector_rotation',       NULL,                'settore diverso dal precedente'),
  (12, 'correlati_approvati',   NULL,                'problemi simili a quelli con BOS alto'),
  (14, 'top_sources',           'high_relevance',   'pomeriggio'),
  (16, 'low_ranking_exploration','low_relevance',   'esplora fonti poco usate'),
  (18, 'sector_rotation',       NULL,                'sera'),
  (20, 'source_refresh',        NULL,                'scoperta nuove fonti - aggiorna ranking'),
  (22, 'top_sources',           'high_relevance',   'tarda sera');

-- RLS scan_schedule
ALTER TABLE scan_schedule ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='scan_schedule' AND policyname='service_role_all') THEN
    CREATE POLICY "service_role_all" ON scan_schedule FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- ============================================================
-- STEP 5: exchange_rates — tasso EUR/USD mensile
-- ============================================================
CREATE TABLE IF NOT EXISTS exchange_rates (
  id serial PRIMARY KEY,
  from_currency text NOT NULL,
  to_currency text NOT NULL,
  rate numeric NOT NULL,
  fetched_at timestamptz DEFAULT now()
);

-- Inserisci tasso iniziale di fallback
INSERT INTO exchange_rates (from_currency, to_currency, rate)
SELECT 'USD', 'EUR', 0.92
WHERE NOT EXISTS (
  SELECT 1 FROM exchange_rates WHERE from_currency = 'USD' AND to_currency = 'EUR'
);

-- RLS exchange_rates
ALTER TABLE exchange_rates ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='exchange_rates' AND policyname='service_role_all') THEN
    CREATE POLICY "service_role_all" ON exchange_rates FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
