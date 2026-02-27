"""
brAIn module: core/utils.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import os, json, time, hashlib, math, re
from datetime import datetime, timezone, timedelta
from calendar import monthrange
import anthropic
from supabase import create_client
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, COMMAND_CENTER_URL, PERPLEXITY_API_KEY, logger, _state
from core.templates import now_rome


def get_telegram_chat_id():
    global TELEGRAM_CHAT_ID
    if TELEGRAM_CHAT_ID:
        return TELEGRAM_CHAT_ID
    try:
        result = supabase.table("org_config").select("value").eq("key", "telegram_user_id").execute()
        if result.data:
            TELEGRAM_CHAT_ID = json.loads(result.data[0]["value"])
    except:
        pass
    return TELEGRAM_CHAT_ID


def notify_telegram(message, level="info", source="agents_runner"):
    # Prova a inviare via command_center (notifiche intelligenti con coda)
    if COMMAND_CENTER_URL:
        try:
            resp = requests.post(
                f"{COMMAND_CENTER_URL}/alert",
                json={"message": message, "level": level, "source": source},
                timeout=10,
            )
            if resp.status_code == 200:
                return
            logger.warning(f"[NOTIFY] command_center returned {resp.status_code}, fallback diretto")
        except Exception as e:
            logger.warning(f"[NOTIFY] command_center non raggiungibile: {e}, fallback diretto")
    # Fallback: invio diretto a Telegram
    chat_id = get_telegram_chat_id()
    if not chat_id or not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"[TELEGRAM] {e}")


def emit_event(source_agent, event_type, target_agent=None, payload=None, priority="normal"):
    try:
        supabase.table("agent_events").insert({
            "event_type": event_type,
            "source_agent": source_agent,
            "target_agent": target_agent,
            "payload": json.dumps(payload or {}),
            "priority": priority,
            "status": "pending",
        }).execute()
    except Exception as e:
        logger.error(f"[EVENT ERROR] {e}")


def get_pending_events(target_agent=None):
    try:
        query = supabase.table("agent_events").select("*").eq("status", "pending")
        if target_agent:
            query = query.eq("target_agent", target_agent)
        result = query.order("created_at").limit(20).execute()
        return result.data or []
    except:
        return []


def mark_event_done(event_id, status="completed"):
    try:
        supabase.table("agent_events").update({
            "status": status,
            "processed_at": now_rome().isoformat(),
        }).eq("id", event_id).execute()
    except:
        pass


def log_to_supabase(agent_id, action, layer, input_summary, output_summary, model_used, tokens_in=0, tokens_out=0, cost=0, duration_ms=0, status="success", error=None):
    try:
        supabase.table("agent_logs").insert({
            "agent_id": agent_id,
            "action": action,
            "layer": layer,
            "input_summary": input_summary[:500] if input_summary else None,
            "output_summary": output_summary[:500] if output_summary else None,
            "model_used": model_used,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "status": status,
            "error": error,
        }).execute()
    except Exception as e:
        logger.error(f"[LOG ERROR] {e}")


def extract_json(text):
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except:
        pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        return json.loads(text[start:end])
    except:
        return None


def search_perplexity(query, max_tokens=600):
    try:
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": query}],
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        logger.warning("[PERPLEXITY] status=%d body=%s", response.status_code, response.text[:200])
        return None
    except Exception as e:
        logger.warning("[PERPLEXITY] error: %s", e)
        return None


# ============================================================
# PARTE 9: SELF-IMPROVEMENT — Preferenze di Mirco
# ============================================================

def get_mirco_preferences():
    """Legge preferenze da org_knowledge per calibrare scan e soluzioni."""
    try:
        result = supabase.table("org_knowledge").select("title, content, category").eq("category", "preference").order("created_at", desc=True).limit(30).execute()
        if not result.data:
            return ""
        lines = []
        for r in result.data:
            lines.append(f"- {r['title']}: {r['content']}")
        return "\n".join(lines)
    except:
        return ""


def get_sector_preference_modifier():
    """Calcola modifier per settore basato su approvazioni/rifiuti di Mirco."""
    try:
        approved = supabase.table("problems").select("sector").eq("status", "approved").eq("status_detail", "active").execute()
        rejected = supabase.table("problems").select("sector").eq("status", "rejected").execute()

        sector_scores = {}
        for p in (approved.data or []):
            s = p.get("sector", "")
            if s:
                sector_scores[s] = sector_scores.get(s, 0) + 1
        for p in (rejected.data or []):
            s = p.get("sector", "")
            if s:
                sector_scores[s] = sector_scores.get(s, 0) - 1.5

        return sector_scores
    except:
        return {}


# ============================================================
# WORLD SCANNER v2.3 — Cicli autonomi con variazione
# ============================================================

SCANNER_WEIGHTS = {
    "market_size": 0.20, "willingness_to_pay": 0.20, "urgency": 0.15,
    "competition_gap": 0.15, "ai_solvability": 0.15, "time_to_market": 0.10,
    "recurring_potential": 0.05,
}

# ---- PIPELINE THRESHOLDS (scala 0-1, armonizzati) — default, sovrascritti da DB ----
# Tutti gli score nel sistema sono su scala 0-1 coerente:
#   weighted_score (problems): sum(val * weight), sum(weights)=1.0 → 0-1
#   overall_score (solutions): (impact + feasibility) / 2 → 0-1
#   feasibility_score (FE): sum(val * weight), sum(weights)=1.0 → 0-1
#   bos_score: pq*0.30 + sq*0.30 + fe*0.40 → 0-1
PIPELINE_THRESHOLDS = {
    "problema": 0.55,       # weighted_score minimo per auto-approvare il problema
    "soluzione": 0.50,      # overall_score minimo della migliore soluzione per proseguire
    "feasibility": 0.45,    # feasibility_score FE minimo per proseguire
    "bos": 0.55,            # BOS minimo per notificare Mirco (approve_bos action)
}
# Le soglie DB (pipeline_thresholds) sovrascrivono questi default.
# Le soglie si aggiornano automaticamente ogni lunedi via /thresholds/weekly.

MIN_SCORE_THRESHOLD = PIPELINE_THRESHOLDS["problema"]


def get_pipeline_thresholds():
    """Legge soglie dinamiche dalla tabella pipeline_thresholds. Fallback ai default."""
    try:
        result = supabase.table("pipeline_thresholds").select(
            "soglia_problema, soglia_soluzione, soglia_feasibility, soglia_bos"
        ).order("id", desc=True).limit(1).execute()
        if result.data:
            row = result.data[0]
            return {
                "problema": float(row.get("soglia_problema") or PIPELINE_THRESHOLDS["problema"]),
                "soluzione": float(row.get("soglia_soluzione") or PIPELINE_THRESHOLDS["soluzione"]),
                "feasibility": float(row.get("soglia_feasibility") or PIPELINE_THRESHOLDS["feasibility"]),
                "bos": float(row.get("soglia_bos") or PIPELINE_THRESHOLDS["bos"]),
            }
    except Exception as e:
        logger.warning(f"[THRESHOLDS] DB read failed, uso default: {e}")
    return dict(PIPELINE_THRESHOLDS)

SCANNER_SECTORS = [
    "food", "health", "finance", "education", "legal",
    "ecommerce", "hr", "real_estate", "sustainability",
    "cybersecurity", "entertainment", "logistics",
]

SCANNER_ANALYSIS_PROMPT = """Sei il World Scanner di brAIn, un'organizzazione AI-native che cerca problemi SPECIFICI e AZIONABILI.

