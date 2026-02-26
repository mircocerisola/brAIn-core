-- brAIn Migration: Smoke Test CSO Autonomo Multi-Metodo
-- Brand identity per progetto + 5 metodi smoke test + blocker management
-- Data: 2026-02-26

-- Brand identity columns su projects
ALTER TABLE projects ADD COLUMN IF NOT EXISTS brand_name text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS brand_email text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS brand_domain text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS brand_linkedin text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS brand_landing_url text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS smoke_test_method text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS smoke_test_results jsonb;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS smoke_test_kpi_target jsonb;

-- Smoke test extras per multi-method
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS cold_email_sequence jsonb;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS ads_plan jsonb;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS concierge_plan jsonb;
ALTER TABLE smoke_tests ADD COLUMN IF NOT EXISTS total_cost_eur float DEFAULT 0;

-- Prospect extras
ALTER TABLE smoke_test_prospects ADD COLUMN IF NOT EXISTS company text;
ALTER TABLE smoke_test_prospects ADD COLUMN IF NOT EXISTS role text;
ALTER TABLE smoke_test_prospects ADD COLUMN IF NOT EXISTS linkedin_url text;
ALTER TABLE smoke_test_prospects ADD COLUMN IF NOT EXISTS touchpoint_day int DEFAULT 0;
ALTER TABLE smoke_test_prospects ADD COLUMN IF NOT EXISTS response_text text;
