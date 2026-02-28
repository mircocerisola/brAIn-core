"""
brAIn Memory v2.0 — Tre livelli di memoria per i Chief Agent.
L1 Working: topic_conversation_history (scritto da command-center)
L2 Episodic: episodic_memory (riassunti sessioni via Haiku)
L3 Semantic: chief_knowledge + org_shared_knowledge (fatti estratti via Haiku)

v2.0: search_relevant_memories (Haiku re-ranking), save_task_learning, embedding-ready.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from core.config import supabase, claude, logger
from core.templates import now_rome


# ============================================================
# L2 — EPISODIC MEMORY
# ============================================================

def create_episode(scope_type: str, scope_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Riassume una sequenza di messaggi e salva in episodic_memory.
    messages: lista di {"role": "user"|"bot", "text": str}
    """
    if not messages:
        return {"status": "skipped", "reason": "no messages"}

    # Costruisci testo per Haiku
    lines = [f"{m['role'].upper()}: {m['text'][:300]}" for m in messages[-30:]]
    conversation_text = "\n".join(lines)[:4000]

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=(
                "Riassumi in max 150 parole la conversazione: "
                "decisioni prese, preferenze espresse, task discussi, contesto importante. "
                "Testo piano, italiano. NO markdown."
            ),
            messages=[{"role": "user", "content": conversation_text}],
        )
        summary = resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"[MEMORY] create_episode Haiku error: {e}")
        return {"status": "error", "error": str(e)}

    try:
        result = supabase.table("episodic_memory").insert({
            "scope_type": scope_type,
            "scope_id": scope_id,
            "summary": summary,
            "messages_covered": len(messages),
            "importance": 3,
            "created_at": now_rome().isoformat(),
        }).execute()
        episode_id = result.data[0]["id"] if result.data else None
        logger.info(f"[MEMORY] Episode created scope={scope_type}:{scope_id} id={episode_id}")
        return {"status": "ok", "episode_id": episode_id}
    except Exception as e:
        logger.warning(f"[MEMORY] create_episode DB insert error: {e}")
        return {"status": "error", "error": str(e)}


def get_episodes(scope_type: str, scope_id: str, limit: int = 5) -> List[str]:
    """
    Carica gli ultimi N episodi per scope e aggiorna i contatori di accesso.
    Ritorna lista di stringhe (i summary).
    """
    try:
        r = supabase.table("episodic_memory") \
            .select("id,summary") \
            .eq("scope_type", scope_type) \
            .eq("scope_id", scope_id) \
            .order("created_at", desc=True) \
            .limit(limit).execute()

        if not r.data:
            return []

        ids = [row["id"] for row in r.data]
        summaries = [row["summary"] for row in r.data]

        # v5.36: batch UPDATE access count — RPC singola invece di 15 query
        try:
            supabase.rpc("increment_episode_access", {"episode_ids": ids}).execute()
        except Exception as e:
            # Fallback: UPDATE singoli senza SELECT (1 query per ID, non 3)
            logger.debug(f"[MEMORY] RPC increment_episode_access fallback: {e}")
            try:
                now = now_rome().isoformat()
                for eid in ids:
                    supabase.table("episodic_memory").update({
                        "last_accessed_at": now,
                    }).eq("id", eid).execute()
            except Exception as e2:
                logger.debug(f"[MEMORY] access_count fallback error: {e2}")

        return summaries
    except Exception as e:
        logger.warning(f"[MEMORY] get_episodes error: {e}")
        return []


def update_project_episode(project_id: int, event_text: str, status: str, next_step: str) -> None:
    """
    Crea un episodio di progetto con importanza alta (5).
    Chiamare dopo ogni aggiornamento di projects.status.
    """
    summary = (
        f"Progetto {project_id}: {event_text}. "
        f"Status: {status}. "
        f"Prossimo step: {next_step}."
    )
    try:
        supabase.table("episodic_memory").insert({
            "scope_type": "project",
            "scope_id": str(project_id),
            "summary": summary,
            "messages_covered": 1,
            "importance": 5,
            "created_at": now_rome().isoformat(),
        }).execute()
        logger.info(f"[MEMORY] Project episode saved project_id={project_id} status={status}")
    except Exception as e:
        logger.warning(f"[MEMORY] update_project_episode error: {e}")


