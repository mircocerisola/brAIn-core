"""
brAIn World Scanner Agent v3.0
Layer 1 — Scansiona il web per identificare problemi globali risolvibili.
v3.0: specificità obbligatoria, nuovi campi demografici, scoring con penalità/bonus.
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

MIN_SCORE_THRESHOLD = 0.55  # Abbassato perché il nuovo algoritmo è più severo

SECTORS = [
    "food", "health", "finance", "education", "legal",
    "ecommerce", "hr", "real_estate", "sustainability",
    "cybersecurity", "entertainment", "logistics",
]

# Parole generiche che indicano un target non specifico
GENERIC_TARGET_TERMS = [
    "aziende", "companies", "persone", "people", "utenti", "users",
    "imprenditori", "entrepreneurs", "professionisti", "professionals",
    "individui", "individuals", "clienti", "customers", "lavoratori", "workers",
]

ANALYSIS_PROMPT = """Sei il World Scanner di brAIn, un'organizzazione AI-native che cerca problemi SPECIFICI e AZIONABILI.

REGOLA FONDAMENTALE: ogni problema deve riguardare un segmento PRECISO di persone in un contesto geografico PRECISO con prove CONCRETE.

ESEMPIO SBAGLIATO (troppo generico, rifiutato):
"Le PMI faticano con la gestione finanziaria"

ESEMPIO CORRETTO (specifico, azionabile):
"Gli elettricisti autonomi italiani tra 30-45 anni non hanno accesso a corsi di aggiornamento normativo certificati a meno di 500 EUR — perdendo commesse pubbliche per mancanza di certificazioni"

Per ogni problema identificato (massimo 3), fornisci TUTTI questi campi:

1. IDENTIFICAZIONE TARGET:
   - target_customer: segmento SPECIFICO — professione + fascia d'età + contesto (NON "aziende" o "persone" generici)
   - target_geography: paese/regione SPECIFICA + perché proprio lì (es. "Italia centro-nord, dove la burocrazia è particolarmente pesante")
   - problem_frequency: frequenza con cui il target incontra il problema (daily/weekly/monthly/quarterly)

2. DESCRIZIONE PROBLEMA:
   - current_workaround: come il target risolve OGGI il problema e perché è insufficiente
   - pain_intensity: intensità del dolore da 1 (fastidio) a 5 (blocca il business/la vita)
   - evidence: dato CONCRETO e verificabile — statistica con fonte, numero di persone colpite, dimensione mercato in EUR/USD

3. TIMING:
   - why_now: perché questo problema è rilevante ORA e non 3 anni fa (cambio normativo, tecnologia, comportamento)

4. DATI QUANTITATIVI — 7 score da 0.0 a 1.0:
   - market_size: dimensione mercato (0.1=nicchia <1M EUR, 0.5=medio 10-100M EUR, 1.0=miliardi)
   - willingness_to_pay: disponibilità a pagare (0.1=difficile convincerli, 1.0=pagano già o chiedono attivamente)
   - urgency: urgenza del problema per il target (0.1=fastidio, 1.0=perde soldi/clienti oggi)
   - competition_gap: gap competitivo (1.0=nessuna soluzione, 0.0=mercato saturo)
   - ai_solvability: risolvibilità con AI/automazione (0.1=richiede umani, 1.0=100% automatizzabile)
   - time_to_market: velocità di lancio (1.0=1 settimana, 0.3=3 mesi, 0.0=anni)
   - recurring_potential: potenziale ricorrente (1.0=problema quotidiano, 0.3=mensile, 0.0=una tantum)

5. CLASSIFICAZIONE:
   - sector: uno tra food, health, finance, education, legal, ecommerce, hr, real_estate, sustainability, cybersecurity, entertainment, logistics
   - geographic_scope: global, continental, national, regional
   - top_markets: lista 3-5 codici paese ISO (es. ["IT","DE","FR"])

6. FONTI:
   - source_name: nome fonte specifica
   - source_url: URL approssimativo

RIFIUTA qualsiasi problema che non abbia:
- target_customer con professione specifica e contesto demografico
- evidence con dato numerico verificabile
- why_now che spieghi il timing attuale

