-- Migration: crea tabella action_queue per coda azioni prioritizzata
-- brAIn v2.0 — Command Center action queue

CREATE TABLE IF NOT EXISTS action_queue (
  id serial PRIMARY KEY,
  user_id bigint NOT NULL,
  action_type text NOT NULL,
  title text NOT NULL,
  description text NOT NULL,
  payload jsonb,
  priority int NOT NULL DEFAULT 5,
  urgency int NOT NULL DEFAULT 5,
  importance int NOT NULL DEFAULT 5,
  priority_score float GENERATED ALWAYS AS ((priority * 0.3) + (urgency * 0.4) + (importance * 0.3)) STORED,
  status text DEFAULT 'pending',
  created_at timestamptz DEFAULT now(),
  completed_at timestamptz
);

-- Indice per query frequenti: pending ordinati per priority_score
CREATE INDEX IF NOT EXISTS idx_action_queue_pending ON action_queue (status, priority_score DESC)
  WHERE status = 'pending';

-- Indice per user_id
CREATE INDEX IF NOT EXISTS idx_action_queue_user ON action_queue (user_id, status);

-- RLS: abilita e policy
ALTER TABLE action_queue ENABLE ROW LEVEL SECURITY;

-- Policy: service_role ha accesso completo (il bot usa service_role key)
CREATE POLICY "service_role_all" ON action_queue
  FOR ALL
  USING (true)
  WITH CHECK (true);
