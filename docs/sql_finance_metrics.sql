-- brAIn Finance Metrics — tabella METABOLISM
-- Eseguire su Supabase SQL Editor

CREATE TABLE IF NOT EXISTS finance_metrics (
    id BIGSERIAL PRIMARY KEY,
    report_date DATE NOT NULL UNIQUE,
    total_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    total_cost_eur NUMERIC(12, 6) NOT NULL DEFAULT 0,
    cost_by_agent JSONB DEFAULT '{}',
    calls_by_agent JSONB DEFAULT '{}',
    total_api_calls INTEGER NOT NULL DEFAULT 0,
    successful_calls INTEGER NOT NULL DEFAULT 0,
    failed_calls INTEGER NOT NULL DEFAULT 0,
    total_tokens_in BIGINT NOT NULL DEFAULT 0,
    total_tokens_out BIGINT NOT NULL DEFAULT 0,
    burn_rate_daily_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    projected_monthly_usd NUMERIC(12, 4) NOT NULL DEFAULT 0,
    projected_monthly_eur NUMERIC(12, 4) NOT NULL DEFAULT 0,
    budget_eur NUMERIC(10, 2) NOT NULL DEFAULT 1000,
    budget_usage_pct NUMERIC(6, 2) NOT NULL DEFAULT 0,
    alerts_triggered JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indice per query rapide per data
CREATE INDEX IF NOT EXISTS idx_finance_metrics_date ON finance_metrics (report_date DESC);

-- RLS (Row Level Security) — stessa policy delle altre tabelle
ALTER TABLE finance_metrics ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_full_access" ON finance_metrics
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- Commento tabella
COMMENT ON TABLE finance_metrics IS 'METABOLISM — metriche finanziarie giornaliere, burn rate, proiezioni budget';
