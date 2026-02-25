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
        approved = supabase.table("problems").select("sector").eq("status", "approved").execute()
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

MIN_SCORE_THRESHOLD = 0.55

SCANNER_SECTORS = [
    "food", "health", "finance", "education", "legal",
    "ecommerce", "hr", "real_estate", "sustainability",
    "cybersecurity", "entertainment", "logistics",
]

SCANNER_ANALYSIS_PROMPT = """Sei il World Scanner di brAIn, un'organizzazione AI-native.
Analizzi risultati di ricerca per identificare problemi reali e concreti che colpiscono persone o organizzazioni.

Per ogni problema identificato (massimo 3), fornisci:

1. DATI QUANTITATIVI - 7 score da 0.0 a 1.0:
   - market_size: quante persone/organizzazioni colpisce
   - willingness_to_pay: quanto pagherebbero per una soluzione
   - urgency: quanto e' urgente risolverlo
   - competition_gap: quanto sono deboli le soluzioni attuali
   - ai_solvability: quanto si puo risolvere con AI
   - time_to_market: quanto veloce si puo lanciare
   - recurring_potential: problema ricorrente = revenue ricorrente

REGOLA CRITICA SUGLI SCORE: Devi usare TUTTA la scala da 0.0 a 1.0. NON dare score tutti simili.
Usa questi riferimenti precisi:
- 0.0-0.2 = molto basso
- 0.2-0.4 = basso
- 0.4-0.6 = medio
- 0.6-0.8 = alto
- 0.8-1.0 = molto alto

Ogni problema DEVE avere almeno 2 score sotto 0.4 e almeno 1 score sotto 0.3.

2. DATI QUALITATIVI:
   - who_is_affected: chi soffre? Sii specifico
   - real_world_example: storia concreta
   - why_it_matters: perche ci tiene a risolverlo

3. CLASSIFICAZIONE:
   - sector: uno tra food, health, finance, education, legal, ecommerce, hr, real_estate, sustainability, cybersecurity, entertainment, logistics
   - geographic_scope: global, continental, national, regional
   - top_markets: lista 3-5 codici paese ISO

NON riproporre problemi generici. Cerca problemi specifici con gap reale.

LINGUA: Rispondi SEMPRE in italiano. Titoli, descrizioni, who_is_affected, real_world_example, why_it_matters: tutto in italiano.

REGOLA DIVERSITA SETTORI: i problemi devono riguardare settori DIVERSI.

{preferences_block}

Rispondi SOLO con JSON:
{{"problems":[{{"title":"titolo","description":"descrizione","who_is_affected":"chi","real_world_example":"storia","why_it_matters":"perche","sector":"food","geographic_scope":"global","top_markets":["US","UK"],"market_size":0.8,"willingness_to_pay":0.3,"urgency":0.6,"competition_gap":0.8,"ai_solvability":0.9,"time_to_market":0.4,"recurring_potential":0.2,"source_name":"fonte","source_url":"url"}}],"new_sources":[{{"name":"nome","url":"url","category":"tipo","sectors":["settore"]}}]}}
SOLO JSON."""


def get_scan_strategy():
    """Determina quale strategia usare basata su ora e giorno (rotazione 6 cicli)."""
    now = datetime.now(timezone.utc)
    # Ogni 4 ore = 6 cicli al giorno. Combiniamo giorno e ciclo per variazione.
    cycle_in_day = now.hour // 4  # 0-5
    day_offset = now.timetuple().tm_yday % 6
    strategy_index = (cycle_in_day + day_offset) % 6

    strategies = [
        "top_sources",        # Ciclo 1: scan fonti top ranked
        "low_ranked_gems",    # Ciclo 2: esplora fonti a basso ranking
        "sector_deep_dive",   # Ciclo 3: deep dive settore con meno problemi
        "correlated_problems",# Ciclo 4: problemi correlati ad approvati
        "emerging_trends",    # Ciclo 5: trend emergenti e futuri
        "source_refresh",     # Ciclo 6: rivaluta fonti, cerca nuove
    ]
    return strategies[strategy_index], strategy_index


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