REGOLA FONDAMENTALE: ogni problema deve riguardare un segmento PRECISO di persone in un contesto geografico PRECISO con prove CONCRETE.

ESEMPIO SBAGLIATO (troppo generico, rifiutato):
"Le PMI faticano con la gestione finanziaria"

ESEMPIO CORRETTO (specifico, azionabile):
"Gli elettricisti autonomi italiani tra 30-45 anni non hanno accesso a corsi di aggiornamento normativo certificati a meno di 500 EUR"

Per ogni problema identificato (massimo 3), fornisci TUTTI questi campi:

1. IDENTIFICAZIONE TARGET (OBBLIGATORIO — rifiuta se non hai dati specifici):
   - target_customer: segmento SPECIFICO — professione + fascia d'eta' + contesto (NON "aziende" o "persone")
   - target_geography: paese/regione SPECIFICA + perche' proprio li'
   - problem_frequency: daily/weekly/monthly/quarterly

2. DESCRIZIONE PROBLEMA (OBBLIGATORIO):
   - current_workaround: come il target risolve OGGI il problema e perche' e' insufficiente
   - pain_intensity: 1 (fastidio) a 5 (blocca il business/la vita)
   - evidence: dato CONCRETO e verificabile — statistica con fonte, numero persone colpite, dimensione mercato

