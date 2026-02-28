-- v5.34: Universal context system + CTO architecture + CPeO versioning

-- 1. topic_context_summary: running summary incrementale per topic
CREATE TABLE IF NOT EXISTS topic_context_summary (
    scope_id TEXT PRIMARY KEY,
    summary TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT now(),
    chief_id TEXT DEFAULT ''
);
ALTER TABLE topic_context_summary ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_topic_ctx" ON topic_context_summary FOR ALL USING (true) WITH CHECK (true);

-- 2. cto_architecture_index: indice file-level del codebase
CREATE TABLE IF NOT EXISTS cto_architecture_index (
    file_path TEXT PRIMARY KEY,
    file_type TEXT DEFAULT '',
    classes JSONB DEFAULT '[]',
    methods JSONB DEFAULT '[]',
    imports JSONB DEFAULT '[]',
    line_count INTEGER DEFAULT 0,
    last_scanned TIMESTAMPTZ DEFAULT now(),
    content_hash TEXT DEFAULT ''
);
ALTER TABLE cto_architecture_index ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_arch_idx" ON cto_architecture_index FOR ALL USING (true) WITH CHECK (true);

-- 3. cto_architecture_summary: snapshot giornaliero architettura
CREATE TABLE IF NOT EXISTS cto_architecture_summary (
    id SERIAL PRIMARY KEY,
    snapshot_date DATE DEFAULT CURRENT_DATE UNIQUE,
    total_files INTEGER DEFAULT 0,
    total_methods INTEGER DEFAULT 0,
    total_classes INTEGER DEFAULT 0,
    total_lines INTEGER DEFAULT 0,
    summary_text TEXT DEFAULT '',
    dependency_graph JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE cto_architecture_summary ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_arch_sum" ON cto_architecture_summary FOR ALL USING (true) WITH CHECK (true);

-- 4. cto_security_reports: report sicurezza
CREATE TABLE IF NOT EXISTS cto_security_reports (
    id SERIAL PRIMARY KEY,
    report_date DATE DEFAULT CURRENT_DATE,
    dependencies_checked INTEGER DEFAULT 0,
    vulnerabilities JSONB DEFAULT '[]',
    env_vars_audit JSONB DEFAULT '{}',
    owasp_risks JSONB DEFAULT '[]',
    summary TEXT DEFAULT '',
    severity TEXT DEFAULT 'low' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE cto_security_reports ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_sec_rep" ON cto_security_reports FOR ALL USING (true) WITH CHECK (true);

-- 5. brain_versions: tracciamento versioni deploy
CREATE TABLE IF NOT EXISTS brain_versions (
    id SERIAL PRIMARY KEY,
    service_name TEXT NOT NULL,
    version_tag TEXT DEFAULT '',
    revision TEXT DEFAULT '',
    deployed_at TIMESTAMPTZ DEFAULT now(),
    changes_summary TEXT DEFAULT '',
    files_changed JSONB DEFAULT '[]',
    deployed_by TEXT DEFAULT 'system'
);
CREATE INDEX IF NOT EXISTS idx_brain_versions_service ON brain_versions(service_name, deployed_at DESC);
ALTER TABLE brain_versions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_versions" ON brain_versions FOR ALL USING (true) WITH CHECK (true);

-- 6. improvement_log: log miglioramenti continui
CREATE TABLE IF NOT EXISTS improvement_log (
    id SERIAL PRIMARY KEY,
    version_from TEXT DEFAULT '',
    version_to TEXT DEFAULT '',
    improvement_type TEXT DEFAULT '' CHECK (improvement_type IN ('feature', 'fix', 'performance', 'security', 'refactor')),
    description TEXT DEFAULT '',
    metrics_before JSONB DEFAULT '{}',
    metrics_after JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE improvement_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_improve" ON improvement_log FOR ALL USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
