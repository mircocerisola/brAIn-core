"""
brAIn World Scanner Agent v2.2
Layer 1 — Scansiona il web per identificare problemi globali risolvibili.
Soglia minima score: 0.65 per salvare problemi.
"""

import os
import json
import time
import hashlib
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic
from supabase import create_client
import requests

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

WEIGHTS = {
    "market_size": 0.20,
    "willingness_to_pay": 0.20,
    "urgency": 0.15,
    "competition_gap": 0.15,
    "ai_solvability": 0.15,
    "time_to_market": 0.10,
    "recurring_potential": 0.05,
}

MIN_SCORE_THRESHOLD = 0.65

SECTORS = [
    "food", "health", "finance", "education", "legal",
    "ecommerce", "hr", "real_estate", "sustainability",
    "cybersecurity", "entertainment", "logistics",
]

ANALYSIS_PROMPT = """Sei il World Scanner di brAIn, un'organizzazione AI-native.
Analizzi risultati di ricerca per identificare problemi reali e concreti che colpiscono persone o organizzazioni.

Per ogni problema identificato (massimo 3), fornisci:

1. DATI QUANTITATIVI - 7 score da 0.0 a 1.0:
   - market_size: quante persone/organizzazioni colpisce
   - willingness_to_pay: quanto pagherebbero per una soluzione
   - urgency: quanto e' urgente risolverlo
   - competition_gap: quanto sono deboli le soluzioni attuali (1.0 = nessuna soluzione, 0.0 = gia risolto)
   - ai_solvability: quanto si puo risolvere con AI e automazione
   - time_to_market: quanto veloce si puo lanciare (1.0 = 1 settimana, 0.0 = anni)
   - recurring_potential: problema ricorrente = revenue ricorrente (1.0 = quotidiano, 0.0 = una tantum)

2. DATI QUALITATIVI - scrivi come se stessi raccontando a un amico:
   - who_is_affected: chi soffre di questo problema? Sii specifico (eta, ruolo, contesto)
   - real_world_example: racconta una storia concreta, un esempio reale di qualcuno che vive questo problema
   - why_it_matters: perche questa persona o organizzazione ci tiene davvero a risolverlo

3. CLASSIFICAZIONE:
   - sector: uno tra food, health, finance, education, legal, ecommerce, hr, real_estate, sustainability, cybersecurity, entertainment, logistics
   - geographic_scope: global, continental, national, regional
   - top_markets: lista 3-5 codici paese ISO (es. ["US","UK","DE","IT"])

4. FONTI:
   - source_name: da quale fonte/sito viene questa informazione
   - source_url: URL approssimativo

NON riproporre problemi generici o ovvi. Cerca problemi specifici, concreti, dove vedi un gap reale.

Rispondi SOLO con JSON valido:
{"problems":[{"title":"titolo breve e chiaro","description":"descrizione 2-3 frasi","who_is_affected":"chi soffre","real_world_example":"storia concreta","why_it_matters":"perche conta","sector":"food","geographic_scope":"global","top_markets":["US","UK","DE"],"market_size":0.8,"willingness_to_pay":0.7,"urgency":0.6,"competition_gap":0.8,"ai_solvability":0.9,"time_to_market":0.7,"recurring_potential":0.6,"source_name":"nome fonte","source_url":"url"}],"new_sources":[{"name":"nome","url":"url","category":"tipo","sectors":["settore"]}]}
SOLO JSON."""


def get_top_sources(limit=10):
    try:
        result = supabase.table("scan_sources") \
            .select("*") \
            .eq("status", "active") \
            .order("relevance_score", desc=True) \
            .limit(limit) \
            .execute()
        return result.data
    except Exception as e:
        print(f"[ERROR] Recupero fonti: {e}")
        return []


def get_existing_fingerprints():
    try:
        result = supabase.table("problems") \
            .select("fingerprint") \
            .not_.is_("fingerprint", "null") \
            .execute()
        return {r["fingerprint"] for r in result.data}
    except:
        return set()


def make_fingerprint(title, sector):
    text = f"{title.lower().strip()}_{sector.lower().strip()}"
    return hashlib.md5(text.encode()).hexdigest()


def normalize_urgency(value):
    """Converte urgency numerica in testo per il database"""
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