# ============================================================
# L3 — SEMANTIC MEMORY
# ============================================================

def extract_semantic_facts(message: str, chief_id: str) -> Dict[str, Any]:
    """
    Estrae fatti permanenti da un messaggio e li salva in chief_knowledge o org_shared_knowledge.
    Ritorna {"facts_saved": N}
    """
    if not message or len(message.strip()) < 20:
        return {"facts_saved": 0}

    extract_prompt = (
        f"Analizza questo messaggio e estrai SOLO fatti permanenti e rilevanti "
        f"(preferenze stabili, decisioni irrevocabili, contesto organizzativo). "
        f"Se non ci sono fatti permanenti, ritorna lista vuota.\n\n"
        f"Messaggio: {message[:1000]}\n\n"
        f"Rispondi SOLO con JSON valido:\n"
        f'[{{"title": "...", "content": "...", "importance": 1-5, "target": "{chief_id}|shared"}}]'
        f"\noppure [] se nessun fatto permanente."
    )

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": extract_prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if not m:
            return {"facts_saved": 0}
        facts: List[Dict] = json.loads(m.group(0))
    except Exception as e:
        logger.debug(f"[MEMORY] extract_semantic_facts Haiku error: {e}")
        return {"facts_saved": 0}

    if not facts:
        return {"facts_saved": 0}

    saved = 0
    for fact in facts:
        importance = fact.get("importance", 3)
        if importance < 3:
            continue  # salva solo fatti rilevanti

        title = (fact.get("title") or "")[:200]
        content = (fact.get("content") or "")[:500]
        target = fact.get("target", chief_id)

        if not title or not content:
            continue

        try:
            if target == "shared":
                # Controlla duplicati in org_shared_knowledge
                dup = supabase.table("org_shared_knowledge") \
                    .select("id") \
                    .ilike("title", f"%{title[:50]}%") \
                    .limit(1).execute()
                if dup.data:
                    supabase.table("org_shared_knowledge").update({
                        "content": content,
                        "importance": importance,
                    }).eq("id", dup.data[0]["id"]).execute()
                else:
                    supabase.table("org_shared_knowledge").insert({
                        "title": title,
                        "content": content,
                        "category": "preference",
                        "importance": importance,
                        "source": "extracted",
                    }).execute()
            else:
                # Salva in chief_knowledge per il chief specifico
                ck_chief_id = target if target != "shared" else chief_id
                dup = supabase.table("chief_knowledge") \
                    .select("id") \
                    .eq("chief_id", ck_chief_id) \
                    .ilike("title", f"%{title[:50]}%") \
                    .limit(1).execute()
                if dup.data:
                    supabase.table("chief_knowledge").update({
                        "content": content,
                        "importance": importance,
                    }).eq("id", dup.data[0]["id"]).execute()
                else:
                    supabase.table("chief_knowledge").insert({
                        "chief_id": ck_chief_id,
                        "knowledge_type": "preference",
                        "title": title,
                        "content": content,
                        "importance": importance,
                        "source": "extracted",
                    }).execute()
            saved += 1
        except Exception as e:
            logger.debug(f"[MEMORY] save fact error: {e}")

    if saved > 0:
        logger.info(f"[MEMORY] Extracted {saved} semantic facts chief={chief_id}")
    return {"facts_saved": saved}


# ============================================================
# RELEVANCE SEARCH — Haiku re-ranking
# ============================================================

