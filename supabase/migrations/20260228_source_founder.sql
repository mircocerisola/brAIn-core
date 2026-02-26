-- brAIn v5.5c — Colonna source su solutions e bos_archive
-- Distingue idee del founder (source='founder') dalle soluzioni generate dal sistema (source='system')

ALTER TABLE solutions ADD COLUMN IF NOT EXISTS source text DEFAULT 'system';
ALTER TABLE bos_archive ADD COLUMN IF NOT EXISTS source text DEFAULT 'system';

-- Marca le soluzioni del ristorante come founder idea
UPDATE solutions
SET source = 'founder'
WHERE (title ILIKE '%ristorante%' OR title ILIKE '%prenotazioni%' OR description ILIKE '%ristorante%' OR description ILIKE '%prenotazioni%');
