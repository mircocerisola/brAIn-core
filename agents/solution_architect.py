"""
brAIn Solution Architect Agent v2.0
Layer 2 — Genera soluzioni SOLO per i problemi approvati da Mirco.
v2.0: BMG framework, ricerca competitiva Perplexity, campi MVP obbligatori, paradox of specificity.
"""

import os
import json
import time
from dotenv import load_dotenv
import anthropic
from supabase import create_client
import requests

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

# Pesi BOS Solution Quality Scores v2.0 (somma = 1.0)
BOS_WEIGHTS = {
    "uniqueness": 0.25,       # Quanto è unica vs competitor
    "moat_potential": 0.20,   # Network effect, dati proprietari
    "value_multiplier": 0.20, # 10x improvement su almeno una dimensione
    "revenue_clarity": 0.15,  # Chiarezza modello di revenue
    "ai_nativeness": 0.10,    # Quanto è core-AI
    "simplicity": 0.10,       # Utente capisce in <10 secondi
}

GENERATION_PROMPT = """Sei il Solution Architect di brAIn, un'organizzazione AI-native.
Genera 2 soluzioni MVP-ready per il problema dato, basandoti su:
1. Business Model Canvas (Osterwalder): value prop + segmento + revenue + canali + costi
2. Principio YC "10x better": la soluzione DEVE essere 10x migliore dello status quo su almeno una dimensione
3. "Paradox of Specificity" (First Round): più è specifica per un segmento, più è forte il moat
4. Competitor research fornita: identifica il gap reale e costruisci su quello

VINCOLI ORGANIZZATIVI:
- 1 persona, 20h/settimana, competenza tecnica minima
- Budget: 1000 EUR/mese totale, primo progetto SOTTO 200 EUR/mese
- Stack: Claude API, Supabase, Python, Cloud Run, Telegram
- Revenue entro 3 mesi, marginalità alta priorità assoluta
- Vantaggio sleale: AI-native, zero overhead umano, velocità di iterazione

Per ogni soluzione fornisci TUTTI questi campi:

BUSINESS MODEL CANVAS:
- value_proposition: frase unica — "aiutiamo [target specifico] a [fare X] senza [pain attuale]"
- customer_segment: identico al target_customer del problema (professione + età + contesto)
- revenue_model: SaaS_mensile | marketplace | one_time | freemium | transactional
- price_point: prezzo stimato EUR/mese o per transazione CON giustificazione (es. "29 EUR/mese — il 5% del risparmio medio di 600 EUR")
- distribution_channel: come raggiungiamo i PRIMI 100 clienti senza paid ads (es. community LinkedIn, SEO long-tail, partnership associazione di categoria)

MVP SPEC:
- mvp_features: lista delle 3 funzionalità MINIME per validare l'ipotesi di valore (non di più)
- mvp_build_time: giorni stimati per costruire l'MVP con agenti AI (20h/settimana)
- mvp_cost_eur: costo totale in EUR per costruire e lanciare (hosting + API + tools)

MOAT:
- unfair_advantage: perché brAIn (AI-native, velocità, zero overhead) batte un team tradizionale su questa specifica soluzione
- competitive_gap: cosa mancano ai competitor esistenti che noi copriamo

CLASSIFICAZIONE:
- sector: macro-settore
- sub_sector: sotto-settore specifico (es. education/certification, finance/compliance)

BOS SOLUTION QUALITY SCORES (0.0-1.0):
- uniqueness: penalizza se esistono >3 competitor diretti con feature identiche (0-1)
- moat_potential: network effects o dati proprietari = 1.0, solo brand = 0.3 (0-1)
- value_multiplier: 10x improvement = 1.0, 5x = 0.7, 2x = 0.4, <2x = 0.1 (scala logaritmica)
- revenue_clarity: SaaS con prezzo definito = 1.0, "valutiamo" = 0.5, "vedremo" = 0.0 (0-1)
- ai_nativeness: togli AI e non funziona = 1.0, togli AI e funziona uguale = 0.0 (0-1)
- simplicity: utente capisce il valore in <10 secondi = 1.0, richiede spiegazione = 0.3 (0-1)

IMPORTANTE:
- complexity: ESATTAMENTE uno tra: low, medium, high
- Sii SPECIFICO: NON "app per PMI" ma "bot Telegram per elettricisti che risponde a query sulla normativa CEI"
- Il customer_segment DEVE coincidere con il target_customer del problema

Rispondi SOLO con JSON:
{"solutions":[{"title":"nome specifico","description":"cosa fa in modo specifico","approach":"come si implementa tecnicamente","value_proposition":"aiutiamo X a fare Y senza Z","customer_segment":"segmento preciso","revenue_model":"SaaS_mensile","price_point":"29 EUR/mese — giustificazione","distribution_channel":"come raggiungiamo i primi 100 clienti","mvp_features":["feature 1 minima","feature 2 minima","feature 3 minima"],"mvp_build_time":14,"mvp_cost_eur":120,"unfair_advantage":"perché AI-native batte team tradizionale","competitive_gap":"cosa mancano ai competitor","sector":"education","sub_sector":"education/certification","feasibility_score":0.8,"impact_score":0.7,"complexity":"low","time_to_market":"2 settimane","nocode_compatible":true,"cost_estimate":"50 EUR/mese","revenue_model_detail":"come genera soldi nel dettaglio","uniqueness":0.7,"moat_potential":0.6,"value_multiplier":0.8,"simplicity":0.7,"revenue_clarity":0.8,"ai_nativeness":0.9}],"best_pick":"quale delle due e perché in 2 frasi"}
SOLO JSON."""


