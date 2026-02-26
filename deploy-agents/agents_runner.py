"""
brAIn Agents Runner v2.0
Cloud Run service — sistema event-driven proattivo.
Event bus in Supabase, cicli autonomi con variazione, BOS integrato, self-improvement.
Pipeline: scan -> SA (3 fasi + BOS SQ) -> FE (+ BOS Feas) -> BOS -> verdict -> notifica.
"""

import os
import json
import time
import hashlib
import logging
import asyncio
import math
from calendar import monthrange
from datetime import datetime, timezone, timedelta
from aiohttp import web
from dotenv import load_dotenv
import anthropic
from supabase import create_client
import requests

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8080))

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = None
COMMAND_CENTER_URL = os.getenv("COMMAND_CENTER_URL", "")
SUPABASE_ACCESS_TOKEN = os.getenv("SUPABASE_ACCESS_TOKEN", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")


# ============================================================
# UTILITA CONDIVISE
# ============================================================

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
            "processed_at": datetime.now(timezone.utc).isoformat(),
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


def search_perplexity(query):
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
                "max_tokens": 600,
            },
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        return None
    except:
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
    "problema": 0.65,       # weighted_score minimo per auto-approvare il problema
    "soluzione": 0.70,      # overall_score minimo della migliore soluzione per proseguire
    "feasibility": 0.70,    # feasibility_score FE minimo per proseguire
    "bos": 0.80,            # BOS minimo per notificare Mirco (approve_bos action)
}
# Target: solo il 10% dei BOS generati deve superare soglia_bos.
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
    now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)
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


def scanner_make_fingerprint(title, sector):
    text = f"{title.lower().strip()}_{sector.lower().strip()}"
    return hashlib.md5(text.encode()).hexdigest()


def scanner_normalize_urgency(value):
    if isinstance(value, str):
        v = value.lower().strip()
        if v in ("low", "medium", "high", "critical"):
            return v
        try:
            value = float(v)
        except:
            return "medium"
    if isinstance(value, (int, float)):
        if value >= 0.85:
            return "critical"
        elif value >= 0.65:
            return "high"
        elif value >= 0.4:
            return "medium"
        else:
            return "low"
    return "medium"


SCANNER_GENERIC_TERMS = [
    "aziende", "companies", "persone", "people", "utenti", "users",
    "imprenditori", "entrepreneurs", "professionisti", "professionals",
    "individui", "individuals", "clienti", "customers", "lavoratori", "workers",
]


def scanner_calculate_weighted_score(problem):
    base_score = 0.0
    for param, weight in SCANNER_WEIGHTS.items():
        value = problem.get(param, 0.5)
        if isinstance(value, (int, float)):
            base_score += float(value) * weight

    adjustments = 0.0
    multiplier = 1.0

    target_customer = problem.get("target_customer", "").lower()
    evidence = problem.get("evidence", "")
    why_now = problem.get("why_now", "")
    pain_intensity = problem.get("pain_intensity", 3)

    # Penalita' per genericita'
    generic_count = sum(1 for t in SCANNER_GENERIC_TERMS if t in target_customer.split())
    if generic_count > 0 and len(target_customer.split()) <= 3:
        adjustments -= 0.20
    if not evidence or len(evidence) < 30:
        adjustments -= 0.15
    if not why_now or len(why_now) < 20:
        adjustments -= 0.10
    if isinstance(pain_intensity, (int, float)) and pain_intensity < 3:
        multiplier *= 0.7

    # Bonus per specificita'
    has_age = any(c.isdigit() for c in target_customer)
    has_many_words = len(target_customer.split()) >= 4
    if has_age or has_many_words:
        adjustments += 0.10
    has_number = any(c.isdigit() for c in evidence)
    has_source = any(t in evidence.lower() for t in ["fonte", "source", "report", "%", "milion", "miliard"])
    if has_number and (has_source or len(evidence) > 80):
        adjustments += 0.10

    final_score = (base_score + adjustments) * multiplier
    return round(max(0.0, min(1.0, final_score)), 4)


def normalize_batch_scores(problems_data):
    if len(problems_data) < 2:
        return problems_data
    problems_data.sort(key=lambda x: x["_weighted"], reverse=True)
    n = len(problems_data)
    best_score = min(problems_data[0]["_weighted"], 0.92)
    worst_score = max(best_score - (n * 0.12), 0.25)
    if n == 1:
        problems_data[0]["_weighted"] = best_score
    elif n == 2:
        problems_data[0]["_weighted"] = best_score
        problems_data[1]["_weighted"] = round(best_score - 0.15, 4)
    else:
        step = (best_score - worst_score) / (n - 1)
        for i, p in enumerate(problems_data):
            p["_weighted"] = round(best_score - (i * step), 4)
            p["_weighted"] = max(0.15, min(1.0, p["_weighted"]))
    return problems_data


def get_standard_queries(sources):
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