Rispondi SOLO con JSON valido:
{"problems":[{"title":"titolo breve e specifico","description":"descrizione 2-3 frasi","target_customer":"elettricisti autonomi italiani 30-45 anni","target_geography":"Italia, particolarmente nord e centro","problem_frequency":"monthly","current_workaround":"cercano corsi online generici o ignorano la normativa","pain_intensity":4,"evidence":"In Italia ci sono 180.000 elettricisti autonomi (CGIA 2024), il 60% non è aggiornato sulla norma CEI 64-8/7 entrata in vigore nel 2023","why_now":"Aggiornamento CEI 64-8/7 del 2023 ha reso obbligatoria la certificazione per impianti industriali — entrata in vigore graduata fino a 2025","who_is_affected":"chi soffre specificamente","real_world_example":"storia concreta","why_it_matters":"perche conta economicamente","sector":"education","geographic_scope":"national","top_markets":["IT"],"market_size":0.4,"willingness_to_pay":0.7,"urgency":0.8,"competition_gap":0.7,"ai_solvability":0.8,"time_to_market":0.8,"recurring_potential":0.6,"source_name":"CGIA Mestre","source_url":"https://cgia.it"}],"new_sources":[{"name":"nome","url":"url","category":"tipo","sectors":["settore"]}]}
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
    # Query specifiche per segmento demografico — NON generiche
    sector_queries = {
        "food": "specific problems faced by independent restaurant owners small cafes 2026 niche market gaps",
        "health": "underserved specific patient groups healthcare gaps niche problems 2026 specific demographics",
        "finance": "specific financial problems self-employed freelancers specific professions compliance 2026",
        "education": "professional certification training gaps specific professions mandatory regulations 2026",
        "legal": "legal compliance problems specific small business types specific regulations 2026",
        "ecommerce": "specific seller problems niche categories marketplace friction specific demographics 2026",
        "hr": "specific hiring problems specific industry sectors company size underserved 2026",
        "real_estate": "specific pain points property managers small landlords specific regions 2026",
        "sustainability": "specific green compliance problems SMEs specific industries regulations 2026",
        "cybersecurity": "specific cybersecurity gaps micro-business specific sectors lack of tools 2026",
        "entertainment": "specific creator monetization problems niche content types platforms 2026",
        "logistics": "last-mile specific problems specific goods types regions underserved 2026",
    }

    for sector in all_sectors:
        if sector in sector_queries:
            queries.append((sector, sector_queries[sector]))

    # Query cross-settoriali focalizzate su specificità demografica
    queries.append(("cross", "specific professional groups facing regulatory compliance gaps losing business 2026"))
    queries.append(("cross", "niche market underserved specific age group profession geography problem 2026"))
    queries.append(("cross", "mandatory certification training gap specific trade workers 2026 Italy Germany France"))

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


def validate_specificity(prob):
    """
    Valida che il problema abbia i campi di specificità richiesti.
    Ritorna (is_valid, rejection_reason).
    """
    target_customer = prob.get("target_customer", "").strip()
    evidence = prob.get("evidence", "").strip()
    why_now = prob.get("why_now", "").strip()
    pain_intensity = prob.get("pain_intensity", 0)

    # Controlla target_customer non generico
    if not target_customer:
        return False, "target_customer mancante"

    target_lower = target_customer.lower()
    for generic_term in GENERIC_TARGET_TERMS:
        # Il termine generico da solo (senza qualificatori) è il problema
        words = target_lower.split()
        if generic_term in words:
            # Verifica che ci siano qualificatori (età, paese, settore specifico)
            has_qualifier = any(
                char.isdigit() for char in target_customer
            ) or len(target_customer.split()) > 3
            if not has_qualifier:
                return False, f"target_customer troppo generico (contiene '{generic_term}' senza qualificatori)"

    # Controlla che target_customer abbia almeno professione o contesto specifico
    if len(target_customer.split()) < 3:
        return False, "target_customer troppo vago (meno di 3 parole)"

    # Controlla evidence
    if not evidence:
        return False, "evidence mancante"
    if len(evidence) < 30:
        return False, "evidence troppo vaga (meno di 30 caratteri)"

    # Controlla why_now
    if not why_now:
        return False, "why_now mancante"
    if len(why_now) < 20:
        return False, "why_now troppo vaga"

    return True, None


