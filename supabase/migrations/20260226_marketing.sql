-- Migration: brand_assets, marketing_reports
-- Data: 2026-02-26

CREATE TABLE IF NOT EXISTS brand_assets (
  id serial PRIMARY KEY,
  project_id int REFERENCES projects(id),
  target text DEFAULT 'project',
  brand_name text,
  tagline text,
  logo_url text,
  brand_dna_md text,
  positioning_md text,
  content_kit_md text,
  growth_strategy_md text,
  social_strategy_md text,
  pr_kit_md text,
  customer_marketing_md text,
  marketing_ops_md text,
  status text DEFAULT 'pending',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_brand_assets_project ON brand_assets(project_id);

CREATE TABLE IF NOT EXISTS marketing_reports (
  id serial PRIMARY KEY,
  project_id int REFERENCES projects(id),
  week_start date,
  landing_visits int DEFAULT 0,
  cac_eur float,
  email_open_rate float,
  conversion_rate float,
  north_star_value float,
  channel_breakdown jsonb,
  recorded_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_marketing_reports_project ON marketing_reports(project_id);