def run_scan(queries, max_problems=None):
    """
    Core scan logic con soglie dinamiche da DB.
    max_problems: se impostato, si ferma dopo aver salvato N problemi di qualità.
    """
    thresholds = get_pipeline_thresholds()
    soglia_problema = thresholds["problema"]

    try:
        sources = supabase.table("scan_sources").select("*").eq("status", "active").order("relevance_score", desc=True).limit(10).execute()
        sources = sources.data or []
    except:
        sources = []

    try:
        fps_result = supabase.table("problems").select("fingerprint").not_.is_("fingerprint", "null").execute()
        existing_fps = {r["fingerprint"] for r in fps_result.data}
    except:
        existing_fps = set()

    source_map = {s["name"]: s["id"] for s in sources}

    # Preferenze per il prompt
    preferences = get_mirco_preferences()
    sector_mods = get_sector_preference_modifier()

    preferences_block = ""
    if preferences:
        preferences_block = f"PREFERENZE DI MIRCO (calibra la ricerca di conseguenza):\n{preferences}\n"
    if sector_mods:
        favored = [s for s, v in sector_mods.items() if v > 0]
        disfavored = [s for s, v in sector_mods.items() if v < -1]
        if favored:
            preferences_block += f"Settori preferiti: {', '.join(favored)}\n"
        if disfavored:
            preferences_block += f"Settori poco interessanti: {', '.join(disfavored)} — riduci priorita\n"

    analysis_prompt = SCANNER_ANALYSIS_PROMPT.replace("{preferences_block}", preferences_block)

    search_results = []
    for sector, query in queries:
        result = search_perplexity(query)
        if result:
            search_results.append((sector, query, result))
        time.sleep(1)

    if not search_results:
        return {"status": "no_results", "saved": 0}

    total_saved = 0
    all_scores = []
    saved_problem_ids = []
    source_problem_scores = {}  # {source_id: [weighted_score, ...]} per aggiornamento mirato

    batch_size = 4
    for i in range(0, len(search_results), batch_size):
        batch = search_results[i:i + batch_size]
        combined = "\n\n---\n\n".join([
            f"Settore: {sector}\nQuery: {query}\nRisultati: {result}"
            for sector, query, result in batch
        ])

        start = time.time()
        try:
            response = claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=4096,
                system=analysis_prompt,
                messages=[{"role": "user", "content": f"Analizza e identifica problemi. SOLO JSON:\n\n{combined}"}]
            )
            duration = int((time.time() - start) * 1000)
            reply = response.content[0].text

            log_to_supabase("world_scanner", "scan_v2", 1,
                f"Batch {len(batch)} ricerche", reply[:500],
                "claude-haiku-4-5",
                response.usage.input_tokens, response.usage.output_tokens,
                (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
                duration)

            data = extract_json(reply)
            if data:
                batch_problems = []
                for prob in data.get("problems", []):
                    title = prob.get("title", "")
                    sector = prob.get("sector", "general")
                    if sector not in SCANNER_SECTORS:
                        sector = "ecommerce"

                    fp = scanner_make_fingerprint(title, sector)
                    if fp in existing_fps:
                        continue

                    weighted = scanner_calculate_weighted_score(prob)

                    # Sector preference modifier
                    mod = sector_mods.get(sector, 0)
                    if mod > 2:
                        weighted = round(weighted * 1.05, 4)
                    elif mod < -2:
                        weighted = round(weighted * 0.90, 4)

                    low_count = sum(1 for param in SCANNER_WEIGHTS if prob.get(param, 0.5) < 0.5 and isinstance(prob.get(param, 0.5), (int, float)))
                    if low_count == 0:
                        weighted = round(weighted * 0.8, 4)

                    batch_problems.append({
                        "_weighted": weighted, "_prob": prob,
                        "_title": title, "_sector": sector, "_fp": fp,
                    })

                batch_problems = normalize_batch_scores(batch_problems)

                for bp in batch_problems:
                    prob = bp["_prob"]
                    title = bp["_title"]
                    sector = bp["_sector"]
                    fp = bp["_fp"]
                    weighted = bp["_weighted"]

                    # Determina status in base alla soglia dinamica
                    # Sotto soglia: archiviato (nessuna notifica, nessuna pipeline)
                    # Sopra soglia: new (va in pipeline automatica)
                    save_status = "new" if weighted >= soglia_problema else "archived"

                    urgency_text = scanner_normalize_urgency(prob.get("urgency", 0.5))

                    source_id = None
                    source_name = prob.get("source_name", "")
                    for sname, sid in source_map.items():
                        if sname.lower() in source_name.lower() or source_name.lower() in sname.lower():
                            source_id = sid
                            break

                    top_markets = prob.get("top_markets", [])
                    if isinstance(top_markets, str):
                        top_markets = json.loads(top_markets)

                    pain_intensity_val = prob.get("pain_intensity", None)
                    if isinstance(pain_intensity_val, (int, float)):
                        pain_intensity_val = int(pain_intensity_val)

                    try:
                        insert_result = supabase.table("problems").insert({
                            "title": title,
                            "description": prob.get("description", ""),
                            "domain": sector, "sector": sector,
                            "geographic_scope": prob.get("geographic_scope", "global"),
                            "top_markets": json.dumps(top_markets),
                            "market_size": float(prob.get("market_size", 0.5)),
                            "willingness_to_pay": float(prob.get("willingness_to_pay", 0.5)),
                            "urgency": urgency_text,
                            "competition_gap": float(prob.get("competition_gap", 0.5)),
                            "ai_solvability": float(prob.get("ai_solvability", 0.5)),
                            "time_to_market": float(prob.get("time_to_market", 0.5)),
                            "recurring_potential": float(prob.get("recurring_potential", 0.5)),
                            "weighted_score": weighted, "score": weighted,
                            "who_is_affected": prob.get("who_is_affected", ""),
                            "real_world_example": prob.get("real_world_example", ""),
                            "why_it_matters": prob.get("why_it_matters", ""),
                            # Nuovi campi specificita' v3.0
                            "target_customer": prob.get("target_customer", ""),
                            "target_geography": prob.get("target_geography", ""),
                            "problem_frequency": prob.get("problem_frequency", ""),
                            "current_workaround": prob.get("current_workaround", ""),
                            "pain_intensity": pain_intensity_val,
                            "evidence": prob.get("evidence", ""),
                            "why_now": prob.get("why_now", ""),
                            "fingerprint": fp, "source_id": source_id,
                            "source_ids": json.dumps([source_id] if source_id else []),
                            "status": save_status,
                            "status_detail": "active" if save_status == "new" else "archived",
                            "created_by": "world_scanner_v3",
                        }).execute()

                        existing_fps.add(fp)
                        if save_status == "new":
                            total_saved += 1
                            all_scores.append(weighted)
                            # Traccia score per aggiornamento mirato del relevance_score
                            if source_id is not None:
                                source_problem_scores.setdefault(source_id, []).append(weighted)
                            if insert_result.data:
                                saved_problem_ids.append(insert_result.data[0]["id"])
                        else:
                            logger.debug(f"[SCAN] '{title[:50]}': score={weighted:.2f} < soglia {soglia_problema} → archived")

                    except Exception as e:
                        if "idx_problems_fingerprint" not in str(e):
                            logger.error(f"[SAVE ERROR] {e}")

                for ns in data.get("new_sources", []):
                    try:
                        name = ns.get("name", "")
                        if name:
                            supabase.table("scan_sources").insert({
                                "name": name, "url": ns.get("url", ""),
                                "category": ns.get("category", "other"),
                                "sectors": json.dumps(ns.get("sectors", [])),
                                "relevance_score": 0.4, "status": "active",
                                "notes": "Scoperta automatica",
                            }).execute()
                    except:
                        pass

        except Exception as e:
            logger.error(f"[BATCH ERROR] {e}")
        time.sleep(1)

    # Aggiorna statistiche fonti — solo quelle che hanno contribuito problemi
    now_iso = datetime.now(timezone.utc).isoformat()
    for source in sources:
        sid = source.get("id")
        try:
            if sid in source_problem_scores:
                # Fonte che ha prodotto almeno un problema: aggiorna stats
                scores = source_problem_scores[sid]
                avg_score = sum(scores) / len(scores)
                old_found = source.get("problems_found", 0)
                old_avg = source.get("avg_problem_score", 0) or 0
                new_found = old_found + len(scores)
                new_avg = (old_avg * old_found + sum(scores)) / new_found if new_found > 0 else avg_score
                old_rel = source.get("relevance_score", 0.5)
                new_rel = min(1.0, old_rel + 0.02) if avg_score > 0.6 else max(0.1, old_rel - 0.02) if avg_score < 0.4 else old_rel
                supabase.table("scan_sources").update({
                    "problems_found": new_found,
                    "avg_problem_score": round(new_avg, 4),
                    "relevance_score": round(new_rel, 4),
                    "last_scanned": now_iso,
                }).eq("id", sid).execute()
            else:
                # Fonte scansionata ma senza problemi: aggiorna solo last_scanned
                supabase.table("scan_sources").update({
                    "last_scanned": now_iso,
                }).eq("id", sid).execute()
        except:
            pass

    # Emetti eventi
    if total_saved > 0:
        emit_event("world_scanner", "scan_completed", None,
            {"problems_saved": total_saved, "problem_ids": saved_problem_ids,
             "avg_score": sum(all_scores) / len(all_scores) if all_scores else 0})
        high_score_ids = [pid for pid, sc in zip(saved_problem_ids, all_scores) if sc >= MIN_SCORE_THRESHOLD]
        if high_score_ids:
            emit_event("world_scanner", "problems_found", "command_center",
                {"problem_ids": high_score_ids, "count": len(high_score_ids)})

    if total_saved >= 3:
        emit_event("world_scanner", "batch_scan_complete", "knowledge_keeper",
            {"problems_saved": total_saved, "avg_score": sum(all_scores) / len(all_scores) if all_scores else 0})

    return {"status": "completed", "saved": total_saved, "saved_ids": saved_problem_ids, "max_hit": max_problems is not None and total_saved >= max_problems}


def run_world_scanner():
    """
    Scan ogni 2 ore con rotazione intelligente da scan_schedule.
    Obiettivo: esattamente 1 problema di alta qualità per scan.
    Se il primo tentativo non trova un problema valido, riprova con una fonte alternativa.
    """
    strategy = get_scan_schedule_strategy()
    logger.info(f"World Scanner v3.0 starting — strategia: {strategy}")

    log_to_supabase("world_scanner", f"scan_v3_{strategy}", 1,
        f"Strategia: {strategy}", None, "none")

    # Costruisci query basate sulla strategia
    queries_primary, _ = build_strategy_queries(strategy)

    # Tenta con strategia primaria — limita a max 4 query per scan
    queries_primary = queries_primary[:4]
    result = run_scan(queries_primary, max_problems=1)

    # Se non trovato nulla, riprova con top_sources come fallback
    if result.get("saved", 0) == 0 and strategy != "top_sources":
        logger.info(f"[SCANNER] Nessun problema valido con '{strategy}', ritento con top_sources")
        log_to_supabase("world_scanner", "scan_retry_fallback", 1,
            f"Retry da {strategy}", "top_sources", "none")
        try:
            sources = supabase.table("scan_sources").select("*").eq("status", "active")\
                .order("relevance_score", desc=True).limit(5).execute()
            fallback_queries = get_standard_queries(sources.data or [])[:3]
        except:
            fallback_queries = [("cross", "specific niche professional problem concrete evidence 2026")]
        result = run_scan(fallback_queries, max_problems=1)

    # Pipeline automatica in background
    saved_ids = result.get("saved_ids", [])
    if saved_ids:
        import threading
        threading.Thread(target=run_auto_pipeline, args=(saved_ids,), daemon=True).start()
        logger.info(f"[PIPELINE] Avviata per {len(saved_ids)} problemi")
    else:
        logger.info(f"[SCANNER] Nessun problema salvato questo ciclo (qualità insufficiente — OK)")

    return result


def run_custom_scan(topic):
    logger.info(f"World Scanner custom scan: {topic}")
    queries = [
        ("custom", f"{topic} biggest problems pain points"),
        ("custom", f"{topic} unsolved needs market gap"),
        ("custom", f"{topic} consumers complaints frustrations"),
    ]
    result = run_scan(queries)
    saved_ids = result.get("saved_ids", [])
    if saved_ids:
        import threading
        threading.Thread(target=run_auto_pipeline, args=(saved_ids,), daemon=True).start()
    elif result.get("saved", 0) == 0:
        notify_telegram(f"Scan su '{topic}' completato ma non ho trovato problemi nuovi.")
    return result


# ============================================================
# SOLUTION ARCHITECT v2.0 — 3 fasi + BOS SQ
# ============================================================

RESEARCH_PROMPT = """Sei un analista di mercato esperto. Dati i risultati di ricerca sul web, crea un DOSSIER COMPETITIVO per il problema dato.

LINGUA: Rispondi SEMPRE in italiano.

Il dossier deve includere:
1. SOLUZIONI ESISTENTI: chi gia' risolve questo problema? Nome, cosa fa, prezzo, punti deboli.
2. GAP DI MERCATO: cosa manca nelle soluzioni attuali?
3. TENTATIVI FALLITI: qualcuno ha provato e fallito? Perche'?
4. INSIGHT ESPERTI: cosa dicono ricercatori, analisti, utenti su Reddit/forum?
5. DIMENSIONE OPPORTUNITA: quanto vale questo mercato?

Rispondi SOLO con JSON:
{"existing_solutions":[{"name":"nome","what_it_does":"cosa fa","price":"costo","weaknesses":"punti deboli","market_share":"stima"}],"market_gaps":["gap1","gap2"],"failed_attempts":[{"who":"chi","why_failed":"perche"}],"expert_insights":["insight1","insight2"],"market_size_estimate":"stima valore mercato","key_finding":"la scoperta piu' importante in una frase"}
SOLO JSON."""

GENERATION_PROMPT = """Sei il Solution Architect di brAIn, un'organizzazione AI-native.
Genera 3 soluzioni MVP-ready basandoti su:
1. Business Model Canvas (Osterwalder): value prop + segmento + revenue + canali + costi
2. Principio YC "10x better": la soluzione DEVE essere 10x migliore dello status quo su almeno una dimensione
3. "Paradox of Specificity" (First Round): piu' e' specifica per un segmento, piu' e' forte il moat
4. Dossier competitivo fornito: identifica il GAP reale e costruisci su quello

LINGUA: Rispondi SEMPRE in italiano. Tutto in italiano.

VINCOLI: 1 persona, 20h/settimana, competenza tecnica minima. Budget: sotto 200 EUR/mese primo progetto.

REGOLE CRITICHE:
- Il customer_segment DEVE coincidere esattamente con il target_customer del problema
- Sii SPECIFICO: NON "app per PMI" ma "bot Telegram per elettricisti che risponde a query sulla normativa CEI"
- NON proporre soluzioni che gia' esistono e funzionano bene — cerca gli spazi vuoti

Per ogni soluzione fornisci:

BUSINESS MODEL CANVAS:
- title, description
- value_proposition: frase unica — "aiutiamo [target specifico] a [fare X] senza [pain attuale]"
- target_segment, job_to_be_done
- revenue_model: SaaS_mensile | marketplace | one_time | freemium | transactional
- price_point_eur: prezzo EUR/mese con giustificazione
- distribution_channel: come raggiungiamo i primi 100 clienti senza paid ads

MVP SPEC:
- mvp_features: lista 3 funzionalita' MINIME per validare l'ipotesi di valore
- mvp_build_time_days: giorni per costruire MVP con agenti AI (20h/settimana)
- mvp_cost_eur: costo totale MVP (hosting + API + tools)
- unfair_advantage: perche' AI-native batte team tradizionale su questa soluzione
- competitive_gap: cosa mancano ai competitor che noi copriamo

METRICHE:
- monthly_revenue_potential, monthly_burn_rate, competitive_moat
- novelty_score (0-1), opportunity_score (0-1), defensibility_score (0-1)

BOS SOLUTION QUALITY SCORES (0.0-1.0, scala severa):
- uniqueness: penalizza se >3 competitor diretti con feature identiche
- moat_potential: network effects o dati proprietari = 1.0, solo brand = 0.3
- value_multiplier: 10x = 1.0, 5x = 0.7, 2x = 0.4, <2x = 0.1 (scala logaritmica)
- revenue_clarity: SaaS con prezzo definito = 1.0, "valutiamo" = 0.5, "vedremo" = 0.0
- ai_nativeness: togli AI e non funziona = 1.0, togli AI e funziona uguale = 0.0
- simplicity: utente capisce in <10 secondi = 1.0

{preferences_block}

Rispondi SOLO con JSON:
{{"solutions":[{{"title":"titolo specifico","description":"cosa fa in modo specifico","value_proposition":"aiutiamo X a fare Y senza Z","target_segment":"segmento preciso","job_to_be_done":"job da fare","revenue_model":"SaaS_mensile","price_point_eur":29,"distribution_channel":"community LinkedIn + SEO long-tail","mvp_features":["feature 1","feature 2","feature 3"],"mvp_build_time_days":14,"mvp_cost_eur":80,"unfair_advantage":"perche AI-native batte team tradizionale","competitive_gap":"cosa mancano i competitor","monthly_revenue_potential":"500-2000 EUR","monthly_burn_rate":"50 EUR","competitive_moat":"cosa ci rende difendibili","novelty_score":0.7,"opportunity_score":0.8,"defensibility_score":0.6,"uniqueness":0.7,"moat_potential":0.6,"value_multiplier":0.8,"simplicity":0.7,"revenue_clarity":0.8,"ai_nativeness":0.9}}],"ranking_rationale":"perche' hai messo la prima in cima"}}
SOLO JSON."""

SA_FEASIBILITY_PROMPT = """Sei un CTO pragmatico. Valuta la fattibilita' di ogni soluzione dati questi VINCOLI.

LINGUA: Rispondi SEMPRE in italiano.

VINCOLI ATTUALI:
- 1 persona, 20h/settimana, competenza tecnica minima
- Budget: 1000 euro/mese totale, primo progetto sotto 200 euro/mese
- Stack: Claude API, Supabase, Python, Google Cloud Run, Telegram Bot
- Obiettivo: revenue entro 3 mesi

Per ogni soluzione valuta:
- feasibility_score: 0.0-1.0
- complexity: low/medium/high
- time_to_mvp, cost_estimate, tech_stack_fit (0-1)
- biggest_risk, recommended_mvp, nocode_compatible (bool)

Rispondi SOLO con JSON:
{"assessments":[{"solution_title":"","feasibility_score":0.7,"complexity":"medium","time_to_mvp":"3 settimane","cost_estimate":"80 euro/mese","tech_stack_fit":0.8,"biggest_risk":"rischio","recommended_mvp":"cosa costruire","nocode_compatible":true}],"best_feasible":"quale e perche","best_overall":"quale in assoluto"}
SOLO JSON."""


def research_problem(problem):
    logger.info(f"[SA] Fase 1: Ricerca per '{problem['title'][:60]}'")
    title = problem["title"]
    sector = problem.get("sector", "")

    search_queries = [
        f"{title} existing solutions competitors market",
        f"{title} startup failed attempts lessons learned",
        f"{title} reddit forum user complaints workarounds",
        f"{title} market size revenue opportunity {sector}",
    ]

    search_results = []
    for q in search_queries:
        result = search_perplexity(q)
        if result:
            search_results.append(result)
        time.sleep(1)

    if not search_results:
        return None

    combined_research = "\n\n---\n\n".join(search_results)
    problem_context = (
        f"PROBLEMA: {title}\n"
        f"Descrizione: {problem.get('description', '')}\n"
        f"Settore: {sector}\n"
        f"Chi e' colpito: {problem.get('who_is_affected', '')}\n"
        f"Perche conta: {problem.get('why_it_matters', '')}"
    )

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=3000,
            system=RESEARCH_PROMPT,
            messages=[{"role": "user", "content": f"{problem_context}\n\nRISULTATI RICERCA:\n{combined_research}\n\nCrea il dossier. SOLO JSON."}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("solution_architect", "research", 2,
            f"Ricerca: {title[:100]}", reply[:500],
            "claude-haiku-4-5",
            response.usage.input_tokens, response.usage.output_tokens,
            (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            duration)

        return extract_json(reply)
    except Exception as e:
        logger.error(f"[SA RESEARCH ERROR] {e}")
        return None


def generate_solutions_unconstrained(problem, dossier):
    logger.info(f"[SA] Fase 2: Generazione per '{problem['title'][:60]}'")

    preferences = get_mirco_preferences()
    preferences_block = ""
    if preferences:
        preferences_block = f"PREFERENZE DI MIRCO (calibra le soluzioni):\n{preferences}\n"

    gen_prompt = GENERATION_PROMPT.replace("{preferences_block}", preferences_block)

    problem_context = (
        f"PROBLEMA: {problem['title']}\n"
        f"Descrizione: {problem.get('description', '')}\n"
        f"Settore: {problem.get('sector', '')}\n"
        f"Score: {problem.get('weighted_score', '')}\n\n"
        f"TARGET SPECIFICO:\n"
        f"  Target customer: {problem.get('target_customer', problem.get('who_is_affected', ''))}\n"
        f"  Geografia: {problem.get('target_geography', problem.get('geographic_scope', ''))}\n"
        f"  Mercati: {problem.get('top_markets', '')}\n\n"
        f"DETTAGLI PROBLEMA:\n"
        f"  Frequenza: {problem.get('problem_frequency', '')}\n"
        f"  Workaround attuale: {problem.get('current_workaround', '')}\n"
        f"  Pain intensity: {problem.get('pain_intensity', '')}/5\n"
        f"  Evidence: {problem.get('evidence', '')}\n"
        f"  Why now: {problem.get('why_now', '')}\n\n"
        f"Chi e' colpito: {problem.get('who_is_affected', '')}\n"
        f"Esempio reale: {problem.get('real_world_example', '')}\n"
        f"Perche conta: {problem.get('why_it_matters', '')}"
    )

    dossier_text = json.dumps(dossier, indent=2, ensure_ascii=False)

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            system=gen_prompt,
            messages=[{"role": "user", "content": f"{problem_context}\n\nDOSSIER COMPETITIVO:\n{dossier_text}\n\nGenera 3 soluzioni. SOLO JSON."}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("solution_architect", "generate_unconstrained", 2,
            f"Soluzioni per: {problem['title'][:100]}", reply[:500],
            "claude-sonnet-4-5",
            response.usage.input_tokens, response.usage.output_tokens,
            (response.usage.input_tokens * 3.0 + response.usage.output_tokens * 15.0) / 1_000_000,
            duration)

        return extract_json(reply)
    except Exception as e:
        logger.error(f"[SA GENERATE ERROR] {e}")
        return None


def assess_feasibility(problem, solutions_data):
    logger.info(f"[SA] Fase 3: Fattibilita per '{problem['title'][:60]}'")
    solutions_text = json.dumps(solutions_data.get("solutions", []), indent=2, ensure_ascii=False)

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            system=SA_FEASIBILITY_PROMPT,
            messages=[{"role": "user", "content": f"PROBLEMA: {problem['title']}\n\nSOLUZIONI:\n{solutions_text}\n\nValuta fattibilita. SOLO JSON."}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("solution_architect", "assess_feasibility", 2,
            f"Fattibilita: {problem['title'][:100]}", reply[:500],
            "claude-haiku-4-5",
            response.usage.input_tokens, response.usage.output_tokens,
            (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            duration)

        return extract_json(reply)
    except Exception as e:
        logger.error(f"[SA FEASIBILITY ERROR] {e}")
        return None


def save_solution_v2(problem_id, sol, assessment, ranking_rationale, dossier):
    try:
        complexity = str(assessment.get("complexity", "medium")).lower().strip()
        if "low" in complexity:
            complexity = "low"
        elif "high" in complexity:
            complexity = "high"
        else:
            complexity = "medium"

        mvp_features = sol.get("mvp_features", [])
        if isinstance(mvp_features, str):
            try:
                mvp_features = json.loads(mvp_features)
            except:
                mvp_features = [mvp_features]

        sol_result = supabase.table("solutions").insert({
            "problem_id": problem_id,
            "title": sol.get("title", "Senza titolo"),
            "description": sol.get("description", ""),
            "approach": json.dumps({
                "value_proposition": sol.get("value_proposition", ""),
                "target_segment": sol.get("target_segment", ""),
                "job_to_be_done": sol.get("job_to_be_done", ""),
                "revenue_model": sol.get("revenue_model", ""),
                "competitive_moat": sol.get("competitive_moat", ""),
                "recommended_mvp": assessment.get("recommended_mvp", ""),
                "monthly_revenue_potential": sol.get("monthly_revenue_potential", ""),
                "monthly_burn_rate": sol.get("monthly_burn_rate", ""),
                "biggest_risk": assessment.get("biggest_risk", ""),
                "market_gaps": dossier.get("market_gaps", []),
                "existing_competitors": [s.get("name", "") for s in dossier.get("existing_solutions", [])],
                "ranking_rationale": ranking_rationale,
            }, ensure_ascii=False),
            "sector": sol.get("sector", ""),
            "sub_sector": sol.get("sub_sector", ""),
            "status": "proposed",
            "created_by": "solution_architect_v2",
            # Nuovi campi MVP v2.0
            "value_proposition": sol.get("value_proposition", ""),
            "customer_segment": sol.get("target_segment", ""),
            "revenue_model": sol.get("revenue_model", ""),
            "price_point": str(sol.get("price_point_eur", "")),
            "distribution_channel": sol.get("distribution_channel", ""),
            "mvp_features": json.dumps(mvp_features) if mvp_features else None,
            "mvp_build_time": int(sol.get("mvp_build_time_days", 0)) if sol.get("mvp_build_time_days") else None,
            "mvp_cost_eur": float(sol.get("mvp_cost_eur", 0)) if sol.get("mvp_cost_eur") else None,
            "unfair_advantage": sol.get("unfair_advantage", ""),
            "competitive_gap": sol.get("competitive_gap", ""),
        }).execute()

        sol_id = sol_result.data[0]["id"]

        novelty = float(sol.get("novelty_score", 0.5))
        opportunity = float(sol.get("opportunity_score", 0.5))
        defensibility = float(sol.get("defensibility_score", 0.5))
        feasibility = float(assessment.get("feasibility_score", 0.5))
        tech_fit = float(assessment.get("tech_stack_fit", 0.5))

        impact = round((novelty + opportunity + defensibility) / 3, 4)
        overall = round((impact + feasibility) / 2, 4)

        supabase.table("solution_scores").insert({
            "solution_id": sol_id,
            "feasibility_score": feasibility,
            "impact_score": impact,
            "cost_estimate": str(assessment.get("cost_estimate", "unknown")),
            "complexity": complexity,
            "time_to_market": str(assessment.get("time_to_mvp", "unknown")),
            "nocode_compatible": bool(assessment.get("nocode_compatible", True)),
            "overall_score": overall,
            "notes": json.dumps({
                "novelty": novelty,
                "opportunity": opportunity,
                "defensibility": defensibility,
                "tech_stack_fit": tech_fit,
                "revenue_model": sol.get("revenue_model", ""),
                "monthly_revenue_potential": sol.get("monthly_revenue_potential", ""),
                "monthly_burn_rate": sol.get("monthly_burn_rate", ""),
                "uniqueness": float(sol.get("uniqueness", 0.5)),
                "moat_potential": float(sol.get("moat_potential", 0.5)),
                "value_multiplier": float(sol.get("value_multiplier", 0.5)),
                "simplicity": float(sol.get("simplicity", 0.5)),
                "revenue_clarity": float(sol.get("revenue_clarity", 0.5)),
                "ai_nativeness": float(sol.get("ai_nativeness", 0.5)),
            }, ensure_ascii=False),
            "scored_by": "solution_architect_v2",
        }).execute()

        return sol_id, overall

    except Exception as e:
        logger.error(f"[SAVE SOL V2 ERROR] {e}")
        return None, 0


def run_solution_architect(problem_id=None):
    logger.info("Solution Architect v2.0 starting (3 fasi)...")

    try:
        query = supabase.table("problems").select("*").eq("status", "approved").order("weighted_score", desc=True)
        if problem_id:
            query = query.eq("id", problem_id)
        problems = query.execute()
        problems = problems.data or []
    except:
        problems = []

    if not problems:
        return {"status": "no_problems", "saved": 0}

    try:
        existing = supabase.table("solutions").select("problem_id").execute()
        existing_ids = {s["problem_id"] for s in (existing.data or [])}
    except:
        existing_ids = set()

    if not problem_id:
        problems = [p for p in problems if p["id"] not in existing_ids]
    if not problems:
        return {"status": "all_solved", "saved": 0}

    total_saved = 0
    all_solution_ids = []

    for problem in problems:
        dossier = research_problem(problem)
        if not dossier:
            dossier = {"existing_solutions": [], "market_gaps": ["nessun dato"], "failed_attempts": [], "expert_insights": [], "market_size_estimate": "sconosciuto", "key_finding": "ricerca non disponibile"}

        solutions_data = generate_solutions_unconstrained(problem, dossier)
        if not solutions_data or not solutions_data.get("solutions"):
            logger.warning(f"[SA] Nessuna soluzione valida per '{problem['title'][:60]}'. "
                f"Risposta: {str(solutions_data)[:200] if solutions_data else 'None'}")
            continue

        ranking_rationale = solutions_data.get("ranking_rationale", "")

        feasibility_data = assess_feasibility(problem, solutions_data)
        if not feasibility_data:
            feasibility_data = {"assessments": [], "best_feasible": "", "best_overall": ""}

        feas_map = {}
        for a in feasibility_data.get("assessments", []):
            feas_map[a.get("solution_title", "")] = a

        problem_solution_ids = []
        for sol in solutions_data.get("solutions", []):
            title = sol.get("title", "")
            assessment = feas_map.get(title, {
                "feasibility_score": 0.5, "complexity": "medium",
                "time_to_mvp": "sconosciuto", "cost_estimate": "sconosciuto",
                "tech_stack_fit": 0.5, "biggest_risk": "non valutato",
                "recommended_mvp": "non valutato", "nocode_compatible": True,
            })

            sol_id, overall = save_solution_v2(problem["id"], sol, assessment, ranking_rationale, dossier)
            if sol_id:
                if overall < PIPELINE_THRESHOLDS["soluzione"]:
                    logger.info(f"[SA] {title[:40]}: overall={overall:.2f} sotto soglia {PIPELINE_THRESHOLDS['soluzione']}, salvata ma non prioritaria")
                total_saved += 1
                problem_solution_ids.append(sol_id)
                all_solution_ids.append(sol_id)

        # Emit event per questo problema
        if problem_solution_ids:
            emit_event("solution_architect", "solutions_generated", "feasibility_engine",
                {"solution_ids": problem_solution_ids, "problem_id": str(problem["id"])})

        time.sleep(2)

    logger.info(f"Solution Architect v2.0 completato: {total_saved} soluzioni")
    return {"status": "completed", "saved": total_saved, "solution_ids": all_solution_ids}


# ============================================================
# FEASIBILITY ENGINE v1.1 — con BOS Feasibility
# ============================================================

FEASIBILITY_ENGINE_PROMPT = """Sei il Feasibility Engine di brAIn, un'organizzazione AI-native.
Valuti la fattibilita' economica e tecnica di soluzioni AI con MASSIMO realismo.

LINGUA: Rispondi SEMPRE in italiano.

VINCOLI:
- 1 persona, 20h/settimana, competenza tecnica minima
- Budget: 1000 EUR/mese totale, primo progetto sotto 200 EUR/mese
- Stack: Claude API, Supabase, Python, Google Cloud Run, Telegram Bot
- Obiettivo: revenue entro 3 mesi, marginalita' alta priorita' assoluta

REGOLE DI REALISMO: sii PESSIMISTA su revenue, PESSIMISTA su timeline.
Se il mercato ha >5 competitor attivi e non hai vantaggio 10x chiaro, di' NO_GO.

1. COSTO MVP: dev_hours, dev_cost_eur, api_monthly_eur, hosting_monthly_eur, other_monthly_eur, total_mvp_cost_eur, total_monthly_cost_eur
2. TIMELINE: weeks_to_mvp, weeks_to_revenue
3. REVENUE (3 scenari a 6 mesi): pessimistic_monthly_eur, realistic_monthly_eur, optimistic_monthly_eur, pricing_model, price_point_eur
4. MARGINALITA: monthly_margin_pessimistic/realistic/optimistic, margin_percentage_realistic, breakeven_months
5. COMPETITION: competition_score (0-1), direct_competitors, indirect_competitors, our_advantage
6. GO/NO-GO: decision (GO/CONDITIONAL_GO/NO_GO), confidence (0-1), reasoning, conditions, biggest_risk, biggest_opportunity

7. BOS FEASIBILITY SCORES v2.0 (0.0-1.0, scala a step SEVERA):
   - mvp_cost_score: <200 EUR/mese=1.0, 200-500=0.8, 500-2000=0.5, >2000=0.2
   - time_to_market: <1 settimana=1.0, 1-2 sett=0.8, 1 mese=0.5, 2 mesi=0.3, >2 mesi=0.1
   - ai_buildability: interamente con Claude Code + agenti=1.0, richiede dev umano=0.4, richiede team=0.0
   - margin_potential: >80% margine=1.0, 50-80%=0.7, 20-50%=0.4, <20%=0.1
   - market_access: 100 clienti via SEO/community senza paid ads=1.0, solo partnership=0.5, solo cold outreach=0.3, enterprise=0.0
   - recurring_revenue: SaaS mensile=1.0, abbonamento annuale=0.8, pay_per_use frequente=0.6, one_shot=0.2
   - scalability: 100->10.000 clienti senza costi proporzionali=1.0, richiede support umano=0.4, richiede team=0.0

Rispondi SOLO con JSON:
{"mvp_cost":{"dev_hours":0,"dev_cost_eur":0,"api_monthly_eur":0,"hosting_monthly_eur":0,"other_monthly_eur":0,"total_mvp_cost_eur":0,"total_monthly_cost_eur":0},"timeline":{"weeks_to_mvp":0,"weeks_to_revenue":0},"revenue":{"pessimistic_monthly_eur":0,"realistic_monthly_eur":0,"optimistic_monthly_eur":0,"pricing_model":"","price_point_eur":0},"margin":{"monthly_margin_pessimistic":0,"monthly_margin_realistic":0,"monthly_margin_optimistic":0,"margin_percentage_realistic":0,"breakeven_months":0},"competition":{"competition_score":0.0,"direct_competitors":0,"indirect_competitors":0,"our_advantage":""},"recommendation":{"decision":"GO","confidence":0.0,"reasoning":"","conditions":"","biggest_risk":"","biggest_opportunity":""},"bos_feasibility":{"mvp_cost_score":0.0,"time_to_market":0.0,"ai_buildability":0.0,"margin_potential":0.0,"market_access":0.0,"recurring_revenue":0.0,"scalability":0.0}}
SOLO JSON."""


def feasibility_calculate_score(analysis):
    """
    Scoring BOS v2.0 con scala non-lineare (^1.5) per evitare clustering alto.
    Distribuzione risultante: 0.9 grezzo -> 0.85, 0.7 -> 0.59, 0.5 -> 0.35, 0.3 -> 0.16
    """
    if not analysis:
        return 0.0

    bos = analysis.get("bos_feasibility", {})
    params = [
        ("mvp_cost_score", 0.20),
        ("time_to_market", 0.15),
        ("ai_buildability", 0.15),
        ("margin_potential", 0.20),
        ("market_access", 0.15),
        ("recurring_revenue", 0.10),
        ("scalability", 0.05),
    ]

    raw_score = 0.0
    for key, weight in params:
        val = float(bos.get(key, 0.0))
        raw_score += max(0.0, min(1.0, val)) * weight

    # Moltiplicatore decisione
    rec = analysis.get("recommendation", {})
    decision = rec.get("decision", "NO_GO")
    confidence = float(rec.get("confidence", 0.5))
    if decision == "NO_GO":
        raw_score *= 0.5
    elif decision == "CONDITIONAL_GO":
        raw_score *= max(0.7, confidence)

    # Scala non-lineare: penalizza mediocri, premia eccellenti
    final_score = raw_score ** 1.5
    return round(max(0.0, min(1.0, final_score)), 4)


def run_feasibility_engine(solution_id=None, notify=True):
    logger.info("Feasibility Engine v1.1 starting...")

    try:
        query = supabase.table("solutions").select(
            "*, problems(title, description, sector, who_is_affected, why_it_matters, "
            "weighted_score, target_customer, target_geography, pain_intensity, evidence, why_now)"
        )
        if solution_id:
            query = query.eq("id", solution_id)
        else:
            query = query.eq("status", "proposed").is_("feasibility_details", "null")
        result = query.order("created_at", desc=True).limit(20).execute()
        solutions = result.data or []
    except Exception as e:
        logger.error(f"[FE] Recupero soluzioni: {e}")
        return {"status": "error", "error": str(e)}

    if not solutions:
        return {"status": "no_solutions", "evaluated": 0}

    evaluated = 0
    go_solutions = []
    conditional_solutions = []

    for sol in solutions:
        title = sol.get("title", "Senza titolo")
        sector = sol.get("sector", "")
        logger.info(f"[FE] Valutazione: {title[:60]}")

        problem = sol.get("problems", {}) or {}

        try:
            scores_result = supabase.table("solution_scores").select("*").eq("solution_id", sol["id"]).execute()
            scores = scores_result.data[0] if scores_result.data else {}
        except:
            scores = {}

        competition_research = search_perplexity(
            f"{title} competitors alternatives market size pricing {sector}"
        )
        time.sleep(1)

        approach = sol.get("approach", "")
        if isinstance(approach, str):
            try:
                approach_data = json.loads(approach)
                approach_text = json.dumps(approach_data, indent=2, ensure_ascii=False)
            except:
                approach_text = approach
        else:
            approach_text = json.dumps(approach, indent=2, ensure_ascii=False)

        context = (
            f"SOLUZIONE: {title}\n"
            f"Descrizione: {sol.get('description', '')}\n"
            f"Approccio: {approach_text}\n"
            f"Settore: {sector} / {sol.get('sub_sector', '')}\n"
            f"Value Proposition: {sol.get('value_proposition', '')}\n"
            f"Customer Segment: {sol.get('customer_segment', '')}\n"
            f"Revenue Model: {sol.get('revenue_model', '')}\n"
            f"Price Point: {sol.get('price_point', '')}\n"
            f"Distribution Channel: {sol.get('distribution_channel', '')}\n"
            f"MVP Build Time: {sol.get('mvp_build_time', '')} giorni\n"
            f"MVP Cost EUR: {sol.get('mvp_cost_eur', '')} EUR\n"
            f"Unfair Advantage: {sol.get('unfair_advantage', '')}\n"
            f"Competitive Gap: {sol.get('competitive_gap', '')}\n\n"
            f"PROBLEMA: {problem.get('title', '')}\n"
            f"Target Customer: {problem.get('target_customer', problem.get('who_is_affected', ''))}\n"
            f"Target Geography: {problem.get('target_geography', '')}\n"
            f"Pain Intensity: {problem.get('pain_intensity', '')}/5\n"
            f"Evidence: {problem.get('evidence', '')}\n"
            f"Why Now: {problem.get('why_now', '')}\n"
            f"Score problema: {problem.get('weighted_score', '')}\n\n"
            f"SCORE SA: Feasibility={scores.get('feasibility_score', 'N/A')} Impact={scores.get('impact_score', 'N/A')} Complexity={scores.get('complexity', 'N/A')}\n"
        )
        if competition_research:
            context += f"\nRICERCA COMPETITIVA:\n{competition_research}\n"

        start = time.time()
        try:
            response = claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2048,
                system=FEASIBILITY_ENGINE_PROMPT,
                messages=[{"role": "user", "content": f"Valuta. SOLO JSON:\n\n{context}"}]
            )
            duration = int((time.time() - start) * 1000)
            reply = response.content[0].text

            log_to_supabase("feasibility_engine", "analyze_feasibility", 2,
                f"Feasibility: {title[:100]}", reply[:500],
                "claude-haiku-4-5",
                response.usage.input_tokens, response.usage.output_tokens,
                (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
                duration)

            analysis = extract_json(reply)
        except Exception as e:
            logger.error(f"[FE ANALYSIS ERROR] {e}")
            analysis = None

        if not analysis:
            continue

        feasibility_score = feasibility_calculate_score(analysis)

        if feasibility_score < PIPELINE_THRESHOLDS["feasibility"]:
            logger.info(f"[FE] {title[:40]}: feasibility={feasibility_score:.2f} sotto soglia {PIPELINE_THRESHOLDS['feasibility']}")

        try:
            supabase.table("solutions").update({
                "feasibility_score": feasibility_score,
                "feasibility_details": json.dumps(analysis, ensure_ascii=False),
            }).eq("id", sol["id"]).execute()
            evaluated += 1
        except Exception as e:
            logger.error(f"[FE SAVE ERROR] {e}")
            continue

        decision = analysis.get("recommendation", {}).get("decision", "NO_GO")
        if decision == "GO":
            go_solutions.append({"title": title, "score": feasibility_score, "analysis": analysis, "sol_id": sol["id"]})
        elif decision == "CONDITIONAL_GO":
            conditional_solutions.append({"title": title, "score": feasibility_score, "analysis": analysis, "sol_id": sol["id"]})

        # Emit feasibility_completed event
        emit_event("feasibility_engine", "feasibility_completed", None,
            {"solution_id": str(sol["id"]), "score": feasibility_score, "decision": decision})

        # Calcola BOS
        bos_result = calculate_bos(sol["id"])
        if bos_result:
            logger.info(f"[FE] {title[:40]}: FE={feasibility_score:.2f} | {decision} | BOS={bos_result['bos_score']:.2f} {bos_result['verdict']}")

            # Emit bos_calculated event
            emit_event("feasibility_engine", "bos_calculated", None,
                {"solution_id": str(sol["id"]), "bos_score": bos_result["bos_score"], "verdict": bos_result["verdict"]})

            # Auto-cascade basata su verdict
            if bos_result["verdict"] == "AUTO-GO":
                emit_event("bos_engine", "auto_go", "project_builder",
                    {"solution_id": str(sol["id"]), "title": title, "bos": bos_result["bos_score"]}, "high")
            elif bos_result["verdict"] == "REVIEW":
                emit_event("bos_engine", "review_request", "command_center",
                    {"solution_id": str(sol["id"]), "title": title, "bos": bos_result["bos_score"]}, "high")
            else:
                emit_event("bos_engine", "archive", None,
                    {"solution_id": str(sol["id"]), "title": title, "bos": bos_result["bos_score"]})

        if notify and bos_result and bos_result["verdict"] in ("AUTO-GO", "REVIEW"):
            card = format_bos_card(title, bos_result)
            notify_telegram(card)

        time.sleep(1)

    if go_solutions:
        best = sorted(go_solutions, key=lambda x: x["score"], reverse=True)[0]
        emit_event("feasibility_engine", "solution_go", "project_builder",
            {"title": best["title"], "score": best["score"]}, "high")

    logger.info(f"Feasibility Engine completato: {evaluated} valutate, {len(go_solutions)} GO")
    return {
        "status": "completed",
        "evaluated": evaluated,
        "go": len(go_solutions),
        "conditional_go": len(conditional_solutions),
        "no_go": evaluated - len(go_solutions) - len(conditional_solutions),
    }


# ============================================================
# BOS — brAIn Opportunity Score
# ============================================================

BOS_SQ_WEIGHTS = {
    "uniqueness": 0.25, "moat_potential": 0.20, "value_multiplier": 0.20,
    "simplicity": 0.10, "revenue_clarity": 0.15, "ai_nativeness": 0.10,
}

BOS_FEAS_WEIGHTS = {
    "mvp_cost_score": 0.20, "time_to_market": 0.15, "ai_buildability": 0.15,
    "margin_potential": 0.20, "market_access": 0.15,
    "recurring_revenue": 0.10, "scalability": 0.05,
}

BOS_PARAM_NAMES = {
    "problem_quality": "Qualita problema",
    "sq_uniqueness": "Unicita", "sq_moat_potential": "Difendibilita",
    "sq_value_multiplier": "Valore/prezzo", "sq_simplicity": "Semplicita",
    "sq_revenue_clarity": "Chiarezza revenue", "sq_ai_nativeness": "AI-nativa",
    "fe_mvp_cost_score": "Costo MVP", "fe_time_to_market": "Velocita lancio",
    "fe_ai_buildability": "Costruibile con AI", "fe_margin_potential": "Potenziale margine",
    "fe_market_access": "Accesso mercato", "fe_recurring_revenue": "Revenue ricorrente",
    "fe_scalability": "Scalabilita",
}


def calculate_bos(solution_id):
    """BOS = Problem Quality (30%) + Solution Quality (30%) + Feasibility (40%)"""
    try:
        sol_result = supabase.table("solutions").select(
            "*, problems(weighted_score, title)"
        ).eq("id", solution_id).execute()
        if not sol_result.data:
            return None
        sol = sol_result.data[0]

        scores_result = supabase.table("solution_scores").select("*").eq("solution_id", solution_id).execute()
        scores = scores_result.data[0] if scores_result.data else {}
    except Exception as e:
        logger.error(f"[BOS] Recupero dati: {e}")
        return None

    problem = sol.get("problems", {}) or {}

    problem_quality = min(1.0, max(0.0, float(problem.get("weighted_score", 0) or 0)))

    notes = scores.get("notes", "{}")
    if isinstance(notes, str):
        try:
            notes_data = json.loads(notes)
        except:
            notes_data = {}
    else:
        notes_data = notes or {}

    solution_quality = 0.0
    sq_details = {}
    for param, weight in BOS_SQ_WEIGHTS.items():
        value = min(1.0, max(0.0, float(notes_data.get(param, 0.5))))
        sq_details[param] = round(value, 4)
        solution_quality += value * weight

    feasibility_details_raw = sol.get("feasibility_details", "{}")
    if isinstance(feasibility_details_raw, str):
        try:
            fe_data = json.loads(feasibility_details_raw)
        except:
            fe_data = {}
    else:
        fe_data = feasibility_details_raw or {}

    bos_feas = fe_data.get("bos_feasibility", {})

    feasibility_score = 0.0
    feas_details = {}
    for param, weight in BOS_FEAS_WEIGHTS.items():
        value = min(1.0, max(0.0, float(bos_feas.get(param, 0.5))))
        feas_details[param] = round(value, 4)
        feasibility_score += value * weight

    bos_raw = problem_quality * 0.30 + solution_quality * 0.30 + feasibility_score * 0.40
    # Scala non-lineare: penalizza mediocri, premia eccellenti (^1.3 sul BOS composito)
    bos = round(min(1.0, max(0.0, bos_raw)) ** 1.3, 4)

    # Soglia dinamica da DB: >= soglia_bos → AUTO-GO (notifica Mirco), altrimenti ARCHIVE
    thresholds = get_pipeline_thresholds()
    if bos >= thresholds["bos"]:
        verdict = "AUTO-GO"
    else:
        verdict = "ARCHIVE"

    all_params = {"problem_quality": problem_quality}
    for k, v in sq_details.items():
        all_params[f"sq_{k}"] = v
    for k, v in feas_details.items():
        all_params[f"fe_{k}"] = v

    sorted_params = sorted(all_params.items(), key=lambda x: x[1], reverse=True)
    top_strengths = [{"param": k, "value": round(v, 2)} for k, v in sorted_params[:3]]
    top_risks = [{"param": k, "value": round(v, 2)} for k, v in sorted_params[-2:]]

    bos_details = {
        "bos_score": bos, "verdict": verdict,
        "problem_quality": round(problem_quality, 4),
        "solution_quality": round(solution_quality, 4),
        "feasibility_score": round(feasibility_score, 4),
        "sq_details": sq_details, "feas_details": feas_details,
        "top_strengths": top_strengths, "top_risks": top_risks,
    }

    try:
        supabase.table("solutions").update({
            "bos_score": bos,
            "bos_details": json.dumps(bos_details, ensure_ascii=False),
        }).eq("id", solution_id).execute()
    except Exception as e:
        logger.error(f"[BOS] Salvataggio: {e}")

    logger.info(f"[BOS] {sol.get('title', '?')[:40]}: {bos:.2f} {verdict}")
    return bos_details


def check_bos_weekly_target():
    """Verifica target dinamico: solo il 10% dei BOS deve superare soglia_bos."""
    try:
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        result = supabase.table("solutions").select(
            "bos_score"
        ).not_.is_("bos_score", "null").gte("created_at", week_ago).execute()

        if not result.data:
            return None

        thresholds = get_pipeline_thresholds()
        scores = [float(s["bos_score"]) for s in result.data]
        total = len(scores)
        above_threshold = sum(1 for s in scores if s >= thresholds["bos"])
        pct_above = round(above_threshold / total * 100, 1) if total > 0 else 0

        stats = {
            "total_bos": total,
            "above_threshold": above_threshold,
            "pct_above": pct_above,
            "target_pct": 10.0,
            "on_target": pct_above <= 10.0,
            "avg_bos": round(sum(scores) / total, 3) if total else 0,
        }

        if not stats["on_target"]:
            logger.warning(f"[BOS TARGET] {pct_above}% sopra soglia (target: 10%). "
                f"{above_threshold}/{total} BOS >= {thresholds['bos']}")

        return stats
    except Exception as e:
        logger.error(f"[BOS TARGET] Errore: {e}")
        return None


def format_bos_card(solution_title, bos_details):
    """Card BOS compatta — formato decimale leggibile su mobile."""
    bos = bos_details["bos_score"]
    verdict = bos_details["verdict"]
    pq = bos_details["problem_quality"]
    sq = bos_details["solution_quality"]
    fe = bos_details["feasibility_score"]

    lines = [
        f"BOS: {solution_title}",
        "",
        f"Score: {bos:.2f} | {verdict}",
        "",
        f"Problem:     {pq:.2f} (x0.30)",
        f"Solution:    {sq:.2f} (x0.30)",
        f"Feasibility: {fe:.2f} (x0.40)",
        "",
        "FORZE:",
    ]

    for s in bos_details["top_strengths"]:
        name = BOS_PARAM_NAMES.get(s["param"], s["param"])
        lines.append(f"  + {name}: {s['value']:.2f}")

    lines.append("")
    lines.append("RISCHI:")
    for r in bos_details["top_risks"]:
        name = BOS_PARAM_NAMES.get(r["param"], r["param"])
        lines.append(f"  - {name}: {r['value']:.2f}")

    return "\n".join(lines)


def run_bos_endpoint_logic(solution_id=None):
    if solution_id:
        result = calculate_bos(solution_id)
        if result:
            sol_result = supabase.table("solutions").select("title").eq("id", solution_id).execute()
            title = sol_result.data[0]["title"] if sol_result.data else "?"
            card = format_bos_card(title, result)
            notify_telegram(card)
            return {"status": "completed", "bos": result}
        return {"status": "error", "error": "calcolo fallito"}

    try:
        sols = supabase.table("solutions").select("id, title").not_.is_("feasibility_details", "null").is_("bos_score", "null").limit(50).execute()
        solutions = sols.data or []
    except Exception as e:
        return {"status": "error", "error": str(e)}

    calculated = 0
    for sol in solutions:
        result = calculate_bos(sol["id"])
        if result:
            calculated += 1
            if result["verdict"] in ("AUTO-GO", "REVIEW"):
                card = format_bos_card(sol["title"], result)
                notify_telegram(card)

    return {"status": "completed", "calculated": calculated}


# ============================================================
# PIPELINE AUTOMATICA v2.0 — 4 step con soglie dinamiche
# Mirco vede SOLO il BOS finale se supera la soglia.
# Nessuna notifica intermedia per problema, soluzione, feasibility.
# ============================================================

def enqueue_bos_action(problem_id, solution_id, problem_title, sol_title, sol_desc, bos_score, bos_data):
    """Inserisce azione approve_bos in action_queue e notifica Mirco con il formato BOS standard."""
    chat_id = get_telegram_chat_id()
    if not chat_id:
        logger.warning("[BOS ACTION] telegram_chat_id non trovato in org_config")
        return

    # Conta azioni pending per determinare [N in coda]
    try:
        pending = supabase.table("action_queue").select("id", count="exact") \
            .eq("user_id", int(chat_id)).eq("status", "pending").execute()
        pending_count = (pending.count or 0) + 1
    except:
        pending_count = 1

    # Descrizione per action_queue (dettagliata, usata da "Dettagli")
    desc_detail = (
        f"Soluzione: {sol_title}\n"
        f"Score BOS: {bos_score:.2f}/1\n"
        f"Problem quality: {bos_data.get('problem_quality', 0):.2f} | "
        f"Solution quality: {bos_data.get('solution_quality', 0):.2f} | "
        f"Feasibility: {bos_data.get('feasibility_score', 0):.2f}\n"
        f"{(sol_desc or '')[:400]}"
    )

    # Inserisci in action_queue
    action_db_id = None
    try:
        result = supabase.table("action_queue").insert({
            "user_id": int(chat_id),
            "action_type": "approve_bos",
            "title": f"BOS PRONTO \u2014 {problem_title[:60]}",
            "description": desc_detail,
            "payload": json.dumps({
                "problem_id": str(problem_id),
                "solution_id": str(solution_id),
                "bos_score": bos_score,
                "problem_title": problem_title[:80],
                "sol_title": sol_title[:80],
            }),
            "priority": 9,
            "urgency": 9,
            "importance": 9,
            "status": "pending",
        }).execute()
        if result.data:
            action_db_id = result.data[0]["id"]
    except Exception as e:
        logger.error(f"[BOS ACTION] enqueue error: {e}")
        return

    # Notifica Mirco con inline keyboard — Fix 3
    sep = "\u2501" * 15
    desc_2lines = "\n".join((sol_desc or "Descrizione non disponibile").split("\n")[:2])[:200]
    msg = (
        f"\u26a1 AZIONE RICHIESTA [{pending_count} in coda]\n"
        f"{sep}\n"
        f"\U0001f3af BOS PRONTO \u2014 {problem_title[:60]}\n"
        f"Score: {bos_score:.2f}/1 | Soluzione: {sol_title[:50]}\n"
        f"{desc_2lines}\n"
        f"{sep}"
    )
    bos_reply_markup = {
        "inline_keyboard": [[
            {"text": "\u2705 Approva", "callback_data": f"bos_approve:{solution_id}:{action_db_id}"},
            {"text": "\u274c Rifiuta", "callback_data": f"bos_reject:{solution_id}:{action_db_id}"},
            {"text": "\U0001f50d Dettagli", "callback_data": f"bos_detail:{action_db_id}"},
        ]]
    }
    chat_id_direct = get_telegram_chat_id()
    if chat_id_direct and TELEGRAM_BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id_direct, "text": msg, "reply_markup": bos_reply_markup},
                timeout=10,
            )
        except Exception as e:
            logger.error(f"[BOS ACTION] sendMessage error: {e}")
            notify_telegram(msg, level="critical", source="pipeline")
    else:
        notify_telegram(msg, level="critical", source="pipeline")

    # Informa il Command Center di caricare questa azione come current_action
    if COMMAND_CENTER_URL and action_db_id:
        try:
            requests.post(
                f"{COMMAND_CENTER_URL}/action/set",
                json={"chat_id": str(chat_id), "action_id": str(action_db_id)},
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"[BOS ACTION] /action/set error (non critico): {e}")

    logger.info(f"[BOS ACTION] Enqueued id={action_db_id} per '{problem_title[:50]}' BOS={bos_score:.2f}")


def run_auto_pipeline(saved_problem_ids):
    """Pipeline automatica: problema → SA (best solution) → FE → BOS → approve_bos action.
    Mirco riceve notifica SOLO se BOS >= soglia_bos. Zero notifiche intermedie."""
    if not saved_problem_ids:
        return

    logger.info(f"[PIPELINE] Avvio per {len(saved_problem_ids)} problemi")
    log_to_supabase("pipeline", "auto_pipeline_start", 0,
        f"{len(saved_problem_ids)} problemi", None, "none")

    thresholds = get_pipeline_thresholds()
    pipeline_start = time.time()
    bos_generated = 0
    bos_approved = 0

    for pid in saved_problem_ids:
        try:
            prob_result = supabase.table("problems").select("*").eq("id", pid).execute()
            if not prob_result.data:
                continue
            problem = prob_result.data[0]
            problem_score = float(problem.get("weighted_score", 0) or 0)
            problem_title = problem.get("title", "?")

            # STEP 1: verifica soglia problema (già filtrata in run_scan, ricontrollo difensivo)
            if problem_score < thresholds["problema"]:
                logger.info(f"[PIPELINE] '{problem_title[:50]}': score={problem_score:.2f} < soglia_problema={thresholds['problema']:.2f} → archived")
                supabase.table("problems").update({"status": "archived", "status_detail": "archived"}).eq("id", pid).execute()
                continue

            # STEP 2: Solution Architect — genera 3 soluzioni
            dossier = research_problem(problem)
            if not dossier:
                dossier = {"existing_solutions": [], "market_gaps": ["nessun dato"],
                    "failed_attempts": [], "expert_insights": [],
                    "market_size_estimate": "sconosciuto", "key_finding": "ricerca non disponibile"}

            solutions_data = generate_solutions_unconstrained(problem, dossier)
            if not solutions_data or not solutions_data.get("solutions"):
                logger.warning(f"[PIPELINE] SA generazione fallita per '{problem_title[:50]}'")
                continue

            ranking_rationale = solutions_data.get("ranking_rationale", "")
            feasibility_data = assess_feasibility(problem, solutions_data)
            if not feasibility_data:
                feasibility_data = {"assessments": [], "best_feasible": "", "best_overall": ""}

            feas_map = {}
            for a in feasibility_data.get("assessments", []):
                feas_map[a.get("solution_title", "")] = a

            # Salva tutte e 3 le soluzioni, trova quella con overall_score più alto
            best_sol_id = None
            best_overall = 0.0
            for sol in solutions_data.get("solutions", []):
                sol_title = sol.get("title", "")
                assessment = feas_map.get(sol_title, {
                    "feasibility_score": 0.5, "complexity": "medium",
                    "time_to_mvp": "sconosciuto", "cost_estimate": "sconosciuto",
                    "tech_stack_fit": 0.5, "biggest_risk": "non valutato",
                    "recommended_mvp": "non valutato", "nocode_compatible": True,
                })
                sol_id, overall = save_solution_v2(problem["id"], sol, assessment, ranking_rationale, dossier)
                if sol_id and overall > best_overall:
                    best_overall = overall
                    best_sol_id = sol_id

            # Verifica soglia soluzione (best overall_score)
            if not best_sol_id or best_overall < thresholds["soluzione"]:
                logger.info(f"[PIPELINE] '{problem_title[:50]}': best_overall={best_overall:.2f} < soglia_soluzione={thresholds['soluzione']:.2f} → archived silenziosamente")
                supabase.table("problems").update({"status": "archived", "status_detail": "archived"}).eq("id", pid).execute()
                time.sleep(1)
                continue

            # STEP 3: Feasibility Engine — solo sulla migliore soluzione
            run_feasibility_engine(solution_id=best_sol_id, notify=False)

            # Rileggi feasibility_score aggiornato
            sol_row = supabase.table("solutions").select(
                "feasibility_score, title, description"
            ).eq("id", best_sol_id).execute()
            if not sol_row.data:
                continue
            fe_score = float(sol_row.data[0].get("feasibility_score", 0) or 0)
            sol_title = sol_row.data[0].get("title", "?")
            sol_desc = sol_row.data[0].get("description", "")

            if fe_score < thresholds["feasibility"]:
                logger.info(f"[PIPELINE] '{sol_title[:50]}': fe_score={fe_score:.2f} < soglia_feasibility={thresholds['feasibility']:.2f} → archived silenziosamente")
                supabase.table("problems").update({"status": "archived", "status_detail": "archived"}).eq("id", pid).execute()
                time.sleep(1)
                continue

            # STEP 4: BOS — calcola e notifica Mirco solo se >= soglia_bos
            bos_data = calculate_bos(best_sol_id)
            if not bos_data:
                continue

            bos_score = bos_data["bos_score"]
            bos_generated += 1

            if bos_score >= thresholds["bos"]:
                bos_approved += 1
                enqueue_bos_action(pid, best_sol_id, problem_title, sol_title, sol_desc, bos_score, bos_data)
            else:
                logger.info(f"[PIPELINE] '{sol_title[:50]}': BOS={bos_score:.2f} < soglia_bos={thresholds['bos']:.2f} → archived silenziosamente")
                supabase.table("problems").update({"status": "archived", "status_detail": "archived"}).eq("id", pid).execute()

            time.sleep(2)

        except Exception as e:
            logger.error(f"[PIPELINE] Error pid={pid}: {e}")

    pipeline_duration = int(time.time() - pipeline_start)

    log_to_supabase("pipeline", "auto_pipeline_complete", 0,
        f"{len(saved_problem_ids)} problemi → {bos_generated} BOS → {bos_approved} notifiche Mirco",
        f"soglie: P={thresholds['problema']} S={thresholds['soluzione']} F={thresholds['feasibility']} BOS={thresholds['bos']}",
        "none", 0, 0, 0, pipeline_duration * 1000)

    logger.info(f"[PIPELINE] Completata in {pipeline_duration}s: {bos_approved}/{bos_generated} BOS notificati a Mirco")


# ============================================================
# KNOWLEDGE KEEPER v1.1
# ============================================================

KNOWLEDGE_PROMPT = """Sei il Knowledge Keeper di brAIn.
Analizza i log degli agenti e estrai lezioni apprese.

LINGUA: Rispondi SEMPRE in italiano. Titoli e contenuti delle lezioni in italiano.

Rispondi SOLO con JSON:
{"lessons":[{"title":"titolo","content":"descrizione","category":"process","actionable":"azione"}],"patterns":[{"pattern":"descrizione","frequency":"quanto"}],"summary":"riassunto breve"}

Categorie: process, technical, strategic, cost, performance.
SOLO JSON."""


def run_knowledge_keeper():
    logger.info("Knowledge Keeper v1.1 starting...")

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        logs = supabase.table("agent_logs").select("*").gte("created_at", since).order("created_at", desc=True).limit(50).execute()
        logs = logs.data or []
    except:
        logs = []

    if not logs:
        return {"status": "no_logs", "saved": 0}

    simple_logs = [{
        "agent": l.get("agent_id"), "action": l.get("action"),
        "status": l.get("status"), "model": l.get("model_used"),
        "tokens_in": l.get("tokens_input"), "tokens_out": l.get("tokens_output"),
        "cost": l.get("cost_usd"), "duration_ms": l.get("duration_ms"),
        "error": l.get("error"), "time": l.get("created_at"),
    } for l in logs]

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=KNOWLEDGE_PROMPT,
            messages=[{"role": "user", "content": f"Analizza SOLO JSON:\n\n{json.dumps(simple_logs, default=str)}"}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("knowledge_keeper", "analyze_logs", 5,
            f"Analizzati {len(logs)} log", reply[:500],
            "claude-haiku-4-5",
            response.usage.input_tokens, response.usage.output_tokens,
            (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            duration)

        data = extract_json(reply)
        saved = 0
        if data:
            for lesson in data.get("lessons", []):
                try:
                    supabase.table("org_knowledge").insert({
                        "title": lesson.get("title", ""),
                        "content": lesson.get("content", ""),
                        "category": lesson.get("category", "general"),
                        "source": "knowledge_keeper_v1",
                    }).execute()
                    saved += 1
                except:
                    pass

            for p in data.get("patterns", []):
                if "error" in p.get("pattern", "").lower() or "fail" in p.get("pattern", "").lower():
                    emit_event("knowledge_keeper", "error_pattern_detected", None,
                        {"pattern": p["pattern"]}, "high")
                    notify_telegram(f"Pattern di errore rilevato: {p['pattern']}")

        return {"status": "completed", "saved": saved}

    except Exception as e:
        logger.error(f"[KK ERROR] {e}")
        return {"status": "error", "error": str(e)}


# ============================================================
# CAPABILITY SCOUT v1.1
# ============================================================

SCOUT_TOPICS = [
    "new AI agent frameworks tools 2025 2026",
    "Claude API new features updates 2026",
    "best no-code AI automation tools 2026",
    "Supabase new features updates 2026",
    "open source AI tools for startups 2026",
]

SCOUT_PROMPT = """Sei il Capability Scout di brAIn.
brAIn usa: Claude API (Haiku/Sonnet), Supabase, Python, Google Cloud Run, Telegram Bot.
Budget: 1000 euro/mese. Preferenza: no-code o low-code.

Seleziona SOLO le 3-5 scoperte piu rilevanti.

Rispondi SOLO con JSON:
{"discoveries":[{"tool_name":"nome","category":"ai_model","description":"cosa fa","potential_impact":"come aiuta brAIn","cost":"stima","relevance":"high","action":"evaluate"}],"summary":"riassunto"}
SOLO JSON."""


def run_capability_scout():
    logger.info("Capability Scout v1.1 starting...")

    search_results = []
    for topic in SCOUT_TOPICS:
        result = search_perplexity(topic)
        if result:
            search_results.append((topic, result))
        time.sleep(1)

    if not search_results:
        return {"status": "no_results", "saved": 0}

    combined = "\n\n---\n\n".join([f"Topic: {t}\nResults: {r}" for t, r in search_results])

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            system=SCOUT_PROMPT,
            messages=[{"role": "user", "content": f"Analizza SOLO JSON:\n\n{combined}"}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("capability_scout", "analyze_discoveries", 5,
            f"Analizzati {len(search_results)} topic", reply[:500],
            "claude-haiku-4-5",
            response.usage.input_tokens, response.usage.output_tokens,
            (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            duration)

        data = extract_json(reply)
        saved = 0
        if data:
            for disc in data.get("discoveries", []):
                if disc.get("relevance") in ("high", "medium"):
                    try:
                        status = "evaluating" if disc.get("action") in ("adopt", "evaluate") else "discovered"
                        supabase.table("capability_log").insert({
                            "tool_name": disc.get("tool_name", ""),
                            "category": disc.get("category", "other"),
                            "description": disc.get("description", ""),
                            "potential_impact": disc.get("potential_impact", ""),
                            "cost": disc.get("cost", "unknown"),
                            "status": status,
                        }).execute()
                        saved += 1

                        if disc.get("relevance") == "high" and disc.get("action") == "adopt":
                            emit_event("capability_scout", "high_impact_tool", None,
                                {"tool": disc["tool_name"], "impact": disc.get("potential_impact", "")}, "high")
                            notify_telegram(f"Tool consigliato: {disc['tool_name']}\n{disc.get('potential_impact', '')}")
                    except:
                        pass

        return {"status": "completed", "saved": saved}

    except Exception as e:
        logger.error(f"[SCOUT ERROR] {e}")
        return {"status": "error", "error": str(e)}


# ============================================================
# FINANCE AGENT v2.0 — CFO AI ENTERPRISE
# ============================================================

MONTHLY_BUDGET_EUR = 1000.0
DEFAULT_USD_TO_EUR = 0.92
DAILY_COST_ALERT_USD = 5.0
BUDGET_ALERT_PCT = 70.0
FIXED_COSTS_MONTHLY_EUR = {
    "claude_max": 100.0,
    "supabase": 25.0,
    "perplexity": 15.0,
}
FIXED_COSTS_TOTAL_EUR = sum(FIXED_COSTS_MONTHLY_EUR.values())  # 140 EUR/mese
FIXED_COSTS_DAILY_EUR = round(FIXED_COSTS_TOTAL_EUR / 30, 2)   # ~4.67 EUR/giorno


# ---------- DATA FETCHING ----------

def finance_get_usd_to_eur():
    """Ritorna tasso USD→EUR. Cache mensile in exchange_rates. Fallback 0.92."""
    try:
        # Controlla cache in exchange_rates (valida 30 giorni)
        result = supabase.table("exchange_rates").select("rate,fetched_at").eq(
            "from_currency", "USD").eq("to_currency", "EUR").order(
            "fetched_at", desc=True).limit(1).execute()
        if result.data:
            fetched_at = result.data[0]["fetched_at"]
            # Parsifica e controlla se < 30 giorni
            if isinstance(fetched_at, str):
                fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            else:
                fetched_dt = fetched_at
            age_days = (datetime.now(timezone.utc) - fetched_dt).days
            if age_days < 30:
                return float(result.data[0]["rate"])
    except Exception as e:
        logger.warning(f"[FINANCE] Lettura exchange_rates fallita: {e}")

    # Fetch da frankfurter.app
    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=EUR",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            rate = float(data["rates"]["EUR"])
            # Salva in exchange_rates
            try:
                supabase.table("exchange_rates").insert({
                    "from_currency": "USD",
                    "to_currency": "EUR",
                    "rate": rate,
                }).execute()
            except Exception as save_err:
                logger.warning(f"[FINANCE] Salvataggio exchange_rates fallito: {save_err}")
            logger.info(f"[FINANCE] Tasso USD→EUR aggiornato: {rate}")
            return rate
    except Exception as e:
        logger.warning(f"[FINANCE] Fetch frankfurter.app fallito: {e}")

    return DEFAULT_USD_TO_EUR


def _paginated_logs(select, filters_fn=None, limit_per_page=1000):
    """Fetch all agent_logs with pagination (bypasses 1000-row limit)."""
    all_data = []
    offset = 0
    while True:
        q = supabase.table("agent_logs").select(select)
        if filters_fn:
            q = filters_fn(q)
        result = q.range(offset, offset + limit_per_page - 1).execute()
        if not result.data:
            break
        all_data.extend(result.data)
        if len(result.data) < limit_per_page:
            break
        offset += limit_per_page
    return all_data


def finance_get_daily_costs(date_str):
    """Costi aggregati per un singolo giorno."""
    day_start = f"{date_str}T00:00:00+00:00"
    day_end = f"{date_str}T23:59:59+00:00"
    try:
        logs = _paginated_logs(
            "agent_id,action,cost_usd,tokens_input,tokens_output,model_used,status",
            lambda q: q.gte("created_at", day_start).lte("created_at", day_end),
        )
    except Exception as e:
        logger.error(f"[FINANCE] {e}")
        return None

    total_cost = 0.0
    total_calls = 0
    successful = 0
    failed = 0
    tokens_in = 0
    tokens_out = 0
    cost_by_agent = {}
    calls_by_agent = {}
    cost_by_action = {}
    cost_by_model = {}

    for log in logs:
        agent = log.get("agent_id", "unknown")
        action = log.get("action", "unknown")
        model = log.get("model_used", "unknown")
        cost = float(log.get("cost_usd", 0) or 0)
        total_cost += cost
        total_calls += 1
        tokens_in += int(log.get("tokens_input", 0) or 0)
        tokens_out += int(log.get("tokens_output", 0) or 0)
        if log.get("status") == "success":
            successful += 1
        else:
            failed += 1
        cost_by_agent[agent] = cost_by_agent.get(agent, 0.0) + cost
        calls_by_agent[agent] = calls_by_agent.get(agent, 0) + 1
        cost_by_action[action] = cost_by_action.get(action, 0.0) + cost
        cost_by_model[model] = cost_by_model.get(model, 0.0) + cost

    return {
        "date": date_str,
        "total_cost_usd": round(total_cost, 6),
        "total_calls": total_calls,
        "successful_calls": successful,
        "failed_calls": failed,
        "total_tokens_in": tokens_in,
        "total_tokens_out": tokens_out,
        "cost_by_agent": {k: round(v, 6) for k, v in cost_by_agent.items()},
        "calls_by_agent": calls_by_agent,
        "cost_by_action": {k: round(v, 6) for k, v in cost_by_action.items()},
        "cost_by_model": {k: round(v, 6) for k, v in cost_by_model.items()},
    }


def finance_get_range_costs(start_date, end_date):
    """Costi totali per un range di date. Ritorna dict come finance_get_daily_costs."""
    try:
        logs = _paginated_logs(
            "agent_id,action,cost_usd,tokens_input,tokens_output,model_used,status",
            lambda q: q.gte("created_at", f"{start_date}T00:00:00+00:00")
                       .lte("created_at", f"{end_date}T23:59:59+00:00"),
        )
    except:
        return None
    total_cost = sum(float(l.get("cost_usd", 0) or 0) for l in logs)
    cost_by_agent = {}
    for l in logs:
        a = l.get("agent_id", "unknown")
        cost_by_agent[a] = cost_by_agent.get(a, 0.0) + float(l.get("cost_usd", 0) or 0)
    return {
        "total_cost_usd": round(total_cost, 6),
        "total_calls": len(logs),
        "cost_by_agent": {k: round(v, 6) for k, v in cost_by_agent.items()},
    }


def finance_get_all_time_costs():
    """Costi totali DA SEMPRE. Usa agent_logs con paginazione."""
    try:
        logs = _paginated_logs("cost_usd")
    except:
        return 0.0
    return round(sum(float(l.get("cost_usd", 0) or 0) for l in logs), 4)


def finance_get_daily_series(days):
    """Array di costi giornalieri per gli ultimi N giorni. Ritorna [(date_str, cost_usd), ...]."""
    now = datetime.now(timezone.utc)
    series = []
    for i in range(days, 0, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        costs = finance_get_daily_costs(d)
        series.append((d, costs["total_cost_usd"] if costs else 0.0))
    return series


def finance_get_month_costs(year, month):
    """Costi variabili totali del mese (USD)."""
    first_day = f"{year}-{month:02d}-01"
    _, last = monthrange(year, month)
    last_day = f"{year}-{month:02d}-{last}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    end = min(last_day, today)
    data = finance_get_range_costs(first_day, end)
    return data["total_cost_usd"] if data else 0.0


# ---------- CASH FLOW INTELLIGENCE ----------

def finance_burn_rates():
    """Burn rate: oggi, media 7gg, media 30gg (tutto in USD variabili)."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    today_costs = finance_get_daily_costs(today)
    today_usd = today_costs["total_cost_usd"] if today_costs else 0.0

    # Media 7 giorni
    start_7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    data_7 = finance_get_range_costs(start_7, today)
    avg_7 = round(data_7["total_cost_usd"] / 7, 4) if data_7 else 0.0

    # Media 30 giorni
    start_30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    data_30 = finance_get_range_costs(start_30, today)
    avg_30 = round(data_30["total_cost_usd"] / 30, 4) if data_30 else 0.0

    return {"today_usd": today_usd, "avg_7d_usd": avg_7, "avg_30d_usd": avg_30}


def finance_projections(usd_to_eur):
    """Proiezione costi variabili a 30, 60, 90 giorni (EUR) basata su media 7gg."""
    rates = finance_burn_rates()
    daily = rates["avg_7d_usd"] if rates["avg_7d_usd"] > 0 else rates["avg_30d_usd"]
    daily_eur = daily * usd_to_eur
    return {
        "30d_eur": round(daily_eur * 30 + FIXED_COSTS_TOTAL_EUR, 2),
        "60d_eur": round(daily_eur * 60 + FIXED_COSTS_TOTAL_EUR * 2, 2),
        "90d_eur": round(daily_eur * 90 + FIXED_COSTS_TOTAL_EUR * 3, 2),
        "daily_variable_eur": round(daily_eur, 4),
        "daily_total_eur": round(daily_eur + FIXED_COSTS_DAILY_EUR, 4),
        "burn_rates": rates,
    }


def finance_runway(usd_to_eur):
    """Giorni di runway rimanenti con budget mensile 1000 EUR."""
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    _, days_in_month = monthrange(year, month)
    days_elapsed = now.day

    month_variable_usd = finance_get_month_costs(year, month)
    month_variable_eur = month_variable_usd * usd_to_eur
    # Costi fissi proporzionali ai giorni trascorsi
    month_fixed_eur = FIXED_COSTS_DAILY_EUR * days_elapsed
    month_total_eur = month_variable_eur + month_fixed_eur

    budget_remaining = MONTHLY_BUDGET_EUR - month_total_eur
    daily_total_eur = (month_total_eur / days_elapsed) if days_elapsed > 0 else 0

    if daily_total_eur > 0:
        runway_days = int(budget_remaining / daily_total_eur)
    else:
        runway_days = days_in_month - days_elapsed

    # Proiezione fine mese
    projected_month_eur = round(daily_total_eur * days_in_month, 2) if days_elapsed > 0 else 0
    budget_pct = round((projected_month_eur / MONTHLY_BUDGET_EUR) * 100, 1) if MONTHLY_BUDGET_EUR > 0 else 0

    return {
        "days_remaining": max(runway_days, 0),
        "budget_remaining_eur": round(budget_remaining, 2),
        "month_spent_eur": round(month_total_eur, 2),
        "month_variable_eur": round(month_variable_eur, 2),
        "month_fixed_eur": round(month_fixed_eur, 2),
        "projected_month_eur": projected_month_eur,
        "budget_pct": budget_pct,
        "daily_total_eur": round(daily_total_eur, 4),
    }


# ---------- COST PER VALUE ----------

def finance_cost_per_value(days=30):
    """Costo per problema trovato, soluzione generata, BOS calcolato."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # Costi per agente nel periodo
    range_data = finance_get_range_costs(since, now.strftime("%Y-%m-%d"))
    agent_costs = range_data["cost_by_agent"] if range_data else {}

    scanner_cost = agent_costs.get("world_scanner", 0)
    sa_cost = agent_costs.get("solution_architect", 0)
    fe_cost = agent_costs.get("feasibility_engine", 0)
    bos_cost = agent_costs.get("bos_scorer", 0)

    # Conteggi valore prodotto
    since_ts = f"{since}T00:00:00+00:00"
    try:
        problems = supabase.table("problems").select("id", count="exact").gte("created_at", since_ts).execute()
        n_problems = problems.count or 0
    except:
        n_problems = 0
    try:
        solutions = supabase.table("solutions").select("id", count="exact").gte("created_at", since_ts).execute()
        n_solutions = solutions.count or 0
    except:
        n_solutions = 0
    try:
        bos_done = supabase.table("solutions").select("id", count="exact") \
            .gte("created_at", since_ts).not_.is_("bos_score", "null").execute()
        n_bos = bos_done.count or 0
    except:
        n_bos = 0

    cost_per_problem = round(scanner_cost / n_problems, 4) if n_problems > 0 else 0
    cost_per_solution = round((sa_cost + fe_cost) / n_solutions, 4) if n_solutions > 0 else 0
    cost_per_bos = round(bos_cost / n_bos, 4) if n_bos > 0 else 0
    total_value_cost = scanner_cost + sa_cost + fe_cost + bos_cost

    # Efficienza per agente: valore prodotto vs costo
    efficiency = {}
    if scanner_cost > 0 and n_problems > 0:
        efficiency["world_scanner"] = {"output": f"{n_problems} problemi", "cost_usd": round(scanner_cost, 4),
                                        "unit_cost": cost_per_problem}
    if sa_cost > 0 and n_solutions > 0:
        efficiency["solution_architect"] = {"output": f"{n_solutions} soluzioni", "cost_usd": round(sa_cost, 4),
                                             "unit_cost": cost_per_solution}
    if bos_cost > 0 and n_bos > 0:
        efficiency["bos_scorer"] = {"output": f"{n_bos} BOS", "cost_usd": round(bos_cost, 4),
                                     "unit_cost": cost_per_bos}

    return {
        "period_days": days,
        "cost_per_problem": cost_per_problem,
        "cost_per_solution": cost_per_solution,
        "cost_per_bos": cost_per_bos,
        "n_problems": n_problems,
        "n_solutions": n_solutions,
        "n_bos": n_bos,
        "total_value_cost": round(total_value_cost, 4),
        "efficiency": efficiency,
    }


# ---------- OTTIMIZZAZIONE ATTIVA ----------

def finance_optimization_suggestions(days=7):
    """Analizza log e suggerisce ottimizzazioni con risparmio stimato."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        logs = _paginated_logs(
            "agent_id,action,model_used,tokens_input,tokens_output,cost_usd",
            lambda q: q.gte("created_at", f"{since}T00:00:00+00:00")
                       .eq("status", "success"),
        )
    except:
        return []

    suggestions = []
    haiku_cost_in = 1.0 / 1_000_000   # $/token Haiku input
    haiku_cost_out = 5.0 / 1_000_000   # $/token Haiku output
    sonnet_cost_in = 3.0 / 1_000_000
    sonnet_cost_out = 15.0 / 1_000_000

    # 1. Prompt lunghi: token_input > 5000
    long_prompts = [l for l in logs if int(l.get("tokens_input", 0) or 0) > 5000]
    if long_prompts:
        by_action = {}
        for l in long_prompts:
            k = f"{l['agent_id']}/{l['action']}"
            by_action.setdefault(k, []).append(int(l.get("tokens_input", 0) or 0))
        worst = sorted(by_action.items(), key=lambda x: sum(x[1]), reverse=True)[:3]
        for action, tokens_list in worst:
            avg_tokens = sum(tokens_list) // len(tokens_list)
            potential_save = sum(tokens_list) * (sonnet_cost_in - haiku_cost_in) * 0.3  # 30% reduction
            suggestions.append({
                "type": "prompt_lungo",
                "target": action,
                "detail": f"Media {avg_tokens:,} token input ({len(tokens_list)} chiamate)",
                "saving_usd": round(potential_save, 4),
            })

    # 2. Chiamate Sonnet che potrebbero essere Haiku
    sonnet_actions = {}
    for l in logs:
        model = l.get("model_used", "")
        if "sonnet" in model.lower():
            action = f"{l['agent_id']}/{l['action']}"
            t_in = int(l.get("tokens_input", 0) or 0)
            t_out = int(l.get("tokens_output", 0) or 0)
            cost = float(l.get("cost_usd", 0) or 0)
            sonnet_actions.setdefault(action, {"count": 0, "total_cost": 0, "t_in": 0, "t_out": 0})
            sonnet_actions[action]["count"] += 1
            sonnet_actions[action]["total_cost"] += cost
            sonnet_actions[action]["t_in"] += t_in
            sonnet_actions[action]["t_out"] += t_out

    # Azioni Sonnet ad alto volume che NON sono generazione critica
    critical_actions = {"generate", "research", "feasibility", "bos"}
    for action, data in sorted(sonnet_actions.items(), key=lambda x: x[1]["total_cost"], reverse=True)[:3]:
        action_name = action.split("/")[-1] if "/" in action else action
        if not any(c in action_name.lower() for c in critical_actions):
            haiku_equiv = data["t_in"] * haiku_cost_in + data["t_out"] * haiku_cost_out
            saving = data["total_cost"] - haiku_equiv
            if saving > 0.001:
                suggestions.append({
                    "type": "downgrade_haiku",
                    "target": action,
                    "detail": f"{data['count']} chiamate Sonnet, possibile switch a Haiku",
                    "saving_usd": round(saving, 4),
                })

    # 3. Chiamate ridondanti: stesso agente+azione in < 60 secondi (detect from duplicates)
    action_counts = {}
    for l in logs:
        k = f"{l['agent_id']}/{l['action']}"
        action_counts[k] = action_counts.get(k, 0) + 1
    avg_per_action = sum(action_counts.values()) / len(action_counts) if action_counts else 0
    for action, count in action_counts.items():
        if count > avg_per_action * 3 and count > 10:
            per_call_cost = sum(float(l.get("cost_usd", 0) or 0) for l in logs
                                if f"{l['agent_id']}/{l['action']}" == action) / count
            potential_reduction = int(count * 0.3)  # assume 30% are redundant
            suggestions.append({
                "type": "chiamate_ridondanti",
                "target": action,
                "detail": f"{count} chiamate in {days}gg (media {avg_per_action:.0f}), possibile riduzione 30%",
                "saving_usd": round(potential_reduction * per_call_cost, 4),
            })

    total_saving = sum(s["saving_usd"] for s in suggestions)
    if suggestions:
        suggestions.append({"type": "totale", "detail": f"Risparmio stimato totale", "saving_usd": round(total_saving, 4)})

    return suggestions


# ---------- ANOMALY DETECTION ----------

def finance_detect_anomalies():
    """IQR su costi giornalieri + 3-sigma per agente."""
    alerts = []

    # Ultimi 30 giorni di costi giornalieri
    series = finance_get_daily_series(30)
    costs = [c for _, c in series if c > 0]

    if len(costs) >= 7:
        sorted_costs = sorted(costs)
        n = len(sorted_costs)
        q1 = sorted_costs[n // 4]
        q3 = sorted_costs[3 * n // 4]
        iqr = q3 - q1
        upper_fence = q3 + 1.5 * iqr

        # Controlla oggi
        today_cost = series[-1][1] if series else 0
        if today_cost > upper_fence and today_cost > 0:
            alerts.append({
                "type": "iqr_daily",
                "message": f"Costo oggi ${today_cost:.4f} supera soglia IQR ${upper_fence:.4f} (Q3={q3:.4f} + 1.5*IQR={iqr:.4f})",
                "severity": "warning",
            })

    # 3-sigma per agente (ultimi 30gg aggregati per agente per giorno)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        logs = _paginated_logs(
            "agent_id,cost_usd,created_at",
            lambda q: q.gte("created_at", f"{since}T00:00:00+00:00"),
        )
    except:
        logs = []

    # Aggrega per agente per giorno
    agent_daily = {}
    for l in logs:
        agent = l.get("agent_id", "unknown")
        day = l.get("created_at", "")[:10]
        cost = float(l.get("cost_usd", 0) or 0)
        agent_daily.setdefault(agent, {})
        agent_daily[agent][day] = agent_daily[agent].get(day, 0) + cost

    for agent, daily_map in agent_daily.items():
        values = list(daily_map.values())
        if len(values) < 7:
            continue
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance) if variance > 0 else 0
        threshold = mean + 3 * std

        # Controlla ultimo giorno
        today = now.strftime("%Y-%m-%d")
        today_val = daily_map.get(today, 0)
        if today_val > threshold and std > 0 and today_val > 0.01:
            alerts.append({
                "type": "3sigma_agent",
                "message": f"Agente {agent}: ${today_val:.4f} oggi > 3sigma ${threshold:.4f} (media=${mean:.4f}, std=${std:.4f})",
                "severity": "warning",
            })

    return alerts


# ---------- METRICHE CFO TECH ----------

def finance_cfo_metrics(usd_to_eur):
    """Metriche enterprise: margine operativo, rapporti, unit economics."""
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    _, days_in_month = monthrange(year, month)
    days_elapsed = now.day

    # Costi variabili mese corrente
    variable_usd = finance_get_month_costs(year, month)
    variable_eur = variable_usd * usd_to_eur

    # Costi fissi proporzionali
    fixed_eur = FIXED_COSTS_DAILY_EUR * days_elapsed
    total_eur = variable_eur + fixed_eur

    # Rapporti
    fixed_pct = round((fixed_eur / total_eur) * 100, 1) if total_eur > 0 else 0
    variable_pct = round(100 - fixed_pct, 1)

    # Margine operativo (budget - costi) / budget
    projected_total = round((total_eur / days_elapsed) * days_in_month, 2) if days_elapsed > 0 else 0
    operating_margin = round(((MONTHLY_BUDGET_EUR - projected_total) / MONTHLY_BUDGET_EUR) * 100, 1) if MONTHLY_BUDGET_EUR > 0 else 0

    # Unit economics ultimi 30gg
    cpv = finance_cost_per_value(30)

    # Costo acquisizione per problema (include tutto il pipeline)
    total_pipeline_cost = cpv["total_value_cost"]
    total_output = cpv["n_problems"] + cpv["n_solutions"] + cpv["n_bos"]
    cost_per_output = round(total_pipeline_cost / total_output, 4) if total_output > 0 else 0

    return {
        "operating_margin_pct": operating_margin,
        "fixed_costs_pct": fixed_pct,
        "variable_costs_pct": variable_pct,
        "fixed_eur_month": round(FIXED_COSTS_TOTAL_EUR, 2),
        "variable_eur_month": round((variable_eur / days_elapsed) * days_in_month, 2) if days_elapsed > 0 else 0,
        "projected_total_eur": projected_total,
        "cost_per_problem": cpv["cost_per_problem"],
        "cost_per_solution": cpv["cost_per_solution"],
        "cost_per_bos": cpv["cost_per_bos"],
        "cost_per_pipeline_output": cost_per_output,
        "unit_economics": cpv["efficiency"],
    }


# ---------- PERSISTENCE ----------

def finance_save_metrics(daily_data, projection_usd, projection_eur, budget_pct, alerts, usd_to_eur):
    row = {
        "report_date": daily_data["date"],
        "total_cost_usd": daily_data["total_cost_usd"],
        "total_cost_eur": round(daily_data["total_cost_usd"] * usd_to_eur, 4),
        "cost_by_agent": json.dumps(daily_data["cost_by_agent"]),
        "calls_by_agent": json.dumps(daily_data["calls_by_agent"]),
        "total_api_calls": daily_data["total_calls"],
        "successful_calls": daily_data["successful_calls"],
        "failed_calls": daily_data["failed_calls"],
        "total_tokens_in": daily_data["total_tokens_in"],
        "total_tokens_out": daily_data["total_tokens_out"],
        "burn_rate_daily_usd": daily_data["total_cost_usd"],
        "projected_monthly_usd": projection_usd,
        "projected_monthly_eur": projection_eur,
        "budget_eur": MONTHLY_BUDGET_EUR,
        "budget_usage_pct": budget_pct,
        "alerts_triggered": json.dumps(alerts),
    }
    try:
        supabase.table("finance_metrics").insert(row).execute()
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            try:
                del row["report_date"]
                supabase.table("finance_metrics").update(row).eq("report_date", daily_data["date"]).execute()
            except:
                pass


# ---------- REPORT: MATTUTINO (8:00) ----------

def finance_morning_report():
    """Report CFO mattutino: costi ieri, trend, burn rate, runway, alert."""
    logger.info("[FINANCE] Morning report starting...")
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    usd_to_eur = finance_get_usd_to_eur()

    # Costi ieri
    daily = finance_get_daily_costs(yesterday)
    if not daily:
        return {"status": "error", "error": "no data"}

    cost_eur = round(daily["total_cost_usd"] * usd_to_eur, 4)

    # Burn rates e runway
    rates = finance_burn_rates()
    rw = finance_runway(usd_to_eur)

    # Trend: ieri vs media 7gg
    pct_vs_7d = round(((daily["total_cost_usd"] - rates["avg_7d_usd"]) / rates["avg_7d_usd"]) * 100, 1) if rates["avg_7d_usd"] > 0 else 0
    trend_symbol = "+" if pct_vs_7d >= 0 else ""

    # Anomalie
    anomalies = finance_detect_anomalies()

    # Salva metriche
    finance_save_metrics(daily, rw["projected_month_eur"] / usd_to_eur if usd_to_eur > 0 else 0,
                         rw["projected_month_eur"], rw["budget_pct"],
                         anomalies, usd_to_eur)

    lines = [
        f"CFO REPORT {yesterday}",
        "",
        f"Costi API ieri: ${daily['total_cost_usd']:.4f} ({cost_eur:.4f} EUR)",
        f"Chiamate: {daily['total_calls']} ({daily['successful_calls']} ok, {daily['failed_calls']} err)",
    ]

    if daily["cost_by_agent"]:
        for agent, c in sorted(daily["cost_by_agent"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {agent}: ${c:.4f} ({daily['calls_by_agent'].get(agent, 0)} call)")

    lines.extend([
        "",
        f"Burn rate: ${rates['today_usd']:.4f}/oggi | ${rates['avg_7d_usd']:.4f}/7gg | ${rates['avg_30d_usd']:.4f}/30gg",
        f"Trend ieri vs 7gg: {trend_symbol}{pct_vs_7d}%",
        "",
        f"Mese speso: {rw['month_spent_eur']:.2f} EUR (var {rw['month_variable_eur']:.2f} + fissi {rw['month_fixed_eur']:.2f})",
        f"Proiezione mese: {rw['projected_month_eur']:.2f} EUR / Budget: {MONTHLY_BUDGET_EUR:.0f} EUR ({rw['budget_pct']:.1f}%)",
        f"Runway: {rw['days_remaining']} giorni | Rimangono: {rw['budget_remaining_eur']:.2f} EUR",
    ])

    if anomalies:
        lines.append("")
        for a in anomalies:
            lines.append(f"ALERT: {a['message']}")

    report = "\n".join(lines)
    notify_telegram(report, level="info", source="finance_agent")

    # Alert budget anticipato
    if rw["budget_pct"] > 80:
        days_to_budget = rw["days_remaining"]
        notify_telegram(
            f"ALERT BUDGET: proiezione {rw['projected_month_eur']:.2f} EUR = {rw['budget_pct']:.1f}% del budget. "
            f"Runway: {days_to_budget} giorni.",
            level="warning" if rw["budget_pct"] < 95 else "critical",
            source="finance_agent",
        )

    log_to_supabase("finance_agent", "morning_report", 6,
        f"Morning {yesterday}", f"${daily['total_cost_usd']:.4f}, runway {rw['days_remaining']}d",
        "none", 0, 0, 0, 0)

    logger.info(f"[FINANCE] Morning report done: ${daily['total_cost_usd']:.4f}")
    return {"status": "completed", "date": yesterday, "cost_usd": daily["total_cost_usd"],
            "runway_days": rw["days_remaining"], "budget_pct": rw["budget_pct"]}


# ---------- REPORT: SETTIMANALE (Domenica sera) ----------

def finance_weekly_report():
    """Report CFO settimanale: analisi completa, confronto, ottimizzazioni, anomalie."""
    logger.info("[FINANCE] Weekly report starting...")
    now = datetime.now(timezone.utc)
    usd_to_eur = finance_get_usd_to_eur()

    # Questa settimana (lun-dom)
    days_back = now.weekday()  # 0=lunedi
    week_start = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    week_end = now.strftime("%Y-%m-%d")
    this_week = finance_get_range_costs(week_start, week_end)

    # Settimana precedente
    prev_start = (now - timedelta(days=days_back + 7)).strftime("%Y-%m-%d")
    prev_end = (now - timedelta(days=days_back + 1)).strftime("%Y-%m-%d")
    prev_week = finance_get_range_costs(prev_start, prev_end)

    tw_cost = this_week["total_cost_usd"] if this_week else 0
    pw_cost = prev_week["total_cost_usd"] if prev_week else 0
    tw_eur = round(tw_cost * usd_to_eur, 2)
    pw_eur = round(pw_cost * usd_to_eur, 2)
    tw_calls = this_week["total_calls"] if this_week else 0
    pw_calls = prev_week["total_calls"] if prev_week else 0

    pct_cost = round(((tw_cost - pw_cost) / pw_cost) * 100, 1) if pw_cost > 0 else 0
    pct_calls = round(((tw_calls - pw_calls) / pw_calls) * 100, 1) if pw_calls > 0 else 0

    # Efficienza
    cpv = finance_cost_per_value(7)
    anomalies = finance_detect_anomalies()
    optimizations = finance_optimization_suggestions(7)
    cfo = finance_cfo_metrics(usd_to_eur)

    lines = [
        f"CFO REPORT SETTIMANALE {week_start} / {week_end}",
        "",
        f"Costi API: ${tw_cost:.4f} ({tw_eur:.2f} EUR) | Chiamate: {tw_calls}",
    ]

    if this_week and this_week["cost_by_agent"]:
        for agent, c in sorted(this_week["cost_by_agent"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {agent}: ${c:.4f}")

    cost_arrow = "+" if pct_cost >= 0 else ""
    calls_arrow = "+" if pct_calls >= 0 else ""
    lines.extend([
        "",
        f"vs settimana precedente: costi {cost_arrow}{pct_cost}% | chiamate {calls_arrow}{pct_calls}%",
        f"  Precedente: ${pw_cost:.4f} ({pw_eur:.2f} EUR) | {pw_calls} chiamate",
        "",
        "EFFICIENZA:",
        f"  Problemi trovati: {cpv['n_problems']} (${cpv['cost_per_problem']:.4f}/problema)",
        f"  Soluzioni generate: {cpv['n_solutions']} (${cpv['cost_per_solution']:.4f}/soluzione)",
        f"  BOS calcolati: {cpv['n_bos']} (${cpv['cost_per_bos']:.4f}/BOS)",
    ])

    if cpv["efficiency"]:
        best = min(cpv["efficiency"].items(), key=lambda x: x[1]["unit_cost"])
        worst = max(cpv["efficiency"].items(), key=lambda x: x[1]["unit_cost"])
        if best[0] != worst[0]:
            lines.append(f"  Piu efficiente: {best[0]} (${best[1]['unit_cost']:.4f}/output)")
            lines.append(f"  Meno efficiente: {worst[0]} (${worst[1]['unit_cost']:.4f}/output)")

    if anomalies:
        lines.append("")
        lines.append("ANOMALIE:")
        for a in anomalies:
            lines.append(f"  {a['message']}")

    if optimizations:
        lines.append("")
        lines.append("OTTIMIZZAZIONI SUGGERITE:")
        for i, opt in enumerate(optimizations, 1):
            if opt["type"] == "totale":
                lines.append(f"  Risparmio stimato totale: ${opt['saving_usd']:.4f}/settimana")
            else:
                lines.append(f"  {i}. [{opt['type']}] {opt['target']}: {opt['detail']} (saving: ${opt['saving_usd']:.4f})")

    lines.extend([
        "",
        "METRICHE CFO:",
        f"  Margine operativo: {cfo['operating_margin_pct']:.1f}%",
        f"  Fissi/Variabili: {cfo['fixed_costs_pct']:.0f}%/{cfo['variable_costs_pct']:.0f}%",
        f"  Costo per output pipeline: ${cfo['cost_per_pipeline_output']:.4f}",
    ])

    report = "\n".join(lines)
    notify_telegram(report, level="info", source="finance_agent")

    log_to_supabase("finance_agent", "weekly_report", 6,
        f"Weekly {week_start}/{week_end}", f"${tw_cost:.4f} vs ${pw_cost:.4f}",
        "none", 0, 0, 0, 0)

    logger.info(f"[FINANCE] Weekly report done: ${tw_cost:.4f}")
    return {"status": "completed", "week": f"{week_start}/{week_end}",
            "cost_usd": tw_cost, "vs_prev_pct": pct_cost}


# ---------- REPORT: MENSILE (1° del mese) ----------

def finance_monthly_report():
    """Report CFO mensile: trend, piano ottimizzazione, previsione mese successivo."""
    logger.info("[FINANCE] Monthly report starting...")
    now = datetime.now(timezone.utc)
    usd_to_eur = finance_get_usd_to_eur()

    # Mese precedente
    if now.month == 1:
        prev_year, prev_month = now.year - 1, 12
    else:
        prev_year, prev_month = now.year, now.month - 1

    prev_variable_usd = finance_get_month_costs(prev_year, prev_month)
    prev_variable_eur = round(prev_variable_usd * usd_to_eur, 2)
    prev_total_eur = round(prev_variable_eur + FIXED_COSTS_TOTAL_EUR, 2)

    # Due mesi fa (per confronto)
    if prev_month == 1:
        pp_year, pp_month = prev_year - 1, 12
    else:
        pp_year, pp_month = prev_year, prev_month - 1
    pp_variable_usd = finance_get_month_costs(pp_year, pp_month)
    pp_total_eur = round(pp_variable_usd * usd_to_eur + FIXED_COSTS_TOTAL_EUR, 2)

    pct_vs_prev = round(((prev_total_eur - pp_total_eur) / pp_total_eur) * 100, 1) if pp_total_eur > 0 else 0

    # Costi da sempre
    all_time_usd = finance_get_all_time_costs()
    all_time_eur = round(all_time_usd * usd_to_eur, 2)

    # Ottimizzazioni e metriche
    optimizations = finance_optimization_suggestions(30)
    cfo = finance_cfo_metrics(usd_to_eur)
    cpv = finance_cost_per_value(30)

    # Previsione mese successivo (basata su media ultimi 2 mesi variabili)
    avg_variable = (prev_variable_usd + pp_variable_usd) / 2 if pp_variable_usd > 0 else prev_variable_usd
    forecast_variable_eur = round(avg_variable * usd_to_eur, 2)
    forecast_total_eur = round(forecast_variable_eur + FIXED_COSTS_TOTAL_EUR, 2)

    month_name = f"{prev_year}-{prev_month:02d}"
    lines = [
        f"CFO REPORT MENSILE {month_name}",
        "",
        f"Costi API: ${prev_variable_usd:.4f} ({prev_variable_eur:.2f} EUR)",
        f"Costi fissi: {FIXED_COSTS_TOTAL_EUR:.2f} EUR",
    ]
    for name, cost in FIXED_COSTS_MONTHLY_EUR.items():
        lines.append(f"  {name}: {cost:.2f} EUR")
    lines.extend([
        f"TOTALE: {prev_total_eur:.2f} EUR / Budget: {MONTHLY_BUDGET_EUR:.0f} EUR ({round(prev_total_eur/MONTHLY_BUDGET_EUR*100, 1)}%)",
    ])

    if pp_total_eur > 0:
        trend_arrow = "+" if pct_vs_prev >= 0 else ""
        lines.append(f"vs mese precedente: {trend_arrow}{pct_vs_prev}% ({pp_total_eur:.2f} EUR)")
    lines.append("")

    lines.extend([
        "COSTI DA SEMPRE:",
        f"  API totali: ${all_time_usd:.4f} ({all_time_eur:.2f} EUR)",
        "",
        "UNIT ECONOMICS (30gg):",
        f"  Costo per problema: ${cpv['cost_per_problem']:.4f}",
        f"  Costo per soluzione: ${cpv['cost_per_solution']:.4f}",
        f"  Costo per BOS: ${cpv['cost_per_bos']:.4f}",
        f"  Pipeline: {cpv['n_problems']} problemi -> {cpv['n_solutions']} soluzioni -> {cpv['n_bos']} BOS",
        "",
        "METRICHE CFO:",
        f"  Margine operativo: {cfo['operating_margin_pct']:.1f}%",
        f"  Fissi: {cfo['fixed_costs_pct']:.0f}% | Variabili: {cfo['variable_costs_pct']:.0f}%",
    ])

    if optimizations:
        lines.append("")
        lines.append("PIANO OTTIMIZZAZIONE:")
        for i, opt in enumerate(optimizations, 1):
            if opt["type"] == "totale":
                lines.append(f"  Risparmio stimato: ${opt['saving_usd']:.4f}/mese")
            else:
                lines.append(f"  {i}. {opt['detail']} (saving: ${opt['saving_usd']:.4f})")

    lines.extend([
        "",
        "PREVISIONE MESE PROSSIMO:",
        f"  API: {forecast_variable_eur:.2f} EUR (media 2 mesi)",
        f"  Fissi: {FIXED_COSTS_TOTAL_EUR:.2f} EUR",
        f"  TOTALE previsto: {forecast_total_eur:.2f} EUR",
    ])

    report = "\n".join(lines)
    notify_telegram(report, level="info", source="finance_agent")

    log_to_supabase("finance_agent", "monthly_report", 6,
        f"Monthly {month_name}", f"Total {prev_total_eur:.2f} EUR",
        "none", 0, 0, 0, 0)

    logger.info(f"[FINANCE] Monthly report done: {prev_total_eur:.2f} EUR")
    return {"status": "completed", "month": month_name, "total_eur": prev_total_eur,
            "forecast_eur": forecast_total_eur}


# ---------- ENTRY POINT PRINCIPALE ----------

def run_finance_agent(target_date=None):
    """Analisi finanziaria completa — usata da /finance endpoint."""
    logger.info("Finance Agent v2.0 — CFO AI starting...")

    now = datetime.now(timezone.utc)
    if target_date:
        date_str = target_date
    else:
        yesterday = now - timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")

    usd_to_eur = finance_get_usd_to_eur()
    daily = finance_get_daily_costs(date_str)
    if daily is None:
        return {"status": "error", "error": "agent_logs read failed"}

    rw = finance_runway(usd_to_eur)
    rates = finance_burn_rates()
    anomalies = finance_detect_anomalies()
    cpv = finance_cost_per_value(30)
    cfo = finance_cfo_metrics(usd_to_eur)
    optimizations = finance_optimization_suggestions(7)

    # Salva metriche
    finance_save_metrics(daily, rw["projected_month_eur"] / usd_to_eur if usd_to_eur > 0 else 0,
                         rw["projected_month_eur"], rw["budget_pct"], anomalies, usd_to_eur)

    # Alert budget anticipato (se proiezione > 80% budget)
    if rw["budget_pct"] > 80:
        notify_telegram(
            f"ALERT CFO: proiezione mese {rw['projected_month_eur']:.2f} EUR = {rw['budget_pct']:.1f}% budget. "
            f"Runway: {rw['days_remaining']}gg.",
            level="warning" if rw["budget_pct"] < 95 else "critical",
            source="finance_agent",
        )

    # Alert anomalie
    for a in anomalies:
        notify_telegram(f"ANOMALIA CFO: {a['message']}", level=a["severity"], source="finance_agent")

    log_to_supabase("finance_agent", "full_analysis", 6,
        f"Analysis {date_str}", f"Budget {rw['budget_pct']:.1f}%, runway {rw['days_remaining']}d",
        "none", 0, 0, 0, 0)

    logger.info(f"[FINANCE] Full analysis done: {rw['budget_pct']:.1f}% budget, {rw['days_remaining']}d runway")
    return {
        "status": "completed",
        "date": date_str,
        "daily_cost_usd": daily["total_cost_usd"],
        "runway": rw,
        "burn_rates": rates,
        "anomalies": len(anomalies),
        "cfo_metrics": cfo,
        "cost_per_value": cpv,
        "optimizations": len(optimizations),
    }


# ============================================================
# PARTE 8: SISTEMA REPORT (costi ogni 4h ore pari, attività ore dispari)
# ============================================================

MESI_IT_REPORT = {1:"gen",2:"feb",3:"mar",4:"apr",5:"mag",6:"giu",
                  7:"lug",8:"ago",9:"set",10:"ott",11:"nov",12:"dic"}


def _get_rome_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Rome")
    except Exception:
        return timezone(timedelta(hours=1))


def _format_rome_time(ts_str):
    """Converte timestamp UTC in HH:MM Europe/Rome."""
    if not ts_str:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        rome = dt.astimezone(_get_rome_tz())
        return rome.strftime("%H:%M")
    except Exception:
        return str(ts_str)[:16]


def _make_bar(value, max_value, length=5):
    """Barra proporzionale ▓░ di lunghezza fissa."""
    if max_value <= 0:
        return "░" * length
    filled = max(0, min(length, round(value / max_value * length)))
    return "▓" * filled + "░" * (length - filled)


def _shorten_agent_name(name):
    mapping = {
        "world_scanner": "World Scanner",
        "solution_architect": "Solution Arch.",
        "spec_generator": "Spec Generator",
        "build_agent": "Build Agent",
        "knowledge_keeper": "Knowledge Keeper",
        "command_center": "Command Center",
        "daily_report": "Daily Report",
        "cost_report": "Cost Report",
        "activity_report": "Activity Report",
        "validation_agent": "Validation",
        "capability_scout": "Cap. Scout",
        "bos_calculator": "BOS Calc.",
    }
    return mapping.get(name, name[:18])


def _get_period_cost(since_iso, until_iso=None):
    """Costi in EUR e breakdown per agente per il periodo dato. Ritorna (total_eur, {agent: eur})."""
    usd_to_eur = finance_get_usd_to_eur()
    try:
        q = supabase.table("agent_logs").select("agent_id,cost_usd").gte("created_at", since_iso)
        if until_iso:
            q = q.lte("created_at", until_iso)
        logs = q.execute().data or []
    except Exception as e:
        logger.warning(f"[PERIOD_COST] {e}")
        return 0.0, {}
    by_agent = {}
    total_usd = 0.0
    for l in logs:
        a = l.get("agent_id", "unknown")
        c = float(l.get("cost_usd", 0) or 0)
        total_usd += c
        by_agent[a] = by_agent.get(a, 0.0) + c
    total_eur = round(total_usd * usd_to_eur, 4)
    by_agent_eur = {k: round(v * usd_to_eur, 4) for k, v in by_agent.items()}
    return total_eur, by_agent_eur


def generate_cost_report_v2():
    """Report costi ogni 4h (ore pari Europe/Rome): ultime 4h / oggi / 7g / mese + top spender."""
    logger.info("[REPORT] Generating cost report...")
    now_utc = datetime.now(timezone.utc)
    rome_tz = _get_rome_tz()
    now_rome = now_utc.astimezone(rome_tz)
    data_it = f"{now_rome.day} {MESI_IT_REPORT[now_rome.month]} {now_rome.year} {now_rome.strftime('%H:%M')}"

    since_4h = (now_utc - timedelta(hours=4)).isoformat()
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    since_7d = (now_utc - timedelta(days=7)).isoformat()
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    cost_4h, agents_4h = _get_period_cost(since_4h)
    cost_today, _ = _get_period_cost(today_start)
    cost_7d, _ = _get_period_cost(since_7d)
    cost_month, _ = _get_period_cost(month_start)

    # Spike detection: ultime 4h vs media (7g / 42 periodi da 4h)
    avg_4h = cost_7d / 42 if cost_7d > 0 else 0
    spike_pct = ((cost_4h - avg_4h) / avg_4h * 100) if avg_4h > 0 and cost_4h > avg_4h * 2 else 0

    sorted_agents = sorted(agents_4h.items(), key=lambda x: x[1], reverse=True)
    top4 = sorted_agents[:4]
    altri_cost = sum(v for _, v in sorted_agents[4:])
    display_agents = top4 + ([("Altri", altri_cost)] if altri_cost > 0 else [])
    max_cost = max((v for _, v in display_agents), default=1) or 1

    sep = "\u2501" * 15
    lines = [
        f"\U0001f4b6 *COSTI brAIn* \u2014 {data_it}",
        sep,
        f"\U0001f550 Ultime 4h:   \u20ac{cost_4h:.2f}",
        f"\U0001f4c5 Oggi:        \u20ac{cost_today:.2f}",
        f"\U0001f4c6 7 giorni:    \u20ac{cost_7d:.2f}",
        f"\U0001f5d3 Mese:        \u20ac{cost_month:.2f}",
        sep,
        "Top spender:",
    ]
    for i, (agent, cost) in enumerate(display_agents):
        prefix = "\u2514" if i == len(display_agents) - 1 else "\u251c"
        short = _shorten_agent_name(agent)
        bar = _make_bar(cost, max_cost)
        lines.append(f"{prefix} {short:<18} \u20ac{cost:.2f}  {bar}")

    if spike_pct >= 100:
        lines.append(sep)
        lines.append(f"\u26a0\ufe0f Spike rilevato: +{spike_pct:.0f}%")

    report = "\n".join(lines)
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\U0001f50d Dettaglio ora", "callback_data": "cost_detail_4h"},
            {"text": "\U0001f4ca 7 giorni", "callback_data": "cost_trend_7d"},
        ]]
    }
    chat_id_report = get_telegram_chat_id()
    if chat_id_report and TELEGRAM_BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id_report, "text": report, "reply_markup": reply_markup, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception as e:
            logger.error(f"[COST_REPORT] Telegram error: {e}")
    log_to_supabase("cost_report", "generate", 0, f"Cost report {data_it}", report[:300], "none")
    return {"status": "ok", "type": "cost", "date": data_it}


def generate_activity_report_v2():
    """Report attività ogni 4h (ore dispari Europe/Rome): scanner, pipeline, cantieri."""
    logger.info("[REPORT] Generating activity report...")
    now_utc = datetime.now(timezone.utc)
    rome_tz = _get_rome_tz()
    now_rome = now_utc.astimezone(rome_tz)
    data_it = f"{now_rome.day} {MESI_IT_REPORT[now_rome.month]} {now_rome.year} {now_rome.strftime('%H:%M')}"

    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    since_8h = (now_utc - timedelta(hours=8)).isoformat()

    # --- SCANNER ---
    try:
        probs = supabase.table("problems").select("id,weighted_score").gte("created_at", today_start).execute().data or []
        prob_count = len(probs)
        avg_score = sum(float(p.get("weighted_score", 0) or 0) for p in probs) / prob_count if prob_count else 0.0
    except Exception:
        prob_count = 0; avg_score = 0.0
    try:
        last_scan_res = supabase.table("agent_logs").select("created_at").eq("agent_id", "world_scanner").order("created_at", desc=True).limit(1).execute().data or []
        last_scan_str = _format_rome_time(last_scan_res[0]["created_at"]) if last_scan_res else "\u2014"
    except Exception:
        last_scan_str = "\u2014"

    # --- PIPELINE ---
    try:
        bos_today = supabase.table("solutions").select("id,bos_score").gte("created_at", today_start).not_.is_("bos_score", "null").execute().data or []
        bos_count = len(bos_today)
        avg_bos = sum(float(b.get("bos_score", 0) or 0) for b in bos_today) / bos_count if bos_count else 0.0
    except Exception:
        bos_count = 0; avg_bos = 0.0
    try:
        pending_res = supabase.table("action_queue").select("id").eq("action_type", "approve_bos").eq("status", "pending").execute().data or []
        pending_count = len(pending_res)
    except Exception:
        pending_count = 0

    # --- CANTIERI ---
    try:
        cantieri = supabase.table("projects").select("id,name,status,created_at,build_phase").neq("status", "archived").execute().data or []
    except Exception:
        cantieri = []

    # Scanner silenzioso: nessun problema nelle ultime 8h
    try:
        probs_8h = supabase.table("problems").select("id").gte("created_at", since_8h).execute().data or []
        scanner_silent = len(probs_8h) == 0
    except Exception:
        scanner_silent = False

    sep = "\u2501" * 15
    lines = [
        f"\u2699\ufe0f *ATTIVIT\u00c0 brAIn* \u2014 {data_it}",
        sep,
        "\U0001f50d Scanner",
        f"\u251c Problemi trovati oggi:     {prob_count}",
        f"\u251c Score medio:               {avg_score:.2f}",
        f"\u2514 Ultimo scan:               {last_scan_str}",
        "",
        "\U0001f9e0 Pipeline",
        f"\u251c BOS generati oggi:         {bos_count}",
        f"\u251c Score medio BOS:           {avg_bos:.2f}",
        f"\u2514 In attesa approvazione:    {pending_count}",
        "",
        "\U0001f3d7\ufe0f Cantieri",
    ]
    if cantieri:
        first = cantieri[0]
        last_upd = _format_rome_time(first.get("created_at"))
        lines.append(f"\u251c Attivi:                    {len(cantieri)} \u2014 {first.get('name', '?')[:25]}")
        lines.append(f"\u251c Status:                    {first.get('status', '?')}")
        lines.append(f"\u2514 Creato:                    {last_upd}")
    else:
        lines.append("\u2514 Nessun cantiere attivo")

    if scanner_silent:
        lines.append("")
        lines.append("\u26a0\ufe0f Scanner silenzioso \u2014 verifica")

    report = "\n".join(lines)
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\U0001f4cb Problemi", "callback_data": "act_problemi"},
            {"text": "\U0001f3c6 Top BOS", "callback_data": "act_top_bos"},
            {"text": "\U0001f3d7\ufe0f Cantieri", "callback_data": "act_cantieri"},
        ]]
    }
    chat_id_report = get_telegram_chat_id()
    if chat_id_report and TELEGRAM_BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id_report, "text": report, "reply_markup": reply_markup, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception as e:
            logger.error(f"[ACTIVITY_REPORT] Telegram error: {e}")
    log_to_supabase("activity_report", "generate", 0, f"Activity report {data_it}", report[:300], "none")
    return {"status": "ok", "type": "activity", "date": data_it}


def update_kpi_daily():
    """Aggiorna kpi_daily per oggi. Chiamare a mezzanotte via Cloud Scheduler → /kpi/update."""
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime("%Y-%m-%d")
    today_start = f"{today}T00:00:00+00:00"

    try:
        prob_res = supabase.table("problems").select("id,weighted_score").gte("created_at", today_start).execute().data or []
        problems_found = len(prob_res)
        avg_problem_score = sum(float(p.get("weighted_score", 0) or 0) for p in prob_res) / problems_found if problems_found else 0.0
    except Exception:
        problems_found = 0; avg_problem_score = 0.0
    try:
        bos_res = supabase.table("solutions").select("id,bos_score").gte("created_at", today_start).not_.is_("bos_score", "null").execute().data or []
        bos_generated = len(bos_res)
        avg_bos_score = sum(float(b.get("bos_score", 0) or 0) for b in bos_res) / bos_generated if bos_generated else 0.0
    except Exception:
        bos_generated = 0; avg_bos_score = 0.0
    try:
        active_cantieri = len(supabase.table("projects").select("id").neq("status", "archived").execute().data or [])
    except Exception:
        active_cantieri = 0
    try:
        mvps_launched = len(supabase.table("projects").select("id").eq("status", "launch_approved").gte("created_at", today_start).execute().data or [])
    except Exception:
        mvps_launched = 0
    cost_today, _ = _get_period_cost(today_start)
    try:
        api_calls = supabase.table("agent_logs").select("id", count="exact").gte("created_at", today_start).execute().count or 0
    except Exception:
        api_calls = 0
    try:
        supabase.table("kpi_daily").upsert({
            "date": today,
            "problems_found": problems_found,
            "avg_problem_score": round(avg_problem_score, 4),
            "bos_generated": bos_generated,
            "avg_bos_score": round(avg_bos_score, 4),
            "mvps_launched": mvps_launched,
            "active_cantieri": active_cantieri,
            "total_cost_eur": round(cost_today, 4),
            "api_calls": api_calls,
        }, on_conflict="date").execute()
        logger.info(f"[KPI] kpi_daily aggiornata per {today}")
    except Exception as e:
        logger.error(f"[KPI] Upsert fallito: {e}")
    return {"status": "ok", "date": today}


# ============================================================
# PARTE 1: EVENT PROCESSOR — cascade completa
# ============================================================

def process_events():
    events = get_pending_events()
    processed = 0

    for event in events:
        event_type = event.get("event_type", "")
        target = event.get("target_agent", "")
        payload = event.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except:
                payload = {}

        try:
            if event_type == "scan_completed":
                # Trigger solution generation per problemi con score >= soglia_problema
                problem_ids = payload.get("problem_ids", [])
                for pid in problem_ids:
                    try:
                        prob = supabase.table("problems").select("weighted_score").eq("id", pid).execute()
                        if prob.data and float(prob.data[0].get("weighted_score", 0) or 0) >= MIN_SCORE_THRESHOLD:
                            emit_event("event_processor", "problem_ready", "solution_architect",
                                {"problem_id": str(pid)})
                    except:
                        pass
                mark_event_done(event["id"])

            elif event_type == "problems_found":
                # Notifica pura informativa — pipeline continua in autonomia
                problem_ids = payload.get("problem_ids", [])
                count = payload.get("count", len(problem_ids))
                notify_telegram(f"Scanner: trovati {count} nuovi problemi sopra soglia. Pipeline in elaborazione automatica.")
                mark_event_done(event["id"])

            elif event_type == "problem_approved":
                problem_id = payload.get("problem_id")
                if problem_id:
                    run_solution_architect(problem_id=problem_id)
                mark_event_done(event["id"])

            elif event_type == "solutions_generated":
                # Trigger feasibility engine
                solution_ids = payload.get("solution_ids", [])
                for sid in solution_ids:
                    run_feasibility_engine(solution_id=sid, notify=True)
                mark_event_done(event["id"])

            elif event_type == "feasibility_completed":
                # BOS is already calculated inline in run_feasibility_engine
                mark_event_done(event["id"])

            elif event_type == "bos_calculated":
                bos_score = payload.get("bos_score", 0)
                verdict = payload.get("verdict", "ARCHIVE")
                solution_id = payload.get("solution_id")

                # Solo AUTO-GO viene processato. REVIEW eliminato — la pipeline
                # decide direttamente tramite enqueue_bos_action se BOS >= soglia_bos.
                if verdict == "AUTO-GO":
                    emit_event("event_processor", "auto_go", "project_builder",
                        {"solution_id": solution_id, "bos": bos_score}, "high")
                # ARCHIVE: nessuna azione

                mark_event_done(event["id"])

            elif event_type == "mirco_feedback":
                # Self-improvement: salva preferenza
                feedback_type = payload.get("type", "")
                item_id = payload.get("item_id", "")
                action = payload.get("action", "")
                reason = payload.get("reason", "")

                if feedback_type and action:
                    try:
                        supabase.table("org_knowledge").insert({
                            "title": f"Preferenza: {feedback_type} {action}",
                            "content": f"Mirco ha {action} un {feedback_type}. ID: {item_id}. Motivo: {reason}",
                            "category": "preference",
                            "source": "mirco_feedback",
                        }).execute()
                    except:
                        pass

                mark_event_done(event["id"])

            elif event_type == "batch_scan_complete" and target == "knowledge_keeper":
                run_knowledge_keeper()
                mark_event_done(event["id"])

            elif event_type == "solution_go" and target == "project_builder":
                # Futuro: Project Builder
                mark_event_done(event["id"])

            elif event_type == "review_request":
                # Tipo obsoleto — la pipeline v2 usa solo approve_bos action.
                # Gestiamo silenziosamente per compatibilità con eventi esistenti in DB.
                mark_event_done(event["id"])

            elif event_type == "error_pattern_detected":
                # Gia notificato da knowledge_keeper
                mark_event_done(event["id"])

            elif event_type == "high_impact_tool":
                # Gia notificato da capability_scout
                mark_event_done(event["id"])

            else:
                mark_event_done(event["id"])

            processed += 1

        except Exception as e:
            logger.error(f"[EVENT ERROR] {event_type}: {e}")
            mark_event_done(event["id"], "failed")

    return {"processed": processed}


# ============================================================
# THRESHOLD MANAGER — Aggiornamento settimanale soglie dinamiche
# ============================================================

def run_weekly_threshold_update():
    """Aggiorna le soglie della pipeline in base al bos_approval_rate settimanale.
    Chiamato ogni lunedi alle 08:00 via Cloud Scheduler → /thresholds/weekly.
    Target: bos_approval_rate <= 10%."""
    logger.info("[THRESHOLDS] Weekly update starting...")

    thresholds = get_pipeline_thresholds()
    soglia_bos = thresholds["bos"]

    # BOS calcolati nell'ultima settimana
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        result = supabase.table("solutions").select("bos_score") \
            .not_.is_("bos_score", "null").gte("created_at", week_ago).execute()
        scores = [float(s["bos_score"]) for s in (result.data or [])]
    except Exception as e:
        logger.error(f"[THRESHOLDS] DB read error: {e}")
        scores = []

    total = len(scores)
    above_threshold = sum(1 for s in scores if s >= soglia_bos) if scores else 0
    bos_approval_rate = round(above_threshold / total * 100, 1) if total > 0 else 0.0

    # Calcola factor di aggiustamento
    factor = 1.0
    reason = f"bos_approval_rate={bos_approval_rate:.1f}% nel target (<=10%), soglie invariate"

    if bos_approval_rate > 20:
        factor = 1.05
        reason = f"bos_approval_rate={bos_approval_rate:.1f}% > 20%, alzo soglie del 5%"
    elif bos_approval_rate > 10:
        factor = 1.02
        reason = f"bos_approval_rate={bos_approval_rate:.1f}% > 10%, alzo soglie del 2%"
    elif total == 0 or bos_approval_rate == 0.0:
        # Controlla se anche la settimana precedente aveva 0 BOS
        try:
            prev_rows = supabase.table("pipeline_thresholds").select("bos_approval_rate") \
                .order("id", desc=True).limit(2).execute()
            prev_data = prev_rows.data or []
            consecutive_zero = (
                len(prev_data) >= 1 and
                (prev_data[0].get("bos_approval_rate") or 0.0) == 0.0
            )
        except:
            consecutive_zero = False

        if consecutive_zero:
            factor = 0.95
            reason = "bos_approval_rate=0% per 2+ settimane consecutive, abbasso soglie del 5%"
        else:
            reason = "bos_approval_rate=0% (prima settimana senza BOS), soglie invariate in attesa"

    new_problema = round(min(0.95, max(0.30, thresholds["problema"] * factor)), 3)
    new_soluzione = round(min(0.95, max(0.30, thresholds["soluzione"] * factor)), 3)
    new_feasibility = round(min(0.95, max(0.30, thresholds["feasibility"] * factor)), 3)
    new_bos = round(min(0.95, max(0.30, thresholds["bos"] * factor)), 3)

    # Salva nuove soglie in DB
    try:
        supabase.table("pipeline_thresholds").insert({
            "soglia_problema": new_problema,
            "soglia_soluzione": new_soluzione,
            "soglia_feasibility": new_feasibility,
            "soglia_bos": new_bos,
            "bos_approval_rate": bos_approval_rate,
            "update_reason": reason,
        }).execute()
    except Exception as e:
        logger.error(f"[THRESHOLDS] Save error: {e}")

    # Report a Mirco con formato standard
    sep = "\u2501" * 15
    changed = factor != 1.0
    msg = (
        f"AGGIORNAMENTO SOGLIE SETTIMANALE\n"
        f"{sep}\n"
        f"BOS approvati questa settimana: {above_threshold}/{total} ({bos_approval_rate:.1f}%)\n"
        f"Soglie aggiornate:\n"
        f"- Problema: {thresholds['problema']:.2f} \u2192 {new_problema:.2f}" + (" (=" if not changed else "") + "\n"
        f"- Soluzione: {thresholds['soluzione']:.2f} \u2192 {new_soluzione:.2f}\n"
        f"- Feasibility: {thresholds['feasibility']:.2f} \u2192 {new_feasibility:.2f}\n"
        f"- BOS: {thresholds['bos']:.2f} \u2192 {new_bos:.2f}\n"
        f"Motivo: {reason}\n"
        f"{sep}\n"
        f"Vuoi modificare manualmente le soglie?"
    )
    notify_telegram(msg, level="info", source="threshold_manager")

    log_to_supabase("threshold_manager", "weekly_update", 0,
        f"bos_rate={bos_approval_rate}% total={total}", reason, "none")

    logger.info(f"[THRESHOLDS] Weekly update done. factor={factor} bos_rate={bos_approval_rate}%")
    return {
        "status": "completed",
        "total_bos": total,
        "above_threshold": above_threshold,
        "bos_approval_rate": bos_approval_rate,
        "new_thresholds": {
            "problema": new_problema,
            "soluzione": new_soluzione,
            "feasibility": new_feasibility,
            "bos": new_bos,
        },
        "factor": factor,
        "reason": reason,
    }


# ============================================================
# IDEA RECYCLER
# ============================================================

def run_idea_recycler():
    """Rivaluta problemi e soluzioni archiviate."""
    logger.info("Idea Recycler starting...")

    try:
        archived = supabase.table("problems").select("id, title, sector, weighted_score, created_at") \
            .eq("status_detail", "archived").order("weighted_score", desc=True).limit(10).execute()
        archived = archived.data or []
    except:
        archived = []

    if not archived:
        return {"status": "no_archived", "recycled": 0}

    recycled = 0
    for problem in archived:
        age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(problem["created_at"].replace("Z", "+00:00"))).days
        if age_days < 14:
            continue

        title = problem["title"]
        sector = problem.get("sector", "")
        result = search_perplexity(f"{title} new developments changes 2026")

        if result and ("growing" in result.lower() or "increasing" in result.lower() or "new" in result.lower()):
            try:
                supabase.table("reevaluation_log").insert({
                    "problem_id": problem["id"],
                    "reason": "Periodic recycler - potential relevance change",
                    "new_data": result[:500],
                }).execute()

                emit_event("idea_recycler", "problem_may_be_relevant", "command_center",
                    {"problem_id": str(problem["id"]), "title": title})

                recycled += 1
            except:
                pass
        time.sleep(1)

    log_to_supabase("idea_recycler", "recycle", 5,
        f"Rivalutati {len(archived)} problemi", f"{recycled} potenzialmente rilevanti",
        "none")

    return {"status": "completed", "recycled": recycled}


# ============================================================
# TARGETED SCAN — scansione mirata su fonte/settore specifico
# ============================================================

def run_targeted_scan(source_name=None, use_top=False, sector=None):
    """
    Scan mirato su una fonte specifica, le top fonti, o un settore.
    Bypassa la rotazione normale del scan_schedule.
    """
    try:
        q = supabase.table("scan_sources").select("*").eq("status", "active")
        if source_name:
            q = q.ilike("name", f"%{source_name}%")
            sources_data = q.execute()
        elif sector:
            # Cerca fonti che coprono quel settore
            q = q.ilike("sectors", f"%{sector}%")
            sources_data = q.order("relevance_score", desc=True).limit(5).execute()
        elif use_top:
            sources_data = q.order("relevance_score", desc=True).limit(3).execute()
        else:
            sources_data = q.order("relevance_score", desc=True).limit(5).execute()
        sources = sources_data.data or []
    except Exception as e:
        logger.error(f"[TARGETED SCAN] Errore fetch fonti: {e}")
        sources = []

    if not sources:
        label = source_name or sector or "top"
        logger.warning(f"[TARGETED SCAN] Nessuna fonte trovata per: {label}")
        return {"status": "no_sources", "saved": 0, "message": f"Nessuna fonte trovata per: {label}"}

    source_names_used = [s["name"] for s in sources]
    logger.info(f"[TARGETED SCAN] Fonti usate: {source_names_used}")

    queries = get_standard_queries(sources)[:4]
    result = run_scan(queries, max_problems=1)

    saved_ids = result.get("saved_ids", [])
    if saved_ids:
        import threading
        threading.Thread(target=run_auto_pipeline, args=(saved_ids,), daemon=True).start()

    return {
        "status": "completed",
        "sources_used": source_names_used,
        "saved": result.get("saved", 0),
        "saved_ids": saved_ids,
    }


# ============================================================
# SOURCE REFRESH
# ============================================================

def run_source_refresh():
    """Aggiorna ranking fonti e cerca nuove fonti."""
    logger.info("Source Refresh starting...")

    try:
        sources = supabase.table("scan_sources").select("*").eq("status", "active").execute()
        sources = sources.data or []
    except:
        sources = []

    updated = 0
    for source in sources:
        last_scanned = source.get("last_scanned")
        problems_found = source.get("problems_found", 0)

        if last_scanned:
            try:
                last_dt = datetime.fromisoformat(last_scanned.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - last_dt).days
            except:
                days_since = 30
        else:
            days_since = 30

        # Penalizza fonti che non producono risultati
        if days_since > 14 and problems_found == 0:
            new_rel = max(0.1, source.get("relevance_score", 0.5) - 0.05)
            try:
                supabase.table("scan_sources").update({
                    "relevance_score": round(new_rel, 4),
                }).eq("id", source["id"]).execute()
                updated += 1
            except:
                pass

    log_to_supabase("source_refresh", "refresh", 1,
        f"{len(sources)} fonti analizzate", f"{updated} aggiornate",
        "none")

    return {"status": "completed", "sources": len(sources), "updated": updated}


# ============================================================
# SOURCES CLEANUP WEEKLY — pulizia fonti con soglie dinamiche
# ============================================================

def run_sources_cleanup_weekly():
    """
    Pulizia fonti settimanale con soglie dinamiche.
    - Archivia il 20% peggiore (per avg_problem_score) tra fonti con 5+ scan
    - Archivia sempre fonti con avg_problem_score < 0.25 dopo 5+ scan
    Eseguita ogni lunedì dal Cloud Scheduler.
    """
    logger.info("Sources cleanup weekly starting...")

    try:
        sources_result = supabase.table("scan_sources").select("*").eq("status", "active").execute()
        all_sources = sources_result.data or []
    except Exception as e:
        logger.error(f"[CLEANUP] Errore fetch fonti: {e}")
        return {"status": "error", "error": str(e)}

    # Fonti qualificate: almeno 5 problemi trovati
    qualified = [s for s in all_sources if (s.get("problems_found") or 0) >= 5]

    archived_sources = []
    dynamic_threshold = None
    absolute_threshold = 0.25

    if qualified:
        # Ordina per avg_problem_score crescente (peggiori prima)
        qualified_sorted = sorted(qualified, key=lambda x: x.get("avg_problem_score") or 0)

        # Calcola soglia: archivia il 20% peggiore
        bottom_count = max(1, int(len(qualified_sorted) * 0.20))
        bottom_sources = qualified_sorted[:bottom_count]

        if bottom_sources:
            dynamic_threshold = bottom_sources[-1].get("avg_problem_score") or 0

        # Archivia: nel 20% peggiore OPPURE sotto soglia assoluta
        for s in qualified_sorted:
            score = s.get("avg_problem_score") or 0
            if s in bottom_sources or score < absolute_threshold:
                try:
                    threshold_used = min(dynamic_threshold or 0, absolute_threshold) if s in bottom_sources else absolute_threshold
                    supabase.table("scan_sources").update({
                        "status": "archived",
                        "notes": f"Archiviata automaticamente {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: score {score:.2f} (soglia {threshold_used:.2f})",
                    }).eq("id", s["id"]).execute()
                    archived_sources.append({"id": s["id"], "name": s["name"], "score": round(score, 3)})
                    logger.info(f"[CLEANUP] Archiviata: {s['name']} (score {score:.2f})")
                except Exception as e:
                    logger.warning(f"[CLEANUP] Errore archiviazione {s['name']}: {e}")

    # Ricalcola conteggio attive dopo pulizia
    active_count = len(all_sources) - len(archived_sources)

    # Aggiorna source_thresholds
    try:
        supabase.table("source_thresholds").insert({
            "dynamic_threshold": dynamic_threshold,
            "absolute_threshold": absolute_threshold,
            "active_sources_count": active_count,
            "archived_this_week": len(archived_sources),
            "target_active_pct": 0.80,
            "update_reason": "pulizia settimanale automatica",
        }).execute()
    except Exception as e:
        logger.warning(f"[CLEANUP] Errore salvataggio source_thresholds: {e}")

    # Notifiche a Mirco — Fix 3: pulsanti Riattiva/Ok
    SEP = "━━━━━━━━━━━━━━━"
    if archived_sources:
        lines = [f"\U0001f4e6 Pulizia fonti settimanale: {len(archived_sources)} archiviate, soglia: {dynamic_threshold:.2f if dynamic_threshold else 'N/A'}"]
        for a in archived_sources:
            lines.append(f"- {a['name']} (score {a['score']:.2f})")
        src_msg = "\n".join(lines)
        # Pulsanti Riattiva (max 3) + Ok
        src_keyboard_rows = []
        for a in archived_sources[:3]:
            src_keyboard_rows.append([
                {"text": f"\U0001f504 Riattiva: {a['name'][:20]}", "callback_data": f"source_reactivate:{a['id']}"},
            ])
        src_keyboard_rows.append([
            {"text": "\u2705 Ok, capito", "callback_data": "source_archive_ok"},
        ])
        src_reply_markup = {"inline_keyboard": src_keyboard_rows}
        chat_id_src = get_telegram_chat_id()
        if chat_id_src and TELEGRAM_BOT_TOKEN:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id_src, "text": src_msg, "reply_markup": src_reply_markup},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"[CLEANUP] notify error: {e}")
                notify_telegram(src_msg)
        else:
            notify_telegram(src_msg)

    # Report settimanale soglie
    dt_str = f"{dynamic_threshold:.2f}" if dynamic_threshold is not None else "N/A"
    report = (
        f"📊 AGGIORNAMENTO SOGLIE FONTI\n{SEP}\n"
        f"Fonti attive: {active_count}/{len(all_sources)}\n"
        f"Fonti archiviate questa settimana: {len(archived_sources)}\n"
        f"Soglia dinamica attuale: {dt_str}\n"
        f"Soglia assoluta: {absolute_threshold}\n"
        f"{SEP}"
    )
    notify_telegram(report)

    log_to_supabase("source_cleanup", "weekly_cleanup", 1,
        f"{len(all_sources)} fonti analizzate",
        f"{len(archived_sources)} archiviate, soglia={dt_str}",
        "none")

    return {
        "status": "completed",
        "total_sources": len(all_sources),
        "archived": len(archived_sources),
        "dynamic_threshold": dynamic_threshold,
        "active_count": active_count,
    }


# ============================================================
# LAYER 3: EXECUTION PIPELINE — init_project, spec, landing, build, validation
# ============================================================

import re as _re
import base64 as _base64

GITHUB_TOKEN_AR = os.getenv("GITHUB_TOKEN")
GITHUB_API_BASE_AR = "https://api.github.com"
GITHUB_OWNER_AR = "mircocerisola"


def _github_project_api(method, repo, endpoint, data=None):
    """GitHub API per un repo di progetto specifico."""
    if not GITHUB_TOKEN_AR:
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN_AR}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"{GITHUB_API_BASE_AR}/repos/{repo}{endpoint}"
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=30)
        elif method == "PUT":
            r = requests.put(url, headers=headers, json=data, timeout=30)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=data, timeout=30)
        else:
            return None
        if r.status_code in (200, 201):
            return r.json()
        logger.warning(f"[GITHUB_AR] {method} {repo}{endpoint} -> {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"[GITHUB_AR] {e}")
        return None


def _commit_to_project_repo(repo, path, content, message):
    """Committa un file (crea o aggiorna) su un repo di progetto."""
    content_b64 = _base64.b64encode(content.encode("utf-8")).decode("utf-8")
    existing = _github_project_api("GET", repo, f"/contents/{path}")
    data = {"message": message, "content": content_b64}
    if existing and "sha" in existing:
        data["sha"] = existing["sha"]
    result = _github_project_api("PUT", repo, f"/contents/{path}", data)
    return result is not None


def _create_github_repo(slug, name):
    """Crea repo privato brain-[slug] tramite GitHub API."""
    if not GITHUB_TOKEN_AR:
        logger.warning("[INIT] GITHUB_TOKEN non disponibile")
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN_AR}",
        "Accept": "application/vnd.github.v3+json",
    }
    try:
        r = requests.post(
            f"{GITHUB_API_BASE_AR}/user/repos",
            headers=headers,
            json={
                "name": f"brain-{slug}",
                "private": True,
                "description": f"brAIn Project: {name}",
                "auto_init": True,
            },
            timeout=30,
        )
        if r.status_code in (200, 201):
            data = r.json()
            return data.get("full_name")
        logger.warning(f"[INIT] GitHub repo creation -> {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"[INIT] GitHub repo error: {e}")
        return None


def _get_telegram_group_id():
    """Legge telegram_group_id da org_config. Gestisce sia string che int (jsonb)."""
    try:
        r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
        if r.data:
            val = r.data[0]["value"]
            if isinstance(val, (int, float)):
                return int(val)
            return json.loads(str(val))
    except Exception as e:
        logger.warning(f"[GROUP_ID] {e}")
    return None


def _create_forum_topic(group_id, name):
    """Crea Forum Topic nel gruppo Telegram. Ritorna topic_id."""
    if not TELEGRAM_BOT_TOKEN or not group_id:
        return None
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/createForumTopic",
            json={"chat_id": group_id, "name": f"\U0001f3d7\ufe0f Cantiere {name}", "icon_color": 7322096},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("result", {}).get("message_thread_id")
        logger.warning(f"[INIT] createForumTopic -> {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[INIT] Forum topic error: {e}")
    return None


def _send_to_topic(group_id, topic_id, text, reply_markup=None):
    """Invia messaggio nel Forum Topic del progetto."""
    if not TELEGRAM_BOT_TOKEN:
        return
    # Fallback a DM se group non configurato
    chat_id = group_id if group_id else get_telegram_chat_id()
    payload = {"chat_id": chat_id, "text": text}
    if group_id and topic_id:
        payload["message_thread_id"] = topic_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception as e:
        logger.error(f"[TG_TOPIC] {e}")


def _slugify(text, max_len=20):
    """Genera slug da testo: lowercase, trattini, max_len chars."""
    slug = text.lower().strip()
    slug = _re.sub(r"[^\w\s-]", "", slug)
    slug = _re.sub(r"[\s_]+", "-", slug)
    slug = _re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len].rstrip("-")


# ---- SUPABASE MANAGEMENT API + GCP SECRET MANAGER ----

def _create_supabase_project(slug):
    """Crea un progetto Supabase via Management API. Best-effort, ritorna (db_url, db_key) o (None, None)."""
    if not SUPABASE_ACCESS_TOKEN:
        logger.warning("[SUPABASE_MGMT] SUPABASE_ACCESS_TOKEN mancante, skip creazione DB separato")
        return None, None
    try:
        resp = http_requests.post(
            "https://api.supabase.com/v1/projects",
            headers={
                "Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "name": f"brain-{slug}",
                "organization_id": os.getenv("SUPABASE_ORG_ID", ""),
                "plan": "free",
                "region": "eu-central-1",
                "db_pass": _generate_db_pass(),
            },
            timeout=60,
        )
        if resp.status_code not in (200, 201):
            logger.warning(f"[SUPABASE_MGMT] Creazione fallita {resp.status_code}: {resp.text[:200]}")
            return None, None
        data = resp.json()
        project_ref = data.get("id", "")
        db_url = f"postgresql://postgres@db.{project_ref}.supabase.co:5432/postgres"
        # Recupera API key (anon)
        keys_resp = http_requests.get(
            f"https://api.supabase.com/v1/projects/{project_ref}/api-keys",
            headers={"Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}"},
            timeout=30,
        )
        anon_key = ""
        if keys_resp.status_code == 200:
            for k in keys_resp.json():
                if k.get("name") == "anon":
                    anon_key = k.get("api_key", "")
                    break
        logger.info(f"[SUPABASE_MGMT] Creato brain-{slug} ref={project_ref}")
        return db_url, anon_key
    except Exception as e:
        logger.warning(f"[SUPABASE_MGMT] {e}")
        return None, None


def _generate_db_pass():
    import secrets, string
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(24))


def _save_gcp_secret(secret_id, value):
    """Salva valore in GCP Secret Manager via REST API. Best-effort."""
    project_num = os.getenv("GCP_PROJECT_NUMBER", "402184600300")
    project_id_gcp = "brain-core-487914"
    # Usa metadata token su Cloud Run
    try:
        meta = http_requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        access_token = meta.json().get("access_token", "")
    except Exception:
        logger.warning(f"[GCP_SECRET] Impossibile ottenere metadata token (locale?)")
        return False
    if not access_token:
        return False
    base = f"https://secretmanager.googleapis.com/v1/projects/{project_id_gcp}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    import base64
    encoded = base64.b64encode(value.encode()).decode()
    # Crea secret se non esiste
    try:
        http_requests.post(
            f"{base}/secrets",
            headers=headers,
            json={"replication": {"automatic": {}}},
            params={"secretId": secret_id},
            timeout=15,
        )
    except Exception:
        pass
    # Aggiungi versione
    try:
        resp = http_requests.post(
            f"{base}/secrets/{secret_id}:addVersion",
            headers=headers,
            json={"payload": {"data": encoded}},
            timeout=15,
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"[GCP_SECRET] {e}")
        return False


def get_project_db(project_id):
    """Ritorna connessione psycopg2 al DB separato del progetto, o None."""
    try:
        import psycopg2
        proj = supabase.table("projects").select("db_url,db_key_secret_name").eq("id", project_id).execute()
        if not proj.data:
            return None
        project = proj.data[0]
        db_url = project.get("db_url")
        if not db_url:
            return None
        secret_name = project.get("db_key_secret_name", "")
        # Recupera password da Secret Manager
        db_pass = ""
        if secret_name:
            project_id_gcp = "brain-core-487914"
            try:
                meta = http_requests.get(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                    headers={"Metadata-Flavor": "Google"}, timeout=5,
                )
                access_token = meta.json().get("access_token", "")
                if access_token:
                    resp = http_requests.get(
                        f"https://secretmanager.googleapis.com/v1/projects/{project_id_gcp}/secrets/{secret_name}/versions/latest:access",
                        headers={"Authorization": f"Bearer {access_token}"}, timeout=10,
                    )
                    if resp.status_code == 200:
                        import base64
                        db_pass = base64.b64decode(resp.json()["payload"]["data"]).decode()
            except Exception as e:
                logger.warning(f"[GET_PROJECT_DB] secret fetch: {e}")
        conn = psycopg2.connect(db_url.replace("postgresql://postgres@", f"postgresql://postgres:{db_pass}@"),
                                sslmode="require")
        return conn
    except Exception as e:
        logger.warning(f"[GET_PROJECT_DB] {e}")
        return None


# ---- SPEC GENERATOR (inlined) ----

SPEC_SYSTEM_PROMPT_AR = """Sei l'Architect di brAIn, un'organizzazione AI-native che costruisce prodotti con marginalita' alta.
Genera un SPEC.md COMPLETO e OTTIMIZZATO PER AI CODING AGENTS (Claude Code).

REGOLE:
- Ogni fase di build: task ATOMICI con comandi copy-paste pronti
- Nessuna ambiguita' tecnica: endpoint, schemi DB, variabili d'ambiente tutti espliciti
- Stack: Python + Supabase + Google Cloud Run (sempre, salvo eccezioni giustificate)
- Costo infrastruttura target: < 50 EUR/mese
- Deploy target: Google Cloud Run europe-west3, Container Docker
- Modelli LLM: usa SEMPRE Claude API (claude-haiku-4-5 per task veloci, claude-sonnet-4-5 per task complessi). NON usare mai GPT, OpenAI, Gemini o altri provider LLM.

STRUTTURA OBBLIGATORIA (usa esattamente questi header):

## 1. Sintesi del Progetto
Una frase: cosa fa, per chi, perche' vale.

## 2. Problema e Target Customer
Target specifico (professione + eta' + contesto), geografia, frequenza problema, pain intensity.

## 3. Soluzione Proposta
Funzionalita' core MVP (massimo 3), value proposition in 2 righe, differenziatore chiave.

## 4. Analisi Competitiva e Differenziazione
Top 3 competitor con pricing, nostro vantaggio competitivo concreto.

## 5. Architettura Tecnica e Stack
Diagramma testuale del flusso dati, componenti, API utilizzate, schema DB (tabelle + colonne principali).

## 6. KPI e Metriche di Successo
KPI primario (con target settimana 4 e settimana 12), revenue target mese 3, criteri SCALE/PIVOT/KILL.

## 7. Variabili d'Ambiente Necessarie
Lista completa ENV VAR con descrizione (una per riga, formato KEY=descrizione).

## 8. Fasi di Build MVP
Fase 1: Setup repo e struttura base
Fase 2: Core logic [nome funzionalita']
Fase 3: Interfaccia utente / API
Fase 4: Test, deploy Cloud Run, monitoraggio
Ogni fase: lista task atomici, tempo stimato, comandi chiave.

## 9. Go-To-Market — Primo Cliente
Come acquisire il primo cliente pagante in 14 giorni. Canale specifico, messaggio, pricing iniziale.

## 10. Roadmap Post-MVP
3 iterazioni successive (settimane 4, 8, 12) con funzionalita' e ricavi target.

DOPO LA SEZIONE 10, includi OBBLIGATORIAMENTE questo blocco (NON omettere, NON modificare i marker):

<!-- JSON_SPEC:
{
  "stack": ["elenco", "tecnologie", "usate"],
  "kpis": {
    "primary": "nome KPI principale",
    "target_week4": 0,
    "target_week12": 0,
    "revenue_target_month3_eur": 0
  },
  "mvp_build_time_days": 0,
  "mvp_cost_eur": 0
}
:JSON_SPEC_END -->"""


SPEC_HUMAN_SYSTEM_PROMPT = """Sei un consulente di business che spiega un progetto in modo chiaro a un imprenditore.
Dato un SPEC tecnico, genera una versione leggibile per Mirco (CEO, non tecnico profondo).

FORMATO OBBLIGATORIO (testo piano, NO markdown asterischi, max 900 caratteri totali):

[NOME PROGETTO]
━━━━━━━━━━━━━━━
Problema: [1 riga, cosa risolve]
Target: [chi sono i clienti, 1 riga]
Soluzione: [cosa fa concretamente, 1-2 righe]
━━━━━━━━━━━━━━━
Revenue model: [come guadagna]
Primo cliente: [come acquisire in 14gg, 1 riga]
KPI principale: [metrica + target settimana 4]
━━━━━━━━━━━━━━━
Build: [N giorni] | Costo infra: EUR [N]/mese
Rischio principale: [1 riga onesta]
━━━━━━━━━━━━━━━

Risposta SOLO con questo formato, niente altro, niente introduzioni."""


def run_spec_generator(project_id):
    """Genera SPEC.md per il progetto.
    Legge TUTTI i dati ESCLUSIVAMENTE da Supabase (projects + solutions + problems).
    NON usa sessioni Telegram, contesti conversazionali o dati esterni alla DB.
    Fail esplicito se bos_id o solution mancano — mai generare da dati vuoti.
    """
    start = time.time()
    logger.info(f"[SPEC] Avvio per project_id={project_id}")

    # 1. Carica progetto da DB
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            logger.error(f"[SPEC] Project {project_id} non trovato in DB")
            return {"status": "error", "error": f"project {project_id} not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    logger.info(f"[SPEC] Generando SPEC per progetto: {project.get('name', 'N/A')!r} (id={project_id})")

    solution_id = project.get("bos_id")
    github_repo = project.get("github_repo", "")
    bos_score = float(project.get("bos_score") or 0)

    # 2. VALIDAZIONE ESPLICITA: bos_id obbligatorio
    if not solution_id:
        err = (f"project {project_id} ha bos_id=NULL — "
               "impossibile generare SPEC senza BOS associato dal database")
        logger.error(f"[SPEC] {err}")
        return {"status": "error", "error": err}

    # 3. Carica soluzione BOS da DB — UNICA fonte di verità
    try:
        sol = supabase.table("solutions").select("*").eq("id", int(solution_id)).execute()
        if not sol.data:
            err = f"solution {solution_id} non trovata in DB (bos_id di project {project_id})"
            logger.error(f"[SPEC] {err}")
            return {"status": "error", "error": err}
        solution = sol.data[0]
    except Exception as e:
        return {"status": "error", "error": f"solution load error: {e}"}

    logger.info(f"[SPEC] Solution caricata: id={solution_id} title={solution.get('title','')[:60]!r}")

    # 4. Carica problema associato dalla DB (se problem_id disponibile)
    problem = {}
    problem_id = solution.get("problem_id")
    if problem_id:
        try:
            prob = supabase.table("problems").select("*").eq("id", int(problem_id)).execute()
            if prob.data:
                problem = prob.data[0]
                logger.info(f"[SPEC] Problem caricato: id={problem_id} title={problem.get('title','')[:60]!r}")
            else:
                logger.warning(f"[SPEC] Problem {problem_id} non trovato in DB — sezione PROBLEMA limitata")
        except Exception as e:
            logger.warning(f"[SPEC] Problem load error: {e}")
    else:
        logger.warning(f"[SPEC] Solution {solution_id} ha problem_id=NULL — sezione PROBLEMA derivata dalla soluzione")

    # 5. Carica feasibility scores dalla DB
    feasibility_details = ""
    try:
        fe = supabase.table("solution_scores").select("*").eq("solution_id", int(solution_id)).execute()
        if fe.data:
            feasibility_details = json.dumps(fe.data[0], default=str)[:600]
            logger.info(f"[SPEC] Feasibility scores caricati per solution {solution_id}")
    except Exception as e:
        logger.warning(f"[SPEC] Feasibility load: {e}")

    # 6. Estrai campi — SOLO da DB, nessun fallback a contesti esterni
    sol_title       = solution.get("title") or project.get("name") or "MVP"
    sol_description = solution.get("description") or ""
    sol_sector      = solution.get("sector") or ""
    sol_sub_sector  = solution.get("sub_sector") or ""
    sol_market      = str(solution.get("market_analysis") or "")[:400]
    sol_feasibility = float(solution.get("feasibility_score") or bos_score)
    sol_revenue     = solution.get("revenue_model") or ""
    sol_advantage   = solution.get("competitive_advantage") or ""
    sol_target      = solution.get("target_customer") or ""

    prob_title       = problem.get("title") or ""
    prob_description = problem.get("description") or ""
    prob_target      = problem.get("target_customer") or sol_target
    prob_geography   = problem.get("target_geography") or ""
    prob_urgency     = str(problem.get("urgency") or "")
    prob_evidence    = (problem.get("evidence") or "")[:300]
    prob_why_now     = (problem.get("why_now") or "")[:300]

    logger.info(
        f"[SPEC] Dati pronti per Claude: sol={sol_title!r:.60} prob={prob_title!r:.40} "
        f"has_problem={bool(problem)} has_feasibility={bool(feasibility_details)}"
    )

    # 7. Ricerca competitiva via Perplexity (unico dato esterno al DB)
    competitor_query = (f"competitor analysis '{sol_title}' settore '{sol_sector}' "
                        f"— top solutions, pricing, market size 2026")
    competitor_info = search_perplexity(competitor_query) or "Dati competitivi non disponibili."

    # 8. User prompt — SOLO dati da DB, nessun contesto sessione
    user_prompt = f"""Genera il SPEC.md per questo progetto.
FONTE DATI: record Supabase — solutions.id={solution_id}, problems.id={problem_id or 'non collegato'}.
NON inventare dati non presenti qui sotto. Se un campo mostra "(non disponibile)", derivalo logicamente dalla descrizione della soluzione.

=== PROGETTO ===
Nome: {project.get("name") or sol_title}
Slug: {project.get("slug") or ""}
BOS score: {bos_score:.2f}/1.00

=== SOLUZIONE BOS (id={solution_id}) ===
Titolo: {sol_title}
Descrizione: {sol_description[:800]}
Settore: {sol_sector} / {sol_sub_sector}
Target customer: {sol_target or "(vedi problema)"}
Revenue model: {sol_revenue or "da definire in base al settore"}
Vantaggio competitivo: {sol_advantage or "(non disponibile)"}
Market analysis: {sol_market or "(non disponibile)"}
Feasibility score: {sol_feasibility:.2f}/1.00

=== PROBLEMA ORIGINALE (id={problem_id or "non collegato"}) ===
Titolo: {prob_title or "(non disponibile — deriva dalla soluzione)"}
Descrizione: {prob_description[:600] or "(non disponibile)"}
Target: {prob_target or "(non disponibile)"}
Geografia: {prob_geography or "(non disponibile)"}
Urgency score: {prob_urgency or "(non disponibile)"}
Evidence: {prob_evidence or "(non disponibile)"}
Why now: {prob_why_now or "(non disponibile)"}

=== ANALISI COMPETITIVA (Perplexity) ===
{competitor_info[:800]}

=== FEASIBILITY DETAILS ===
{feasibility_details or "Non disponibile"}

Genera il SPEC.md completo seguendo esattamente la struttura richiesta."""

    tokens_in = tokens_out = 0
    spec_md = ""
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8000,
            system=SPEC_SYSTEM_PROMPT_AR,
            messages=[{"role": "user", "content": user_prompt}],
        )
        spec_md = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[SPEC] Claude error: {e}")
        log_to_supabase("spec_generator", "spec_generate", 3, f"project={project_id}", str(e),
                        "claude-sonnet-4-5", 0, 0, 0, int((time.time() - start) * 1000), "error", str(e))
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    stack = []
    kpis = {}
    try:
        match = _re.search(r'<!-- JSON_SPEC:\s*(.*?)\s*:JSON_SPEC_END -->', spec_md, _re.DOTALL)
        if match:
            spec_meta = json.loads(match.group(1))
            stack = spec_meta.get("stack", [])
            kpis = spec_meta.get("kpis", {})
            if spec_meta.get("mvp_build_time_days"):
                kpis["mvp_build_time_days"] = spec_meta["mvp_build_time_days"]
            if spec_meta.get("mvp_cost_eur"):
                kpis["mvp_cost_eur"] = spec_meta["mvp_cost_eur"]
    except Exception as e:
        logger.warning(f"[SPEC] JSON extraction error: {e}")

    # MACRO-TASK 4: genera SPEC_HUMAN via Haiku
    spec_human_md = ""
    try:
        human_resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=SPEC_HUMAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": spec_md[:4000]}],
        )
        spec_human_md = human_resp.content[0].text.strip()
        cost += (human_resp.usage.input_tokens * 0.8 + human_resp.usage.output_tokens * 4.0) / 1_000_000
        logger.info(f"[SPEC] SPEC_HUMAN generata: {len(spec_human_md)} chars")
    except Exception as e:
        logger.warning(f"[SPEC] SPEC_HUMAN generation error: {e}")

    try:
        supabase.table("projects").update({
            "spec_md": spec_md,
            "spec_human_md": spec_human_md or None,
            "stack": json.dumps(stack) if stack else None,
            "kpis": json.dumps(kpis) if kpis else None,
            "status": "spec_generated",
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[SPEC] DB update error: {e}")

    if github_repo:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        # Commit come SPEC_CODE.md (versione tecnica per AI agents)
        ok = _commit_to_project_repo(
            github_repo, "SPEC_CODE.md", spec_md,
            f"feat: SPEC_CODE.md rigenerato da brAIn — {ts}",
        )
        if ok:
            logger.info(f"[SPEC] SPEC_CODE.md committato su {github_repo}")
        # Mantieni anche SPEC.md per compatibilità backward
        _commit_to_project_repo(github_repo, "SPEC.md", spec_md,
                                f"feat: SPEC.md sync — {ts}")
        if spec_human_md:
            _commit_to_project_repo(github_repo, "SPEC_HUMAN.md", spec_human_md,
                                    f"feat: SPEC_HUMAN.md generato — {ts}")

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("spec_generator", "spec_generate", 3,
                    f"project={project_id} solution={solution_id} problem={problem_id}",
                    f"SPEC {len(spec_md)} chars stack={stack}",
                    "claude-sonnet-4-5", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[SPEC] Completato project={project_id} in {duration_ms}ms spec_len={len(spec_md)}")
    return {"status": "ok", "project_id": project_id, "spec_length": len(spec_md),
            "solution_id": solution_id, "problem_id": problem_id,
            "stack": stack, "kpis": kpis, "cost_usd": round(cost, 5)}


# ---- LANDING PAGE GENERATOR (inlined) ----

LP_SYSTEM_PROMPT_AR = """Sei un designer/copywriter esperto. Genera HTML single-file per una landing page MVP.

REQUISITI:
- HTML completo (<!DOCTYPE html> ... </html>), CSS inline nel <style>, nessuna dipendenza esterna
- Mobile-first, responsive, caricamento istantaneo
- Colori: bianco + un colore primario coerente col settore
- Font: system-ui / -apple-system (nessun Google Fonts)
- NO JavaScript complesso

STRUTTURA OBBLIGATORIA:
1. Hero section: headline + sottotitolo + CTA button
2. 3 benefit cards con icona emoji + titolo + descrizione 1 riga
3. Social proof placeholder: "[NUMERO] clienti gia' iscritti"
4. Form contatto: nome + email + messaggio + button
5. Footer: "Prodotto da brAIn — AI-native organization"

REGOLE COPYWRITING:
- Headline: beneficio concreto in < 8 parole (NON il nome del prodotto)
- CTA: verbo d'azione + beneficio
- Nessun gergo tecnico

Rispondi SOLO con il codice HTML, senza spiegazioni, senza blocchi markdown."""


def run_landing_page_generator(project_id):
    """Genera HTML landing page e salva in projects.landing_page_html."""
    start = time.time()
    logger.info(f"[LP] Avvio per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", "MVP")
    spec_md = project.get("spec_md", "")

    solution_desc = ""
    target_customer = ""
    problem_desc = ""
    bos_id = project.get("bos_id")
    if bos_id:
        try:
            sol = supabase.table("solutions").select("title,description,problem_id").eq("id", bos_id).execute()
            if sol.data:
                solution_desc = sol.data[0].get("description", "")[:400]
                prob_id = sol.data[0].get("problem_id")
                if prob_id:
                    prob = supabase.table("problems").select("title,description,target_customer").eq("id", prob_id).execute()
                    if prob.data:
                        target_customer = prob.data[0].get("target_customer", "")
                        problem_desc = prob.data[0].get("description", "")[:300]
        except Exception as e:
            logger.warning(f"[LP] Solution/problem load: {e}")

    user_prompt = f"""Progetto: {name}
Target customer: {target_customer or "professionisti e PMI"}
Problema risolto: {problem_desc or "inefficienza nel flusso di lavoro"}
Soluzione: {solution_desc or spec_md[:300]}
Genera la landing page HTML completa."""

    tokens_in = tokens_out = 0
    html = ""
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=3000,
            system=LP_SYSTEM_PROMPT_AR,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_html = response.content[0].text.strip()
        # Strip markdown code fences se il modello le ha aggiunte
        if raw_html.startswith("```"):
            lines = raw_html.split("\n")
            # rimuovi prima riga (```html o ```) e ultima riga (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
        else:
            lines = raw_html.split("\n")
        html = "\n".join(lines).strip()
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[LP] Claude error: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    try:
        supabase.table("projects").update({"landing_page_html": html}).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[LP] DB update error: {e}")

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("landing_page_generator", "lp_generate", 3,
                    f"project={project_id}", f"HTML {len(html)} chars",
                    "claude-haiku-4-5", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[LP] Completato project={project_id} in {duration_ms}ms")
    return {"status": "ok", "project_id": project_id, "html_length": len(html), "cost_usd": round(cost, 6)}


# ---- BUILD PROMPT GENERATOR ----

def generate_build_prompt(project_id):
    """Genera il prompt Claude Code, lo salva in DB e avvia il build agent automatico."""
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", "MVP")
    slug = project.get("slug", "")
    github_repo = project.get("github_repo", "")
    stack = project.get("stack") or []
    kpis = project.get("kpis") or {}
    topic_id = project.get("topic_id")

    if isinstance(stack, str):
        try:
            stack = json.loads(stack)
        except:
            stack = []
    if isinstance(kpis, str):
        try:
            kpis = json.loads(kpis)
        except:
            kpis = {}

    # Estrai env vars dal spec_md (sezione 7)
    env_vars_section = ""
    spec_md = project.get("spec_md", "")
    if spec_md:
        match = _re.search(r'## 7\. Variabili d.Ambiente.*?\n(.*?)(?=## 8\.)', spec_md, _re.DOTALL)
        if match:
            env_vars_section = match.group(1).strip()[:800]

    service_name = f"{slug}-mvp" if slug else "mvp"
    artifact_tag = f"europe-west3-docker.pkg.dev/brain-core-487914/brain-repo/{service_name}:latest"

    prompt = f"""Esegui con --dangerously-skip-permissions. Non chiedere autorizzazione per nessuna operazione. Non fermarti per conferme intermedie. Procedi autonomamente fino al completamento.

Repo progetto: brain-{slug} (privato su GitHub mircocerisola)
La SPEC completa e' nel campo spec_md del progetto in Supabase (project_id={project_id}).

Costruisci l'MVP "{name}" seguendo esattamente le Fasi di Build nella sezione 8 del SPEC.md, in ordine (Fase 1 -> Fase 4).

Dopo ogni fase, committa su GitHub con un messaggio descrittivo (es: "feat(fase-1): setup repo e struttura base").

Dopo la Fase 4:
  - Pusha su origin main
  - Build Docker: gcloud builds submit --region=europe-west3 --tag {artifact_tag} .
  - Deploy Cloud Run: gcloud run deploy {service_name} --image {artifact_tag} --region europe-west3 --platform managed --quiet

Env vars necessarie (configurare prima del deploy):
{env_vars_section if env_vars_section else "(vedi sezione 7 del SPEC.md)"}

Stack: {", ".join(stack) if stack else "Python + Supabase + Cloud Run"}
Modelli LLM: usa SEMPRE Claude API (claude-haiku-4-5 o claude-sonnet-4-5), MAI OpenAI/GPT.

REGOLA ASSOLUTA: zero decisioni architetturali autonome.
Committa ogni file creato/modificato — mai lavorare in locale senza committare."""

    # Salva in DB (senza mandarlo su Telegram)
    try:
        supabase.table("projects").update({"build_prompt": prompt}).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[BUILD_PROMPT] DB update error: {e}")

    # Notifica breve: build avviato
    group_id = _get_telegram_group_id()
    _send_to_topic(group_id, topic_id,
                   f"\U0001f6e0\ufe0f Build avviato per \"{name}\".\nGenerando codice MVP in corso...")

    # Avvia build agent in background (non blocca)
    import threading as _threading_build
    _threading_build.Thread(target=run_build_agent, args=(project_id,), daemon=True).start()

    logger.info(f"[BUILD_PROMPT] Generato per project={project_id}, build agent avviato")
    return {"status": "ok", "project_id": project_id, "prompt_length": len(prompt)}


FASE_DESCRIPTIONS = {
    1: "Struttura progetto (main.py, requirements.txt, Dockerfile, .env.example)",
    2: "Logica core e integrazione DB",
    3: "API endpoints e business logic",
    4: "Deploy, monitoring e ottimizzazioni",
}


def run_build_agent(project_id):
    """Build agent autonomo: genera Fase 1 (struttura base), committa su GitHub, notifica per review."""
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return
        project = proj.data[0]
    except Exception as e:
        logger.error(f"[BUILD_AGENT] DB load: {e}")
        return

    name = project.get("name", "MVP")
    github_repo = project.get("github_repo", "")
    spec_md = project.get("spec_md", "")
    stack = project.get("stack") or []
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()

    if isinstance(stack, str):
        try:
            stack = json.loads(stack)
        except:
            stack = []

    if not spec_md or not github_repo:
        _send_to_topic(group_id, topic_id, f"\u274c Build {name}: SPEC o repo mancanti. Impossibile procedere.")
        return

    stack_str = ", ".join(stack) if stack else "Python, Supabase, Cloud Run"

    slug = project.get("slug", "")

    # Genera solo Fase 1: struttura base
    build_prompt = f"""Esegui con --dangerously-skip-permissions. Non chiedere autorizzazione per nessuna operazione. Non fermarti per conferme.

Sei un senior Python developer. Genera il codice per la Fase 1 (struttura base) dell'MVP "{name}".

SPEC.md (estratto):
{spec_md[:5000]}

REQUISITI FASE 1:
- Stack: {stack_str}
- Modelli LLM: usa SEMPRE Claude API (claude-haiku-4-5 o claude-sonnet-4-5), NON OpenAI/GPT
- Genera: main.py (o app.py), requirements.txt, Dockerfile, .env.example
- Il codice deve essere funzionante e deployabile su Google Cloud Run europe-west3
- Usa Supabase per il database (variabili SUPABASE_URL, SUPABASE_KEY)
- Struttura pulita: solo file essenziali per far partire il progetto

FORMATO OUTPUT per ogni file:
=== FILE: nome_file ===
[contenuto del file]
=== END FILE ===

Genera SOLO i file della struttura base."""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8000,
            messages=[{"role": "user", "content": build_prompt}],
        )
        code_output = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[BUILD_AGENT] Claude error: {e}")
        _send_to_topic(group_id, topic_id, f"\u274c Build {name} fallito: {e}")
        return

    # Parse e commit dei file generati
    file_pattern = _re.compile(r'=== FILE: (.+?) ===\n(.*?)(?==== END FILE ===)', _re.DOTALL)
    matches = list(file_pattern.finditer(code_output))
    files_committed = 0

    for match in matches:
        filepath = match.group(1).strip()
        content = match.group(2).strip()
        if content and filepath:
            ok = _commit_to_project_repo(
                github_repo, filepath, content,
                f"feat(fase-1): {filepath}",
            )
            if ok:
                files_committed += 1

    # Fallback se nessun file parsato
    if files_committed == 0 and code_output:
        _commit_to_project_repo(github_repo, "main.py", code_output, "feat(fase-1): MVP structure")
        files_committed = 1

    # Salva log iterazione su GitHub
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H")
    iter_content = f"# Fase 1 — {FASE_DESCRIPTIONS[1]}\n\nData: {datetime.now(timezone.utc).isoformat()}\n\nFile generati: {files_committed}\n\n---\n\n{code_output}"
    _commit_to_project_repo(github_repo, f"iterations/{ts}_fase1.md", iter_content, "log(fase-1): iterazione salvata")

    # Aggiorna status e build_phase
    try:
        supabase.table("projects").update({
            "status": "review_phase1",
            "build_phase": 1,
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.warning(f"[BUILD_AGENT] DB update status: {e}")

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000
    log_to_supabase("build_agent", "build_fase1", 3,
                    f"project={project_id}", f"{files_committed} file committati",
                    "claude-sonnet-4-5", tokens_in, tokens_out, cost, 0)

    # Card summary — Fix 2
    file_list = "\n".join([f"  \u2022 {m.group(1).strip()}" for m in matches]) if matches else "  \u2022 main.py (fallback)"
    sep = "\u2501" * 15
    result_msg = (
        f"\u256d\u2500\u2500 Fase 1 completata \u2500\u2500\u256e\n"
        f"\U0001f4e6 {FASE_DESCRIPTIONS[1]}\n"
        f"{sep}\n"
        f"\U0001f4c1 File ({files_committed}):\n{file_list}\n"
        f"{sep}\n"
        f"\U0001f4c1 Repo: brain-{slug} (privato)\n"
        f"{sep}\n"
        f"Come si comporta? Cosa vuoi cambiare?"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\u2705 Continua", "callback_data": f"build_continue:{project_id}:1"},
            {"text": "\u270f\ufe0f Modifica", "callback_data": f"build_modify:{project_id}:1"},
        ]]
    }
    _send_to_topic(group_id, topic_id, result_msg, reply_markup=reply_markup)
    logger.info(f"[BUILD_AGENT] Fase 1 completata project={project_id}, {files_committed} file committati")


# ---- ENQUEUE SPEC REVIEW ACTION ----

def _extract_spec_bullets(spec_md):
    """Estrae 3 bullet points dalla SPEC usando Claude Haiku. Max 60 chars ciascuno."""
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": (
                "Analizza questo SPEC e ritorna SOLO 3 bullet points in italiano, "
                "max 60 caratteri ciascuno, separati da newline. "
                "Solo i 3 bullet, niente altro.\n\n" + spec_md[:3000]
            )}],
        )
        text = response.content[0].text.strip()
        bullets = [b.strip().lstrip("\u2022-* ").strip() for b in text.split("\n") if b.strip()]
        bullets = [b[:60] for b in bullets if b][:3]
        while len(bullets) < 3:
            bullets.append("Vedi SPEC per dettagli")
        return bullets
    except Exception as e:
        logger.warning(f"[SPEC_BULLETS] {e}")
        return ["Vedi SPEC per dettagli"] * 3


def enqueue_spec_review_action(project_id):
    """Inserisce azione spec_review in action_queue e invia al topic con inline keyboard.
    MACRO-TASK 4: usa SPEC_HUMAN se disponibile, altrimenti bullets come fallback.
    """
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return
        project = proj.data[0]
    except Exception as e:
        logger.error(f"[SPEC_REVIEW] DB load: {e}")
        return

    name = project.get("name", f"Progetto {project_id}")
    bos_score = project.get("bos_score", 0) or 0
    slug = project.get("slug", "")
    github_repo = project.get("github_repo", "")
    topic_id = project.get("topic_id")
    spec_md = project.get("spec_md", "")
    spec_human_md = project.get("spec_human_md", "")

    # Inserisci in action_queue
    chat_id = get_telegram_chat_id()
    action_db_id = None
    if chat_id:
        try:
            result = supabase.table("action_queue").insert({
                "user_id": int(chat_id),
                "action_type": "spec_review",
                "title": f"SPEC PRONTA \u2014 {name[:60]}",
                "description": f"BOS score: {bos_score:.2f} | Repo: {github_repo}",
                "payload": json.dumps({
                    "project_id": str(project_id),
                    "slug": slug,
                    "github_repo": github_repo,
                }),
                "priority": 8,
                "urgency": 8,
                "importance": 8,
                "status": "pending",
            }).execute()
            if result.data:
                action_db_id = result.data[0]["id"]
        except Exception as e:
            logger.error(f"[SPEC_REVIEW] action_queue insert: {e}")

    sep = "\u2501" * 15

    # MACRO-TASK 4: usa SPEC_HUMAN se disponibile, altrimenti bullets
    if spec_human_md:
        msg = f"{spec_human_md}\n{sep}"
    else:
        bullets = _extract_spec_bullets(spec_md) if spec_md else ["Vedi SPEC per dettagli"] * 3
        msg = (
            f"\U0001f4cb SPEC pronta \u2014 {name}\n"
            f"Punti chiave:\n"
            f"\u2022 {bullets[0]}\n"
            f"\u2022 {bullets[1]}\n"
            f"\u2022 {bullets[2]}\n"
            f"{sep}"
        )

    # MACRO-TASK 4: nuovo layout pulsanti — riga 1: Valida + Modifica, riga 2: Versione completa
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "\u2705 Valida", "callback_data": f"spec_validate:{project_id}"},
                {"text": "\u270f\ufe0f Modifica", "callback_data": f"spec_edit:{project_id}"},
            ],
            [
                {"text": "\U0001f4c4 Versione completa (SPEC_CODE)", "callback_data": f"spec_full:{project_id}"},
            ],
        ]
    }

    group_id = _get_telegram_group_id()
    _send_to_topic(group_id, topic_id, msg, reply_markup=reply_markup)

    logger.info(f"[SPEC_REVIEW] Enqueued action_id={action_db_id} per project={project_id}")


# ---- INIT PROJECT ----

def init_project(solution_id):
    """Inizializza progetto da BOS approvato: DB, GitHub repo, Forum Topic, spec, landing, enqueue review."""
    logger.info(f"[INIT] Avvio per solution_id={solution_id}")

    # 1. Carica soluzione e problema
    try:
        sol = supabase.table("solutions").select("*").eq("id", solution_id).execute()
        if not sol.data:
            logger.error(f"[INIT] Solution {solution_id} non trovata")
            return {"status": "error", "error": "solution not found"}
        solution = sol.data[0]
        sol_title = solution.get("title", f"Project {solution_id}")
        bos_score = float(solution.get("bos_score") or 0)
    except Exception as e:
        logger.error(f"[INIT] Solution load error: {e}")
        return {"status": "error", "error": str(e)}

    # 2. Genera slug unico
    base_slug = _slugify(sol_title)
    slug = base_slug
    # Controlla unicita'
    try:
        existing = supabase.table("projects").select("id").eq("slug", slug).execute()
        if existing.data:
            slug = f"{base_slug[:17]}-{solution_id}"
    except:
        pass

    name = sol_title[:80]

    # 3. Crea record in DB
    project_id = None
    try:
        result = supabase.table("projects").insert({
            "name": name,
            "slug": slug,
            "bos_id": int(solution_id),
            "bos_score": bos_score,
            "status": "init",
        }).execute()
        if result.data:
            project_id = result.data[0]["id"]
        else:
            logger.error("[INIT] Inserimento projects fallito")
            return {"status": "error", "error": "db insert failed"}
    except Exception as e:
        logger.error(f"[INIT] DB insert error: {e}")
        return {"status": "error", "error": str(e)}

    logger.info(f"[INIT] Progetto creato: id={project_id} slug={slug}")

    # 3b. MACRO-TASK 1: Crea Supabase project separato (best-effort)
    db_url, db_anon_key = _create_supabase_project(slug)
    if db_url:
        secret_id = f"brain-{slug}-supabase-key"
        _save_gcp_secret(secret_id, db_anon_key or "")
        try:
            supabase.table("projects").update({
                "db_url": db_url,
                "db_key_secret_name": secret_id,
            }).eq("id", project_id).execute()
        except Exception as e:
            logger.warning(f"[INIT] db_url update error: {e}")
        logger.info(f"[INIT] Supabase separato: {db_url[:60]}")
    else:
        logger.info("[INIT] DB separato non creato (best-effort, procedo senza)")

    # 4. Crea GitHub repo
    github_repo = _create_github_repo(slug, name)
    if github_repo:
        try:
            supabase.table("projects").update({"github_repo": github_repo}).eq("id", project_id).execute()
        except:
            pass
        logger.info(f"[INIT] GitHub repo: {github_repo}")
    else:
        logger.warning(f"[INIT] GitHub repo creation fallita, procedo senza")

    # 5. Crea Forum Topic
    group_id = _get_telegram_group_id()
    topic_id = None
    if group_id:
        topic_id = _create_forum_topic(group_id, name)
        if topic_id:
            try:
                supabase.table("projects").update({"topic_id": topic_id}).eq("id", project_id).execute()
            except:
                pass
            logger.info(f"[INIT] Forum Topic creato: topic_id={topic_id}")
            # Messaggio di benvenuto nel topic
            _send_to_topic(group_id, topic_id,
                           f"\U0001f680 Progetto '{name}' avviato!\nBOS score: {bos_score:.2f}\nGenerazione SPEC in corso...")
    else:
        logger.info("[INIT] telegram_group_id non configurato, Forum Topic non creato")

    # 6. Genera SPEC
    spec_result = run_spec_generator(project_id)
    if spec_result.get("status") != "ok":
        logger.error(f"[INIT] Spec generation fallita: {spec_result}")
        if group_id and topic_id:
            _send_to_topic(group_id, topic_id, f"\u26a0\ufe0f Errore generazione SPEC: {spec_result.get('error')}")
        return {"status": "error", "error": "spec generation failed", "detail": spec_result}

    logger.info(f"[INIT] SPEC generata: {spec_result.get('spec_length')} chars")

    # 7. Genera Landing Page
    lp_result = run_landing_page_generator(project_id)
    if lp_result.get("status") == "ok":
        logger.info(f"[INIT] Landing page generata: {lp_result.get('html_length')} chars")
        if group_id and topic_id:
            _send_to_topic(group_id, topic_id, "Landing page HTML generata. Pronta per deploy quando vuoi.")
    else:
        logger.warning(f"[INIT] Landing page generation fallita (non critico): {lp_result}")

    # 8. Enqueue spec review action con inline keyboard
    enqueue_spec_review_action(project_id)

    logger.info(f"[INIT] Completato: project_id={project_id} slug={slug}")
    return {
        "status": "ok",
        "project_id": project_id,
        "slug": slug,
        "github_repo": github_repo,
        "topic_id": topic_id,
    }


# ---- LEGAL AGENT (MACRO-TASK 2) ----

LEGAL_SYSTEM_PROMPT = """Sei il Legal Agent di brAIn, esperto di diritto digitale europeo (GDPR, AI Act, Direttiva E-Commerce, normativa italiana).
Analizza un progetto e valuta i rischi legali per operare in Europa.

RISPOSTA: JSON puro, nessun testo fuori.
{
  "green_points": ["punto OK 1", "punto OK 2"],
  "yellow_points": ["attenzione 1: cosa fare"],
  "red_points": ["blocco critico 1: perche' blocca il lancio"],
  "report_md": "# Review Legale\\n## Punti OK\\n...\\n## Attenzione\\n...\\n## Blocchi\\n...",
  "can_proceed": true
}

REGOLE:
- green_points: aspetti legalmente OK (es: no dati sensibili, B2B chiaro)
- yellow_points: aspetti da sistemare prima del lancio ma non bloccanti
- red_points: problemi che bloccano il lancio (es: raccolta dati senza consenso, attivita' finanziaria non autorizzata)
- can_proceed: false se ci sono red_points, true altrimenti
- Sii concreto: cita norme specifiche (art. GDPR, AI Act art., ecc.)
- Se settore = health/finance/legal: tratta come alta priorita'"""


def run_legal_review(project_id):
    """MACRO-TASK 2: Review legale del progetto. Triggered dopo validazione SPEC."""
    start = time.time()
    logger.info(f"[LEGAL] Avvio review per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()

    if not spec_md:
        return {"status": "error", "error": "spec_md mancante"}

    # Notifica avvio nel topic
    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, f"\u2696\ufe0f Review legale in corso per {name}...")

    user_prompt = f"""Progetto: {name}
Settore: {sector or "non specificato"}

SPEC (estratto rilevante per analisi legale):
{spec_md[:5000]}

Analizza i rischi legali per operare in Europa con questo progetto."""

    tokens_in = tokens_out = 0
    review_data = {}
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            system=LEGAL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        # Estrai JSON
        import re as _re2
        m = _re2.search(r'\{[\s\S]*\}', raw)
        if m:
            review_data = json.loads(m.group(0))
        else:
            review_data = json.loads(raw)
    except Exception as e:
        logger.error(f"[LEGAL] Claude error: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    green = review_data.get("green_points", [])
    yellow = review_data.get("yellow_points", [])
    red = review_data.get("red_points", [])
    can_proceed = review_data.get("can_proceed", len(red) == 0)
    report_md = review_data.get("report_md", "")

    # Salva in legal_reviews
    review_id = None
    try:
        res = supabase.table("legal_reviews").insert({
            "project_id": project_id,
            "review_type": "spec_review",
            "status": "completed",
            "green_points": json.dumps(green),
            "yellow_points": json.dumps(yellow),
            "red_points": json.dumps(red),
            "report_md": report_md,
        }).execute()
        if res.data:
            review_id = res.data[0]["id"]
    except Exception as e:
        logger.error(f"[LEGAL] DB insert: {e}")

    # Aggiorna status progetto
    new_status = "legal_ok" if can_proceed else "legal_blocked"
    try:
        supabase.table("projects").update({"status": new_status}).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[LEGAL] status update: {e}")

    # Invia card nel topic
    sep = "\u2501" * 15
    msg = (
        f"\u2696\ufe0f Review Legale \u2014 {name}\n"
        f"{sep}\n"
        f"\U0001f7e2 OK: {len(green)} punti | \U0001f7e1 Attenzione: {len(yellow)} | \U0001f534 Blocchi: {len(red)}\n"
        f"{sep}"
    )
    if red:
        msg += "\n\U0001f534 " + "\n\U0001f534 ".join(red[:3])
        msg += f"\n{sep}"
    elif yellow:
        msg += "\n\U0001f7e1 " + "\n\U0001f7e1 ".join(yellow[:2])
        msg += f"\n{sep}"

    if can_proceed:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "\U0001f4c4 Dettaglio review", "callback_data": f"legal_read:{project_id}:{review_id or 0}"},
                    {"text": "\U0001f680 Procedi build", "callback_data": f"legal_proceed:{project_id}"},
                ],
            ]
        }
    else:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "\U0001f4c4 Dettaglio review", "callback_data": f"legal_read:{project_id}:{review_id or 0}"},
                    {"text": "\U0001f534 Blocca progetto", "callback_data": f"legal_block:{project_id}"},
                ],
            ]
        }

    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, msg, reply_markup=reply_markup)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("legal_agent", "legal_review", 2,
                    f"project={project_id}", f"green={len(green)} yellow={len(yellow)} red={len(red)}",
                    "claude-sonnet-4-5", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[LEGAL] Completato project={project_id} green={len(green)} yellow={len(yellow)} red={len(red)}")
    return {
        "status": "ok",
        "project_id": project_id,
        "review_id": review_id,
        "can_proceed": can_proceed,
        "green": len(green), "yellow": len(yellow), "red": len(red),
    }


def generate_project_docs(project_id):
    """MACRO-TASK 2: Genera Privacy Policy, ToS, Client Contract per il progetto."""
    start = time.time()
    logger.info(f"[LEGAL_DOCS] Avvio per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("name,spec_md,slug").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    github_repo = project.get("github_repo") or project.get("slug", "")

    docs = {}
    total_cost = 0.0
    for doc_type, doc_name in [
        ("privacy_policy", "Privacy Policy"),
        ("terms_of_service", "Termini di Servizio"),
        ("client_contract", "Contratto Cliente"),
    ]:
        prompt = f"""Genera {doc_name} per il prodotto "{name}" (legge italiana/europea).
Estrai le caratteristiche rilevanti dalla SPEC: {spec_md[:2000]}
Formato: testo legale formale, sezioni numerate, italiano.
Max 800 parole. Solo il documento, niente intro."""
        try:
            resp = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            docs[doc_type] = resp.content[0].text.strip()
            total_cost += (resp.usage.input_tokens * 0.8 + resp.usage.output_tokens * 4.0) / 1_000_000
        except Exception as e:
            logger.warning(f"[LEGAL_DOCS] {doc_type}: {e}")
            docs[doc_type] = f"[Errore generazione {doc_name}]"

    # Commit su GitHub
    if github_repo:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _commit_to_project_repo(github_repo, "docs/privacy_policy.md",
                                docs.get("privacy_policy", ""), f"docs: Privacy Policy {ts}")
        _commit_to_project_repo(github_repo, "docs/terms_of_service.md",
                                docs.get("terms_of_service", ""), f"docs: Terms of Service {ts}")
        _commit_to_project_repo(github_repo, "docs/client_contract.md",
                                docs.get("client_contract", ""), f"docs: Client Contract {ts}")

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("legal_agent", "generate_docs", 2,
                    f"project={project_id}", f"docs generati: {list(docs.keys())}",
                    "claude-haiku-4-5-20251001", 0, 0, total_cost, duration_ms)

    logger.info(f"[LEGAL_DOCS] Completato project={project_id} in {duration_ms}ms")
    return {"status": "ok", "project_id": project_id, "docs": list(docs.keys())}


def monitor_brain_compliance():
    """MACRO-TASK 2: Weekly compliance check per brAIn stessa. Ogni lunedi 07:00."""
    start = time.time()
    logger.info("[COMPLIANCE] Avvio monitoraggio settimanale brAIn")

    prompt = """Sei il Legal Monitor di brAIn. Analizza l'organismo brAIn e verifica la compliance.
brAIn e' un'organizzazione AI-native che:
- Scansiona problemi globali via Perplexity API (web scraping indiretto)
- Genera soluzioni con Claude AI
- Costruisce e lancia MVP
- Raccoglie feedback da prospect via email/Telegram
- Opera in Europa (Italia, Frankfurt)

Verifica compliance con: GDPR, AI Act 2026, Direttiva E-Commerce, normativa italiana.
Risposta in testo piano italiano, max 10 righe, formato:
COMPLIANCE CHECK brAIn — [data]
[status: OK/ATTENZIONE/CRITICO]
[elenco punti numerati]"""

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        report = resp.content[0].text.strip()
        cost = (resp.usage.input_tokens * 0.8 + resp.usage.output_tokens * 4.0) / 1_000_000
    except Exception as e:
        logger.error(f"[COMPLIANCE] {e}")
        return {"status": "error", "error": str(e)}

    # Invia a Mirco
    chat_id = get_telegram_chat_id()
    if chat_id:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if token:
            try:
                http_requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": report},
                    timeout=15,
                )
            except Exception as e:
                logger.warning(f"[COMPLIANCE] Telegram: {e}")

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("legal_agent", "compliance_check", 1,
                    "brain_compliance_weekly", report[:200],
                    "claude-haiku-4-5-20251001", 0, 0, cost, duration_ms)

    return {"status": "ok", "report": report}


# ---- SMOKE TEST AGENT (MACRO-TASK 3) ----

def run_smoke_test_setup(project_id):
    """MACRO-TASK 3: Setup smoke test — crea record, trova 50 prospect via Perplexity, salva."""
    start = time.time()
    logger.info(f"[SMOKE] Avvio setup per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    landing_url = project.get("smoke_test_url") or project.get("landing_page_url", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()

    if not spec_md:
        return {"status": "error", "error": "spec_md mancante"}

    # Notifica avvio
    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, f"\U0001f9ea Smoke test avviato per {name}\nRicerca prospect in corso...")

    # Crea record smoke_test
    smoke_id = None
    try:
        res = supabase.table("smoke_tests").insert({
            "project_id": project_id,
            "landing_page_url": landing_url or "",
        }).execute()
        if res.data:
            smoke_id = res.data[0]["id"]
    except Exception as e:
        logger.error(f"[SMOKE] smoke_tests insert: {e}")
        return {"status": "error", "error": str(e)}

    # Estrai target dalla SPEC per trovare prospect
    spec_lines = spec_md[:3000]
    target_query = ""
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": (
                f"Da questa SPEC, estrai il target customer in 1 riga concisa per una query di ricerca Perplexity "
                f"(es: 'avvocati italiani 35-50 anni studio legale piccolo'). Solo la riga.\n\n{spec_lines}"
            )}],
        )
        target_query = resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"[SMOKE] target extraction: {e}")
        target_query = f"clienti di {name}"

    # Trova prospect via Perplexity
    prospects_raw = []
    try:
        query = (f"trova 20 {target_query} con contatto email o LinkedIn pubblico in Italia. "
                 f"Elenca nome, ruolo, email/LinkedIn in formato: Nome | Ruolo | Contatto")
        perplexity_result = search_perplexity(query)
        if perplexity_result:
            # Estrai righe con | come separatore
            for line in perplexity_result.split("\n"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3 and parts[2] and ("@" in parts[2] or "linkedin" in parts[2].lower()):
                    prospects_raw.append({
                        "name": parts[0][:100],
                        "contact": parts[2][:200],
                        "channel": "email" if "@" in parts[2] else "linkedin",
                    })
    except Exception as e:
        logger.warning(f"[SMOKE] Perplexity prospect search: {e}")

    # Inserisci prospect in DB
    inserted = 0
    for p in prospects_raw[:50]:
        try:
            supabase.table("smoke_test_prospects").insert({
                "smoke_test_id": smoke_id,
                "project_id": project_id,
                "name": p["name"],
                "contact": p["contact"],
                "channel": p["channel"],
                "status": "pending",
            }).execute()
            inserted += 1
        except Exception as e:
            logger.warning(f"[SMOKE] prospect insert: {e}")

    # Aggiorna conteggio
    try:
        supabase.table("smoke_tests").update({"prospects_count": inserted}).eq("id", smoke_id).execute()
    except Exception:
        pass

    # Aggiorna status progetto
    try:
        supabase.table("projects").update({"status": "smoke_test_running"}).eq("id", project_id).execute()
    except Exception:
        pass

    # Invia card con risultato
    sep = "\u2501" * 15
    msg = (
        f"\U0001f9ea Smoke Test \u2014 {name}\n"
        f"{sep}\n"
        f"Prospect trovati: {inserted}\n"
        f"Landing: {landing_url or 'non ancora deployata'}\n"
        f"Analisi risultati disponibile dopo 7 giorni.\n"
        f"{sep}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "\u2705 Avvia Outreach", "callback_data": f"smoke_approve:{project_id}:{smoke_id}"},
                {"text": "\u274c Annulla", "callback_data": f"smoke_cancel:{project_id}:{smoke_id}"},
            ],
        ]
    }
    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, msg, reply_markup=reply_markup)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("smoke_test_agent", "smoke_setup", 2,
                    f"project={project_id}", f"smoke_id={smoke_id} prospects={inserted}",
                    "claude-haiku-4-5-20251001", 0, 0, 0, duration_ms)

    logger.info(f"[SMOKE] Setup completato project={project_id} smoke_id={smoke_id} prospects={inserted}")
    return {"status": "ok", "project_id": project_id, "smoke_id": smoke_id, "prospects_count": inserted}


