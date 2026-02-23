"""
brAIn Agents Runner v1.2
Cloud Run service — agenti schedulati + scan on-demand + proattivita.
Score normalization, query diversificate, scan custom via chat.
"""

import os
import json
import time
import hashlib
import logging
import asyncio
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


def notify_telegram(message):
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
# WORLD SCANNER v2.2
# ============================================================

SCANNER_WEIGHTS = {
    "market_size": 0.20, "willingness_to_pay": 0.20, "urgency": 0.15,
    "competition_gap": 0.15, "ai_solvability": 0.15, "time_to_market": 0.10,
    "recurring_potential": 0.05,
}

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
- 0.0-0.2 = molto basso (es. market_size: problema di nicchia <10K persone; time_to_market: servono anni)
- 0.2-0.4 = basso (es. willingness_to_pay: pagherebbero poco; competition_gap: esistono gia buone soluzioni)
- 0.4-0.6 = medio (es. urgency: fastidioso ma non critico; ai_solvability: AI aiuta ma non risolve tutto)
- 0.6-0.8 = alto (es. market_size: milioni di persone; recurring_potential: settimanale)
- 0.8-1.0 = molto alto (es. urgency: emergenza; competition_gap: zero soluzioni; time_to_market: 1 settimana)

Ogni problema DEVE avere almeno 2 score sotto 0.4 e almeno 1 score sotto 0.3. Se un problema sembra perfetto su tutto, stai sbagliando — cerca i punti deboli reali.

Se identifichi 3 problemi, il loro score medio pesato NON deve essere lo stesso — differenzia: uno forte, uno medio, uno debole.

2. DATI QUALITATIVI:
   - who_is_affected: chi soffre? Sii specifico (eta, ruolo, contesto)
   - real_world_example: storia concreta di qualcuno che vive questo problema
   - why_it_matters: perche ci tiene a risolverlo

3. CLASSIFICAZIONE:
   - sector: uno tra food, health, finance, education, legal, ecommerce, hr, real_estate, sustainability, cybersecurity, entertainment, logistics
   - geographic_scope: global, continental, national, regional
   - top_markets: lista 3-5 codici paese ISO

NON riproporre problemi generici. Cerca problemi specifici con gap reale.

REGOLA SULLA DIVERSITA DEI SETTORI: i problemi che identifichi devono riguardare settori DIVERSI. Se la query parla di food, trova problemi di food. Se parla di health, trova problemi di health. NON trasformare tutto in un problema di AI o tecnologia — cerca i problemi UMANI concreti del settore specifico.

Rispondi SOLO con JSON:
{"problems":[{"title":"titolo","description":"descrizione","who_is_affected":"chi","real_world_example":"storia","why_it_matters":"perche","sector":"food","geographic_scope":"global","top_markets":["US","UK"],"market_size":0.8,"willingness_to_pay":0.3,"urgency":0.6,"competition_gap":0.8,"ai_solvability":0.9,"time_to_market":0.4,"recurring_potential":0.2,"source_name":"fonte","source_url":"url"}],"new_sources":[{"name":"nome","url":"url","category":"tipo","sectors":["settore"]}]}
SOLO JSON."""


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
    """Forza distribuzione ampia degli score in un batch."""
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
    """Query diversificate per settore — zero bias tech/AI"""
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
    """Core scan logic — usato sia per scan standard che custom"""
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

    # Ricerca
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
    high_score_problems = []

    # Analisi batch
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
                system=SCANNER_ANALYSIS_PROMPT,
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
                        supabase.table("problems").insert({
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

                        if weighted >= 0.85:
                            high_score_problems.append({"title": title, "score": weighted, "sector": sector})
                            emit_event("world_scanner", "high_score_problem", "solution_architect",
                                {"title": title, "score": weighted, "sector": sector}, "high")

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

    # Notifiche proattive
    if high_score_problems:
        msg = f"Ho trovato {len(high_score_problems)} problemi con score alto:\n\n"
        for p in high_score_problems:
            msg += f"- [{p['score']:.2f}] {p['title']} ({p['sector']})\n"
        msg += "\nVuoi che te li approfondisca?"
        notify_telegram(msg)

    if total_saved >= 3:
        emit_event("world_scanner", "batch_scan_complete", "knowledge_keeper",
            {"problems_saved": total_saved, "avg_score": sum(all_scores) / len(all_scores) if all_scores else 0}, "normal")

    return {"status": "completed", "saved": total_saved, "high_score": len(high_score_problems)}


def run_world_scanner():
    logger.info("World Scanner v2.2 starting (standard scan)...")
    try:
        sources = supabase.table("scan_sources").select("*").eq("status", "active").order("relevance_score", desc=True).limit(10).execute()
        sources = sources.data or []
    except:
        sources = []

    queries = get_standard_queries(sources)
    result = run_scan(queries)
    logger.info(f"World Scanner completato: {result}")
    return result


def run_custom_scan(topic):
    """Scan mirato su un argomento specifico richiesto da Mirco"""
    logger.info(f"World Scanner custom scan: {topic}")

    queries = [
        ("custom", f"{topic} biggest problems pain points"),
        ("custom", f"{topic} unsolved needs market gap"),
        ("custom", f"{topic} consumers complaints frustrations"),
    ]

    result = run_scan(queries)

    if result.get("saved", 0) > 0:
        notify_telegram(f"Scan su '{topic}' completato: {result['saved']} problemi trovati. Chiedimi di vederli!")
    else:
        notify_telegram(f"Scan su '{topic}' completato ma non ho trovato problemi nuovi. Vuoi che provi con un angolo diverso?")

    logger.info(f"Custom scan completato: {result}")
    return result


# ============================================================
# SOLUTION ARCHITECT v2.0 — 3 fasi: Ricerca, Generazione, Fattibilita
# ============================================================

# FASE 1: Prompt per ricerca competitiva
RESEARCH_PROMPT = """Sei un analista di mercato esperto. Dati i risultati di ricerca sul web, crea un DOSSIER COMPETITIVO per il problema dato.

