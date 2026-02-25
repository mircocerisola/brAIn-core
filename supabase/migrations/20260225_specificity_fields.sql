-- Migration: campi specificità per problems e solutions
-- brAIn v3.0 — qualità e specificità problemi/soluzioni

-- PROBLEMS: nuovi campi obbligatori per specificità
ALTER TABLE problems ADD COLUMN IF NOT EXISTS target_customer text;
ALTER TABLE problems ADD COLUMN IF NOT EXISTS target_geography text;
ALTER TABLE problems ADD COLUMN IF NOT EXISTS problem_frequency text;
ALTER TABLE problems ADD COLUMN IF NOT EXISTS current_workaround text;
ALTER TABLE problems ADD COLUMN IF NOT EXISTS pain_intensity int;
ALTER TABLE problems ADD COLUMN IF NOT EXISTS evidence text;
ALTER TABLE problems ADD COLUMN IF NOT EXISTS why_now text;

-- SOLUTIONS: nuovi campi MVP-ready
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS value_proposition text;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS customer_segment text;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS revenue_model text;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS price_point text;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS distribution_channel text;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS mvp_features jsonb;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS mvp_build_time int;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS mvp_cost_eur numeric;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS unfair_advantage text;
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS competitive_gap text;

-- Indici utili per query per target_customer e pain_intensity
CREATE INDEX IF NOT EXISTS idx_problems_pain_intensity ON problems (pain_intensity);
CREATE INDEX IF NOT EXISTS idx_problems_target_geography ON problems (target_geography);
