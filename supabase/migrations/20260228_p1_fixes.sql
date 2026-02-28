-- P1-6: agent_performance — aggiungere colonne mancanti per post_task_learning()
-- La tabella esiste gia' con schema (agent_id, metric_name, metric_value, context, measured_at)
-- Il codice usa: agent_name, task_title, competenza, success, score_before, score_after, lesson
ALTER TABLE agent_performance ADD COLUMN IF NOT EXISTS agent_name text;
ALTER TABLE agent_performance ADD COLUMN IF NOT EXISTS task_title text;
ALTER TABLE agent_performance ADD COLUMN IF NOT EXISTS competenza text;
ALTER TABLE agent_performance ADD COLUMN IF NOT EXISTS success boolean DEFAULT true;
ALTER TABLE agent_performance ADD COLUMN IF NOT EXISTS score_before integer DEFAULT 50;
ALTER TABLE agent_performance ADD COLUMN IF NOT EXISTS score_after integer DEFAULT 50;
ALTER TABLE agent_performance ADD COLUMN IF NOT EXISTS lesson text;

-- P1-7: finance_metrics table (usata da CFO e finance.py)
CREATE TABLE IF NOT EXISTS finance_metrics (
    id serial PRIMARY KEY,
    report_date date UNIQUE NOT NULL,
    total_cost_usd float DEFAULT 0,
    total_cost_eur float DEFAULT 0,
    cost_by_agent jsonb DEFAULT '{}',
    calls_by_agent jsonb DEFAULT '{}',
    total_api_calls integer DEFAULT 0,
    successful_calls integer DEFAULT 0,
    failed_calls integer DEFAULT 0,
    total_tokens_in bigint DEFAULT 0,
    total_tokens_out bigint DEFAULT 0,
    burn_rate_daily_usd float DEFAULT 0,
    projected_monthly_usd float DEFAULT 0,
    projected_monthly_eur float DEFAULT 0,
    budget_eur float DEFAULT 0,
    budget_usage_pct float DEFAULT 0,
    alerts_triggered jsonb DEFAULT '[]',
    created_at timestamptz DEFAULT now()
);

-- P1-9: cleanup task pending_approval vecchi di 7+ giorni
UPDATE code_tasks SET status = 'expired'
WHERE status = 'pending_approval'
  AND created_at < now() - interval '7 days';

-- P1-8: fix Coperti.ai slug e brand_domain
UPDATE projects SET slug = 'coperti-ai', brand_domain = 'coperti.ai'
WHERE id = 5;