3. TIMING (OBBLIGATORIO):
   - why_now: perche' questo problema e' rilevante ORA (cambio normativo, tecnologia, comportamento)

4. DATI QUANTITATIVI — 7 score da 0.0 a 1.0 — usa TUTTA la scala, ogni problema DEVE avere almeno 2 score sotto 0.4:
   - market_size: 0.1=nicchia <1M EUR, 0.5=medio 10-100M EUR, 1.0=miliardi
   - willingness_to_pay: 0.1=difficile convincerli, 1.0=pagano gia' o chiedono attivamente
   - urgency: 0.1=fastidio, 1.0=perde soldi/clienti oggi
   - competition_gap: 1.0=nessuna soluzione, 0.0=mercato saturo
   - ai_solvability: 0.1=richiede umani, 1.0=100% automatizzabile
   - time_to_market: 1.0=1 settimana, 0.3=3 mesi, 0.0=anni
   - recurring_potential: 1.0=quotidiano, 0.3=mensile, 0.0=una tantum

5. CLASSIFICAZIONE:
   - sector: uno tra food, health, finance, education, legal, ecommerce, hr, real_estate, sustainability, cybersecurity, entertainment, logistics
   - geographic_scope: global, continental, national, regional
   - top_markets: lista 3-5 codici paese ISO
   - who_is_affected, real_world_example, why_it_matters: testo descrittivo in italiano

SCARTA qualsiasi problema senza target_customer specifico, evidence con dati numerici, o why_now chiaro.
REGOLA DIVERSITA SETTORI: i problemi devono riguardare settori DIVERSI.

{preferences_block}

