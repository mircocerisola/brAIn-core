-- v5.36: Aggiunge colonna description a projects (referenziata da CMO e CLO)
ALTER TABLE projects ADD COLUMN IF NOT EXISTS description text DEFAULT '';

-- Aggiunge anche pipeline_step se mancante (referenziata da 65+ query)
ALTER TABLE projects ADD COLUMN IF NOT EXISTS pipeline_step text DEFAULT 'init';
