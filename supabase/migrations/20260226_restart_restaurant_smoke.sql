-- Riavvio pipeline ristorante per smoke test CSO v5.8
-- 1. Elimina prospects vecchi
DELETE FROM smoke_test_prospects WHERE smoke_test_id IN (
    SELECT st.id FROM smoke_tests st
    JOIN projects p ON st.project_id = p.id
    WHERE p.slug LIKE '%ristoran%' OR p.slug LIKE '%prenotaz%'
);

-- 2. Elimina smoke tests vecchi
DELETE FROM smoke_tests
WHERE project_id IN (
    SELECT id FROM projects
    WHERE slug LIKE '%ristoran%' OR slug LIKE '%prenotaz%'
);

-- 3. Reset progetto per fresh smoke test
UPDATE projects
SET status = 'init',
    pipeline_step = 'bos_approved',
    pipeline_locked = false,
    smoke_test_method = null,
    brand_name = null,
    brand_email = null,
    brand_domain = null,
    brand_linkedin = null,
    brand_landing_url = null,
    smoke_test_plan = null,
    smoke_test_kpi = null,
    smoke_test_results = null,
    smoke_test_kpi_target = null
WHERE slug LIKE '%ristoran%' OR slug LIKE '%prenotaz%';