def scanner_calculate_weighted_score(problem):
    score = 0
    for param, weight in SCANNER_WEIGHTS.items():
        value = problem.get(param, 0.5)
        if isinstance(value, (int, float)):
            score += value * weight
    return round(score, 4)


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


def run_scan(queries):
    """Core scan logic."""
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
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=analysis_prompt,
                messages=[{"role": "user", "content": f"Analizza e identifica problemi. SOLO JSON:\n\n{combined}"}]
            )
            duration = int((time.time() - start) * 1000)
            reply = response.content[0].text

            log_to_supabase("world_scanner", "scan_v2", 1,
                f"Batch {len(batch)} ricerche", reply[:500],
                "claude-haiku-4-5-20251001",
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

                    if weighted < MIN_SCORE_THRESHOLD:
                        continue

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
                            "fingerprint": fp, "source_id": source_id,
                            "status": "new", "created_by": "world_scanner_v2",
                        }).execute()

                        total_saved += 1
                        all_scores.append(weighted)
                        existing_fps.add(fp)
                        if insert_result.data:
                            saved_problem_ids.append(insert_result.data[0]["id"])

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

    # Aggiorna statistiche fonti
    if all_scores and sources:
        avg_score = sum(all_scores) / len(all_scores)
        for source in sources:
            try:
                old_found = source.get("problems_found", 0)
                old_avg = source.get("avg_problem_score", 0)
                new_found = old_found + len(all_scores)
                new_avg = (old_avg * old_found + avg_score * len(all_scores)) / new_found if old_found > 0 else avg_score
                old_rel = source.get("relevance_score", 0.5)
                new_rel = min(1.0, old_rel + 0.02) if avg_score > 0.6 else max(0.1, old_rel - 0.02) if avg_score < 0.4 else old_rel

                supabase.table("scan_sources").update({
                    "problems_found": new_found,
                    "avg_problem_score": round(new_avg, 4),
                    "relevance_score": round(new_rel, 4),
                    "last_scanned": datetime.now(timezone.utc).isoformat(),
                }).eq("id", source["id"]).execute()
            except:
                pass

    # Emetti eventi
    if total_saved > 0:
        emit_event("world_scanner", "scan_completed", None,
            {"problems_saved": total_saved, "problem_ids": saved_problem_ids,
             "avg_score": sum(all_scores) / len(all_scores) if all_scores else 0})
        # Notifica problemi trovati
        high_score_ids = [pid for pid, sc in zip(saved_problem_ids, all_scores) if sc >= 0.55]
        if high_score_ids:
            emit_event("world_scanner", "problems_found", "command_center",
                {"problem_ids": high_score_ids, "count": len(high_score_ids)})

    if total_saved >= 3:
        emit_event("world_scanner", "batch_scan_complete", "knowledge_keeper",
            {"problems_saved": total_saved, "avg_score": sum(all_scores) / len(all_scores) if all_scores else 0})

    return {"status": "completed", "saved": total_saved, "saved_ids": saved_problem_ids}


def run_world_scanner():
    """Scan con strategia variabile automatica."""
    strategy, idx = get_scan_strategy()
    logger.info(f"World Scanner v2.3 starting — strategy: {strategy} (cycle {idx})")

    log_to_supabase("world_scanner", f"scan_strategy_{strategy}", 1,
        f"Strategia: {strategy}", None, "none")

    queries, strategy_label = build_strategy_queries(strategy)
    result = run_scan(queries)
    logger.info(f"World Scanner completato ({strategy_label}): {result}")

    # Pipeline automatica in background
    saved_ids = result.get("saved_ids", [])
    if saved_ids:
        import threading
        threading.Thread(target=run_auto_pipeline, args=(saved_ids,), daemon=True).start()
        logger.info(f"[PIPELINE] Avviata in background per {len(saved_ids)} problemi")

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