def analyze_feedback_for_spec(project_id):
    """MACRO-TASK 3: Analizza feedback smoke test dopo 7 giorni. Genera SPEC_UPDATES.md e insights."""
    start = time.time()
    logger.info(f"[SMOKE_ANALYZE] Avvio analisi per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()
    github_repo = project.get("github_repo", "")

    # Recupera smoke test più recente
    try:
        st = supabase.table("smoke_tests").select("*").eq("project_id", project_id).order("started_at", desc=True).limit(1).execute()
        if not st.data:
            return {"status": "error", "error": "smoke test not found"}
        smoke = st.data[0]
        smoke_id = smoke["id"]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    # Recupera prospect con feedback
    try:
        prospects = supabase.table("smoke_test_prospects").select("*").eq("smoke_test_id", smoke_id).execute()
        prospects_data = prospects.data or []
    except Exception:
        prospects_data = []

    sent = sum(1 for p in prospects_data if p.get("sent_at"))
    rejected = [p for p in prospects_data if p.get("status") == "rejected"]
    forms = [p for p in prospects_data if p.get("status") == "form_compiled"]
    rejection_reasons = [p.get("rejection_reason", "") for p in rejected if p.get("rejection_reason")]

    conv_rate = (len(forms) / max(sent, 1)) * 100

    # Genera insights con Claude
    insights_prompt = f"""Analizza i risultati di questo smoke test per il prodotto "{name}".

Dati:
- Prospect contattati: {sent}
- Form compilati: {len(forms)}
- Rifiuti: {len(rejected)}
- Tasso conversione: {conv_rate:.1f}%
- Motivi rifiuto principali: {'; '.join(rejection_reasons[:5]) or 'non disponibili'}

SPEC originale (estratto): {spec_md[:2000]}

Rispondi in JSON:
{{
  "overall_signal": "green/yellow/red",
  "key_insights": ["insight 1", "insight 2", "insight 3"],
  "spec_updates": ["modifica 1 alla SPEC", "modifica 2"],
  "recommendation": "PROCEDI/PIVOTA/FERMA",
  "reasoning": "1 paragrafo max"
}}"""

    insights = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": insights_prompt}],
        )
        raw = resp.content[0].text.strip()
        import re as _re3
        m = _re3.search(r'\{[\s\S]*\}', raw)
        if m:
            insights = json.loads(m.group(0))
        cost = (resp.usage.input_tokens * 3.0 + resp.usage.output_tokens * 15.0) / 1_000_000
    except Exception as e:
        logger.error(f"[SMOKE_ANALYZE] Claude: {e}")
        cost = 0.0
        insights = {"overall_signal": "yellow", "key_insights": [], "spec_updates": [],
                    "recommendation": "ANALISI MANUALE RICHIESTA"}

    # Genera SPEC_UPDATES.md
    spec_updates_content = f"# SPEC Updates — {name}\nData: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
    spec_updates_content += f"## Segnale smoke test: {insights.get('overall_signal', 'N/A').upper()}\n\n"
    spec_updates_content += f"## Raccomandazione: {insights.get('recommendation', 'N/A')}\n\n"
    spec_updates_content += f"## Key Insights\n"
    for i, ins in enumerate(insights.get("key_insights", []), 1):
        spec_updates_content += f"{i}. {ins}\n"
    spec_updates_content += f"\n## Modifiche SPEC suggerite\n"
    for i, upd in enumerate(insights.get("spec_updates", []), 1):
        spec_updates_content += f"{i}. {upd}\n"
    spec_updates_content += f"\n## Reasoning\n{insights.get('reasoning', '')}\n"
    spec_updates_content += f"\n## Metriche\n- Contattati: {sent}\n- Form: {len(forms)}\n- Rifiuti: {len(rejected)}\n- Conversione: {conv_rate:.1f}%\n"

    if github_repo:
        _commit_to_project_repo(github_repo, "SPEC_UPDATES.md", spec_updates_content,
                                f"data: SPEC_UPDATES smoke test {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")

    # Salva insights in smoke_tests
    try:
        supabase.table("smoke_tests").update({
            "spec_insights": json.dumps(insights),
            "messages_sent": sent,
            "forms_compiled": len(forms),
            "rejections_with_reason": len(rejection_reasons),
            "conversion_rate": conv_rate,
            "recommendation": insights.get("recommendation", ""),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", smoke_id).execute()
    except Exception as e:
        logger.error(f"[SMOKE_ANALYZE] smoke_tests update: {e}")

    # Aggiorna spec_insights in projects
    try:
        supabase.table("projects").update({
            "spec_insights": json.dumps(insights),
            "status": "smoke_completed",
        }).eq("id", project_id).execute()
    except Exception:
        pass

    # Invia card risultato nel topic
    sep = "\u2501" * 15
    signal = insights.get("overall_signal", "yellow")
    signal_emoji = "\U0001f7e2" if signal == "green" else ("\U0001f534" if signal == "red" else "\U0001f7e1")
    msg = (
        f"\U0001f9ea Smoke Test completato \u2014 {name}\n"
        f"{sep}\n"
        f"Segnale: {signal_emoji} {signal.upper()}\n"
        f"Conversione: {conv_rate:.1f}% ({len(forms)}/{sent})\n"
        f"Raccomandazione: {insights.get('recommendation', 'N/A')}\n"
        f"{sep}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "\U0001f680 Avvia build", "callback_data": f"smoke_proceed:{project_id}"},
                {"text": "\U0001f4ca Insight SPEC", "callback_data": f"smoke_spec_insights:{project_id}:{smoke_id}"},
            ],
            [
                {"text": "\u270f\ufe0f Modifica SPEC", "callback_data": f"smoke_modify_spec:{project_id}"},
            ],
        ]
    }
    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, msg, reply_markup=reply_markup)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("smoke_test_agent", "smoke_analyze", 2,
                    f"project={project_id}", f"conv={conv_rate:.1f}% rec={insights.get('recommendation','')}",
                    "claude-sonnet-4-5", 0, 0, cost, duration_ms)

    logger.info(f"[SMOKE_ANALYZE] Completato project={project_id} conv={conv_rate:.1f}%")
    return {
        "status": "ok",
        "project_id": project_id,
        "smoke_id": smoke_id,
        "conversion_rate": conv_rate,
        "recommendation": insights.get("recommendation", ""),
        "signal": signal,
    }