Rispondi SOLO con JSON:
{{"problems":[{{"title":"titolo specifico","description":"descrizione","target_customer":"elettricisti autonomi italiani 30-45 anni","target_geography":"Italia nord e centro","problem_frequency":"monthly","current_workaround":"cercano corsi online generici","pain_intensity":4,"evidence":"In Italia ci sono 180.000 elettricisti autonomi (CGIA 2024)","why_now":"Norma CEI 64-8/7 del 2023 obbligatoria dal 2025","who_is_affected":"chi soffre","real_world_example":"storia concreta","why_it_matters":"perche conta","sector":"education","geographic_scope":"national","top_markets":["IT"],"market_size":0.4,"willingness_to_pay":0.7,"urgency":0.8,"competition_gap":0.7,"ai_solvability":0.8,"time_to_market":0.8,"recurring_potential":0.6,"source_name":"CGIA Mestre","source_url":"https://cgia.it"}}],"new_sources":[{{"name":"nome","url":"url","category":"tipo","sectors":["settore"]}}]}}
SOLO JSON."""


def get_scan_strategy():
    """Determina quale strategia usare basata su ora (rotazione 6 cicli legacy, usata solo come fallback)."""
    now = now_rome()
    cycle_in_day = now.hour // 4  # 0-5
    day_offset = now.timetuple().tm_yday % 6
    strategy_index = (cycle_in_day + day_offset) % 6
    strategies = [
        "top_sources", "low_ranked_gems", "sector_deep_dive",
        "correlated_problems", "emerging_trends", "source_refresh",
    ]
    return strategies[strategy_index], strategy_index


def get_scan_schedule_strategy():
    """
    Legge la scan_schedule per l'ora corrente (UTC) e restituisce la strategia del slot.
    Aggiorna last_used sulla riga corrispondente.
    Ogni 2 ore un ciclo diverso: 12 slot al giorno.
    """
    now = now_rome()
    current_hour = now.hour
    # Arrotonda all'ora pari (0,2,4,...,22)
    slot_hour = (current_hour // 2) * 2
    try:
        result = supabase.table("scan_schedule").select("*").eq("hour", slot_hour).execute()
        if result.data:
            row = result.data[0]
            strategy = row.get("strategy", "top_sources")
            # Aggiorna last_used
            supabase.table("scan_schedule").update({
                "last_used": now.isoformat()
            }).eq("hour", slot_hour).execute()
            logger.info(f"[SCANNER] Slot {slot_hour:02d}:00 → strategia: {strategy}")
            return strategy
    except Exception as e:
        logger.warning(f"[SCANNER] scan_schedule non disponibile: {e}")
    # Fallback: usa strategia legacy
    strategy, _ = get_scan_strategy()
    return strategy


def get_sector_with_fewest_problems():
    """Ritorna il settore con meno problemi attivi nel DB."""
    sectors = [
        "food", "health", "finance", "education", "legal",
        "ecommerce", "hr", "real_estate", "sustainability",
        "cybersecurity", "entertainment", "logistics",
    ]
    try:
        result = supabase.table("problems").select("sector").eq("status_detail", "active").execute()
        counts = {s: 0 for s in sectors}
        for p in (result.data or []):
            s = p.get("sector", "")
            if s in counts:
                counts[s] += 1
        return min(counts, key=counts.get)
    except:
        return "logistics"


def get_last_sector_rotation():
    """Ritorna l'ultimo settore usato in slot sector_rotation."""
    try:
        result = supabase.table("scan_schedule").select("notes").eq("strategy", "sector_rotation").execute()
        for row in (result.data or []):
            notes = row.get("notes", "")
            if notes.startswith("last_sector:"):
                return notes.split(":")[1].strip()
    except:
        pass
    return None


def get_high_bos_problem_sectors():
    """Ritorna i settori dei problemi con BOS > 0.7."""
    try:
        result = supabase.table("solutions").select(
            "problems(sector)"
        ).gte("bos_score", 0.7).eq("status_detail", "active").limit(5).execute()
        return list({
            sol.get("problems", {}).get("sector", "") for sol in (result.data or [])
            if sol.get("problems", {}).get("sector")
        })
    except:
        return []


