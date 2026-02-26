-- Migration: architettura conoscenza Chief
-- Applied: 2026-02-28

-- Conoscenza condivisa brAIn (DNA, decisioni, processi, valori)
CREATE TABLE IF NOT EXISTS org_shared_knowledge (
    id serial PRIMARY KEY,
    category text NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    importance int DEFAULT 3,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_osk_category ON org_shared_knowledge(category);
CREATE INDEX IF NOT EXISTS idx_osk_importance ON org_shared_knowledge(importance DESC);
ALTER TABLE org_shared_knowledge ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS "service_role_all_osk" ON org_shared_knowledge FOR ALL USING (true) WITH CHECK (true);

-- Conoscenza specialistica per Chief (profili, coaching, report CDO, learning)
CREATE TABLE IF NOT EXISTS chief_knowledge (
    id serial PRIMARY KEY,
    chief_id text NOT NULL,
    knowledge_type text NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    importance int DEFAULT 3,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ck_chief_id ON chief_knowledge(chief_id);
CREATE INDEX IF NOT EXISTS idx_ck_type ON chief_knowledge(knowledge_type);
CREATE INDEX IF NOT EXISTS idx_ck_importance ON chief_knowledge(importance DESC, created_at DESC);
ALTER TABLE chief_knowledge ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS "service_role_all_ck" ON chief_knowledge FOR ALL USING (true) WITH CHECK (true);