# ============================================================
# MARKETING SYSTEM (inlined) — 8 agenti + coordinator
# ============================================================

_MKT_SEP = "\u2501" * 15

def _mkt_card(emoji, title, context, lines):
    """Card Telegram formato brAIn per notifiche marketing."""
    rows = [f"{emoji} *{title}*" + (f" \u2014 {context}" if context else ""), _MKT_SEP]
    for i, l in enumerate(lines):
        if not l:
            rows.append("")
            continue
        pfx = "\u2514" if i == len(lines) - 1 else "\u251c"
        rows.append(l if (l.startswith("\u2514") or l.startswith("\u251c") or l.startswith("\u2501")) else f"{pfx} {l}")
    rows.append(_MKT_SEP)
    return "\n".join(rows)


def _mkt_notify(text, reply_markup=None):
    """Invia card marketing a Mirco (DM)."""
    chat_id = get_telegram_chat_id()
    if not chat_id or not TELEGRAM_BOT_TOKEN:
        return
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"[MKT_NOTIFY] {e}")


def _mkt_commit(repo, subfolder, filename, content, msg):
    """Commit file in /marketing/{subfolder}/{filename} nel repo progetto."""
    path = f"marketing/{subfolder}/{filename}" if subfolder else f"marketing/{filename}"
    return _commit_to_project_repo(repo, path, content, msg)


