-- brAIn Migration: Lean Pipeline Redesign
-- Nuovi pipeline_step per flow CSO→Smoke Test→GO Mirco→COO→Launch
-- Data: 2026-02-26

-- Aggiorna progetti esistenti con vecchi step al nuovo equivalente
UPDATE projects SET pipeline_step = 'spec_pending' WHERE pipeline_step = 'spec_pending';
UPDATE projects SET pipeline_step = 'spec_approved' WHERE pipeline_step = 'spec_approved';
UPDATE projects SET pipeline_step = 'legal_pending' WHERE pipeline_step = 'legal_pending';
UPDATE projects SET pipeline_step = 'legal_approved' WHERE pipeline_step = 'legal_approved';
-- Vecchi smoke step → nuovo smoke_test_designing (da rivalutare)
UPDATE projects SET pipeline_step = 'smoke_test_designing' WHERE pipeline_step IN ('smoke_pending', 'smoke_approved', 'smoke_done');
-- Vecchi build step → build_running
UPDATE projects SET pipeline_step = 'build_running' WHERE pipeline_step IN ('build_pending', 'build_running', 'build_done');

-- Aggiungi colonna smoke_test_plan per il piano smoke del CSO
ALTER TABLE projects ADD COLUMN IF NOT EXISTS smoke_test_plan jsonb DEFAULT NULL;
-- Aggiungi colonna smoke_test_kpi per i criteri successo/fallimento
ALTER TABLE projects ADD COLUMN IF NOT EXISTS smoke_test_kpi jsonb DEFAULT NULL;
-- Aggiungi colonna pipeline_territory per tracciare chi gestisce (cso/coo)
ALTER TABLE projects ADD COLUMN IF NOT EXISTS pipeline_territory text DEFAULT 'cso';

-- Aggiungi campi smoke test plan in smoke_tests
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS method text DEFAULT NULL;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS kpi_success text DEFAULT NULL;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS kpi_failure text DEFAULT NULL;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS duration_days int DEFAULT 7;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS materials_needed text DEFAULT NULL;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS daily_updates jsonb DEFAULT '[]'::jsonb;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS positive_responses int DEFAULT 0;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS negative_responses int DEFAULT 0;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS no_response int DEFAULT 0;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS demo_requests int DEFAULT 0;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS qualitative_feedback jsonb DEFAULT '[]'::jsonb;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS cso_recommendation text DEFAULT NULL;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS mirco_decision text DEFAULT NULL;

-- Indice per pipeline_territory
CREATE INDEX IF NOT EXISTS idx_projects_pipeline_territory ON projects(pipeline_territory);