Il dossier deve includere:
1. SOLUZIONI ESISTENTI: chi gia' risolve (anche parzialmente) questo problema? Nome, cosa fa, prezzo, punti deboli.
2. GAP DI MERCATO: cosa manca nelle soluzioni attuali? Dove i clienti sono insoddisfatti?
3. TENTATIVI FALLITI: qualcuno ha provato e fallito? Perche'?
4. INSIGHT ESPERTI: cosa dicono ricercatori, analisti, utenti su Reddit/forum?
5. DIMENSIONE OPPORTUNITA: quanto vale questo mercato? Quanto si spende oggi?

Rispondi SOLO con JSON:
{"existing_solutions":[{"name":"nome","what_it_does":"cosa fa","price":"costo","weaknesses":"punti deboli","market_share":"stima"}],"market_gaps":["gap1","gap2"],"failed_attempts":[{"who":"chi","why_failed":"perche"}],"expert_insights":["insight1","insight2"],"market_size_estimate":"stima valore mercato","key_finding":"la scoperta piu' importante in una frase"}
SOLO JSON."""

# FASE 2: Prompt per generazione soluzioni SENZA vincoli tech
GENERATION_PROMPT = """Sei un innovation strategist di livello mondiale. Combini il meglio di:
- Opportunity Solution Tree (Teresa Torres): dal problema alle opportunita' ai prodotti
- Blue Ocean Strategy: cerchi spazi vuoti dove nessun competitor opera
- Jobs-to-be-Done: che "lavoro" il cliente sta cercando di fare
- Lean Canvas: proposta valore, segmento, canale, revenue, costi

Hai un DOSSIER COMPETITIVO e un PROBLEMA. Genera 3 soluzioni ordinate per potenziale.

REGOLE CRITICHE:
- NON proporre soluzioni che gia' esistono e funzionano bene (le hai nel dossier)
- Cerca gli SPAZI VUOTI: dove nessuno opera, o dove tutti fanno male
- Pensa a soluzioni che creano un vantaggio difendibile (network effect, dati proprietari, lock-in naturale)
- NON limitarti alla tecnologia: una soluzione puo' essere un servizio, un marketplace, un protocollo, una community
- Sii SPECIFICO: non "piattaforma AI che..." ma "servizio che fa X per Y tramite Z"

Per ogni soluzione fornisci:
- title: nome chiaro
- description: cosa fa in 2 frasi
- value_proposition: perche' il cliente paga — in una frase
- target_segment: chi esattamente (specifico)
- job_to_be_done: quale "lavoro" risolve per il cliente
- revenue_model: come genera soldi (subscription, transazione, freemium, ecc.)
- monthly_revenue_potential: stima revenue mensile a regime (12 mesi)
- monthly_burn_rate: stima costi mensili per operare
- competitive_moat: perche' e' difficile da copiare
- novelty_score: 0.0-1.0 quanto e' nuova vs soluzioni esistenti (0=esiste gia', 1=mai vista)
- opportunity_score: 0.0-1.0 dimensione opportunita' di mercato
- defensibility_score: 0.0-1.0 quanto e' difendibile nel tempo