def build_search_queries(sources):
    all_sectors = set()
    for s in sources:
        sectors = s.get("sectors", [])
        if isinstance(sectors, str):
            sectors = json.loads(sectors)
        all_sectors.update(sectors)

    queries = []
    sector_queries = {
        "food": "biggest unsolved problems food industry consumers restaurants 2026",
        "health": "healthcare problems patients underserved needs gaps 2026",
        "finance": "financial problems consumers small business underserved 2026",
        "education": "education problems students teachers technology gaps 2026",
        "legal": "legal problems small business consumers access to justice 2026",
        "ecommerce": "ecommerce problems sellers buyers fraud trust 2026",
        "hr": "human resources hiring workforce problems pain points 2026",
        "real_estate": "real estate problems renters buyers agents pain points 2026",
        "sustainability": "sustainability environmental problems consumers business gaps 2026",
        "cybersecurity": "cybersecurity problems small business individuals threats 2026",
        "entertainment": "entertainment content creation problems creators consumers 2026",
        "logistics": "logistics supply chain delivery problems underserved 2026",
    }

    for sector in all_sectors:
        if sector in sector_queries:
            queries.append((sector, sector_queries[sector]))

    queries.append(("cross", "most frustrating unsolved daily problems people face 2026"))
    queries.append(("cross", "emerging problems from AI automation affecting workers 2026"))
    queries.append(("cross", "biggest market gaps underserved customer needs 2026"))

    return queries


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
        else:
            print(f"   [ERROR] Perplexity {response.status_code}")
            return None
    except Exception as e:
        print(f"   [ERROR] Search: {e}")
        return None


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


def calculate_weighted_score(problem):
    score = 0
    for param, weight in WEIGHTS.items():
        value = problem.get(param, 0.5)
        if isinstance(value, (int, float)):
            score += value * weight
    return round(score, 4)