def build_strategy_queries(strategy):
    """Costruisce query diverse per ogni strategia."""

    if strategy == "top_sources":
        try:
            sources = supabase.table("scan_sources").select("*").eq("status", "active").order("relevance_score", desc=True).limit(10).execute()
            sources = sources.data or []
        except:
            sources = []
        return get_standard_queries(sources), "top_sources"

    elif strategy == "low_ranked_gems":
        try:
            sources = supabase.table("scan_sources").select("*").eq("status", "active").order("relevance_score").limit(10).execute()
            sources = sources.data or []
        except:
            sources = []
        queries = []
        for s in sources:
            sectors = s.get("sectors", [])
            if isinstance(sectors, str):
                sectors = json.loads(sectors)
            for sector in sectors[:1]:
                queries.append((sector, f"underserved problems {sector} niche opportunities nobody solving"))
        queries.append(("cross", "overlooked everyday problems nobody talks about"))
        return queries, "low_ranked_gems"

    elif strategy == "sector_deep_dive":
        # Trova settore con meno problemi
        try:
            counts = {}
            for sector in SCANNER_SECTORS:
                result = supabase.table("problems").select("id", count="exact").eq("sector", sector).execute()
                counts[sector] = result.count or 0
            target_sector = min(counts, key=counts.get)
        except:
            target_sector = "sustainability"

        queries = [
            (target_sector, f"{target_sector} biggest problems consumers businesses face 2026"),
            (target_sector, f"{target_sector} pain points complaints forums reddit 2026"),
            (target_sector, f"{target_sector} market gaps underserved needs nobody solving"),
            (target_sector, f"{target_sector} startups failed why lessons learned"),
        ]
        return queries, f"deep_dive_{target_sector}"

    elif strategy == "correlated_problems":
        try:
            approved = supabase.table("problems").select("title, sector, description").eq("status", "approved").order("weighted_score", desc=True).limit(5).execute()
            approved = approved.data or []
        except:
            approved = []

        queries = []
        for p in approved[:3]:
            queries.append((p.get("sector", "cross"), f"problems related to {p['title'][:60]} adjacent needs"))
            queries.append((p.get("sector", "cross"), f"people who struggle with {p['title'][:40]} also need"))
        if not queries:
            queries = [("cross", "most frustrating daily problems people pay to solve")]
        return queries, "correlated_problems"

    elif strategy == "emerging_trends":
        queries = [
            ("cross", "emerging problems from AI automation 2026 new pain points"),
            ("cross", "problems that will get worse next 2 years"),
            ("cross", "new regulations creating compliance problems businesses 2026"),
            ("cross", "generational shift problems Gen Z millennials face differently"),
            ("cross", "remote work hybrid problems companies still haven't solved"),
        ]
        return queries, "emerging_trends"

    elif strategy == "source_refresh":
        queries = [
            ("cross", "best sources for market research consumer problems 2026"),
            ("cross", "best subreddits forums for identifying business opportunities"),
            ("cross", "academic research consumer pain points underserved markets"),
        ]
        return queries, "source_refresh"

    # Fallback
    try:
        sources = supabase.table("scan_sources").select("*").eq("status", "active").order("relevance_score", desc=True).limit(10).execute()
        sources = sources.data or []
    except:
        sources = []
    return get_standard_queries(sources), "standard"


def get_standard_queries(sources):
    """Costruisce query standard per World Scanner basate sulle fonti attive."""
    all_sectors = set()
    for s in sources:
        sectors = s.get("sectors", [])
        if isinstance(sectors, str):
            sectors = json.loads(sectors)
        all_sectors.update(sectors)

    sector_queries = {
        "food": "food waste restaurants expired inventory unsold meals problem",
        "health": "patients waiting time mental health access rural areas problem",
        "finance": "small business cash flow invoicing late payments problem",
        "education": "tutoring affordable access learning disabilities students problem",
        "legal": "small business contract disputes legal costs too high problem",
        "ecommerce": "product returns fraud fake reviews online sellers problem",
        "hr": "employee burnout retention turnover small companies problem",
        "real_estate": "rental scams tenant landlord disputes maintenance problem",
        "sustainability": "food packaging waste recycling confusion consumers problem",
        "cybersecurity": "password reuse data breach small business protection problem",
        "entertainment": "independent creators monetization copyright content theft problem",
        "logistics": "last mile delivery cost small business shipping rural problem",
    }

    queries = []
    for sector in all_sectors:
        if sector in sector_queries:
            queries.append((sector, sector_queries[sector]))
    queries.append(("cross", "most frustrating everyday problems people pay to solve"))
    queries.append(("cross", "biggest complaints small business owners daily operations"))
    queries.append(("cross", "underserved customer needs no good solution exists"))
    return queries