def _mkt_load_project(project_id):
    """Carica dati progetto da Supabase. Ritorna dict o None."""
    try:
        r = supabase.table("projects").select("*").eq("id", project_id).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        logger.error(f"[MKT] project load: {e}")
        return None


def _mkt_get_or_create_brand_asset(project_id, target="project"):
    """Ritorna ID brand_assets esistente o ne crea uno nuovo."""
    try:
        r = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if r.data:
            return r.data[0]["id"]
        ins = supabase.table("brand_assets").insert({
            "project_id": project_id, "target": target, "status": "in_progress",
        }).execute()
        return ins.data[0]["id"] if ins.data else None
    except Exception as e:
        logger.error(f"[MKT] brand_asset create: {e}")
        return None


def _mkt_update_brand_asset(asset_id, fields):
    try:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        supabase.table("brand_assets").update(fields).eq("id", asset_id).execute()
    except Exception as e:
        logger.warning(f"[MKT] brand_asset update: {e}")


# ---- AGENT 1: BRAND & CREATIVE ----

def run_brand_agent(project_id, target="project"):
    """Genera brand DNA, guidelines, logo SVG, kit. Commit su GitHub."""
    start = time.time()
    logger.info(f"[BRAND] Avvio brand_agent project={project_id} target={target}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()

    asset_id = _mkt_get_or_create_brand_asset(project_id, target)

    # Ricerca competitiva per naming
    comp_query = f"top brand names {sector} startup 2026 naming trends"
    comp_info = search_perplexity(comp_query) or ""

    brand_prompt = f"""Sei il Chief Brand Officer di brAIn. Genera il brand DNA completo per questo progetto.

Progetto: {name}
Settore: {sector or "non specificato"}
SPEC (estratto): {spec_md[:2000]}
Ricerca naming mercato: {comp_info[:400]}

RISPONDI con JSON puro:
{{
  "naming_options": [
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}},
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}},
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}},
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}},
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}}
  ],
  "recommended_name": "...",
  "tagline": "...",
  "brand_dna": {{
    "mission": "...",
    "vision": "...",
    "values": ["...", "...", "..."],
    "tone_of_voice": "...",
    "persona": "...",
    "positioning": "..."
  }},
  "visual_guidelines": {{
    "primary_color": "#RRGGBB",
    "secondary_color": "#RRGGBB",
    "accent_color": "#RRGGBB",
    "font_heading": "...",
    "font_body": "...",
    "visual_style": "..."
  }},
  "do_list": ["...", "...", "..."],
  "dont_list": ["...", "...", "..."]
}}"""

    tokens_in = tokens_out = 0
    brand_data = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=3000,
            messages=[{"role": "user", "content": brand_prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_mkt
        m = _re_mkt.search(r'\{[\s\S]*\}', raw)
        brand_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[BRAND] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    brand_name = brand_data.get("recommended_name") or name
    tagline = brand_data.get("tagline", "")
    dna = brand_data.get("brand_dna", {})
    vis = brand_data.get("visual_guidelines", {})
    naming_options = brand_data.get("naming_options", [])

    # Verifica disponibilità domini via Perplexity
    domain_query = f"domain availability check {' '.join(o['name'] for o in naming_options[:3])} .io .com"
    domain_info = search_perplexity(domain_query) or "verifica manuale consigliata"

    # Genera BRAND_DNA.md
    brand_dna_md = f"""# Brand DNA — {brand_name}
> {tagline}

## Naming — 5 opzioni
{chr(10).join(f"**{i+1}. {o['name']}** — {o['rationale']}" for i, o in enumerate(naming_options))}

Disponibilità domini: {domain_info[:300]}

**Scelta consigliata: {brand_name}**

## Missione
{dna.get('mission', '')}

## Visione
{dna.get('vision', '')}

## Valori
{chr(10).join(f"- {v}" for v in dna.get('values', []))}

## Tone of Voice
{dna.get('tone_of_voice', '')}

## Persona
{dna.get('persona', '')}

## Posizionamento
{dna.get('positioning', '')}
"""

    # Genera BRAND_GUIDELINES.md
    brand_guidelines_md = f"""# Brand Guidelines — {brand_name}

## Palette Colori
- **Primario:** {vis.get('primary_color', '#000000')}
- **Secondario:** {vis.get('secondary_color', '#FFFFFF')}
- **Accento:** {vis.get('accent_color', '#0066FF')}

## Tipografia
- **Heading:** {vis.get('font_heading', 'Inter')}
- **Body:** {vis.get('font_body', 'Inter')}

## Stile Visivo
{vis.get('visual_style', '')}

## DO ✅
{chr(10).join(f"- {d}" for d in brand_data.get('do_list', []))}

## DON'T ❌
{chr(10).join(f"- {d}" for d in brand_data.get('dont_list', []))}
"""

    # Genera logo SVG base
    primary = vis.get('primary_color', '#0066FF')
    secondary = vis.get('secondary_color', '#FFFFFF')
    initials = ''.join(w[0].upper() for w in brand_name.split()[:2]) or brand_name[:2].upper()
    logo_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">
  <rect width="200" height="200" rx="40" fill="{primary}"/>
  <text x="100" y="120" font-family="Arial,sans-serif" font-size="72" font-weight="bold"
        text-anchor="middle" fill="{secondary}">{initials}</text>
</svg>"""

    # BRAND_KIT_SUMMARY.md (1 pagina)
    brand_kit_md = f"""# Brand Kit — {brand_name}
_{tagline}_

**Colori:** {vis.get('primary_color','#000')} (primario) · {vis.get('secondary_color','#fff')} (secondario) · {vis.get('accent_color','#06f')} (accento)
**Font:** {vis.get('font_heading','Inter')} (heading) · {vis.get('font_body','Inter')} (body)
**Missione:** {dna.get('mission','')}
**Tone:** {dna.get('tone_of_voice','')}
**Persona:** {dna.get('persona','')}
**Do:** {' / '.join(brand_data.get('do_list',[])[:3])}
**Don't:** {' / '.join(brand_data.get('dont_list',[])[:3])}
"""

    # Commit su GitHub
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "brand", "BRAND_DNA.md", brand_dna_md, f"mkt: Brand DNA {brand_name} — {ts}")
        _mkt_commit(github_repo, "brand", "BRAND_GUIDELINES.md", brand_guidelines_md, f"mkt: Brand Guidelines — {ts}")
        _mkt_commit(github_repo, "brand", "logo.svg", logo_svg, f"mkt: Logo SVG — {ts}")
        _mkt_commit(github_repo, "brand", "BRAND_KIT_SUMMARY.md", brand_kit_md, f"mkt: Brand Kit Summary — {ts}")

    # Salva in brand_assets
    if asset_id:
        _mkt_update_brand_asset(asset_id, {"brand_name": brand_name, "tagline": tagline,
                                           "brand_dna_md": brand_dna_md})

    # Notifica Mirco con card + bottone
    card = _mkt_card("\U0001f3a8", "BRAND IDENTITY PRONTA", brand_name, [
        f"Nome consigliato: {brand_name}",
        f"Tagline: {tagline}",
        f"Colore primario: {vis.get('primary_color', 'N/A')}",
        f"Tono: {dna.get('tone_of_voice', 'N/A')[:60]}",
    ])
    _mkt_notify(card, reply_markup={"inline_keyboard": [[
        {"text": "\U0001f4c4 Brand Kit", "callback_data": f"mkt_brand_kit:{project_id}"},
        {"text": "\u27a1\ufe0f Avanti", "callback_data": f"mkt_next:{project_id}:product"},
    ]]})

    # Invia BRAND_KIT_SUMMARY anche come file
    _mkt_send_file(brand_kit_md, f"BRAND_KIT_{brand_name.replace(' ','_')}.md")

    # Aggiorna avatar bot con logo (best-effort)
    _update_bot_avatar_svg(logo_svg)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("brand_agent", "brand_generate", 3,
                    f"project={project_id}", f"brand={brand_name} tagline={tagline}",
                    "claude-sonnet-4-5", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[BRAND] Completato project={project_id} brand={brand_name} in {duration_ms}ms")
    return {"status": "ok", "project_id": project_id, "brand_name": brand_name, "tagline": tagline,
            "asset_id": asset_id, "cost_usd": round(cost, 5)}


def _mkt_send_file(content, filename):
    """Invia file .md a Mirco via sendDocument."""
    chat_id = get_telegram_chat_id()
    if not chat_id or not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
            data={"chat_id": chat_id},
            files={"document": (filename, content.encode("utf-8"), "text/plain")},
            timeout=20,
        )
    except Exception as e:
        logger.warning(f"[MKT_FILE] {e}")


def _update_bot_avatar_svg(svg_content):
    """Aggiorna immagine profilo bot Telegram con logo SVG (best-effort)."""
    # Telegram setChatPhoto richiede PNG/JPEG. Tentiamo inviando come PNG placeholder.
    # In produzione usare libreria Pillow/cairosvg per convertire SVG → PNG.
    # Per ora: solo log dell'intent
    logger.info("[BRAND] Avatar update: richiede conversione SVG→PNG (installare cairosvg+Pillow per deploy)")


# ---- AGENT 2: PRODUCT MARKETING ----

def run_product_marketing_agent(project_id):
    """Genera positioning, messaging, analisi competitiva, sales enablement."""
    start = time.time()
    logger.info(f"[PRODUCT_MKT] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Carica brand DNA se disponibile
    brand_dna_md = ""
    try:
        ba = supabase.table("brand_assets").select("brand_name,brand_dna_md,tagline").eq("project_id", project_id).execute()
        if ba.data:
            brand_dna_md = ba.data[0].get("brand_dna_md", "")
    except:
        pass

    # Ricerca competitiva via Perplexity
    comp_query = f"top 5 competitor '{name}' settore '{sector}' 2026 pricing differenziatori"
    comp_info = search_perplexity(comp_query) or "Dati competitivi non disponibili."

    prompt = f"""Sei il VP Product Marketing di brAIn. Genera framework completo di product marketing.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:2500]}
Brand DNA: {brand_dna_md[:800]}
Dati competitivi: {comp_info[:600]}

Genera in JSON:
{{
  "icp": {{
    "profile": "...",
    "demographics": "...",
    "psychographics": "...",
    "pain_points": ["...", "..."],
    "buying_triggers": ["...", "..."]
  }},
  "value_proposition": "...",
  "unique_differentiators": ["...", "...", "..."],
  "competitors": [
    {{"name": "...", "strengths": "...", "weaknesses": "...", "price": "..."}},
    {{"name": "...", "strengths": "...", "weaknesses": "...", "price": "..."}},
    {{"name": "...", "strengths": "...", "weaknesses": "...", "price": "..."}}
  ],
  "messaging": {{
    "awareness": "...",
    "consideration": "...",
    "decision": "...",
    "retention": "..."
  }},
  "pricing_tiers": [
    {{"name": "...", "price": "...", "features": ["..."], "target": "..."}},
    {{"name": "...", "price": "...", "features": ["..."], "target": "..."}},
    {{"name": "...", "price": "...", "features": ["..."], "target": "..."}}
  ],
  "top_objections": [
    {{"objection": "...", "response": "..."}},
    {{"objection": "...", "response": "..."}},
    {{"objection": "...", "response": "..."}},
    {{"objection": "...", "response": "..."}},
    {{"objection": "...", "response": "..."}}
  ]
}}"""

    tokens_in = tokens_out = 0
    pm_data = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5", max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_mkt2
        m = _re_mkt2.search(r'\{[\s\S]*\}', raw)
        pm_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[PRODUCT_MKT] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    icp = pm_data.get("icp", {})
    competitors = pm_data.get("competitors", [])
    objections = pm_data.get("top_objections", [])
    pricing = pm_data.get("pricing_tiers", [])
    msg_fw = pm_data.get("messaging", {})

    # Genera file
    positioning_md = f"""# Positioning — {name}

## ICP (Ideal Customer Profile)
{icp.get('profile', '')}
- **Demographics:** {icp.get('demographics', '')}
- **Psychographics:** {icp.get('psychographics', '')}
- **Pain points:** {', '.join(icp.get('pain_points', []))}
- **Buying triggers:** {', '.join(icp.get('buying_triggers', []))}

## Value Proposition
{pm_data.get('value_proposition', '')}

## Differenziatori Unici
{chr(10).join(f"- {d}" for d in pm_data.get('unique_differentiators', []))}
"""

    messaging_md = f"""# Messaging Framework — {name}

| Stage | Messaggio |
|-------|-----------|
| Awareness | {msg_fw.get('awareness', '')} |
| Consideration | {msg_fw.get('consideration', '')} |
| Decision | {msg_fw.get('decision', '')} |
| Retention | {msg_fw.get('retention', '')} |
"""

    comp_md = f"""# Analisi Competitiva — {name}

| Competitor | Punti di Forza | Debolezze | Prezzo |
|-----------|----------------|-----------|--------|
{chr(10).join(f"| {c.get('name','')} | {c.get('strengths','')} | {c.get('weaknesses','')} | {c.get('price','')} |" for c in competitors)}
"""

    _obj_lines = "".join(f"**Obiezione {i+1}:** {o.get('objection','')}  \n**Risposta:** {o.get('response','')}\n\n" for i, o in enumerate(objections))
    objections_md = f"""# Objection Handler — {name}

{_obj_lines}"""

    _pricing_lines = "".join(f"## {t.get('name','')} — {t.get('price','')}\n**Target:** {t.get('target','')}\n**Features:** {', '.join(t.get('features',[]))}\n\n" for t in pricing)
    pricing_md = f"""# Pricing Strategy — {name}

{_pricing_lines}"""

    sales_deck_md = f"""# Sales Deck Outline — {name}

1. **Problem** — {icp.get('pain_points', ['pain point'])[0] if icp.get('pain_points') else ''}
2. **Solution** — {pm_data.get('value_proposition', '')[:200]}
3. **Differenziatori** — {', '.join(pm_data.get('unique_differentiators', [])[:3])}
4. **Social proof** — [da aggiungere post-lancio]
5. **Pricing** — {pricing[0].get('name','') if pricing else ''} da {pricing[0].get('price','') if pricing else ''}
6. **CTA** — Inizia gratis / Prenota demo
"""

    # Commit su GitHub
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "product", "POSITIONING.md", positioning_md, f"mkt: Positioning — {ts}")
        _mkt_commit(github_repo, "product", "MESSAGING_FRAMEWORK.md", messaging_md, f"mkt: Messaging Framework — {ts}")
        _mkt_commit(github_repo, "product", "COMPETITIVE_ANALYSIS.md", comp_md, f"mkt: Competitive Analysis — {ts}")
        _mkt_commit(github_repo, "product", "OBJECTION_HANDLER.md", objections_md, f"mkt: Objection Handler — {ts}")
        _mkt_commit(github_repo, "product", "PRICING_STRATEGY.md", pricing_md, f"mkt: Pricing Strategy — {ts}")
        _mkt_commit(github_repo, "product", "SALES_DECK_OUTLINE.md", sales_deck_md, f"mkt: Sales Deck — {ts}")

    # Salva positioning in brand_assets
    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"positioning_md": positioning_md})
    except:
        pass

    card = _mkt_card("\U0001f3af", "PRODUCT MARKETING PRONTO", name, [
        f"ICP: {icp.get('profile','')[:60]}",
        f"Value prop: {pm_data.get('value_proposition','')[:60]}",
        f"Competitor analizzati: {len(competitors)}",
        f"Tier pricing: {len(pricing)}",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("product_marketing_agent", "pm_generate", 3, f"project={project_id}",
                    f"icp={icp.get('profile','')[:80]}", "claude-sonnet-4-5", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[PRODUCT_MKT] Completato project={project_id} in {duration_ms}ms")
    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 3: CONTENT & SEO ----

def run_content_agent(project_id):
    """Genera copy kit, email sequences, SEO strategy, editorial calendar."""
    start = time.time()
    logger.info(f"[CONTENT] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Carica positioning se disponibile
    positioning_ctx = ""
    try:
        ba = supabase.table("brand_assets").select("positioning_md,brand_dna_md").eq("project_id", project_id).execute()
        if ba.data:
            positioning_ctx = ba.data[0].get("positioning_md", "")[:600]
    except:
        pass

    # SEO keyword research via Perplexity
    seo_query = f"top keyword {name} {sector} SEO 2026 search volume intent"
    seo_info = search_perplexity(seo_query) or ""

    prompt = f"""Sei il Content Director di brAIn. Genera tutto il copy e la strategia SEO.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1500]}