GENERATION_PROMPT = """Sei un innovation strategist di livello mondiale. Combini il meglio di:
- Opportunity Solution Tree (Teresa Torres)
- Blue Ocean Strategy
- Jobs-to-be-Done
- Lean Canvas

Hai un DOSSIER COMPETITIVO e un PROBLEMA. Genera 3 soluzioni ordinate per potenziale.

LINGUA: Rispondi SEMPRE in italiano. Titoli, descrizioni, value proposition, tutto in italiano.

REGOLE CRITICHE:
- NON proporre soluzioni che gia' esistono e funzionano bene
- Cerca gli SPAZI VUOTI
- Pensa a soluzioni con vantaggio difendibile
- Sii SPECIFICO

Per ogni soluzione fornisci:
- title, description, value_proposition, target_segment, job_to_be_done
- revenue_model, monthly_revenue_potential, monthly_burn_rate
- competitive_moat, novelty_score (0-1), opportunity_score (0-1), defensibility_score (0-1)

BOS SOLUTION QUALITY SCORES (0.0-1.0 per ognuno):
- uniqueness: unicita rispetto a soluzioni esistenti (peso 25%)
- moat_potential: vantaggio difendibile (peso 20%)
- value_multiplier: valore/prezzo, 10x = 1.0 (peso 20%)
- simplicity: semplicita per il cliente (peso 10%)
- revenue_clarity: chiarezza modello revenue (peso 15%)
- ai_nativeness: quanto e' nativamente AI (peso 10%)

{preferences_block}

Rispondi SOLO con JSON:
{{"solutions":[{{"title":"","description":"","value_proposition":"","target_segment":"","job_to_be_done":"","revenue_model":"","monthly_revenue_potential":"","monthly_burn_rate":"","competitive_moat":"","novelty_score":0.7,"opportunity_score":0.8,"defensibility_score":0.6,"uniqueness":0.7,"moat_potential":0.6,"value_multiplier":0.8,"simplicity":0.7,"revenue_clarity":0.8,"ai_nativeness":0.9}}],"ranking_rationale":"perche' hai messo la prima in cima"}}
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
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system=RESEARCH_PROMPT,
            messages=[{"role": "user", "content": f"{problem_context}\n\nRISULTATI RICERCA:\n{combined_research}\n\nCrea il dossier. SOLO JSON."}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("solution_architect", "research", 2,
            f"Ricerca: {title[:100]}", reply[:500],
            "claude-haiku-4-5-20251001",
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
        f"Chi e' colpito: {problem.get('who_is_affected', '')}\n"
        f"Esempio reale: {problem.get('real_world_example', '')}\n"
        f"Perche conta: {problem.get('why_it_matters', '')}\n"
        f"Score problema: {problem.get('weighted_score', '')}"
    )

    dossier_text = json.dumps(dossier, indent=2, ensure_ascii=False)

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4000,
            system=gen_prompt,
            messages=[{"role": "user", "content": f"{problem_context}\n\nDOSSIER COMPETITIVO:\n{dossier_text}\n\nGenera 3 soluzioni. SOLO JSON."}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("solution_architect", "generate_unconstrained", 2,
            f"Soluzioni per: {problem['title'][:100]}", reply[:500],
            "claude-sonnet-4-5-20250929",
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
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system=SA_FEASIBILITY_PROMPT,
            messages=[{"role": "user", "content": f"PROBLEMA: {problem['title']}\n\nSOLUZIONI:\n{solutions_text}\n\nValuta fattibilita. SOLO JSON."}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("solution_architect", "assess_feasibility", 2,
            f"Fattibilita: {problem['title'][:100]}", reply[:500],
            "claude-haiku-4-5-20251001",
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
Valuti la fattibilita' economica e tecnica di soluzioni AI.

LINGUA: Rispondi SEMPRE in italiano.

VINCOLI:
- 1 persona, 20h/settimana, competenza tecnica minima
- Budget: 1000 EUR/mese totale, primo progetto sotto 200 EUR/mese
- Stack: Claude API, Supabase, Python, Google Cloud Run, Telegram Bot
- Obiettivo: revenue entro 3 mesi, marginalita' alta

Per la soluzione, calcola:

1. COSTO MVP: dev_hours, dev_cost_eur, api_monthly_eur, hosting_monthly_eur, other_monthly_eur, total_mvp_cost_eur, total_monthly_cost_eur
2. TEMPO: weeks_to_mvp, weeks_to_revenue
3. REVENUE (3 scenari 6 mesi): pessimistic_monthly_eur, realistic_monthly_eur, optimistic_monthly_eur, pricing_model, price_point_eur
4. MARGINALITA: monthly_margin_pessimistic/realistic/optimistic, margin_percentage_realistic, breakeven_months
5. COMPETITION: competition_score (0-1), direct_competitors, indirect_competitors, our_advantage
6. GO/NO-GO: decision (GO/CONDITIONAL_GO/NO_GO), confidence (0-1), reasoning, conditions, biggest_risk, biggest_opportunity

7. BOS FEASIBILITY SCORES (0.0-1.0 per ogni parametro):
   - margin_potential (25%): margine >70% = 1.0, <20% = 0.0
   - ai_buildability (20%): completamente AI-driven = 1.0
   - time_to_market_score (15%): 1 settimana = 1.0, 6+ mesi = 0.0
   - mvp_cost_score (15%): sotto 50 EUR/mese = 1.0, sopra 300 = 0.0
   - recurring_revenue (10%): subscription forte = 1.0, one-shot = 0.0
   - market_access (10%): canale diretto/organico = 1.0, enterprise sales = 0.0
   - scalability (5%): auto-scaling = 1.0, richiede team = 0.0

Sii REALISTICO.

Rispondi SOLO con JSON:
{"mvp_cost":{"dev_hours":0,"dev_cost_eur":0,"api_monthly_eur":0,"hosting_monthly_eur":0,"other_monthly_eur":0,"total_mvp_cost_eur":0,"total_monthly_cost_eur":0},"timeline":{"weeks_to_mvp":0,"weeks_to_revenue":0},"revenue":{"pessimistic_monthly_eur":0,"realistic_monthly_eur":0,"optimistic_monthly_eur":0,"pricing_model":"","price_point_eur":0},"margin":{"monthly_margin_pessimistic":0,"monthly_margin_realistic":0,"monthly_margin_optimistic":0,"margin_percentage_realistic":0,"breakeven_months":0},"competition":{"competition_score":0.0,"direct_competitors":0,"indirect_competitors":0,"our_advantage":""},"recommendation":{"decision":"GO","confidence":0.0,"reasoning":"","conditions":"","biggest_risk":"","biggest_opportunity":""},"bos_feasibility":{"margin_potential":0.0,"ai_buildability":0.0,"time_to_market_score":0.0,"mvp_cost_score":0.0,"recurring_revenue":0.0,"market_access":0.0,"scalability":0.0}}
SOLO JSON."""