def analyze_batch(search_results):
    combined = "\n\n---\n\n".join([
        f"Settore: {sector}\nQuery: {query}\nRisultati: {result}"
        for sector, query, result in search_results
    ])

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=ANALYSIS_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Analizza questi risultati e identifica problemi concreti. SOLO JSON:\n\n{combined}"
            }]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        supabase.table("agent_logs").insert({
            "agent_id": "world_scanner",
            "action": "scan_v2",
            "layer": 1,
            "input_summary": f"Batch {len(search_results)} ricerche",
            "output_summary": reply[:500],
            "model_used": "claude-haiku-4-5-20251001",
            "tokens_input": response.usage.input_tokens,
            "tokens_output": response.usage.output_tokens,
            "cost_usd": (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            "duration_ms": duration,
            "status": "success",
        }).execute()

        return reply

    except Exception as e:
        print(f"   [ERROR] Analisi: {e}")
        return None


def save_problems(analysis_text, existing_fps, source_map):
    data = extract_json(analysis_text)
    if data is None:
        print("   [ERROR] JSON non valido")
        return 0, []

    saved = 0
    saved_scores = []

    for prob in data.get("problems", []):
        title = prob.get("title", "")
        sector = prob.get("sector", "general")
        if sector not in SECTORS:
            sector = "ecommerce"

        fp = make_fingerprint(title, sector)
        if fp in existing_fps:
            print(f"   [SKIP] Duplicato: {title}")
            continue

        weighted = calculate_weighted_score(prob)

        if weighted < MIN_SCORE_THRESHOLD:
            print(f"   [SKIP] Score basso ({weighted:.3f}): {title}")
            continue

        source_id = None
        source_name = prob.get("source_name", "")
        for sname, sid in source_map.items():
            if sname.lower() in source_name.lower() or source_name.lower() in sname.lower():
                source_id = sid
                break

        try:
            top_markets = prob.get("top_markets", [])
            if isinstance(top_markets, str):
                top_markets = json.loads(top_markets)

            urgency_text = normalize_urgency(prob.get("urgency", 0.5))

            supabase.table("problems").insert({
                "title": title,
                "description": prob.get("description", ""),
                "domain": sector,
                "sector": sector,
                "geographic_scope": prob.get("geographic_scope", "global"),
                "top_markets": json.dumps(top_markets),
                "market_size": float(prob.get("market_size", 0.5)),
                "willingness_to_pay": float(prob.get("willingness_to_pay", 0.5)),
                "urgency": urgency_text,
                "competition_gap": float(prob.get("competition_gap", 0.5)),
                "ai_solvability": float(prob.get("ai_solvability", 0.5)),
                "time_to_market": float(prob.get("time_to_market", 0.5)),
                "recurring_potential": float(prob.get("recurring_potential", 0.5)),
                "weighted_score": weighted,
                "score": weighted,
                "who_is_affected": prob.get("who_is_affected", ""),
                "real_world_example": prob.get("real_world_example", ""),
                "why_it_matters": prob.get("why_it_matters", ""),
                "fingerprint": fp,
                "source_id": source_id,
                "status": "new",
                "created_by": "world_scanner_v2",
            }).execute()

            saved += 1
            saved_scores.append(weighted)
            existing_fps.add(fp)
            print(f"   [{weighted:.3f}] {title} ({sector}) - {urgency_text}")

        except Exception as e:
            if "idx_problems_fingerprint" in str(e):
                print(f"   [SKIP] Gia presente: {title}")
            else:
                print(f"   [ERROR] Salvataggio: {e}")

    new_sources = data.get("new_sources", [])
    for ns in new_sources:
        try:
            name = ns.get("name", "")
            if name:
                supabase.table("scan_sources").insert({
                    "name": name,
                    "url": ns.get("url", ""),
                    "category": ns.get("category", "other"),
                    "sectors": json.dumps(ns.get("sectors", [])),
                    "relevance_score": 0.4,
                    "status": "active",
                    "notes": "Scoperta automatica dal World Scanner",
                }).execute()
                print(f"   [NEW SOURCE] {name}")
        except:
            pass

    return saved, saved_scores


def update_source_stats(sources, saved_scores):
    if not saved_scores:
        return

    avg_score = sum(saved_scores) / len(saved_scores)

    for source in sources:
        try:
            old_found = source.get("problems_found", 0)
            old_avg = source.get("avg_problem_score", 0)
            new_found = old_found + len(saved_scores)

            if old_found > 0:
                new_avg = (old_avg * old_found + avg_score * len(saved_scores)) / new_found
            else:
                new_avg = avg_score

            old_relevance = source.get("relevance_score", 0.5)
            if avg_score > 0.6:
                new_relevance = min(1.0, old_relevance + 0.02)
            elif avg_score < 0.4:
                new_relevance = max(0.1, old_relevance - 0.02)
            else:
                new_relevance = old_relevance

            supabase.table("scan_sources") \
                .update({
                    "problems_found": new_found,
                    "avg_problem_score": round(new_avg, 4),
                    "relevance_score": round(new_relevance, 4),
                    "last_scanned": datetime.now(timezone.utc).isoformat(),
                }) \
                .eq("id", source["id"]) \
                .execute()

        except Exception as e:
            print(f"   [ERROR] Update source {source.get('name')}: {e}")


def run():
    print("World Scanner v2.1 avviato...")

    sources = get_top_sources(10)
    print(f"   {len(sources)} fonti caricate (ordinate per rilevanza)")
    for s in sources[:5]:
        print(f"   - [{s['relevance_score']}] {s['name']}")

    existing_fps = get_existing_fingerprints()
    print(f"   {len(existing_fps)} problemi esistenti (per deduplicazione)")

    source_map = {s["name"]: s["id"] for s in sources}

    queries = build_search_queries(sources)
    print(f"   {len(queries)} query da eseguire\n")

    search_results = []
    for sector, query in queries:
        print(f"   Cerco [{sector}]: {query[:60]}...")
        result = search_perplexity(query)
        if result:
            search_results.append((sector, query, result))
            print(f"   -> Trovato")
        else:
            print(f"   -> Nessun risultato")
        time.sleep(1)

    print(f"\n   {len(search_results)}/{len(queries)} ricerche completate")

    if not search_results:
        print("   Nessun risultato. Esco.")
        return

    total_saved = 0
    all_scores = []

    batch_size = 4
    for i in range(0, len(search_results), batch_size):
        batch = search_results[i:i + batch_size]
        print(f"\n   Analisi batch {i // batch_size + 1} ({len(batch)} risultati)...")

        analysis = analyze_batch(batch)
        if analysis:
            saved, scores = save_problems(analysis, existing_fps, source_map)
            total_saved += saved
            all_scores.extend(scores)
        else:
            print("   Analisi fallita per questo batch")

        time.sleep(1)

    if all_scores:
        update_source_stats(sources, all_scores)
        avg = sum(all_scores) / len(all_scores)
        print(f"\n   Score medio problemi trovati: {avg:.3f}")

    try:
        supabase.table("scan_logs").insert({
            "agent_id": "world_scanner_v2",
            "query": json.dumps([q for _, q, _ in search_results[:5]]),
            "sources_scanned": len(search_results),
            "results_found": total_saved,
            "duration_ms": 0,
            "status": "completed",
        }).execute()
    except:
        pass

    print(f"\n   Totale: {total_saved} nuovi problemi salvati")
    print("World Scanner v2.1 completato.")


if __name__ == "__main__":
    run()