Positioning: {positioning_ctx}
SEO research: {seo_info[:400]}

Genera JSON:
{{
  "headline": "...",
  "subheadline": "...",
  "cta_primary": "...",
  "cta_secondary": "...",
  "elevator_pitch": "...",
  "one_liner": "...",
  "about_us": "...",
  "seo_keywords": [
    {{"keyword": "...", "intent": "informational/commercial/transactional", "difficulty": "low/medium/high"}},
    {{"keyword": "...", "intent": "...", "difficulty": "..."}},
    {{"keyword": "...", "intent": "...", "difficulty": "..."}},
    {{"keyword": "...", "intent": "...", "difficulty": "..."}},
    {{"keyword": "...", "intent": "...", "difficulty": "..."}}
  ],
  "blog_post_title": "...",
  "blog_post_content": "...",
  "email_onboarding": [
    {{"subject": "...", "preview": "...", "body_summary": "..."}},
    {{"subject": "...", "preview": "...", "body_summary": "..."}},
    {{"subject": "...", "preview": "...", "body_summary": "..."}},
    {{"subject": "...", "preview": "...", "body_summary": "..."}},
    {{"subject": "...", "preview": "...", "body_summary": "..."}}
  ],
  "cold_outreach_template": "..."
}}"""

    tokens_in = tokens_out = 0
    cnt_data = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5", max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_cnt
        m = _re_cnt.search(r'\{[\s\S]*\}', raw)
        cnt_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[CONTENT] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    keywords = cnt_data.get("seo_keywords", [])
    emails = cnt_data.get("email_onboarding", [])

    copy_kit_md = f"""# Copy Kit — {name}