def feasibility_calculate_score(analysis):
    if not analysis:
        return 0.0
    scores = []
    margin = analysis.get("margin", {})
    margin_pct = float(margin.get("margin_percentage_realistic", 0))
    scores.append(min(1.0, max(0.0, margin_pct / 80)) * 0.30)

    timeline = analysis.get("timeline", {})
    weeks_to_rev = float(timeline.get("weeks_to_revenue", 52))
    scores.append(max(0.0, 1.0 - (weeks_to_rev / 24)) * 0.20)

    costs = analysis.get("mvp_cost", {})
    monthly_cost = float(costs.get("total_monthly_cost_eur", 1000))
    scores.append(max(0.0, 1.0 - (monthly_cost / 200)) * 0.20)

    competition = analysis.get("competition", {})
    comp = float(competition.get("competition_score", 0.5))
    scores.append((1.0 - comp) * 0.15)

    rec = analysis.get("recommendation", {})
    confidence = float(rec.get("confidence", 0.5))
    decision = rec.get("decision", "NO_GO")
    decision_mult = 1.0 if decision == "GO" else 0.7 if decision == "CONDITIONAL_GO" else 0.3
    scores.append((confidence * decision_mult) * 0.15)

    return round(min(1.0, max(0.0, sum(scores))), 4)


