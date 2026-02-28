-- P2-13: Colonne mancanti in solutions (usate da architect.py)
ALTER TABLE solutions ADD COLUMN IF NOT EXISTS approach jsonb;

-- P2-13: Colonne mancanti in solution_scores (usate da architect.py)
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS feasibility_score float;
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS impact_score float;
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS cost_estimate text;
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS complexity text;
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS time_to_market text;
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS nocode_compatible boolean DEFAULT false;
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS overall_score float;
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS notes jsonb;
ALTER TABLE solution_scores ADD COLUMN IF NOT EXISTS scored_by text;

-- P2-11: Documentazione tabelle deprecated (zero code references)
COMMENT ON TABLE agent_episodic_memory IS 'DEPRECATED v5.4 — sostituita da episodic_memory con pgvector';
COMMENT ON TABLE agent_semantic_memory IS 'DEPRECATED v5.4 — sostituita da chief_knowledge + org_shared_knowledge';
COMMENT ON TABLE agent_working_memory IS 'DEPRECATED v5.4 — sostituita da topic_conversation_history';