def search_relevant_memories(chief_id: str, query: str, limit: int = 5) -> List[str]:
    """
    Cerca le memorie piu' rilevanti per la query corrente.
    1. Carica ultime 30 chief_knowledge per il Chief
    2. Carica ultimi 10 episodi topic/project per il Chief
    3. Haiku seleziona le piu' rilevanti in UNA sola chiamata
    Ritorna lista di stringhe (memorie selezionate), max `limit`.
    """
    candidates = []

    # Chief knowledge (individuale)
    try:
        r = supabase.table("chief_knowledge") \
            .select("id,title,content,knowledge_type") \
            .eq("chief_id", chief_id) \
            .neq("knowledge_type", "profile") \
            .order("importance", desc=True).limit(30).execute()
        for row in (r.data or []):
            candidates.append({
                "id": "ck_" + str(row["id"]),
                "text": row["title"] + ": " + (row["content"] or "")[:200],
                "source": "knowledge",
            })
    except Exception as e:
        logger.debug("[MEMORY] search_relevant ck error: %s", e)

    # Episodic memory (topic del Chief)
    try:
        r = supabase.table("episodic_memory") \
            .select("id,summary,scope_type,scope_id") \
            .order("created_at", desc=True).limit(15).execute()
        for row in (r.data or []):
            candidates.append({
                "id": "ep_" + str(row["id"]),
                "text": (row["summary"] or "")[:200],
                "source": "episode",
            })
    except Exception as e:
        logger.debug("[MEMORY] search_relevant ep error: %s", e)

    if not candidates:
        return []

    # Se poche candidate, ritorna tutte senza Haiku
    if len(candidates) <= limit:
        return [c["text"] for c in candidates]

    # Haiku re-ranking: UNA sola chiamata
    numbered = "\n".join(
        str(i) + ". " + c["text"][:150]
        for i, c in enumerate(candidates)
    )
    ranking_prompt = (
        "Data questa domanda del CEO:\n"
        "\"" + query[:300] + "\"\n\n"
        "Seleziona i " + str(limit) + " piu' rilevanti tra queste memorie "
        "(rispondi SOLO con i numeri separati da virgola, es: 2,5,0,8,3):\n\n"
        + numbered
    )
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{"role": "user", "content": ranking_prompt}],
        )
        raw = resp.content[0].text.strip()
        # Estrai numeri dalla risposta
        indices = []
        for part in re.findall(r'\d+', raw):
            idx = int(part)
            if 0 <= idx < len(candidates):
                indices.append(idx)
        if not indices:
            # Fallback: primi N per importanza
            return [c["text"] for c in candidates[:limit]]
        # Deduplica preservando ordine
        seen = set()
        unique = []
        for i in indices:
            if i not in seen:
                seen.add(i)
                unique.append(i)
        return [candidates[i]["text"] for i in unique[:limit]]
    except Exception as e:
        logger.debug("[MEMORY] search_relevant Haiku ranking error: %s", e)
        return [c["text"] for c in candidates[:limit]]


def save_task_learning(chief_id: str, task_summary: str, learning: str,
                       importance: int = 3) -> Dict[str, Any]:
    """
    Salva una lezione appresa dopo il completamento di un task.
    Il Chief chiama questa funzione dopo ogni task significativo.
    Dedup: se esiste gia' un fatto con titolo simile, aggiorna.
    """
    if not learning or len(learning.strip()) < 10:
        return {"status": "skipped"}

    title = task_summary[:200] if task_summary else "Lezione appresa"

    try:
        # Dedup per titolo simile
        dup = supabase.table("chief_knowledge") \
            .select("id") \
            .eq("chief_id", chief_id) \
            .eq("knowledge_type", "learning") \
            .ilike("title", "%" + title[:50] + "%") \
            .limit(1).execute()

        if dup.data:
            supabase.table("chief_knowledge").update({
                "content": learning[:500],
                "importance": importance,
                "updated_at": now_rome().isoformat(),
            }).eq("id", dup.data[0]["id"]).execute()
            logger.info("[MEMORY] Updated learning chief=%s title=%s", chief_id, title[:40])
            return {"status": "updated", "id": dup.data[0]["id"]}
        else:
            result = supabase.table("chief_knowledge").insert({
                "chief_id": chief_id,
                "knowledge_type": "learning",
                "title": title,
                "content": learning[:500],
                "importance": importance,
                "source": "extracted",
                "created_at": now_rome().isoformat(),
            }).execute()
            new_id = result.data[0]["id"] if result.data else None
            logger.info("[MEMORY] Saved new learning chief=%s id=%s", chief_id, new_id)
            return {"status": "created", "id": new_id}
    except Exception as e:
        logger.warning("[MEMORY] save_task_learning error: %s", e)
        return {"status": "error", "error": str(e)}