## Headline
{cnt_data.get('headline', '')}

## Subheadline
{cnt_data.get('subheadline', '')}

## CTA Primaria
{cnt_data.get('cta_primary', '')}

## CTA Secondaria
{cnt_data.get('cta_secondary', '')}

## Elevator Pitch (30 secondi)
{cnt_data.get('elevator_pitch', '')}

## One-liner
{cnt_data.get('one_liner', '')}

## About Us
{cnt_data.get('about_us', '')}
"""

    _email_lines = "".join(f"### Email {i+1}: {e.get('subject','')}\n*Preview:* {e.get('preview','')}\n{e.get('body_summary','')}\n\n" for i, e in enumerate(emails))
    email_md = f"""# Email Sequences — {name}

## Onboarding (5 email)
{_email_lines}

## Cold Outreach Template
{cnt_data.get('cold_outreach_template', '')}
"""

    _seo_content_lines = "".join(f"- Articolo SEO: \"{k.get('keyword','')}\" ({k.get('intent','')})\n" for k in keywords[:5])
    seo_md = f"""# SEO Strategy — {name}

## Top Keywords
| Keyword | Intent | Difficoltà |
|---------|--------|-----------|
{chr(10).join(f"| {k.get('keyword','')} | {k.get('intent','')} | {k.get('difficulty','')} |" for k in keywords)}

## Content da Creare
{_seo_content_lines}"""

    # Blog post SEO
    blog_md = f"""# {cnt_data.get('blog_post_title', f'Blog Post — {name}')}

{cnt_data.get('blog_post_content', '')}
"""

    # Editorial calendar 90gg
    editorial_md = f"""# Editorial Calendar — {name} (90 giorni)

## Settimane 1-4 (Lancio)
- Settimana 1: Blog post "{cnt_data.get('blog_post_title','')}"
- Settimana 2: Email onboarding setup
- Settimana 3: Cold outreach batch 1
- Settimana 4: Review performance, ottimizza CTA

## Settimane 5-8 (Crescita)
- Articoli SEO su keyword priorità 2-3
- Email nurturing su lead freddi
- A/B test headline landing page

## Settimane 9-12 (Scale)
- 2 articoli/settimana
- Newsletter settimanale ai subscriber
- Case study primo cliente
"""

    landing_copy_md = f"""# Landing Page Copy — {name}

## HERO
**Headline:** {cnt_data.get('headline', '')}
**Subheadline:** {cnt_data.get('subheadline', '')}
**CTA:** {cnt_data.get('cta_primary', '')}

## VALUE PROP
{cnt_data.get('elevator_pitch', '')}

## CTA FOOTER
{cnt_data.get('cta_secondary', '')}
"""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "content", "COPY_KIT.md", copy_kit_md, f"mkt: Copy Kit — {ts}")
        _mkt_commit(github_repo, "content", "EMAIL_SEQUENCES.md", email_md, f"mkt: Email Sequences — {ts}")
        _mkt_commit(github_repo, "content", "LANDING_PAGE_COPY.md", landing_copy_md, f"mkt: Landing Copy — {ts}")
        _mkt_commit(github_repo, "content", "SEO_STRATEGY.md", seo_md, f"mkt: SEO Strategy — {ts}")
        _mkt_commit(github_repo, "content", "EDITORIAL_CALENDAR.md", editorial_md, f"mkt: Editorial Calendar — {ts}")
        _mkt_commit(github_repo, "content", "BLOG_POST_1.md", blog_md, f"mkt: Blog Post 1 — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"content_kit_md": copy_kit_md})
    except:
        pass

    card = _mkt_card("\u270d\ufe0f", "CONTENT & SEO PRONTO", name, [
        f"Headline: {cnt_data.get('headline','')[:60]}",
        f"Keyword SEO: {len(keywords)} identificate",
        f"Email onboarding: {len(emails)} scritte",
        "Blog post 1 pronto da pubblicare",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("content_agent", "content_generate", 3, f"project={project_id}",
                    f"keywords={len(keywords)} emails={len(emails)}", "claude-sonnet-4-5", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 4: DEMAND GENERATION ----

def run_demand_gen_agent(project_id):
    """Genera growth strategy, paid media plan, funnel map, email automation."""
    start = time.time()
    logger.info(f"[DEMAND_GEN] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Ricerca canali più efficaci per il settore
    channel_query = f"best acquisition channels {sector} B2B B2C 2026 CAC benchmark"
    channel_info = search_perplexity(channel_query) or ""

    prompt = f"""Sei il Head of Growth di brAIn. Genera strategia demand generation completa.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1500]}
Ricerca canali: {channel_info[:400]}

Genera JSON:
{{
  "top_channels": [
    {{"channel": "...", "score": 8, "cac_estimate_eur": 0, "rationale": "..."}},
    {{"channel": "...", "score": 7, "cac_estimate_eur": 0, "rationale": "..."}},
    {{"channel": "...", "score": 6, "cac_estimate_eur": 0, "rationale": "..."}}
  ],
  "paid_platforms": [
    {{"platform": "...", "budget_pct": 0, "targeting": "...", "ad_format": "..."}},
    {{"platform": "...", "budget_pct": 0, "targeting": "...", "ad_format": "..."}}
  ],
  "funnel_stages": {{
    "tofu": {{"content": "...", "cta": "...", "conversion_target": "..."}},
    "mofu": {{"content": "...", "cta": "...", "conversion_target": "..."}},
    "bofu": {{"content": "...", "cta": "...", "conversion_target": "..."}}
  }},
  "ab_tests": [
    {{"test": "...", "hypothesis": "...", "priority": "high/medium"}},
    {{"test": "...", "hypothesis": "...", "priority": "high/medium"}},
    {{"test": "...", "hypothesis": "...", "priority": "high/medium"}}
  ],
  "kpi": {{"cac_target_eur": 0, "ltv_estimate_eur": 0, "month3_users_target": 0}}
}}"""

    tokens_in = tokens_out = 0
    dg_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_dg
        m = _re_dg.search(r'\{[\s\S]*\}', raw)
        dg_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[DEMAND_GEN] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    channels = dg_data.get("top_channels", [])
    paid = dg_data.get("paid_platforms", [])
    funnel = dg_data.get("funnel_stages", {})
    ab_tests = dg_data.get("ab_tests", [])
    kpi = dg_data.get("kpi", {})

    growth_md = f"""# Growth Strategy — {name}

## Top 5 Canali (score costo/efficacia)
| Canale | Score | CAC Est. | Rationale |
|--------|-------|----------|-----------|
{chr(10).join(f"| {c.get('channel','')} | {c.get('score',0)}/10 | €{c.get('cac_estimate_eur',0)} | {c.get('rationale','')[:60]} |" for c in channels)}

## KPI Target
- CAC target: €{kpi.get('cac_target_eur',0)}
- LTV stimato: €{kpi.get('ltv_estimate_eur',0)}
- Utenti mese 3: {kpi.get('month3_users_target',0)}

## Piano 30/60/90 giorni
- **Giorno 1-30:** Setup tracking, lancio 1 canale principale, 10 lead
- **Giorno 31-60:** Ottimizza CAC, aggiungi 2° canale, 50 lead
- **Giorno 61-90:** Scale canale migliore, A/B test, 200 lead
"""

    _paid_lines = "".join(f"## {p.get('platform','')} ({p.get('budget_pct',0)}% budget)\n- Targeting: {p.get('targeting','')}\n- Formato: {p.get('ad_format','')}\n\n" for p in paid)
    paid_md = f"""# Paid Media Plan — {name}

{_paid_lines}"""

    funnel_md = f"""# Funnel Map — {name}

## TOFU (Top of Funnel — Awareness)
{funnel.get('tofu', {}).get('content', '')}
CTA: {funnel.get('tofu', {}).get('cta', '')}
Target conversion: {funnel.get('tofu', {}).get('conversion_target', '')}

## MOFU (Middle of Funnel — Consideration)
{funnel.get('mofu', {}).get('content', '')}
CTA: {funnel.get('mofu', {}).get('cta', '')}
Target conversion: {funnel.get('mofu', {}).get('conversion_target', '')}

## BOFU (Bottom of Funnel — Decision)
{funnel.get('bofu', {}).get('content', '')}
CTA: {funnel.get('bofu', {}).get('cta', '')}
Target conversion: {funnel.get('bofu', {}).get('conversion_target', '')}
"""

    ab_md = f"""# A/B Test Plan — {name}

| Test | Ipotesi | Priorità |
|------|---------|----------|
{chr(10).join(f"| {t.get('test','')} | {t.get('hypothesis','')} | {t.get('priority','')} |" for t in ab_tests)}
"""

    email_auto_md = f"""# Email Automation — {name}

## Sequenze Trigger-Based
- **Onboarding** (trigger: signup) → 5 email in 14 giorni
- **Win-back** (trigger: 30gg inattività) → 3 email
- **Upsell** (trigger: 60gg attivo) → 2 email
- **Referral** (trigger: successo feature chiave) → 1 email

Vedi EMAIL_SEQUENCES.md nel folder /content per i copy completi.
"""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "demand_gen", "GROWTH_STRATEGY.md", growth_md, f"mkt: Growth Strategy — {ts}")
        _mkt_commit(github_repo, "demand_gen", "PAID_MEDIA_PLAN.md", paid_md, f"mkt: Paid Media — {ts}")
        _mkt_commit(github_repo, "demand_gen", "FUNNEL_MAP.md", funnel_md, f"mkt: Funnel Map — {ts}")
        _mkt_commit(github_repo, "demand_gen", "AB_TEST_PLAN.md", ab_md, f"mkt: A/B Tests — {ts}")
        _mkt_commit(github_repo, "demand_gen", "EMAIL_AUTOMATION.md", email_auto_md, f"mkt: Email Automation — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"growth_strategy_md": growth_md})
    except:
        pass

    card = _mkt_card("\U0001f4e3", "DEMAND GEN PRONTO", name, [
        f"Canali top: {', '.join(c.get('channel','') for c in channels[:3])}",
        f"CAC target: €{kpi.get('cac_target_eur',0)}",
        f"A/B test pianificati: {len(ab_tests)}",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("demand_gen_agent", "demand_gen_generate", 3, f"project={project_id}",
                    f"channels={len(channels)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 5: SOCIAL MEDIA ----

def run_social_agent(project_id):
    """Identifica canali social giusti, genera strategy e template post."""
    start = time.time()
    logger.info(f"[SOCIAL] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Identifica canali più attivi nel settore
    social_query = f"social media channels most active {sector} target audience 2026 engagement"
    social_info = search_perplexity(social_query) or ""

    prompt = f"""Sei il Social Media Director di brAIn. Genera strategia social completa.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1000]}
Ricerca social: {social_info[:400]}

Genera JSON:
{{
  "selected_channels": [
    {{"channel": "...", "reason": "...", "frequency": "...", "tone": "..."}},
    {{"channel": "...", "reason": "...", "frequency": "...", "tone": "..."}}
  ],
  "content_templates": [
    {{"channel": "...", "type": "...", "template": "...", "visual_note": "..."}},
    {{"channel": "...", "type": "...", "template": "...", "visual_note": "..."}},
    {{"channel": "...", "type": "...", "template": "...", "visual_note": "..."}}
  ],
  "hashtag_sets": {{
    "brand": ["#...", "#...", "#..."],
    "sector": ["#...", "#...", "#..."],
    "niche": ["#...", "#...", "#..."]
  }},
  "launch_posts": [
    {{"channel": "...", "text": "...", "visual_note": "..."}},
    {{"channel": "...", "text": "...", "visual_note": "..."}},
    {{"channel": "...", "text": "...", "visual_note": "..."}}
  ]
}}"""

    tokens_in = tokens_out = 0
    soc_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_soc
        m = _re_soc.search(r'\{[\s\S]*\}', raw)
        soc_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[SOCIAL] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    channels = soc_data.get("selected_channels", [])
    templates = soc_data.get("content_templates", [])
    hashtags = soc_data.get("hashtag_sets", {})
    launch_posts = soc_data.get("launch_posts", [])

    _soc_channel_lines = "".join(f"### {c.get('channel','')}\n- Motivazione: {c.get('reason','')}\n- Frequenza: {c.get('frequency','')}\n- Tono: {c.get('tone','')}\n\n" for c in channels)
    social_strategy_md = f"""# Social Strategy — {name}

## Canali Selezionati
{_soc_channel_lines}"""

    _tmpl_lines = "".join(f"## Template {i+1} — {t.get('channel','')} ({t.get('type','')})\n```\n{t.get('template','')}\n```\n_Note visual: {t.get('visual_note','')}_\n\n" for i, t in enumerate(templates))
    templates_md = f"""# Content Templates — {name}

{_tmpl_lines}"""

    hashtag_md = f"""# Hashtag Strategy — {name}

## Brand: {' '.join(hashtags.get('brand', []))}
## Settore: {' '.join(hashtags.get('sector', []))}
## Nicchia: {' '.join(hashtags.get('niche', []))}

**Mix consigliato per post:** 3 brand + 4 settore + 3 nicchia = 10 hashtag
"""

    community_md = f"""# Community Playbook — {name}

## Rispondere ai Commenti
- Rispondere entro 2h nei giorni lavorativi
- Tono: {channels[0].get('tone','') if channels else 'professionale ma accessibile'}
- Escalation problemi: tagga @team

## Gestione Crisi
1. Non eliminare commenti negativi
2. Rispondere pubblicamente: "Capisco la tua preoccupazione, ti contatto in privato"
3. Risolvere in DM, poi follow-up pubblico

## Reward Advocates
- Like e repost contenuti utenti positivi
- DM personale ai top advocates
- Tag in post se usano il prodotto
"""

    _post_lines = "".join(f"## Post {i+1} — {p.get('channel','')}\n{p.get('text','')}\n_Visual: {p.get('visual_note','')}_\n\n" for i, p in enumerate(launch_posts))
    launch_posts_md = f"""# Launch Posts — {name}

{_post_lines}"""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "social", "SOCIAL_STRATEGY.md", social_strategy_md, f"mkt: Social Strategy — {ts}")
        _mkt_commit(github_repo, "social", "CONTENT_TEMPLATES.md", templates_md, f"mkt: Content Templates — {ts}")
        _mkt_commit(github_repo, "social", "HASHTAG_STRATEGY.md", hashtag_md, f"mkt: Hashtags — {ts}")
        _mkt_commit(github_repo, "social", "COMMUNITY_PLAYBOOK.md", community_md, f"mkt: Community Playbook — {ts}")
        _mkt_commit(github_repo, "social", "LAUNCH_POSTS.md", launch_posts_md, f"mkt: Launch Posts — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"social_strategy_md": social_strategy_md})
    except:
        pass

    card = _mkt_card("\U0001f4f1", "SOCIAL MEDIA PRONTO", name, [
        f"Canali: {', '.join(c.get('channel','') for c in channels)}",
        f"Template: {len(templates)} | Launch posts: {len(launch_posts)}",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("social_agent", "social_generate", 3, f"project={project_id}",
                    f"channels={len(channels)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 6: PR & COMUNICAZIONE ----

def run_pr_agent(project_id):
    """Genera press kit, media list, press release, outreach sequence."""
    start = time.time()
    logger.info(f"[PR] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Identifica media target via Perplexity
    media_query = f"top media blogger giornalisti tech startup {sector} Italia 2026 contatti pitch"
    media_info = search_perplexity(media_query) or ""

    prompt = f"""Sei il PR Director di brAIn. Genera materiali PR completi.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1200]}
Media landscape: {media_info[:400]}

Genera JSON:
{{
  "company_overview": "...",
  "founder_quote": "...",
  "product_description": "...",
  "key_stats": ["...", "...", "..."],
  "media_faq": [
    {{"q": "...", "a": "..."}},
    {{"q": "...", "a": "..."}}
  ],
  "media_targets": [
    {{"name": "...", "type": "blog/magazine/newsletter", "angle": "...", "contact": "..."}},
    {{"name": "...", "type": "...", "angle": "...", "contact": "..."}},
    {{"name": "...", "type": "...", "angle": "...", "contact": "..."}}
  ],
  "press_release_title": "...",
  "press_release_body": "...",
  "outreach_email_subject": "...",
  "outreach_email_body": "..."
}}"""

    tokens_in = tokens_out = 0
    pr_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_pr
        m = _re_pr.search(r'\{[\s\S]*\}', raw)
        pr_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[PR] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    media_targets = pr_data.get("media_targets", [])
    _press_faq_lines = "".join(f"**Q:** {fq.get('q','')}  \n**A:** {fq.get('a','')}\n\n" for fq in pr_data.get('media_faq', []))

    press_kit_md = f"""# Press Kit — {name}

## Company Overview
{pr_data.get('company_overview', '')}

## Descrizione Prodotto
{pr_data.get('product_description', '')}

## Key Stats
{chr(10).join(f"- {s}" for s in pr_data.get('key_stats', []))}

## Quote Fondatore
_{pr_data.get('founder_quote', '')}_

## FAQ Media
{_press_faq_lines}
"""

    media_list_md = f"""# Media List — {name}

| Media | Tipo | Angle Suggerito | Contatto |
|-------|------|----------------|---------|
{chr(10).join(f"| {m.get('name','')} | {m.get('type','')} | {m.get('angle','')} | {m.get('contact','')} |" for m in media_targets)}
"""

    press_release_md = f"""# {pr_data.get('press_release_title', f'LANCIO — {name}')}

{pr_data.get('press_release_body', '')}

---
_Per informazioni: [contatto press]_
"""

    outreach_md = f"""# PR Outreach Sequence — {name}

## Email 1 — Pitch Iniziale
**Oggetto:** {pr_data.get('outreach_email_subject', '')}

{pr_data.get('outreach_email_body', '')}

## Email 2 — Follow-up (7 giorni dopo)
Oggetto: Re: {pr_data.get('outreach_email_subject', '')}
"Volevo assicurarmi che la mia email precedente non fosse andata persa..."

## Email 3 — Ultimo tentativo (14 giorni dopo)
"Ultima email da parte mia — capisco che sia molto occupato..."
"""

    crisis_md = f"""# Crisis Comms Playbook — {name}

## Principi Fondamentali
1. Rispondere entro 2h da menzione negativa significativa
2. Non eliminare mai contenuti negativi legittimi
3. Ammettere errori onestamente quando necessario

## Protocollo
1. **Valuta**: critica legittima o trolling?
2. **Rispondi**: tono empatico, no difensivo
3. **Risolvi**: offri soluzione concreta
4. **Follow-up**: verifica che l'issue sia chiuso

## Template Risposta Crisi
"Grazie per il feedback. Capisco la tua frustrazione con [issue]. Stiamo [azione] per risolvere. Ti contatto in privato per aiutarti direttamente."
"""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "pr", "PRESS_KIT.md", press_kit_md, f"mkt: Press Kit — {ts}")
        _mkt_commit(github_repo, "pr", "MEDIA_LIST.md", media_list_md, f"mkt: Media List — {ts}")
        _mkt_commit(github_repo, "pr", "PRESS_RELEASE_LAUNCH.md", press_release_md, f"mkt: Press Release — {ts}")
        _mkt_commit(github_repo, "pr", "PR_OUTREACH_SEQUENCE.md", outreach_md, f"mkt: PR Outreach — {ts}")
        _mkt_commit(github_repo, "pr", "CRISIS_COMMS_PLAYBOOK.md", crisis_md, f"mkt: Crisis Comms — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"pr_kit_md": press_kit_md})
    except:
        pass

    card = _mkt_card("\U0001f4f0", "PR KIT PRONTO", name, [
        f"Media target: {len(media_targets)}",
        "Press release pronto",
        "Crisis playbook pronto",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("pr_agent", "pr_generate", 3, f"project={project_id}",
                    f"media={len(media_targets)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 7: CUSTOMER MARKETING ----

def run_customer_marketing_agent(project_id):
    """Genera onboarding journey, retention, referral, upsell strategies."""
    start = time.time()
    logger.info(f"[CUSTOMER_MKT] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    prompt = f"""Sei il Customer Marketing Director di brAIn. Genera strategia lifecycle completa.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1200]}

Genera JSON:
{{
  "onboarding_steps": [
    {{"day": 0, "action": "...", "goal": "...", "metric": "..."}},
    {{"day": 1, "action": "...", "goal": "...", "metric": "..."}},
    {{"day": 7, "action": "...", "goal": "...", "metric": "..."}},
    {{"day": 14, "action": "...", "goal": "...", "metric": "..."}}
  ],
  "aha_moment": "...",
  "retention_tactics": ["...", "...", "...", "...", "..."],
  "churn_signals": ["...", "...", "..."],
  "referral_mechanic": "...",
  "referral_incentive": "...",
  "upsell_triggers": ["...", "...", "..."],
  "upsell_message": "..."
}}"""

    tokens_in = tokens_out = 0
    cm_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_cm
        m = _re_cm.search(r'\{[\s\S]*\}', raw)
        cm_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[CUSTOMER_MKT] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    onboarding_steps = cm_data.get("onboarding_steps", [])

    _onb_steps_lines = "".join(f"### Giorno {s.get('day',0)}\n**Azione:** {s.get('action','')}\n**Goal:** {s.get('goal','')}\n**Metrica:** {s.get('metric','')}\n\n" for s in onboarding_steps)
    onboarding_md = f"""# Onboarding Journey — {name}

## Aha Moment
_{cm_data.get('aha_moment', '')}_

## Step-by-Step
{_onb_steps_lines}"""

    retention_md = f"""# Retention Playbook — {name}

## Tattiche Anti-Churn
{chr(10).join(f"- {t}" for t in cm_data.get('retention_tactics', []))}

## Segnali da Monitorare
{chr(10).join(f"- ⚠️ {s}" for s in cm_data.get('churn_signals', []))}
"""

    referral_md = f"""# Referral Program — {name}

## Meccanica
{cm_data.get('referral_mechanic', '')}

## Incentivo
{cm_data.get('referral_incentive', '')}

## Copy Landing Referral
"Invita un amico e {cm_data.get('referral_incentive', 'ottieni un bonus')}"
"""

    upsell_md = f"""# Upsell Strategy — {name}

## Trigger per Proposta Upgrade
{chr(10).join(f"- {t}" for t in cm_data.get('upsell_triggers', []))}

## Messaggio Upsell
_{cm_data.get('upsell_message', '')}_
"""

    churn_md = f"""# Churn Prevention — {name}

## Segnali di Abbandono
{chr(10).join(f"- 🚨 {s}" for s in cm_data.get('churn_signals', []))}

## Azioni Automatiche
- Segnale 1 → Email re-engagement personalizzata
- Segnale 2 → DM da founder
- Segnale 3 → Offerta speciale + call
"""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "customer", "ONBOARDING_JOURNEY.md", onboarding_md, f"mkt: Onboarding — {ts}")
        _mkt_commit(github_repo, "customer", "RETENTION_PLAYBOOK.md", retention_md, f"mkt: Retention — {ts}")
        _mkt_commit(github_repo, "customer", "REFERRAL_PROGRAM.md", referral_md, f"mkt: Referral — {ts}")
        _mkt_commit(github_repo, "customer", "UPSELL_STRATEGY.md", upsell_md, f"mkt: Upsell — {ts}")
        _mkt_commit(github_repo, "customer", "CHURN_PREVENTION.md", churn_md, f"mkt: Churn Prevention — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"customer_marketing_md": onboarding_md})
    except:
        pass

    card = _mkt_card("\U0001f91d", "CUSTOMER MARKETING PRONTO", name, [
        f"Aha moment: {cm_data.get('aha_moment','')[:60]}",
        f"Tattiche retention: {len(cm_data.get('retention_tactics',[]))}",
        "Referral + upsell strategy pronti",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("customer_marketing_agent", "customer_mkt_generate", 3, f"project={project_id}",
                    f"steps={len(onboarding_steps)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 8: MARKETING OPERATIONS ----

def run_marketing_ops_agent(project_id):
    """Genera tracking plan, attribution model, KPI dashboard, martech stack."""
    start = time.time()
    logger.info(f"[MKT_OPS] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    prompt = f"""Sei il Marketing Ops Lead di brAIn. Genera sistema di misurazione completo.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1000]}

Genera JSON:
{{
  "tracking_events": [
    {{"event": "...", "trigger": "...", "properties": ["...", "..."]}},
    {{"event": "...", "trigger": "...", "properties": ["...", "..."]}},
    {{"event": "...", "trigger": "...", "properties": ["...", "..."]}}
  ],
  "utm_convention": "...",
  "north_star_metric": "...",
  "kpis": [
    {{"kpi": "...", "frequency": "daily/weekly", "target": "...", "tool": "..."}},
    {{"kpi": "...", "frequency": "...", "target": "...", "tool": "..."}},
    {{"kpi": "...", "frequency": "...", "target": "...", "tool": "..."}}
  ],
  "martech_stack": [
    {{"tool": "...", "purpose": "...", "cost_eur": 0, "priority": "must/nice"}},
    {{"tool": "...", "purpose": "...", "cost_eur": 0, "priority": "must/nice"}},
    {{"tool": "...", "purpose": "...", "cost_eur": 0, "priority": "must/nice"}}
  ],
  "attribution_model": "...",
  "attribution_rationale": "..."
}}"""

    tokens_in = tokens_out = 0
    ops_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_ops
        m = _re_ops.search(r'\{[\s\S]*\}', raw)
        ops_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[MKT_OPS] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    tracking_events = ops_data.get("tracking_events", [])
    kpis = ops_data.get("kpis", [])
    martech = ops_data.get("martech_stack", [])

    tracking_md = f"""# Tracking Plan — {name}

## UTM Convention
`{ops_data.get('utm_convention', 'utm_source=canale&utm_medium=tipo&utm_campaign=nome')}`

## Eventi da Tracciare
| Evento | Trigger | Properties |
|--------|---------|-----------|
{chr(10).join(f"| {e.get('event','')} | {e.get('trigger','')} | {', '.join(e.get('properties',[]))} |" for e in tracking_events)}
"""

    attribution_md = f"""# Attribution Model — {name}

## Modello: {ops_data.get('attribution_model', 'Last-touch')}
{ops_data.get('attribution_rationale', '')}

## Setup Consigliato
- Usa UTM su tutti i link
- Tieni source nel cookie per 30 giorni
- Primo touchpoint per awareness, ultimo per conversione
"""

    kpi_dashboard_md = f"""# Marketing KPI Dashboard — {name}

## North Star Metric
**{ops_data.get('north_star_metric', '')}**

## KPI da Monitorare
| KPI | Frequenza | Target | Tool |
|-----|-----------|--------|------|
{chr(10).join(f"| {k.get('kpi','')} | {k.get('frequency','')} | {k.get('target','')} | {k.get('tool','')} |" for k in kpis)}
"""

    martech_md = f"""# Martech Stack — {name}

| Tool | Scopo | Costo/mese | Priorità |
|------|-------|-----------|---------|
{chr(10).join(f"| {t.get('tool','')} | {t.get('purpose','')} | €{t.get('cost_eur',0)} | {t.get('priority','')} |" for t in martech)}