def calculate_weighted_score(prob):
    """
    Calcola score pesato con penalità per genericità e bonus per specificità.
    """
    # Score base dai 7 parametri
    base_score = 0.0
    for param, weight in WEIGHTS.items():
        value = prob.get(param, 0.5)
        if isinstance(value, (int, float)):
            base_score += float(value) * weight

    adjustments = 0.0
    multiplier = 1.0

    target_customer = prob.get("target_customer", "").lower()
    evidence = prob.get("evidence", "")
    why_now = prob.get("why_now", "")
    pain_intensity = prob.get("pain_intensity", 3)

    # PENALITÀ per genericità
    # Target generico senza qualificatori
    generic_count = sum(1 for term in GENERIC_TARGET_TERMS if term in target_customer.split())
    if generic_count > 0 and len(target_customer.split()) <= 3:
        adjustments -= 0.20

    # Evidence vuota o generica
    if not evidence or len(evidence) < 30:
        adjustments -= 0.15

    # why_now mancante
    if not why_now or len(why_now) < 20:
        adjustments -= 0.10

    # pain_intensity bassa
    if isinstance(pain_intensity, (int, float)) and pain_intensity < 3:
        multiplier *= 0.7

    # BONUS per specificità
    # Target con età, professione, geografia specifica
    has_age = any(char.isdigit() for char in target_customer)
    has_many_words = len(target_customer.split()) >= 4
    if has_age or has_many_words:
        adjustments += 0.10

    # Evidence con dato numerico e fonte
    has_number = any(char.isdigit() for char in evidence)
    has_source_hint = any(term in evidence.lower() for term in ["fonte", "source", "report", "studio", "ricerca", "%", "milion", "miliard"])
    if has_number and (has_source_hint or len(evidence) > 80):
        adjustments += 0.10

    final_score = (base_score + adjustments) * multiplier
    return round(max(0.0, min(1.0, final_score)), 4)


def analyze_batch(search_results):
    combined = "\n\n---\n\n".join([
        f"Settore: {sector}\nQuery: {query}\nRisultati: {result}"
        for sector, query, result in search_results
    ])

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=6000,
            system=ANALYSIS_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    "Analizza questi risultati e identifica MASSIMO 3 problemi SPECIFICI e AZIONABILI. "
                    "Ogni problema DEVE avere target_customer con professione specifica, evidence con dato numerico, "
                    "e why_now con spiegazione del timing. Se non riesci a trovare dati concreti per un problema, SCARTALO. "
                    "SOLO JSON:\n\n" + combined
                )
            }]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        supabase.table("agent_logs").insert({
            "agent_id": "world_scanner",
            "action": "scan_v3",
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

        # Validazione specificità obbligatoria
        is_valid, rejection_reason = validate_specificity(prob)
        if not is_valid:
            print(f"   [REJECTED] {title[:60]} — {rejection_reason}")
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
            pain_intensity = prob.get("pain_intensity", None)
            if isinstance(pain_intensity, (int, float)):
                pain_intensity = int(pain_intensity)

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
                # Nuovi campi specificità v3.0
                "target_customer": prob.get("target_customer", ""),
                "target_geography": prob.get("target_geography", ""),
                "problem_frequency": prob.get("problem_frequency", ""),
                "current_workaround": prob.get("current_workaround", ""),
                "pain_intensity": pain_intensity,
                "evidence": prob.get("evidence", ""),
                "why_now": prob.get("why_now", ""),
                "fingerprint": fp,
                "source_id": source_id,
                "status": "new",
                "created_by": "world_scanner_v3",
            }).execute()

            saved += 1
            saved_scores.append(weighted)
            existing_fps.add(fp)
            pain_str = f"pain={pain_intensity}/5" if pain_intensity else ""
            print(f"   [{weighted:.3f}] {title} ({sector}) {pain_str}")

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
    print("World Scanner v3.0 avviato...")

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
    total_rejected = 0
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
        min_s = min(all_scores)
        max_s = max(all_scores)
        print(f"\n   Score: avg={avg:.3f}, min={min_s:.3f}, max={max_s:.3f}")

    try:
        supabase.table("scan_logs").insert({
            "agent_id": "world_scanner_v3",
            "query": json.dumps([q for _, q, _ in search_results[:5]]),
            "sources_scanned": len(search_results),
            "results_found": total_saved,
            "duration_ms": 0,
            "status": "completed",
        }).execute()
    except:
        pass

    print(f"\n   Totale: {total_saved} nuovi problemi salvati")
    print("World Scanner v3.0 completato.")


if __name__ == "__main__":
    run()