# ============================================================
# CLEANUP
# ============================================================

def cleanup_memory() -> Dict[str, Any]:
    """
    Pulizia periodica dei tre livelli di memoria.
    - Elimina episodi poco rilevanti (importance <= 2) non acceduti da 14gg
    - Elimina topic_conversation_history > 7gg
    - Unifica episodi > 30gg con importance <= 3 per scope
    """
    now = now_rome()
    deleted_episodes = 0
    deleted_messages = 0
    merged_episodes = 0

    # 1. Elimina episodi poco rilevanti — v5.36: batch delete (no N+1)
    try:
        cutoff_14 = (now - timedelta(days=14)).isoformat()
        r = supabase.table("episodic_memory") \
            .select("id") \
            .lte("importance", 2) \
            .lt("last_accessed_at", cutoff_14) \
            .execute()
        if r.data:
            ids_to_delete = [row["id"] for row in r.data]
            for eid in ids_to_delete:
                try:
                    supabase.table("episodic_memory").delete().eq("id", eid).execute()
                    deleted_episodes += 1
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[MEMORY CLEANUP] episodic delete error: {e}")

    # 2. Elimina messaggi topic > 7gg — v5.36: batch delete diretto senza SELECT
    try:
        cutoff_7 = (now - timedelta(days=7)).isoformat()
        r = supabase.table("topic_conversation_history") \
            .delete() \
            .lt("created_at", cutoff_7) \
            .execute()
        deleted_messages = len(r.data) if r.data else 0
    except Exception as e:
        logger.warning(f"[MEMORY CLEANUP] messages delete error: {e}")

    # 3. Unifica episodi > 30gg con importance <= 3 per scope
    try:
        cutoff_30 = (now - timedelta(days=30)).isoformat()
        r = supabase.table("episodic_memory") \
            .select("id,scope_type,scope_id,summary") \
            .lte("importance", 3) \
            .lt("created_at", cutoff_30) \
            .execute()
        if r.data:
            # Raggruppa per scope
            by_scope: Dict[str, List[Dict]] = {}
            for row in r.data:
                key = f"{row['scope_type']}:{row['scope_id']}"
                by_scope.setdefault(key, []).append(row)

            for scope_key, episodes in by_scope.items():
                if len(episodes) < 2:
                    continue
                scope_type, scope_id = scope_key.split(":", 1)

                # Genera riassunto unificato con Haiku
                combined = "\n---\n".join([ep["summary"] for ep in episodes])[:4000]
                try:
                    resp = claude.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=300,
                        system="Riassumi in max 200 parole questi episodi di memoria. Mantieni fatti chiave, decisioni, preferenze. Testo piano, italiano.",
                        messages=[{"role": "user", "content": combined}],
                    )
                    merged_summary = resp.content[0].text.strip()
                except Exception as e:
                    logger.warning(f"[MEMORY CLEANUP] merge Haiku error: {e}")
                    continue

                # Inserisci episodio unificato
                try:
                    supabase.table("episodic_memory").insert({
                        "scope_type": scope_type,
                        "scope_id": scope_id,
                        "summary": merged_summary,
                        "messages_covered": sum(ep.get("messages_covered", 1) or 1 for ep in episodes),
                        "importance": 3,
                        "created_at": now_rome().isoformat(),
                    }).execute()
                    merged_episodes += 1
                except Exception as e:
                    logger.warning(f"[MEMORY CLEANUP] merge insert error: {e}")
                    continue

                # Elimina originali
                for ep in episodes:
                    try:
                        supabase.table("episodic_memory").delete().eq("id", ep["id"]).execute()
                    except Exception:
                        pass

    except Exception as e:
        logger.warning(f"[MEMORY CLEANUP] merge episodes error: {e}")

    logger.info(
        f"[MEMORY CLEANUP] deleted_episodes={deleted_episodes} "
        f"deleted_messages={deleted_messages} merged_episodes={merged_episodes}"
    )
    return {
        "status": "ok",
        "deleted_episodes": deleted_episodes,
        "deleted_messages": deleted_messages,
        "merged_episodes": merged_episodes,
    }