def run_feasibility_engine(solution_id=None, notify=True):
    logger.info("Feasibility Engine v1.1 starting...")

    try:
        query = supabase.table("solutions").select(
            "*, problems(title, description, sector, who_is_affected, why_it_matters, weighted_score)"
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
            f"Settore: {sector} / {sol.get('sub_sector', '')}\n\n"
            f"PROBLEMA: {problem.get('title', '')}\n"
            f"Descrizione: {problem.get('description', '')}\n"
            f"Chi e' colpito: {problem.get('who_is_affected', '')}\n"
            f"Score problema: {problem.get('weighted_score', '')}\n\n"
            f"SCORE SA: Feasibility={scores.get('feasibility_score', 'N/A')} Impact={scores.get('impact_score', 'N/A')} Complexity={scores.get('complexity', 'N/A')}\n"
        )
        if competition_research:
            context += f"\nRICERCA COMPETITIVA:\n{competition_research}\n"

        start = time.time()
        try:
            response = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                system=FEASIBILITY_ENGINE_PROMPT,
                messages=[{"role": "user", "content": f"Valuta. SOLO JSON:\n\n{context}"}]
            )
            duration = int((time.time() - start) * 1000)
            reply = response.content[0].text

            log_to_supabase("feasibility_engine", "analyze_feasibility", 2,
                f"Feasibility: {title[:100]}", reply[:500],
                "claude-haiku-4-5-20251001",
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
    "margin_potential": 0.25, "ai_buildability": 0.20, "time_to_market_score": 0.15,
    "mvp_cost_score": 0.15, "recurring_revenue": 0.10, "market_access": 0.10,
    "scalability": 0.05,
}