Rispondi SOLO con JSON:
{"solutions":[{"title":"","description":"","value_proposition":"","target_segment":"","job_to_be_done":"","revenue_model":"","monthly_revenue_potential":"","monthly_burn_rate":"","competitive_moat":"","novelty_score":0.7,"opportunity_score":0.8,"defensibility_score":0.6}],"ranking_rationale":"perche' hai messo la prima in cima"}
SOLO JSON."""

# FASE 3: Prompt per valutazione fattibilita
FEASIBILITY_PROMPT = """Sei un CTO pragmatico. Valuta la fattibilita' di ogni soluzione dati questi VINCOLI:

VINCOLI ATTUALI:
- 1 persona, 20h/settimana, competenza tecnica minima (no-code/low-code preferito)
- Budget: 1000 euro/mese totale, primo progetto sotto 200 euro/mese
- Stack disponibile: Claude API, Supabase (PostgreSQL + pgvector), Python, Google Cloud Run, Telegram Bot
- Obiettivo: revenue entro 3 mesi, marginalita' alta priorita' assoluta
- Puo' usare qualsiasi API/servizio esterno purche' nel budget

Per ogni soluzione valuta:
- feasibility_score: 0.0-1.0 (possiamo farlo con i vincoli attuali?)
- complexity: low/medium/high
- time_to_mvp: tempo per un MVP funzionante
- cost_estimate: costo mensile stimato
- tech_stack_fit: quanto il nostro stack copre il bisogno (0.0-1.0)
- biggest_risk: il rischio principale
- recommended_mvp: cosa costruire come primo test (specifico, concreto)
- nocode_compatible: true/false

Rispondi SOLO con JSON:
{"assessments":[{"solution_title":"","feasibility_score":0.7,"complexity":"medium","time_to_mvp":"3 settimane","cost_estimate":"80 euro/mese","tech_stack_fit":0.8,"biggest_risk":"rischio","recommended_mvp":"cosa costruire","nocode_compatible":true}],"best_feasible":"quale soluzione e' la piu' fattibile e perche'","best_overall":"quale soluzione e' la migliore in assoluto ignorando i vincoli"}
SOLO JSON."""


def research_problem(problem):
    """FASE 1: Ricerca competitiva via Perplexity + analisi Claude"""
    logger.info(f"[SA] Fase 1: Ricerca per '{problem['title'][:60]}'")

    title = problem["title"]
    sector = problem.get("sector", "")
    description = problem.get("description", "")

    # 4 ricerche mirate su Perplexity
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
        logger.warning("[SA] Nessun risultato di ricerca")
        return None

    combined_research = "\n\n---\n\n".join(search_results)

    problem_context = (
        f"PROBLEMA: {title}\n"
        f"Descrizione: {description}\n"
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
    """FASE 2: Generazione soluzioni senza vincoli tech"""
    logger.info(f"[SA] Fase 2: Generazione per '{problem['title'][:60]}'")

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
            model="claude-sonnet-4-5-20250514",
            max_tokens=4000,
            system=GENERATION_PROMPT,
            messages=[{"role": "user", "content": f"{problem_context}\n\nDOSSIER COMPETITIVO:\n{dossier_text}\n\nGenera 3 soluzioni. SOLO JSON."}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("solution_architect", "generate_unconstrained", 2,
            f"Soluzioni per: {problem['title'][:100]}", reply[:500],
            "claude-sonnet-4-5-20250514",
            response.usage.input_tokens, response.usage.output_tokens,
            (response.usage.input_tokens * 3.0 + response.usage.output_tokens * 15.0) / 1_000_000,
            duration)

        return extract_json(reply)

    except Exception as e:
        logger.error(f"[SA GENERATE ERROR] {e}")
        return None


def assess_feasibility(problem, solutions_data):
    """FASE 3: Valutazione fattibilita con vincoli"""
    logger.info(f"[SA] Fase 3: Fattibilita per '{problem['title'][:60]}'")

    solutions_text = json.dumps(solutions_data.get("solutions", []), indent=2, ensure_ascii=False)

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system=FEASIBILITY_PROMPT,
            messages=[{"role": "user", "content": f"PROBLEMA: {problem['title']}\n\nSOLUZIONI DA VALUTARE:\n{solutions_text}\n\nValuta fattibilita. SOLO JSON."}]
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
    """Salva soluzione con tutti i dati delle 3 fasi"""
    try:
        complexity = str(assessment.get("complexity", "medium")).lower().strip()
        if "low" in complexity:
            complexity = "low"
        elif "high" in complexity:
            complexity = "high"
        else:
            complexity = "medium"

        # Salva soluzione
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

        # Score combinato: media di novelty, opportunity, defensibility per impact
        novelty = float(sol.get("novelty_score", 0.5))
        opportunity = float(sol.get("opportunity_score", 0.5))
        defensibility = float(sol.get("defensibility_score", 0.5))
        feasibility = float(assessment.get("feasibility_score", 0.5))
        tech_fit = float(assessment.get("tech_stack_fit", 0.5))

        # Impact score = media dei 3 score strategici
        impact = round((novelty + opportunity + defensibility) / 3, 4)
        # Overall = media di impact e feasibility
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

    # Controlla problemi che hanno gia soluzioni
    try:
        existing = supabase.table("solutions").select("problem_id").execute()
        existing_ids = {s["problem_id"] for s in (existing.data or [])}
    except:
        existing_ids = set()

    problems = [p for p in problems if p["id"] not in existing_ids]
    if not problems:
        return {"status": "all_solved", "saved": 0}

    total_saved = 0
    for problem in problems:
        # FASE 1: Ricerca competitiva
        dossier = research_problem(problem)
        if not dossier:
            dossier = {"existing_solutions": [], "market_gaps": ["nessun dato"], "failed_attempts": [], "expert_insights": [], "market_size_estimate": "sconosciuto", "key_finding": "ricerca non disponibile"}

        # FASE 2: Generazione soluzioni senza vincoli (usa Sonnet per qualita')
        solutions_data = generate_solutions_unconstrained(problem, dossier)
        if not solutions_data or not solutions_data.get("solutions"):
            logger.warning(f"[SA] Nessuna soluzione generata per {problem['title'][:60]}")
            continue

        ranking_rationale = solutions_data.get("ranking_rationale", "")

        # FASE 3: Valutazione fattibilita
        feasibility_data = assess_feasibility(problem, solutions_data)
        if not feasibility_data:
            feasibility_data = {"assessments": [], "best_feasible": "", "best_overall": ""}

        # Mappa fattibilita per titolo
        feas_map = {}
        for a in feasibility_data.get("assessments", []):
            feas_map[a.get("solution_title", "")] = a

        # Salva ogni soluzione
        best_score = 0
        best_title = ""
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
                if overall > best_score:
                    best_score = overall
                    best_title = title

        # Notifica Mirco con risultato
        if total_saved > 0:
            best_feasible = feasibility_data.get("best_feasible", "")
            best_overall = feasibility_data.get("best_overall", "")
            key_finding = dossier.get("key_finding", "")

            msg = f"Ho analizzato '{problem['title']}' in 3 fasi:\n\n"
            msg += f"Ricerca: {key_finding}\n\n"
            msg += f"Miglior soluzione in assoluto: {best_overall}\n"
            msg += f"Piu' fattibile per noi: {best_feasible}\n\n"
            msg += f"{total_saved} soluzioni salvate. Chiedimi i dettagli!"
            notify_telegram(msg)

        time.sleep(2)

    logger.info(f"Solution Architect v2.0 completato: {total_saved} soluzioni")
    return {"status": "completed", "saved": total_saved}


# ============================================================
# KNOWLEDGE KEEPER v1.1
# ============================================================

KNOWLEDGE_PROMPT = """Sei il Knowledge Keeper di brAIn.
Analizza i log degli agenti e estrai lezioni apprese.

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
# EVENT PROCESSOR
# ============================================================