def get_approved_problems():
    try:
        result = supabase.table("problems") \
            .select("*") \
            .eq("status", "approved") \
            .order("weighted_score", desc=True) \
            .execute()
        return result.data
    except Exception as e:
        print(f"[ERROR] Recupero problemi: {e}")
        return []


def research_competitors(problem_title, sector, target_customer):
    """Ricerca competitiva via Perplexity prima di generare la soluzione."""
    if not PERPLEXITY_API_KEY:
        return None

    query = (
        f"existing solutions competitors for: {problem_title} "
        f"targeting {target_customer} in {sector} sector. "
        f"What tools exist? What are their prices? What are their main limitations?"
    )
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
                "max_tokens": 500,
            },
            timeout=30,
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        return None
    except Exception as e:
        print(f"   [ERROR] Competitor research: {e}")
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


def normalize_complexity(value):
    v = str(value).lower().strip()
    if "low" in v:
        return "low"
    if "high" in v:
        return "high"
    return "medium"


def calculate_bos_score(sol):
    """
    Calcola BOS Solution Quality Score con i pesi v2.0.
    Applica scala non-lineare (^1.2) per penalizzare i mediocri.
    """
    total = 0.0
    for key, weight in BOS_WEIGHTS.items():
        val = float(sol.get(key, 0.5))
        val = max(0.0, min(1.0, val))
        total += val * weight

    # Scala non-lineare: penalizza i mediocri, premia gli eccellenti
    bos = total ** 1.2
    return round(max(0.0, min(1.0, bos)), 4)