BOS_PARAM_NAMES = {
    "problem_quality": "Qualita problema",
    "sq_uniqueness": "Unicita", "sq_moat_potential": "Difendibilita",
    "sq_value_multiplier": "Valore/prezzo", "sq_simplicity": "Semplicita",
    "sq_revenue_clarity": "Chiarezza revenue", "sq_ai_nativeness": "AI-nativa",
    "fe_margin_potential": "Potenziale margine", "fe_ai_buildability": "Costruibile con AI",
    "fe_time_to_market_score": "Velocita lancio", "fe_mvp_cost_score": "Costo MVP",
    "fe_recurring_revenue": "Revenue ricorrente", "fe_market_access": "Accesso mercato",
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

    bos = round(problem_quality * 0.30 + solution_quality * 0.30 + feasibility_score * 0.40, 4)
    bos = min(1.0, max(0.0, bos))

    if bos >= 0.75:
        verdict = "AUTO-GO"
    elif bos >= 0.55:
        verdict = "REVIEW"
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
# PIPELINE AUTOMATICA
# ============================================================

def run_auto_pipeline(saved_problem_ids):
    if not saved_problem_ids:
        return

    logger.info(f"[PIPELINE] Avvio per {len(saved_problem_ids)} problemi")
    log_to_supabase("pipeline", "auto_pipeline_start", 0,
        f"{len(saved_problem_ids)} problemi", None, "none")

    pipeline_start = time.time()
    total_solutions = 0
    new_solution_ids = []
    problems_processed = []

    for pid in saved_problem_ids:
        try:
            prob_result = supabase.table("problems").select("*").eq("id", pid).execute()
            if not prob_result.data:
                continue
            problem = prob_result.data[0]

            dossier = research_problem(problem)
            if not dossier:
                dossier = {"existing_solutions": [], "market_gaps": ["nessun dato"],
                    "failed_attempts": [], "expert_insights": [],
                    "market_size_estimate": "sconosciuto", "key_finding": "ricerca non disponibile"}

            solutions_data = generate_solutions_unconstrained(problem, dossier)
            if not solutions_data or not solutions_data.get("solutions"):
                logger.warning(f"[PIPELINE] SA generazione fallita per '{problem['title'][:60]}'. "
                    f"Risposta: {str(solutions_data)[:200] if solutions_data else 'None'}")
                continue

            ranking_rationale = solutions_data.get("ranking_rationale", "")
            feasibility_data = assess_feasibility(problem, solutions_data)
            if not feasibility_data:
                feasibility_data = {"assessments": [], "best_feasible": "", "best_overall": ""}

            feas_map = {}
            for a in feasibility_data.get("assessments", []):
                feas_map[a.get("solution_title", "")] = a

            problem_solutions = 0
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
                    total_solutions += 1
                    problem_solutions += 1
                    new_solution_ids.append(sol_id)

            problems_processed.append({
                "title": problem["title"],
                "score": problem.get("weighted_score", 0),
                "solutions": problem_solutions,
            })

            time.sleep(2)
        except Exception as e:
            logger.error(f"[PIPELINE] SA error pid={pid}: {e}")

    # FASE 2: Feasibility Engine
    go_count = 0
    conditional_count = 0
    no_go_count = 0
    bos_results = []

    for sid in new_solution_ids:
        try:
            result = run_feasibility_engine(solution_id=sid, notify=False)
            if result:
                go_count += result.get("go", 0)
                conditional_count += result.get("conditional_go", 0)
                no_go_count += result.get("no_go", 0)
        except Exception as e:
            logger.error(f"[PIPELINE] FE error sid={sid}: {e}")

    # FASE 3: BOS per tutte
    for sid in new_solution_ids:
        bos_data = calculate_bos(sid)
        if bos_data:
            try:
                sol_data = supabase.table("solutions").select("title").eq("id", sid).execute()
                title = sol_data.data[0]["title"] if sol_data.data else "?"
            except:
                title = "?"
            bos_results.append({"title": title, "bos": bos_data})

    pipeline_duration = int(time.time() - pipeline_start)

    # RIEPILOGO — formato decimale
    msg = f"PIPELINE COMPLETATA ({pipeline_duration}s)\n\n"
    msg += f"Problemi analizzati: {len(problems_processed)}\n"
    for pp in problems_processed:
        msg += f"  Score: {pp['score']:.2f} | {pp['title']} -> {pp['solutions']} sol.\n"
    msg += f"\nSoluzioni: {total_solutions}\n"
    msg += f"Feasibility: {go_count} GO, {conditional_count} conditional, {no_go_count} no-go\n"

    if bos_results:
        bos_sorted = sorted(bos_results, key=lambda x: x["bos"]["bos_score"], reverse=True)
        msg += "\nBOS RANKING:\n"
        for br in bos_sorted[:5]:
            b = br["bos"]
            msg += f"  Score: {b['bos_score']:.2f} | {b['verdict']} | {br['title']}\n"

    msg += "\nChiedimi i dettagli sul Command Center!"
    notify_telegram(msg)

    for br in bos_results:
        if br["bos"]["verdict"] in ("AUTO-GO", "REVIEW"):
            card = format_bos_card(br["title"], br["bos"])
            notify_telegram(card)

    log_to_supabase("pipeline", "auto_pipeline_complete", 0,
        f"{len(saved_problem_ids)} problemi -> {total_solutions} soluzioni",
        f"GO:{go_count} COND:{conditional_count} NO:{no_go_count}",
        "none", 0, 0, 0, pipeline_duration * 1000)


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
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=KNOWLEDGE_PROMPT,
            messages=[{"role": "user", "content": f"Analizza SOLO JSON:\n\n{json.dumps(simple_logs, default=str)}"}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("knowledge_keeper", "analyze_logs", 5,
            f"Analizzati {len(logs)} log", reply[:500],
            "claude-haiku-4-5-20251001",
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
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=SCOUT_PROMPT,
            messages=[{"role": "user", "content": f"Analizza SOLO JSON:\n\n{combined}"}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("capability_scout", "analyze_discoveries", 5,
            f"Analizzati {len(search_results)} topic", reply[:500],
            "claude-haiku-4-5-20251001",
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
    try:
        result = supabase.table("org_config").select("value").eq("key", "usd_to_eur_rate").execute()
        if result.data:
            return float(json.loads(result.data[0]["value"]))
    except:
        pass
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
# PARTE 8: DAILY REPORT ORE 20
# ============================================================

def generate_daily_report():
    """Report giornaliero completo: scan, problemi, soluzioni, BOS, costi, lezioni."""
    logger.info("Generating daily report...")
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    day_start = f"{today}T00:00:00+00:00"

    report_lines = [f"REPORT GIORNALIERO {today}", ""]

    # Scan fatti oggi
    try:
        scans = supabase.table("agent_logs").select("id", count="exact").eq("agent_id", "world_scanner").gte("created_at", day_start).execute()
        scan_count = scans.count or 0
    except:
        scan_count = 0

    # Problemi trovati oggi
    try:
        problems = supabase.table("problems").select("id, title, weighted_score, sector", count="exact").gte("created_at", day_start).execute()
        problem_count = problems.count or 0
        problem_data = problems.data or []
    except:
        problem_count = 0
        problem_data = []

    # Soluzioni generate oggi
    try:
        solutions = supabase.table("solutions").select("id, title, bos_score, bos_details", count="exact").gte("created_at", day_start).execute()
        solution_count = solutions.count or 0
        solution_data = solutions.data or []
    except:
        solution_count = 0
        solution_data = []

    # BOS calcolati oggi
    bos_count = sum(1 for s in solution_data if s.get("bos_score") is not None)

    # Costi oggi
    daily_costs = finance_get_daily_costs(today)
    usd_to_eur = finance_get_usd_to_eur()

    report_lines.append(f"Scan: {scan_count}")
    report_lines.append(f"Problemi trovati: {problem_count}")
    report_lines.append(f"Soluzioni generate: {solution_count}")
    report_lines.append(f"BOS calcolati: {bos_count}")

    if daily_costs:
        cost_eur = round(daily_costs["total_cost_usd"] * usd_to_eur, 4)
        report_lines.append(f"Costi oggi: ${daily_costs['total_cost_usd']:.4f} ({cost_eur:.4f} EUR)")
    report_lines.append("")

    # Top 3 problemi
    if problem_data:
        top_problems = sorted(problem_data, key=lambda x: float(x.get("weighted_score", 0) or 0), reverse=True)[:3]
        report_lines.append("TOP PROBLEMI:")
        for p in top_problems:
            report_lines.append(f"  Score: {float(p.get('weighted_score', 0)):.2f} | {p.get('sector', '?')} | {p['title']}")
        report_lines.append("")

    # Top 3 soluzioni per BOS
    if solution_data:
        top_solutions = sorted([s for s in solution_data if s.get("bos_score")],
            key=lambda x: float(x.get("bos_score", 0) or 0), reverse=True)[:3]
        if top_solutions:
            report_lines.append("TOP SOLUZIONI (BOS):")
            for s in top_solutions:
                bos = float(s.get("bos_score", 0))
                details = s.get("bos_details", {})
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except:
                        details = {}
                verdict = details.get("verdict", "?")
                report_lines.append(f"  Score: {bos:.2f} | {verdict} | {s['title']}")
            report_lines.append("")

    # Lezioni apprese oggi
    try:
        lessons = supabase.table("org_knowledge").select("title").gte("created_at", day_start).limit(3).execute()
        if lessons.data:
            report_lines.append("LEZIONI APPRESE:")
            for l in lessons.data:
                report_lines.append(f"  - {l['title'][:60]}")
            report_lines.append("")
    except:
        pass

    # Azioni pianificate domani
    report_lines.append("DOMANI:")
    strategy, idx = get_scan_strategy()
    report_lines.append(f"  Prossimo scan: strategia {strategy}")
    try:
        pending_events = supabase.table("agent_events").select("event_type", count="exact").eq("status", "pending").execute()
        pending_count = pending_events.count or 0
        if pending_count > 0:
            report_lines.append(f"  Eventi pending: {pending_count}")
    except:
        pass

    report = "\n".join(report_lines)
    notify_telegram(report)

    log_to_supabase("daily_report", "generate", 0,
        f"Report {today}", report[:500], "none")

    return {"status": "completed", "date": today}


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
                # Trigger solution generation per problemi con score >= 0.65
                problem_ids = payload.get("problem_ids", [])
                for pid in problem_ids:
                    try:
                        prob = supabase.table("problems").select("weighted_score").eq("id", pid).execute()
                        if prob.data and float(prob.data[0].get("weighted_score", 0) or 0) >= 0.55:
                            emit_event("event_processor", "problem_ready", "solution_architect",
                                {"problem_id": str(pid)})
                    except:
                        pass
                mark_event_done(event["id"])

            elif event_type == "problems_found":
                # Notify Mirco con top problemi
                problem_ids = payload.get("problem_ids", [])
                count = payload.get("count", len(problem_ids))
                notify_telegram(f"Trovati {count} nuovi problemi con score alto. Controllali sul bot!")
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

                if verdict == "AUTO-GO":
                    emit_event("event_processor", "auto_go", "project_builder",
                        {"solution_id": solution_id, "bos": bos_score}, "high")
                elif verdict == "REVIEW":
                    emit_event("event_processor", "review_request", "command_center",
                        {"solution_id": solution_id, "bos": bos_score}, "high")
                # ARCHIVE: no action needed

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
                sol_id = payload.get("solution_id")
                bos = payload.get("bos", 0)
                if sol_id:
                    try:
                        sol = supabase.table("solutions").select("title").eq("id", sol_id).execute()
                        title = sol.data[0]["title"] if sol.data else "?"
                        notify_telegram(f"REVIEW RICHIESTA\n\nSoluzione: {title}\nBOS: {bos:.2f}\n\nVuoi procedere? Rispondi sul Command Center.")
                    except:
                        pass
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
# IDEA RECYCLER
# ============================================================

def run_idea_recycler():
    """Rivaluta problemi e soluzioni archiviate."""
    logger.info("Idea Recycler starting...")

    try:
        archived = supabase.table("problems").select("id, title, sector, weighted_score, created_at") \
            .eq("status", "archived").order("weighted_score", desc=True).limit(10).execute()
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
    result = generate_daily_report()
    return web.json_response(result)

async def run_recycle_endpoint(request):
    result = run_idea_recycler()
    return web.json_response(result)

async def run_source_refresh_endpoint(request):
    result = run_source_refresh()
    return web.json_response(result)

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
    app.router.add_post("/cycle/scan", run_scanner_endpoint)
    app.router.add_post("/cycle/knowledge", run_knowledge_endpoint)
    app.router.add_post("/cycle/capability", run_scout_endpoint)
    app.router.add_post("/cycle/sources", run_source_refresh_endpoint)
    app.router.add_post("/cycle/recycle", run_recycle_endpoint)
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
