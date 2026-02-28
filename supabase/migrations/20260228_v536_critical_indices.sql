-- v5.36: Indici critici per performance
-- Solo tabelle/colonne verificate esistenti

-- agent_logs: tabella con piu traffico, ogni API call scrive qui
CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_status_time
    ON agent_logs(agent_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_action_time
    ON agent_logs(action, created_at DESC);

-- org_config: letta centinaia di volte/giorno con eq("key", X)
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_config_key
    ON org_config(key);

-- chief_knowledge: letta ad ogni build_base_prompt
CREATE INDEX IF NOT EXISTS idx_chief_knowledge_chief_type_imp
    ON chief_knowledge(chief_id, knowledge_type, importance DESC);

-- episodic_memory: filtrata per scope_type + scope_id
CREATE INDEX IF NOT EXISTS idx_episodic_importance_access
    ON episodic_memory(importance, last_accessed_at);

-- problems: score ordering e sector filtering
CREATE INDEX IF NOT EXISTS idx_problems_status_detail
    ON problems(status, status_detail);
CREATE INDEX IF NOT EXISTS idx_problems_score
    ON problems(weighted_score DESC);

-- solutions: BOS score queries
CREATE INDEX IF NOT EXISTS idx_solutions_problem
    ON solutions(problem_id);
CREATE INDEX IF NOT EXISTS idx_solutions_bos
    ON solutions(bos_score DESC);

-- projects: status + pipeline_step composito
CREATE INDEX IF NOT EXISTS idx_projects_status_step
    ON projects(status, pipeline_step);

-- chief_pending_tasks: filtro per chief + status
CREATE INDEX IF NOT EXISTS idx_chief_pending_chief_status
    ON chief_pending_tasks(chief_id, status);

-- agent_events: polling per pending events
CREATE INDEX IF NOT EXISTS idx_agent_events_pending
    ON agent_events(status, created_at)
    WHERE status = 'pending';