def generate_for_problem(problem):
    target_customer = problem.get("target_customer", problem.get("who_is_affected", ""))
    sector = problem.get("sector", problem.get("domain", ""))

    # Fase 1: ricerca competitor
    print(f"      Ricerca competitor...")
    competitor_research = research_competitors(
        problem["title"], sector, target_customer
    )
    time.sleep(0.5)

    # Fase 2: costruisci contesto completo
    prompt_text = (
        f"PROBLEMA: {problem['title']}\n"
        f"Descrizione: {problem.get('description', '')}\n"
        f"Settore: {sector}\n"
        f"Score: {problem.get('weighted_score', problem.get('score', ''))}\n\n"
        f"TARGET SPECIFICO:\n"
        f"  Target customer: {target_customer}\n"
        f"  Geografia: {problem.get('target_geography', problem.get('geographic_scope', ''))}\n"
        f"  Mercati: {problem.get('top_markets', '')}\n\n"
        f"DETTAGLI PROBLEMA:\n"
        f"  Frequenza: {problem.get('problem_frequency', '')}\n"
        f"  Workaround attuale: {problem.get('current_workaround', '')}\n"
        f"  Pain intensity: {problem.get('pain_intensity', '')}/5\n"
        f"  Evidence: {problem.get('evidence', '')}\n"
        f"  Why now: {problem.get('why_now', '')}\n\n"
        f"CONTESTO QUALITATIVO:\n"
        f"  Chi e' colpito: {problem.get('who_is_affected', '')}\n"
        f"  Esempio reale: {problem.get('real_world_example', '')}\n"
        f"  Perche conta: {problem.get('why_it_matters', '')}\n"
    )

    if competitor_research:
        prompt_text += f"\nCOMPETITOR RESEARCH:\n{competitor_research}\n"

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system=GENERATION_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    "Genera 2 soluzioni MVP-ready per questo problema. "
                    "Sii SPECIFICO — il target_customer della soluzione DEVE essere identico al target del problema. "
                    "SOLO JSON:\n\n" + prompt_text
                )
            }]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        supabase.table("agent_logs").insert({
            "agent_id": "solution_architect",
            "action": "generate_solutions_v2",
            "layer": 2,
            "input_summary": f"Soluzioni per: {problem['title'][:100]}",
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
        print(f"[ERROR] Generazione fallita: {e}")
        return None


def save_solutions(analysis_text, problem_id, problem_sector):
    data = extract_json(analysis_text)
    if data is None:
        print("   [ERROR] JSON non valido")
        return 0

    saved = 0
    for sol in data.get("solutions", []):
        try:
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
                "approach": sol.get("approach", ""),
                "sector": sol.get("sector", problem_sector),
                "sub_sector": sol.get("sub_sector", ""),
                "status": "proposed",
                "created_by": "solution_architect_v2",
                # Nuovi campi MVP v2.0
                "value_proposition": sol.get("value_proposition", ""),
                "customer_segment": sol.get("customer_segment", ""),
                "revenue_model": sol.get("revenue_model", ""),
                "price_point": sol.get("price_point", ""),
                "distribution_channel": sol.get("distribution_channel", ""),
                "mvp_features": json.dumps(mvp_features) if mvp_features else None,
                "mvp_build_time": int(sol.get("mvp_build_time", 0)) if sol.get("mvp_build_time") else None,
                "mvp_cost_eur": float(sol.get("mvp_cost_eur", 0)) if sol.get("mvp_cost_eur") else None,
                "unfair_advantage": sol.get("unfair_advantage", ""),
                "competitive_gap": sol.get("competitive_gap", ""),
            }).execute()

            sol_id = sol_result.data[0]["id"]

            feasibility = float(sol.get("feasibility_score", 0.5))
            impact = float(sol.get("impact_score", 0.5))
            overall = (feasibility + impact) / 2

            # Calcola BOS score con nuovi pesi
            bos_score = calculate_bos_score(sol)

            supabase.table("solution_scores").insert({
                "solution_id": sol_id,
                "feasibility_score": feasibility,
                "impact_score": impact,
                "cost_estimate": str(sol.get("cost_estimate", sol.get("mvp_cost_eur", "unknown"))),
                "complexity": normalize_complexity(sol.get("complexity", "medium")),
                "time_to_market": str(sol.get("time_to_market", "unknown")),
                "nocode_compatible": bool(sol.get("nocode_compatible", True)),
                "overall_score": overall,
                "notes": str(sol.get("revenue_model_detail", sol.get("revenue_model", ""))),
                "scored_by": "solution_architect_v2",
            }).execute()

            # Salva BOS score nella soluzione
            supabase.table("solutions").update({
                "bos_score": bos_score,
                "bos_details": json.dumps({
                    "uniqueness": sol.get("uniqueness", 0.5),
                    "moat_potential": sol.get("moat_potential", 0.5),
                    "value_multiplier": sol.get("value_multiplier", 0.5),
                    "revenue_clarity": sol.get("revenue_clarity", 0.5),
                    "ai_nativeness": sol.get("ai_nativeness", 0.5),
                    "simplicity": sol.get("simplicity", 0.5),
                    "weights": BOS_WEIGHTS,
                    "version": "2.0",
                }),
            }).eq("id", sol_id).execute()

            saved += 1
            print(f"      [BOS:{bos_score:.2f}] {sol.get('title')} ({sol.get('sub_sector', '')})")
            if sol.get("value_proposition"):
                print(f"         VP: {sol.get('value_proposition')[:80]}")

        except Exception as e:
            print(f"   [ERROR] Salvataggio: {e}")

    best = data.get("best_pick", "")
    if best:
        print(f"      Best pick: {best[:150]}")

    return saved


def run():
    print("Solution Architect v2.0 avviato...")

    problems = get_approved_problems()

    if not problems:
        print("   Nessun problema approvato. Approva dei problemi dal bot Telegram prima.")
        return

    print(f"   {len(problems)} problemi approvati da elaborare\n")

    total_saved = 0
    for i, problem in enumerate(problems, 1):
        score = problem.get("weighted_score") or problem.get("score", 0)
        sector = problem.get("sector", problem.get("domain", "?"))
        target = problem.get("target_customer", problem.get("who_is_affected", ""))[:50]
        print(f"   [{i}/{len(problems)}] {problem['title']} ({sector}, score: {score})")
        print(f"      Target: {target}")

        analysis = generate_for_problem(problem)
        if analysis:
            saved = save_solutions(analysis, problem["id"], sector)
            total_saved += saved
        else:
            print("      Generazione fallita, passo al prossimo")

        time.sleep(1)

    print(f"\n   Totale: {total_saved} soluzioni salvate")
    print("Solution Architect v2.0 completato.")


if __name__ == "__main__":
    run()
