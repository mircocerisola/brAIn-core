-- Migration: legal_reviews, smoke_tests, nuove colonne projects
-- Data: 2026-02-26

-- MACRO-TASK 1: colonne DB separato + SPEC doppio formato
ALTER TABLE projects ADD COLUMN IF NOT EXISTS db_url text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS db_key_secret_name text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS spec_human_md text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS smoke_test_url text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS spec_insights jsonb;

-- MACRO-TASK 2: legal_reviews
CREATE TABLE IF NOT EXISTS legal_reviews (
  id serial PRIMARY KEY,
  project_id int REFERENCES projects(id),
  review_type text DEFAULT 'spec_review',
  status text DEFAULT 'pending',
  green_points jsonb,
  yellow_points jsonb,
  red_points jsonb,
  report_md text,
  reviewed_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_legal_reviews_project ON legal_reviews(project_id);

-- MACRO-TASK 3: smoke tests
CREATE TABLE IF NOT EXISTS smoke_tests (
  id serial PRIMARY KEY,
  project_id int REFERENCES projects(id),
  landing_page_url text,
  prospects_count int DEFAULT 0,
  messages_sent int DEFAULT 0,
  landing_visits int DEFAULT 0,
  forms_compiled int DEFAULT 0,
  rejections_with_reason int DEFAULT 0,
  conversion_rate float DEFAULT 0,
  spec_insights jsonb,
  recommendation text,
  started_at timestamptz DEFAULT now(),
  completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS smoke_test_prospects (
  id serial PRIMARY KEY,
  smoke_test_id int REFERENCES smoke_tests(id),
  project_id int REFERENCES projects(id),
  name text,
  contact text,
  channel text DEFAULT 'email',
  status text DEFAULT 'pending',
  rejection_reason text,
  form_message text,
  sent_at timestamptz,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS smoke_test_events (
  id serial PRIMARY KEY,
  smoke_test_id int REFERENCES smoke_tests(id),
  prospect_id int REFERENCES smoke_test_prospects(id),
  event_type text,
  data jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_smoke_tests_project ON smoke_tests(project_id);
CREATE INDEX IF NOT EXISTS idx_smoke_test_events_type ON smoke_test_events(event_type);
