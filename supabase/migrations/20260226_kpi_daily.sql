-- Migration: kpi_daily table
-- Data: 2026-02-26

CREATE TABLE IF NOT EXISTS kpi_daily (
  id serial PRIMARY KEY,
  date date DEFAULT CURRENT_DATE,
  problems_found int DEFAULT 0,
  avg_problem_score float DEFAULT 0,
  bos_generated int DEFAULT 0,
  avg_bos_score float DEFAULT 0,
  mvps_launched int DEFAULT 0,
  active_cantieri int DEFAULT 0,
  total_cost_eur float DEFAULT 0,
  api_calls int DEFAULT 0,
  recorded_at timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS kpi_daily_date_unique ON kpi_daily(date);

-- pg_cron: aggiorna kpi_daily ogni giorno a mezzanotte UTC
-- Richiede pg_cron + pg_net abilitati su Supabase (Pro plan).
-- Alternativa: Cloud Scheduler → POST /kpi/update su agents-runner.
--
-- SELECT cron.schedule(
--   'kpi-daily-midnight',
--   '0 0 * * *',
--   $$
--     SELECT net.http_post(
--       url := current_setting('app.agents_runner_url'),
--       headers := '{"Content-Type": "application/json"}'::jsonb,
--       body := '{}'::jsonb
--     );
--   $$
-- );
