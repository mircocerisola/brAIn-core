-- brAIn v5.0 — C-Suite + Ethics + Manager Permissions
-- Applicare via psycopg2

-- ===== ETHICS =====
CREATE TABLE IF NOT EXISTS ethics_violations (
    id serial PRIMARY KEY,
    project_id int REFERENCES projects(id),
    principle_id text,
    principle_name text,
    violation text,
    severity text DEFAULT 'medium',  -- critical, high, medium, low
    suggestion text,
    blocked boolean DEFAULT false,
    ethics_version text DEFAULT '1.0',
    resolved boolean DEFAULT false,
    resolved_at timestamptz,
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ethics_project ON ethics_violations(project_id);
CREATE INDEX IF NOT EXISTS idx_ethics_blocked ON ethics_violations(blocked) WHERE blocked = true;
ALTER TABLE ethics_violations ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "service_role_all_ethics" ON ethics_violations FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===== C-SUITE =====
CREATE TABLE IF NOT EXISTS chief_memory (
    id serial PRIMARY KEY,
    chief_domain text NOT NULL,    -- finance, strategy, marketing, ops, tech, legal, people, product
    key text NOT NULL,
    value text,
    updated_at timestamptz DEFAULT now(),
    UNIQUE(chief_domain, key)
);
CREATE INDEX IF NOT EXISTS idx_chief_memory_domain ON chief_memory(chief_domain);
ALTER TABLE chief_memory ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "service_role_all_chief_memory" ON chief_memory FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS chief_decisions (
    id serial PRIMARY KEY,
    chief_domain text NOT NULL,
    decision_type text,   -- weekly_briefing, anomaly_alert, recommendation, capability_assessment
    summary text,
    full_text text,
    metadata jsonb,
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chief_decisions_domain ON chief_decisions(chief_domain);
CREATE INDEX IF NOT EXISTS idx_chief_decisions_type ON chief_decisions(decision_type);
ALTER TABLE chief_decisions ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "service_role_all_chief_decisions" ON chief_decisions FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===== MANAGER PERMISSIONS =====
ALTER TABLE project_members ADD COLUMN IF NOT EXISTS permissions jsonb DEFAULT '{"can_view_spec": true, "can_send_feedback": true, "can_approve_launch": false}'::jsonb;
ALTER TABLE project_members ADD COLUMN IF NOT EXISTS revenue_share_pct float DEFAULT 0.0;

CREATE TABLE IF NOT EXISTS manager_revenue_share (
    id serial PRIMARY KEY,
    project_id int REFERENCES projects(id),
    manager_user_id bigint,
    manager_username text,
    share_pct float NOT NULL,           -- percentuale sul revenue del progetto
    brain_share_pct float DEFAULT 50.0, -- quota brAIn (default 50%)
    contract_signed_at timestamptz,
    contract_md text,                   -- testo contratto MANAGER_CONTRACT.md
    active boolean DEFAULT true,
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_revenue_share_project ON manager_revenue_share(project_id);
CREATE INDEX IF NOT EXISTS idx_revenue_share_manager ON manager_revenue_share(manager_user_id);
ALTER TABLE manager_revenue_share ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "service_role_all_revenue_share" ON manager_revenue_share FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