**Costo totale stimato (must-have only):** €{sum(t.get('cost_eur',0) for t in martech if t.get('priority')=='must')}/mese
"""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "ops", "TRACKING_PLAN.md", tracking_md, f"mkt: Tracking Plan — {ts}")
        _mkt_commit(github_repo, "ops", "ATTRIBUTION_MODEL.md", attribution_md, f"mkt: Attribution — {ts}")
        _mkt_commit(github_repo, "ops", "MARKETING_KPI_DASHBOARD.md", kpi_dashboard_md, f"mkt: KPI Dashboard — {ts}")
        _mkt_commit(github_repo, "ops", "MARTECH_STACK.md", martech_md, f"mkt: Martech Stack — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {
                "marketing_ops_md": kpi_dashboard_md, "status": "completed"
            })
    except:
        pass

    card = _mkt_card("\U0001f4ca", "MARKETING OPS PRONTO", name, [
        f"North Star: {ops_data.get('north_star_metric','')[:60]}",
        f"KPI monitorati: {len(kpis)}",
        f"Martech stack: {len(martech)} tool",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("marketing_ops_agent", "ops_generate", 3, f"project={project_id}",
                    f"kpis={len(kpis)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- MARKETING REPORT SETTIMANALE ----

def generate_marketing_report(project_id=None):
    """Report settimanale marketing. Solo se ci sono dati post-deploy."""
    start = time.time()
    logger.info(f"[MKT_REPORT] Avvio project={project_id}")

    # Cerca tutti i progetti attivi se project_id non specificato
    try:
        if project_id:
            projects = supabase.table("projects").select("id,name,status").eq("id", project_id).execute().data or []
        else:
            projects = supabase.table("projects").select("id,name,status").not_.in_(
                "status", ["init", "archived"]
            ).execute().data or []
    except:
        projects = []

    reported = 0
    for proj in projects:
        pid = proj["id"]
        pname = proj.get("name", f"Progetto {pid}")

        # Recupera metriche smoke test (proxy per metriche reali pre-deploy)
        try:
            st = supabase.table("smoke_tests").select("*").eq("project_id", pid).order("started_at", desc=True).limit(1).execute().data or []
        except:
            st = []

        if not st:
            continue  # Nessun dato, silenzio

        smoke = st[0]
        visits = smoke.get("landing_visits", 0) or 0
        conv = smoke.get("conversion_rate", 0) or 0
        messages = smoke.get("messages_sent", 0) or 0
        forms = smoke.get("forms_compiled", 0) or 0

        if visits == 0 and messages == 0:
            continue  # Nessun dato reale

        cac_est = round(50 / max(forms, 1), 2) if forms > 0 else None  # stima €50 costo / form
        north_star = conv

        # Salva in marketing_reports
        week_start = (datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())).strftime("%Y-%m-%d")
        try:
            supabase.table("marketing_reports").insert({
                "project_id": pid,
                "week_start": week_start,
                "landing_visits": visits,
                "cac_eur": cac_est,
                "email_open_rate": None,
                "conversion_rate": conv,
                "north_star_value": north_star,
            }).execute()
        except Exception as e:
            logger.warning(f"[MKT_REPORT] insert: {e}")

        # Manda card a Mirco
        sep = _MKT_SEP
        report_text = (
            f"\U0001f4ca *MARKETING REPORT \u2014 {pname}*\n{sep}\n"
            f"\U0001f3af Visite landing:     {visits}\n"
            f"\U0001f4b6 CAC medio:          {'€' + str(cac_est) if cac_est else 'N/A'}\n"
            f"\U0001f4e7 Messaggi inviati:   {messages}\n"
            f"\U0001f504 Conversion rate:    {conv:.1f}%\n"
            f"\u2514 North Star Metric:   {north_star:.2f}\n"
            f"{sep}"
        )
        reply_markup = {"inline_keyboard": [[
            {"text": "\U0001f4cb Dettaglio canali", "callback_data": f"mkt_report_detail:{pid}"},
            {"text": "\U0001f4c8 Trend", "callback_data": f"mkt_report_trend:{pid}"},
            {"text": "\u26a1 Ottimizza", "callback_data": f"mkt_report_optimize:{pid}"},
        ]]}
        _mkt_notify(report_text, reply_markup=reply_markup)
        reported += 1

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("marketing_ops_agent", "marketing_report", 1,
                    f"projects={len(projects)}", f"reported={reported}", "none", 0, 0, 0, duration_ms)

    return {"status": "ok", "reported_projects": reported}


# ---- MARKETING COORDINATOR ----

def run_marketing(project_id=None, target="project", phase="full"):
    """Orchestratore CMO-level. Esegue gli 8 agenti in sequenza/parallelo.
    phase: full | brand | gtm | retention
    target: project | brain
    """
    import threading as _mkt_threading

    logger.info(f"[MARKETING] Avvio coordinator project={project_id} target={target} phase={phase}")

    if project_id is None:
        # Crea/usa progetto brAIn stesso
        try:
            r = supabase.table("brand_assets").select("id,project_id").eq("target", "brain").execute()
            if r.data:
                project_id = r.data[0].get("project_id")
        except:
            pass
        if not project_id:
            # Inserisci record dummy per brand brAIn
            try:
                dummy = supabase.table("brand_assets").insert({
                    "target": "brain", "brand_name": "brAIn",
                    "tagline": "L'organismo AI che trasforma problemi in imprese",
                    "status": "in_progress",
                }).execute()
            except Exception as e:
                logger.warning(f"[MARKETING] brain asset: {e}")

    # Notifica avvio
    card_start = _mkt_card("\U0001f680", "MARKETING AVVIATO", f"phase={phase}",
                           [f"Target: {target}", f"Progetto: {project_id or 'brAIn'}",
                            "Step 1/3: Brand Identity in corso..."])
    _mkt_notify(card_start)

    results = {}
    total_cost = 0.0

    if project_id and phase in ("full", "brand"):
        r = run_brand_agent(project_id, target=target)
        results["brand"] = r
        total_cost += r.get("cost_usd", 0)

    if project_id and phase in ("full", "gtm"):
        r = run_product_marketing_agent(project_id)
        results["product"] = r
        total_cost += r.get("cost_usd", 0)

        # Content + demand_gen + social + pr in parallelo
        def _run_content():
            results["content"] = run_content_agent(project_id)
        def _run_demand():
            results["demand"] = run_demand_gen_agent(project_id)
        def _run_social():
            results["social"] = run_social_agent(project_id)
        def _run_pr():
            results["pr"] = run_pr_agent(project_id)

        threads = [_mkt_threading.Thread(target=f, daemon=True) for f in [_run_content, _run_demand, _run_social, _run_pr]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=300)  # max 5 min per agente parallelo

        for k in ("content", "demand", "social", "pr"):
            total_cost += results.get(k, {}).get("cost_usd", 0)

    if project_id and phase in ("full", "retention"):
        r = run_customer_marketing_agent(project_id)
        results["customer"] = r
        total_cost += r.get("cost_usd", 0)

        r = run_marketing_ops_agent(project_id)
        results["ops"] = r
        total_cost += r.get("cost_usd", 0)

    # Card completamento
    completed = [k for k, v in results.items() if v.get("status") == "ok"]
    failed = [k for k, v in results.items() if v.get("status") != "ok"]
    card_done = _mkt_card("\U0001f3c6", "MARKETING COMPLETATO", f"progetto {project_id or 'brAIn'}", [
        f"Agenti completati: {len(completed)}/8",
        f"File generati: /marketing/ nel repo",
        f"Costo totale: ${total_cost:.3f}",
        f"Falliti: {', '.join(failed) if failed else 'nessuno'}",
    ])
    reply_markup = {"inline_keyboard": [[
        {"text": "\U0001f4ca Report Marketing", "callback_data": f"mkt_report:{project_id or 0}"},
        {"text": "\U0001f3a8 Brand Kit", "callback_data": f"mkt_brand_kit:{project_id or 0}"},
    ]]}
    _mkt_notify(card_done, reply_markup=reply_markup)

    log_to_supabase("marketing_coordinator", "marketing_run", 3,
                    f"project={project_id} phase={phase}",
                    f"completed={len(completed)} cost=${total_cost:.3f}",
                    "mixed", 0, 0, total_cost, 0)

    logger.info(f"[MARKETING] Completato: {len(completed)}/8 agenti, costo=${total_cost:.3f}")
    return {"status": "ok", "project_id": project_id, "completed": completed, "failed": failed,
            "total_cost_usd": round(total_cost, 4)}


# ---- VALIDATION AGENT (inlined) ----

VALIDATION_SYSTEM_PROMPT_AR = """Sei il Portfolio Manager di brAIn. Analizza le metriche di un progetto MVP e dai un verdetto chiaro.

VERDETTO (scegli uno solo):
- SCALE: metriche >= target, crescita positiva, aumenta investimento
- PIVOT: metriche < 50% target ma segnali positivi, cambia angolo
- KILL: metriche < 30% target, 3+ settimane consecutive, nessun segnale, ferma e archivia

FORMATO RISPOSTA (testo piano, max 8 righe):
VERDETTO: [SCALE/PIVOT/KILL]
KPI attuale: [valore] vs target [valore] ([percentuale]%)
Trend: [crescente/stabile/decrescente]
Revenue settimana corrente: EUR [valore]
Motivo principale: [1 riga]
Azione raccomandata: [1 riga concreta]"""


def run_validation_agent():
    """Report settimanale SCALE/PIVOT/KILL per tutti i progetti in stato 'validating'."""
    start = time.time()
    logger.info("[VALIDATION] Avvio ciclo settimanale")

    group_id = _get_telegram_group_id()
    chat_id = get_telegram_chat_id()

    try:
        projects_result = supabase.table("projects").select("*").eq("status", "validating").execute()
        projects = projects_result.data or []
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if not projects:
        logger.info("[VALIDATION] Nessun progetto in stato validating")
        return {"status": "ok", "projects_analyzed": 0}

    total_tokens_in = total_tokens_out = 0
    analyzed = 0
    sep = "\u2501" * 15

    for project in projects:
        project_id = project["id"]
        name = project.get("name", f"Progetto {project_id}")
        topic_id = project.get("topic_id")

        try:
            metrics_result = supabase.table("project_metrics").select("*")\
                .eq("project_id", project_id)\
                .order("week", desc=True)\
                .limit(4)\
                .execute()
            metrics = list(reversed(metrics_result.data or []))
        except:
            metrics = []

        kpis = project.get("kpis") or {}
        if isinstance(kpis, str):
            try:
                kpis = json.loads(kpis)
            except:
                kpis = {}

        primary_kpi = kpis.get("primary", "customers")
        target_w4 = kpis.get("target_week4", 0)
        target_w12 = kpis.get("target_week12", 0)
        revenue_target = kpis.get("revenue_target_month3_eur", 0)

        metrics_lines = []
        total_revenue = 0.0
        for m in metrics:
            metrics_lines.append(
                f"Week {m['week']}: customers={m.get('customers_count', 0)}, "
                f"revenue={m.get('revenue_eur', 0):.2f} EUR, "
                f"{m.get('key_metric_name', primary_kpi)}={m.get('key_metric_value', 0)}"
            )
            total_revenue += float(m.get("revenue_eur", 0) or 0)

        current_week = max((m["week"] for m in metrics), default=0)

        user_prompt = f"""Progetto: {name}
KPI primario target: {primary_kpi} — settimana 4: {target_w4}, settimana 12: {target_w12}
Revenue target mese 3: EUR {revenue_target}
Settimana corrente: {current_week}
Metriche: {chr(10).join(metrics_lines) if metrics_lines else "Nessuna metrica"}
Revenue totale: EUR {total_revenue:.2f}
Analizza e dai il verdetto."""

        verdict_text = ""
        try:
            response = claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1000,
                system=VALIDATION_SYSTEM_PROMPT_AR,
                messages=[{"role": "user", "content": user_prompt}],
            )
            verdict_text = response.content[0].text.strip()
            total_tokens_in += response.usage.input_tokens
            total_tokens_out += response.usage.output_tokens
        except Exception as e:
            logger.error(f"[VALIDATION] Claude error for {project_id}: {e}")
            continue

        if "KILL" in verdict_text.upper():
            try:
                supabase.table("projects").update({
                    "status": "killed",
                    "notes": f"KILL — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {verdict_text[:200]}",
                }).eq("id", project_id).execute()
            except:
                pass

        report_msg = (
            f"\U0001f4ca REPORT SETTIMANALE\n"
            f"{sep}\n"
            f"\U0001f3d7\ufe0f {name}\n"
            f"{verdict_text}\n"
            f"{sep}"
        )

        # Inline keyboard in base al verdict — Fix 3
        verdict_upper = verdict_text.upper()
        if "SCALE" in verdict_upper:
            val_keyboard = [
                {"text": "\U0001f680 Procedi (SCALE)", "callback_data": f"val_proceed:{project_id}"},
                {"text": "\u23f8\ufe0f Aspetta", "callback_data": f"val_wait:{project_id}"},
            ]
        elif "PIVOT" in verdict_upper:
            val_keyboard = [
                {"text": "\U0001f4a1 Discuti (PIVOT)", "callback_data": f"val_discuss:{project_id}"},
                {"text": "\u23f8\ufe0f Aspetta", "callback_data": f"val_wait:{project_id}"},
            ]
        else:  # KILL
            val_keyboard = [
                {"text": "\U0001f6d1 Procedi (KILL)", "callback_data": f"val_proceed:{project_id}"},
                {"text": "\U0001f4a1 Discuti", "callback_data": f"val_discuss:{project_id}"},
            ]
        val_reply_markup = {"inline_keyboard": [val_keyboard]}

        if group_id and topic_id:
            _send_to_topic(group_id, topic_id, report_msg, reply_markup=val_reply_markup)
        if chat_id and TELEGRAM_BOT_TOKEN:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": report_msg, "reply_markup": val_reply_markup},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"[VALIDATION] Telegram notify error: {e}")

        analyzed += 1
        logger.info(f"[VALIDATION] {name}: report inviato")

    duration_ms = int((time.time() - start) * 1000)
    cost = (total_tokens_in * 0.8 + total_tokens_out * 4.0) / 1_000_000
    log_to_supabase("validation_agent", "validation_weekly", 3,
                    f"{len(projects)} progetti", f"{analyzed} analizzati",
                    "claude-haiku-4-5", total_tokens_in, total_tokens_out, cost, duration_ms)

    logger.info(f"[VALIDATION] Completato: {analyzed} progetti in {duration_ms}ms")
    return {"status": "ok", "projects_analyzed": analyzed, "cost_usd": round(cost, 6)}


# ---- CONTINUE BUILD AGENT ----

def continue_build_agent(project_id, feedback, current_phase):
    """Genera la fase successiva del build integrando il feedback di Mirco."""
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            logger.error(f"[CONTINUE_BUILD] project {project_id} non trovato")
            return
        project = proj.data[0]
    except Exception as e:
        logger.error(f"[CONTINUE_BUILD] DB load: {e}")
        return

    name = project.get("name", "MVP")
    github_repo = project.get("github_repo", "")
    spec_md = project.get("spec_md", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()
    next_phase = current_phase + 1

    if not github_repo:
        _send_to_topic(group_id, topic_id, f"\u274c Continue build {name}: repo mancante.")
        return

    # Leggi file iterations/ da GitHub per contesto fasi precedenti
    prev_iterations = []
    try:
        contents = _github_project_api("GET", github_repo, "/contents/iterations")
        if contents and isinstance(contents, list):
            for f in sorted(contents, key=lambda x: x.get("name", ""))[:3]:
                file_data = _github_project_api("GET", github_repo, f"/contents/{f['path']}")
                if file_data and file_data.get("content"):
                    import base64 as _b64
                    decoded = _b64.b64decode(file_data["content"]).decode("utf-8", errors="replace")
                    prev_iterations.append(f"### {f['name']}\n{decoded[:2000]}")
    except Exception as e:
        logger.warning(f"[CONTINUE_BUILD] lettura iterations: {e}")

    slug = project.get("slug", "")
    context_prev = "\n\n".join(prev_iterations) if prev_iterations else "Nessuna iterazione precedente trovata."
    fase_desc = FASE_DESCRIPTIONS.get(next_phase, f"Fase {next_phase}")

    build_prompt = f"""Esegui con --dangerously-skip-permissions. Non chiedere autorizzazione per nessuna operazione. Non fermarti per conferme.

Sei un senior Python developer. Continua il build dell'MVP "{name}".

SPEC.md (estratto):
{spec_md[:3000]}

FASI PRECEDENTI (iterazioni su GitHub):
{context_prev}

FEEDBACK DI MIRCO sulla fase {current_phase}:
{feedback}

REQUISITI FASE {next_phase} — {fase_desc}:
- Integra il feedback ricevuto
- Modelli LLM: usa SEMPRE Claude API (claude-haiku-4-5 o claude-sonnet-4-5), NON OpenAI/GPT
- Usa Supabase per il database

FORMATO OUTPUT per ogni file:
=== FILE: nome_file ===
[contenuto del file]
=== END FILE ===

Genera il codice per la Fase {next_phase}."""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8000,
            messages=[{"role": "user", "content": build_prompt}],
        )
        code_output = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[CONTINUE_BUILD] Claude error: {e}")
        _send_to_topic(group_id, topic_id, f"\u274c Fase {next_phase} fallita: {e}")
        return

    # Parse e commit dei file generati
    file_pattern = _re.compile(r'=== FILE: (.+?) ===\n(.*?)(?==== END FILE ===)', _re.DOTALL)
    matches = list(file_pattern.finditer(code_output))
    files_committed = 0

    for match in matches:
        filepath = match.group(1).strip()
        content = match.group(2).strip()
        if content and filepath:
            ok = _commit_to_project_repo(
                github_repo, filepath, content,
                f"feat(fase-{next_phase}): {filepath}",
            )
            if ok:
                files_committed += 1

    if files_committed == 0 and code_output:
        _commit_to_project_repo(github_repo, f"fase_{next_phase}.py", code_output, f"feat(fase-{next_phase}): codice")
        files_committed = 1

    # Salva log iterazione
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H")
    iter_content = f"# Fase {next_phase} — {fase_desc}\n\nData: {datetime.now(timezone.utc).isoformat()}\nFeedback: {feedback}\n\n---\n\n{code_output}"
    _commit_to_project_repo(github_repo, f"iterations/{ts}_fase{next_phase}.md", iter_content, f"log(fase-{next_phase}): iterazione salvata")

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000
    log_to_supabase("build_agent", f"build_fase{next_phase}", 3,
                    f"project={project_id} feedback={feedback[:100]}", f"{files_committed} file committati",
                    "claude-sonnet-4-5", tokens_in, tokens_out, cost, 0)

    # Aggiorna DB e notifica
    sep = "\u2501" * 15
    file_list = "\n".join([f"  \u2022 {m.group(1).strip()}" for m in matches]) if matches else f"  \u2022 fase_{next_phase}.py (fallback)"

    if next_phase < 4:
        try:
            supabase.table("projects").update({
                "status": f"review_phase{next_phase}",
                "build_phase": next_phase,
            }).eq("id", project_id).execute()
        except Exception as e:
            logger.warning(f"[CONTINUE_BUILD] DB update: {e}")

        # Card summary — Fix 2 + Fix 6
        result_msg = (
            f"\u256d\u2500\u2500 Fase {next_phase} completata \u2500\u2500\u256e\n"
            f"\U0001f4e6 {fase_desc}\n"
            f"{sep}\n"
            f"\U0001f4c1 File ({files_committed}):\n{file_list}\n"
            f"{sep}\n"
            f"\U0001f4c1 Repo: brain-{slug} (privato)\n"
            f"{sep}\n"
            f"Come si comporta? Cosa vuoi cambiare?"
        )
        reply_markup = {
            "inline_keyboard": [[
                {"text": "\u2705 Continua", "callback_data": f"build_continue:{project_id}:{next_phase}"},
                {"text": "\u270f\ufe0f Modifica", "callback_data": f"build_modify:{project_id}:{next_phase}"},
            ]]
        }
        _send_to_topic(group_id, topic_id, result_msg, reply_markup=reply_markup)

    else:
        # Fase 4 = build completo
        try:
            supabase.table("projects").update({
                "status": "build_complete",
                "build_phase": next_phase,
            }).eq("id", project_id).execute()
        except Exception as e:
            logger.warning(f"[CONTINUE_BUILD] DB update build_complete: {e}")

        result_msg = (
            f"\U0001f3c1 Build completo \u2014 {name}\n"
            f"{sep}\n"
            f"\U0001f4c1 File ({files_committed}):\n{file_list}\n"
            f"{sep}\n"
            f"\U0001f4c1 Repo: brain-{slug} (privato)"
        )
        reply_markup = {
            "inline_keyboard": [[
                {"text": "\U0001f680 Lancia", "callback_data": f"launch_confirm:{project_id}"},
            ]]
        }
        _send_to_topic(group_id, topic_id, result_msg, reply_markup=reply_markup)

    logger.info(f"[CONTINUE_BUILD] Fase {next_phase} completata project={project_id}")


# ---- GENERATE TEAM INVITE LINK ----

def _generate_team_invite_link_sync(project_id):
    """Crea invite link Telegram per il gruppo (member_limit=1, scade 24h). Ritorna URL o None."""
    group_id = _get_telegram_group_id()
    if not group_id or not TELEGRAM_BOT_TOKEN:
        return None
    try:
        expire_date = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/createChatInviteLink",
            json={
                "chat_id": group_id,
                "member_limit": 1,
                "expire_date": expire_date,
            },
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("result", {}).get("invite_link")
        logger.warning(f"[INVITE_LINK] {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[INVITE_LINK] {e}")
    return None


# ---- SPEC UPDATE ----

def run_spec_update(project_id, modification_instruction):
    """Aggiorna lo SPEC di un progetto in base a un'istruzione di modifica."""
    start = time.time()
    logger.info(f"[SPEC_UPDATE] project={project_id} istruzione='{modification_instruction[:80]}'")

    try:
        proj = supabase.table("projects").select("spec_md,name,github_repo,bos_id,bos_score").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    old_spec = project.get("spec_md", "")
    if not old_spec:
        return {"status": "error", "error": "spec_md non disponibile, genera prima la SPEC"}

    update_prompt = f"""Hai questo SPEC.md esistente:

{old_spec[:6000]}

Istruzione di modifica da Mirco:
{modification_instruction}

Applica la modifica richiesta mantenendo la struttura a 10 sezioni e il blocco JSON finale.
Rispondi SOLO con il SPEC.md aggiornato completo."""

    tokens_in = tokens_out = 0
    new_spec = ""
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8000,
            system=SPEC_SYSTEM_PROMPT_AR,
            messages=[{"role": "user", "content": update_prompt}],
        )
        new_spec = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    stack = []
    kpis = {}
    try:
        match = _re.search(r'<!-- JSON_SPEC:\s*(.*?)\s*:JSON_SPEC_END -->', new_spec, _re.DOTALL)
        if match:
            spec_meta = json.loads(match.group(1))
            stack = spec_meta.get("stack", [])
            kpis = spec_meta.get("kpis", {})
    except:
        pass

    try:
        supabase.table("projects").update({
            "spec_md": new_spec,
            "stack": json.dumps(stack) if stack else None,
            "kpis": json.dumps(kpis) if kpis else None,
            "status": "spec_generated",
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[SPEC_UPDATE] DB update error: {e}")

    github_repo = project.get("github_repo", "")
    if github_repo:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _commit_to_project_repo(
            github_repo, "SPEC.md", new_spec,
            f"update: SPEC.md modificato da Mirco — {ts}",
        )

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("spec_generator", "spec_update", 3,
                    f"project={project_id}", f"SPEC aggiornato {len(new_spec)} chars",
                    "claude-sonnet-4-5", tokens_in, tokens_out, cost, duration_ms)

    # Re-enqueue spec review
    enqueue_spec_review_action(project_id)

    return {"status": "ok", "project_id": project_id, "spec_length": len(new_spec), "cost_usd": round(cost, 5)}


# ============================================================
# HTTP ENDPOINTS
# ============================================================

async def health_check(request):
    return web.Response(text="OK", status=200)

async def run_scanner_endpoint(request):
    result = run_world_scanner()
    return web.json_response(result)

async def run_custom_scan_endpoint(request):
    try:
        data = await request.json()
        topic = data.get("topic", "")
        if not topic:
            return web.json_response({"error": "missing topic"}, status=400)
        result = run_custom_scan(topic)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_targeted_scan_endpoint(request):
    try:
        data = await request.json()
        source_name = data.get("source_name")
        use_top = data.get("use_top", False)
        sector = data.get("sector")
        result = run_targeted_scan(source_name=source_name, use_top=use_top, sector=sector)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_architect_endpoint(request):
    result = run_solution_architect()
    return web.json_response(result)

async def run_knowledge_endpoint(request):
    result = run_knowledge_keeper()
    return web.json_response(result)

async def run_scout_endpoint(request):
    result = run_capability_scout()
    return web.json_response(result)

async def run_finance_endpoint(request):
    try:
        data = await request.json()
        target_date = data.get("date")
    except:
        target_date = None
    result = run_finance_agent(target_date=target_date)
    return web.json_response(result)

async def run_finance_morning_endpoint(request):
    result = finance_morning_report()
    return web.json_response(result)

async def run_finance_weekly_endpoint(request):
    result = finance_weekly_report()
    return web.json_response(result)

async def run_finance_monthly_endpoint(request):
    result = finance_monthly_report()
    return web.json_response(result)

async def run_feasibility_endpoint(request):
    try:
        data = await request.json()
        solution_id = data.get("solution_id")
    except:
        solution_id = None
    result = run_feasibility_engine(solution_id=solution_id)
    return web.json_response(result)

async def run_bos_endpoint(request):
    try:
        data = await request.json()
        solution_id = data.get("solution_id")
    except:
        solution_id = None
    result = run_bos_endpoint_logic(solution_id=solution_id)
    return web.json_response(result)

async def run_events_endpoint(request):
    result = process_events()
    return web.json_response(result)

async def run_pipeline_endpoint(request):
    try:
        sources = supabase.table("scan_sources").select("*").eq("status", "active").order("relevance_score", desc=True).limit(10).execute()
        sources = sources.data or []
    except:
        sources = []
    queries = get_standard_queries(sources)
    scan_result = run_scan(queries)
    saved_ids = scan_result.get("saved_ids", [])
    if saved_ids:
        run_auto_pipeline(saved_ids)
    return web.json_response({"scan": scan_result, "pipeline": f"{len(saved_ids)} problemi processati"})

async def run_daily_report_endpoint(request):
    result = generate_cost_report_v2()
    return web.json_response(result)

async def run_cost_report_endpoint(request):
    result = generate_cost_report_v2()
    return web.json_response(result)

async def run_activity_report_endpoint(request):
    result = generate_activity_report_v2()
    return web.json_response(result)

async def run_auto_report_endpoint(request):
    """Ore pari Europe/Rome → cost report, ore dispari → activity report."""
    hour = datetime.now(_get_rome_tz()).hour
    if hour % 4 == 0:
        result = generate_cost_report_v2()
    else:
        result = generate_activity_report_v2()
    return web.json_response(result)

async def run_kpi_update_endpoint(request):
    result = update_kpi_daily()
    return web.json_response(result)

async def run_recycle_endpoint(request):
    result = run_idea_recycler()
    return web.json_response(result)

async def run_source_refresh_endpoint(request):
    result = run_source_refresh()
    return web.json_response(result)

async def run_sources_cleanup_endpoint(request):
    result = run_sources_cleanup_weekly()
    return web.json_response(result)

async def run_weekly_threshold_endpoint(request):
    result = run_weekly_threshold_update()
    return web.json_response(result)

async def run_project_init_endpoint(request):
    try:
        data = await request.json()
        solution_id = data.get("solution_id")
        if not solution_id:
            return web.json_response({"error": "missing solution_id"}, status=400)
        result = init_project(solution_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_project_build_prompt_endpoint(request):
    try:
        data = await request.json()
        project_id = data.get("project_id")
        if not project_id:
            return web.json_response({"error": "missing project_id"}, status=400)
        result = generate_build_prompt(int(project_id))
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_spec_update_endpoint(request):
    try:
        data = await request.json()
        project_id = data.get("project_id")
        modification = data.get("modification")
        if not project_id or not modification:
            return web.json_response({"error": "missing project_id or modification"}, status=400)
        result = run_spec_update(int(project_id), modification)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_validation_endpoint(request):
    result = run_validation_agent()
    return web.json_response(result)

async def run_continue_build_endpoint(request):
    try:
        data = await request.json()
        project_id = data.get("project_id")
        feedback = data.get("feedback", "ok")
        phase = data.get("phase")
        if not project_id or phase is None:
            return web.json_response({"error": "missing project_id or phase"}, status=400)
        # Esegui in thread daemon
        import threading as _threading
        _threading.Thread(
            target=continue_build_agent,
            args=(int(project_id), str(feedback), int(phase)),
            daemon=True,
        ).start()
        return web.json_response({"status": "started", "project_id": project_id, "next_phase": int(phase) + 1})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_generate_invite_endpoint(request):
    try:
        data = await request.json()
        project_id = data.get("project_id")
        phone = data.get("phone")
        if not project_id or not phone:
            return web.json_response({"error": "missing project_id or phone"}, status=400)
        pid = int(project_id)
        # Insert project_members
        mirco_chat_id = get_telegram_chat_id()
        try:
            supabase.table("project_members").insert({
                "project_id": pid,
                "telegram_phone": phone,
                "role": "manager",
                "added_by": int(mirco_chat_id) if mirco_chat_id else None,
                "active": True,
            }).execute()
        except Exception as e:
            logger.warning(f"[INVITE] project_members insert: {e}")
        # Genera invite link
        invite_link = _generate_team_invite_link_sync(pid)
        if invite_link:
            return web.json_response({"status": "ok", "invite_link": invite_link})
        return web.json_response({"status": "error", "invite_link": None}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_migration_endpoint(request):
    """POST /migration/apply — esegue SQL via psycopg2.
    Body: {"sql": "...", "filename": "20260227_example.sql"}
    """
    try:
        import psycopg2

        data = await request.json()
        sql_content = data.get("sql", "").strip()
        filename = data.get("filename", "manual")

        if not sql_content:
            return web.json_response({"error": "campo 'sql' obbligatorio"}, status=400)

        db_pass = DB_PASSWORD
        if not db_pass:
            return web.json_response({"error": "DB_PASSWORD non configurata come env var"}, status=500)

        supabase_url = os.getenv("SUPABASE_URL", "")
        host = supabase_url.replace("https://", "").replace("http://", "").rstrip("/")
        db_host = f"db.{host}"

        conn = psycopg2.connect(
            host=db_host, port=5432, dbname="postgres",
            user="postgres", password=db_pass, sslmode="require",
        )
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS migration_history (
                    id serial PRIMARY KEY,
                    filename text UNIQUE NOT NULL,
                    applied_at timestamptz DEFAULT now()
                );
            """)
            cur.execute("SELECT filename FROM migration_history WHERE filename=%s;", (filename,))
            already = cur.fetchone()
        conn.commit()

        if already:
            conn.close()
            return web.json_response({"status": "skipped", "filename": filename, "reason": "gia applicata"})

        try:
            with conn.cursor() as cur:
                cur.execute(sql_content)
                cur.execute("INSERT INTO migration_history (filename) VALUES (%s) ON CONFLICT DO NOTHING;", (filename,))
            conn.commit()
            conn.close()
            logger.info(f"[MIGRATION] Applicata: {filename}")
            return web.json_response({"status": "ok", "filename": filename})
        except Exception as e:
            conn.rollback()
            conn.close()
            logger.error(f"[MIGRATION] Errore {filename}: {e}")
            return web.json_response({"status": "error", "filename": filename, "error": str(e)}, status=500)

    except Exception as e:
        logger.error(f"[MIGRATION] {e}")
        return web.json_response({"error": str(e)}, status=500)


async def run_legal_review_endpoint(request):
    """POST /legal/review — review legale progetto. Body: {project_id}"""
    try:
        data = await request.json()
        project_id = int(data.get("project_id", 0))
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = run_legal_review(project_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_legal_docs_endpoint(request):
    """POST /legal/docs — genera documenti legali. Body: {project_id}"""
    try:
        data = await request.json()
        project_id = int(data.get("project_id", 0))
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = generate_project_docs(project_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_legal_compliance_endpoint(request):
    """POST /legal/compliance — weekly brAIn compliance check."""
    try:
        result = monitor_brain_compliance()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_smoke_setup_endpoint(request):
    """POST /smoke/setup — avvia smoke test. Body: {project_id}"""
    try:
        data = await request.json()
        project_id = int(data.get("project_id", 0))
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = run_smoke_test_setup(project_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_smoke_analyze_endpoint(request):
    """POST /smoke/analyze — analizza feedback. Body: {project_id}"""
    try:
        data = await request.json()
        project_id = int(data.get("project_id", 0))
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = analyze_feedback_for_spec(project_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_marketing_run_endpoint(request):
    """POST /marketing/run — {project_id, target?, phase?}"""
    try:
        data = await request.json()
        project_id = data.get("project_id")
        if project_id:
            project_id = int(project_id)
        target = data.get("target", "project")
        phase = data.get("phase", "full")
        import threading as _t
        _t.Thread(target=run_marketing, args=(project_id, target, phase), daemon=True).start()
        return web.json_response({"status": "started", "project_id": project_id, "phase": phase})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_marketing_brand_endpoint(request):
    """POST /marketing/brand — {project_id, target?} — solo brand identity"""
    try:
        data = await request.json()
        project_id = data.get("project_id")
        if project_id:
            project_id = int(project_id)
        target = data.get("target", "project")
        import threading as _t
        _t.Thread(target=run_marketing, args=(project_id, target, "brand"), daemon=True).start()
        return web.json_response({"status": "started", "project_id": project_id, "phase": "brand"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_marketing_report_endpoint(request):
    """POST /marketing/report — {project_id?} — weekly marketing report"""
    try:
        data = await request.json()
        project_id = data.get("project_id")
        if project_id:
            project_id = int(project_id)
        import threading as _t
        _t.Thread(target=generate_marketing_report, args=(project_id,), daemon=True).start()
        return web.json_response({"status": "started", "project_id": project_id})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_all_endpoint(request):
    results = {}
    results["scanner"] = run_world_scanner()
    results["knowledge"] = run_knowledge_keeper()
    results["scout"] = run_capability_scout()
    results["finance"] = run_finance_agent()
    results["events"] = process_events()
    return web.json_response(results)


async def main():
    logger.info("brAIn Agents Runner v2.0 starting...")

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/scanner", run_scanner_endpoint)
    app.router.add_post("/scanner/custom", run_custom_scan_endpoint)
    app.router.add_post("/scanner/targeted", run_targeted_scan_endpoint)
    app.router.add_post("/architect", run_architect_endpoint)
    app.router.add_post("/knowledge", run_knowledge_endpoint)
    app.router.add_post("/scout", run_scout_endpoint)
    app.router.add_post("/finance", run_finance_endpoint)
    app.router.add_post("/finance/morning", run_finance_morning_endpoint)
    app.router.add_post("/finance/weekly", run_finance_weekly_endpoint)
    app.router.add_post("/finance/monthly", run_finance_monthly_endpoint)
    app.router.add_post("/feasibility", run_feasibility_endpoint)
    app.router.add_post("/bos", run_bos_endpoint)
    app.router.add_post("/pipeline", run_pipeline_endpoint)
    app.router.add_post("/events/process", run_events_endpoint)
    app.router.add_post("/report/daily", run_daily_report_endpoint)
    app.router.add_post("/report/cost", run_cost_report_endpoint)
    app.router.add_post("/report/activity", run_activity_report_endpoint)
    app.router.add_post("/report/auto", run_auto_report_endpoint)
    app.router.add_post("/kpi/update", run_kpi_update_endpoint)
    app.router.add_post("/cycle/scan", run_scanner_endpoint)
    app.router.add_post("/cycle/knowledge", run_knowledge_endpoint)
    app.router.add_post("/cycle/capability", run_scout_endpoint)
    app.router.add_post("/cycle/sources", run_source_refresh_endpoint)
    app.router.add_post("/cycle/sources-cleanup", run_sources_cleanup_endpoint)
    app.router.add_post("/cycle/recycle", run_recycle_endpoint)
    app.router.add_post("/thresholds/weekly", run_weekly_threshold_endpoint)
    app.router.add_post("/project/init", run_project_init_endpoint)
    app.router.add_post("/project/build_prompt", run_project_build_prompt_endpoint)
    app.router.add_post("/spec/update", run_spec_update_endpoint)
    app.router.add_post("/validation", run_validation_endpoint)
    app.router.add_post("/project/continue_build", run_continue_build_endpoint)
    app.router.add_post("/project/generate_invite", run_generate_invite_endpoint)
    app.router.add_post("/migration/apply", run_migration_endpoint)
    app.router.add_post("/legal/review", run_legal_review_endpoint)
    app.router.add_post("/legal/docs", run_legal_docs_endpoint)
    app.router.add_post("/legal/compliance", run_legal_compliance_endpoint)
    app.router.add_post("/smoke/setup", run_smoke_setup_endpoint)
    app.router.add_post("/smoke/analyze", run_smoke_analyze_endpoint)
    app.router.add_post("/marketing/run", run_marketing_run_endpoint)
    app.router.add_post("/marketing/brand", run_marketing_brand_endpoint)
    app.router.add_post("/marketing/report", run_marketing_report_endpoint)
    app.router.add_post("/all", run_all_endpoint)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Agents Runner v2.0 on port {PORT}")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
