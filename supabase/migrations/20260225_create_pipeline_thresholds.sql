-- Migration: pipeline_thresholds — soglie dinamiche della pipeline automatica
-- brAIn v3.4 — Pipeline automatica con soglie auto-adattive

CREATE TABLE IF NOT EXISTS pipeline_thresholds (
  id serial PRIMARY KEY,
  soglia_problema float NOT NULL DEFAULT 0.65,
  soglia_soluzione float NOT NULL DEFAULT 0.70,
  soglia_feasibility float NOT NULL DEFAULT 0.70,
  soglia_bos float NOT NULL DEFAULT 0.80,
  bos_approval_rate float,
  updated_at timestamptz DEFAULT now(),
  update_reason text
);

-- Trigger per aggiornare updated_at automaticamente
CREATE OR REPLACE FUNCTION update_pipeline_thresholds_ts()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pipeline_thresholds_ts
  BEFORE UPDATE ON pipeline_thresholds
  FOR EACH ROW EXECUTE FUNCTION update_pipeline_thresholds_ts();

-- RLS
ALTER TABLE pipeline_thresholds ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON pipeline_thresholds
  FOR ALL
  USING (true)
  WITH CHECK (true);

-- Riga default iniziale
INSERT INTO pipeline_thresholds (soglia_problema, soglia_soluzione, soglia_feasibility, soglia_bos, update_reason)
VALUES (0.65, 0.70, 0.70, 0.80, 'Valori default iniziali — brAIn v3.4');

-- Pulizia azioni obsolete da action_queue (review_problem, review_solution, review_feasibility)
-- Non devono piu esistere: ora solo approve_bos viene generato automaticamente
UPDATE action_queue
SET status = 'skipped'
WHERE status = 'pending'
  AND action_type IN ('review_problem', 'review_solution', 'review_feasibility');
