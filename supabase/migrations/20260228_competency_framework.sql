-- v5.26: Framework competenze completo per agent_capabilities
-- Aggiunge colonne per framework 30 skill per Chief con livelli attesi 0-100

-- Rendi agent_id nullable (non serve piu come chiave primaria logica)
ALTER TABLE agent_capabilities ALTER COLUMN agent_id DROP NOT NULL;
ALTER TABLE agent_capabilities ALTER COLUMN agent_name DROP NOT NULL;
ALTER TABLE agent_capabilities ALTER COLUMN layer DROP NOT NULL;

-- Rimuovi unique constraint su agent_id (vecchio schema)
ALTER TABLE agent_capabilities DROP CONSTRAINT IF EXISTS agent_capabilities_agent_id_key;

-- Rimuovi status check constraint (limita solo active/disabled/retired)
ALTER TABLE agent_capabilities DROP CONSTRAINT IF EXISTS agent_capabilities_status_check;

-- Aggiungi nuove colonne
ALTER TABLE agent_capabilities ADD COLUMN IF NOT EXISTS competenza TEXT DEFAULT '';
ALTER TABLE agent_capabilities ADD COLUMN IF NOT EXISTS categoria TEXT DEFAULT '';
ALTER TABLE agent_capabilities ADD COLUMN IF NOT EXISTS fonte TEXT DEFAULT '';
ALTER TABLE agent_capabilities ADD COLUMN IF NOT EXISTS livello_atteso INTEGER DEFAULT 80;
ALTER TABLE agent_capabilities ADD COLUMN IF NOT EXISTS descrizione TEXT DEFAULT '';
ALTER TABLE agent_capabilities ADD COLUMN IF NOT EXISTS comportamenti_attesi TEXT DEFAULT '';
ALTER TABLE agent_capabilities ADD COLUMN IF NOT EXISTS score_percentuale INTEGER DEFAULT 50;

-- Colonna gap calcolata (GENERATED ALWAYS AS STORED)
-- Prima drop se esiste per evitare errori
ALTER TABLE agent_capabilities DROP COLUMN IF EXISTS gap;
ALTER TABLE agent_capabilities ADD COLUMN gap INTEGER GENERATED ALWAYS AS (livello_atteso - score_percentuale) STORED;

-- Unique constraint per ON CONFLICT
CREATE UNIQUE INDEX IF NOT EXISTS agent_capabilities_name_competenza_idx ON agent_capabilities (agent_name, competenza);

-- FIX 5: capability_log — aggiungi created_at
ALTER TABLE capability_log ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

-- Reload schema cache
NOTIFY pgrst, 'reload schema';
