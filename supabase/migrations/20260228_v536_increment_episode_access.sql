-- v5.36: RPC per batch access count su episodic_memory
CREATE OR REPLACE FUNCTION increment_episode_access(episode_ids INTEGER[])
RETURNS VOID AS $$
    UPDATE episodic_memory
    SET access_count = access_count + 1,
        last_accessed_at = now()
    WHERE id = ANY(episode_ids);
$$ LANGUAGE sql;