def process_events():
    events = get_pending_events()
    processed = 0

    for event in events:
        event_type = event.get("event_type", "")
        target = event.get("target_agent", "")
        payload = event.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)

        try:
            if event_type == "high_score_problem" and target == "solution_architect":
                mark_event_done(event["id"])

            elif event_type == "batch_scan_complete" and target == "knowledge_keeper":
                run_knowledge_keeper()
                mark_event_done(event["id"])

            elif event_type == "problem_approved":
                run_solution_architect(problem_id=payload.get("problem_id"))
                mark_event_done(event["id"])

            else:
                mark_event_done(event["id"])

            processed += 1

        except Exception as e:
            logger.error(f"[EVENT ERROR] {e}")
            mark_event_done(event["id"], "failed")

    return {"processed": processed}


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

async def run_events_endpoint(request):
    result = process_events()
    return web.json_response(result)

async def run_all_endpoint(request):
    results = {}
    results["scanner"] = run_world_scanner()
    results["architect"] = run_solution_architect()
    results["knowledge"] = run_knowledge_keeper()
    results["scout"] = run_capability_scout()
    results["events"] = process_events()
    return web.json_response(results)


async def main():
    logger.info("brAIn Agents Runner v1.2 starting...")

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/scanner", run_scanner_endpoint)
    app.router.add_post("/scanner/custom", run_custom_scan_endpoint)
    app.router.add_post("/architect", run_architect_endpoint)
    app.router.add_post("/knowledge", run_knowledge_endpoint)
    app.router.add_post("/scout", run_scout_endpoint)
    app.router.add_post("/events", run_events_endpoint)
    app.router.add_post("/all", run_all_endpoint)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Agents Runner on port {PORT}")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
