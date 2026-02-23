"""
brAIn Feasibility Engine v1.0
THINKING — Valuta fattibilita' economica e tecnica delle soluzioni proposte.
Per ogni soluzione: costo MVP, tempo sviluppo, revenue 3 scenari, marginalita', competition, go/no-go.
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Costanti
DEV_HOURLY_RATE_EUR = 50  # costo orario sviluppo (freelance medio EU)
HAIKU_MODEL = "claude-haiku-4-5-20251001"


FEASIBILITY_PROMPT = """Sei il Feasibility Engine di brAIn, un'organizzazione AI-native.
Valuti la fattibilita' economica e tecnica di soluzioni AI.

VINCOLI DELL'ORGANIZZAZIONE:
- 1 persona, 20h/settimana, competenza tecnica minima
- Budget: 1000 EUR/mese totale, primo progetto sotto 200 EUR/mese
- Stack: Claude API (Haiku/Sonnet), Supabase (PostgreSQL + pgvector), Python, Google Cloud Run, Telegram Bot
- Obiettivo: revenue entro 3 mesi, marginalita' alta priorita' assoluta
- Puo' usare API/servizi esterni purche' nel budget

Hai la SOLUZIONE da valutare e una RICERCA COMPETITIVA sul mercato.

Per la soluzione, calcola:

1. COSTO MVP (in EUR):
   - dev_hours: ore di sviluppo stimate per MVP funzionante
   - dev_cost_eur: dev_hours * 50 EUR/ora (costo opportunita')
   - api_monthly_eur: costo mensile API (Claude, Perplexity, altri)
   - hosting_monthly_eur: costo mensile hosting (Cloud Run, Supabase, domini)
   - other_monthly_eur: altri costi ricorrenti (email, pagamenti, ecc.)
   - total_mvp_cost_eur: costo una-tantum per costruire MVP
   - total_monthly_cost_eur: costi operativi mensili

2. TEMPO SVILUPPO:
   - weeks_to_mvp: settimane per MVP (considerando 20h/settimana)
   - weeks_to_revenue: settimane stimate per primo euro di revenue

3. REVENUE MENSILE (3 scenari, a 6 mesi dal lancio):
   - pessimistic_monthly_eur: scenario pessimista (pochi clienti, prezzo basso)
   - realistic_monthly_eur: scenario realistico
   - optimistic_monthly_eur: scenario ottimista (trazione buona)
   - pricing_model: come si fa pagare (subscription, pay-per-use, freemium+premium, ecc.)
   - price_point_eur: prezzo per cliente/mese

4. MARGINALITA':
   - monthly_margin_pessimistic: revenue pessimista - costi mensili
   - monthly_margin_realistic: revenue realistico - costi mensili
   - monthly_margin_optimistic: revenue ottimista - costi mensili
   - margin_percentage_realistic: (margine realistico / revenue realistico) * 100
   - breakeven_months: mesi per recuperare costo MVP (scenario realistico)

5. COMPETITION SCORE (0.0-1.0, dove 0=mercato deserto, 1=mercato saturo):
   - competition_score: quanto e' affollato il mercato
   - direct_competitors: numero di competitor diretti
   - indirect_competitors: numero di alternative indirette
   - our_advantage: perche' possiamo competere nonostante i limiti

6. GO/NO-GO:
   - recommendation: "GO", "CONDITIONAL_GO", o "NO_GO"
   - confidence: 0.0-1.0 quanto sei sicuro della raccomandazione
   - reasoning: motivazione in 2-3 frasi
   - conditions: se CONDITIONAL_GO, cosa deve succedere prima di procedere
   - biggest_risk: rischio principale
   - biggest_opportunity: opportunita' principale

REGOLE:
- Sii REALISTICO, non ottimista. Meglio sottostimare revenue e sovrastimare costi.
- Se il mercato e' saturo e non abbiamo un vantaggio chiaro, di' NO_GO.
- Se i costi superano il budget (200 EUR/mese per primo progetto), segnalalo.
- Considera che l'operatore ha competenza tecnica MINIMA — tempi di sviluppo piu' lunghi.

Rispondi SOLO con JSON:
{"mvp_cost":{"dev_hours":0,"dev_cost_eur":0,"api_monthly_eur":0,"hosting_monthly_eur":0,"other_monthly_eur":0,"total_mvp_cost_eur":0,"total_monthly_cost_eur":0},"timeline":{"weeks_to_mvp":0,"weeks_to_revenue":0},"revenue":{"pessimistic_monthly_eur":0,"realistic_monthly_eur":0,"optimistic_monthly_eur":0,"pricing_model":"","price_point_eur":0},"margin":{"monthly_margin_pessimistic":0,"monthly_margin_realistic":0,"monthly_margin_optimistic":0,"margin_percentage_realistic":0,"breakeven_months":0},"competition":{"competition_score":0.0,"direct_competitors":0,"indirect_competitors":0,"our_advantage":""},"recommendation":{"decision":"GO","confidence":0.0,"reasoning":"","conditions":"","biggest_risk":"","biggest_opportunity":""}}
SOLO JSON."""


def get_telegram_chat_id():
    try:
        result = supabase.table("org_config").select("value").eq("key", "telegram_user_id").execute()
        if result.data:
            return json.loads(result.data[0]["value"])
    except Exception:
        pass
    return None


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
        print(f"[TELEGRAM ERROR] {e}")


def extract_json(text):
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except Exception:
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
    except Exception:
        return None


def search_perplexity(query):
    if not PERPLEXITY_API_KEY:
        return None
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
    except Exception:
        return None


def get_pending_solutions(solution_id=None):
    """Recupera soluzioni proposte che non hanno ancora feasibility_details."""
    try:
        query = supabase.table("solutions").select("*, problems(title, description, sector, who_is_affected, why_it_matters, weighted_score)")
        if solution_id:
            query = query.eq("id", solution_id)
        else:
            query = query.eq("status", "proposed").is_("feasibility_details", "null")
        result = query.order("created_at", desc=True).limit(20).execute()
        return result.data or []
    except Exception as e:
        print(f"[ERROR] Recupero soluzioni: {e}")
        return []


def get_solution_scores(solution_id):
    """Recupera gli score dal Solution Architect."""
    try:
        result = supabase.table("solution_scores").select("*").eq("solution_id", solution_id).execute()
        if result.data:
            return result.data[0]
    except Exception:
        pass
    return {}


def research_competition(solution_title, sector):
    """Ricerca competitiva via Perplexity."""
    query = f"{solution_title} competitors alternatives market size pricing {sector}"
    return search_perplexity(query)


def analyze_solution(solution, problem, scores, competition_research):
    """Chiama Haiku per analisi di fattibilita' completa."""
    # Estrai approach se e' JSON
    approach = solution.get("approach", "")
    if isinstance(approach, str):
        try:
            approach_data = json.loads(approach)
            approach_text = json.dumps(approach_data, indent=2, ensure_ascii=False)
        except Exception:
            approach_text = approach
    else:
        approach_text = json.dumps(approach, indent=2, ensure_ascii=False)

    context = (
        f"SOLUZIONE: {solution['title']}\n"
        f"Descrizione: {solution.get('description', '')}\n"
        f"Approccio: {approach_text}\n"
        f"Settore: {solution.get('sector', '')} / {solution.get('sub_sector', '')}\n\n"
        f"PROBLEMA ORIGINALE: {problem.get('title', '')}\n"
        f"Descrizione problema: {problem.get('description', '')}\n"
        f"Chi e' colpito: {problem.get('who_is_affected', '')}\n"
        f"Perche' conta: {problem.get('why_it_matters', '')}\n"
        f"Score problema: {problem.get('weighted_score', '')}\n\n"
        f"SCORE SOLUTION ARCHITECT:\n"
        f"Feasibility: {scores.get('feasibility_score', 'N/A')}\n"
        f"Impact: {scores.get('impact_score', 'N/A')}\n"
        f"Complexity: {scores.get('complexity', 'N/A')}\n"
        f"Time to market: {scores.get('time_to_market', 'N/A')}\n"
        f"Cost estimate SA: {scores.get('cost_estimate', 'N/A')}\n"
        f"Notes: {scores.get('notes', '')}\n"
    )

    if competition_research:
        context += f"\nRICERCA COMPETITIVA:\n{competition_research}\n"

    start = time.time()
    try:
        response = claude.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2048,
            system=FEASIBILITY_PROMPT,
            messages=[{"role": "user", "content": f"Valuta questa soluzione. SOLO JSON:\n\n{context}"}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        # Log
        supabase.table("agent_logs").insert({
            "agent_id": "feasibility_engine",
            "action": "analyze_feasibility",
            "layer": 2,
            "input_summary": f"Feasibility: {solution['title'][:100]}",
            "output_summary": reply[:500],
            "model_used": HAIKU_MODEL,
            "tokens_input": response.usage.input_tokens,
            "tokens_output": response.usage.output_tokens,
            "cost_usd": (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            "duration_ms": duration,
            "status": "success",
        }).execute()

        return extract_json(reply)

    except Exception as e:
        print(f"[ERROR] Analisi fallita: {e}")
        return None


def calculate_feasibility_score(analysis):
    """Calcola score composito 0-1 dai risultati dell'analisi."""
    if not analysis:
        return 0.0

    scores = []

    # 1. Marginalita' realistica (peso 0.30)
    margin = analysis.get("margin", {})
    margin_pct = float(margin.get("margin_percentage_realistic", 0))
    margin_score = min(1.0, max(0.0, margin_pct / 80))  # 80%+ margine = score 1.0
    scores.append(("margin", margin_score, 0.30))

    # 2. Tempo al revenue (peso 0.20)
    timeline = analysis.get("timeline", {})
    weeks_to_rev = float(timeline.get("weeks_to_revenue", 52))
    time_score = max(0.0, 1.0 - (weeks_to_rev / 24))  # 24+ settimane = 0
    scores.append(("time", time_score, 0.20))

    # 3. Costi mensili entro budget (peso 0.20)
    costs = analysis.get("mvp_cost", {})
    monthly_cost = float(costs.get("total_monthly_cost_eur", 1000))
    cost_score = max(0.0, 1.0 - (monthly_cost / 200))  # >200 EUR/mese = 0
    scores.append(("cost", cost_score, 0.20))

    # 4. Competition bassa (peso 0.15)
    competition = analysis.get("competition", {})
    comp = float(competition.get("competition_score", 0.5))
    comp_score = 1.0 - comp  # meno competition = meglio
    scores.append(("competition", comp_score, 0.15))

    # 5. Confidence della raccomandazione (peso 0.15)
    rec = analysis.get("recommendation", {})
    confidence = float(rec.get("confidence", 0.5))
    decision = rec.get("decision", "NO_GO")
    decision_mult = 1.0 if decision == "GO" else 0.7 if decision == "CONDITIONAL_GO" else 0.3
    rec_score = confidence * decision_mult
    scores.append(("recommendation", rec_score, 0.15))

    total = sum(score * weight for _, score, weight in scores)
    return round(min(1.0, max(0.0, total)), 4)


def save_feasibility(solution_id, analysis, feasibility_score):
    """Aggiorna la soluzione con score e dettagli."""
    try:
        supabase.table("solutions").update({
            "feasibility_score": feasibility_score,
            "feasibility_details": json.dumps(analysis, ensure_ascii=False),
        }).eq("id", solution_id).execute()
        return True
    except Exception as e:
        print(f"[ERROR] Salvataggio feasibility: {e}")
        return False


def run(solution_id=None):
    print("Feasibility Engine v1.0 avviato...")

    solutions = get_pending_solutions(solution_id)

    if not solutions:
        print("   Nessuna soluzione da valutare.")
        return {"status": "no_solutions", "evaluated": 0}

    print(f"   {len(solutions)} soluzioni da valutare\n")

    evaluated = 0
    go_solutions = []
    conditional_solutions = []

    for i, sol in enumerate(solutions, 1):
        title = sol.get("title", "Senza titolo")
        sector = sol.get("sector", "")
        print(f"   [{i}/{len(solutions)}] {title}")

        # Recupera problema collegato
        problem = sol.get("problems", {}) or {}
        if not problem:
            problem = {"title": "", "description": ""}

        # Recupera score dal Solution Architect
        scores = get_solution_scores(sol["id"])

        # Ricerca competitiva
        print(f"      Ricerca competitiva...")
        competition = research_competition(title, sector)

        # Analisi Haiku
        print(f"      Analisi fattibilita'...")
        analysis = analyze_solution(sol, problem, scores, competition)

        if not analysis:
            print(f"      [ERROR] Analisi fallita, skip")
            continue

        # Calcola score composito
        feasibility_score = calculate_feasibility_score(analysis)

        # Salva su DB
        saved = save_feasibility(sol["id"], analysis, feasibility_score)
        if saved:
            evaluated += 1

            decision = analysis.get("recommendation", {}).get("decision", "NO_GO")
            margin_real = analysis.get("margin", {}).get("monthly_margin_realistic", 0)
            weeks = analysis.get("timeline", {}).get("weeks_to_mvp", "?")

            print(f"      Score: {feasibility_score:.2f} | {decision} | Margine: {margin_real} EUR/mese | MVP: {weeks} sett.")

            if decision == "GO":
                go_solutions.append({"title": title, "score": feasibility_score, "analysis": analysis})
            elif decision == "CONDITIONAL_GO":
                conditional_solutions.append({"title": title, "score": feasibility_score, "analysis": analysis})

        time.sleep(1)

    # Notifica Telegram
    if go_solutions or conditional_solutions:
        msg = f"FEASIBILITY ENGINE - {evaluated} soluzioni valutate\n\n"

        if go_solutions:
            msg += "GO:\n"
            for s in sorted(go_solutions, key=lambda x: x["score"], reverse=True):
                a = s["analysis"]
                rev = a.get("revenue", {}).get("realistic_monthly_eur", 0)
                cost = a.get("mvp_cost", {}).get("total_monthly_cost_eur", 0)
                weeks = a.get("timeline", {}).get("weeks_to_mvp", "?")
                msg += f"  [{s['score']:.2f}] {s['title']}\n"
                msg += f"    Revenue: {rev} EUR/mese, Costi: {cost} EUR/mese, MVP: {weeks} sett.\n"

        if conditional_solutions:
            msg += "\nCONDITIONAL GO:\n"
            for s in sorted(conditional_solutions, key=lambda x: x["score"], reverse=True):
                a = s["analysis"]
                cond = a.get("recommendation", {}).get("conditions", "")
                msg += f"  [{s['score']:.2f}] {s['title']}\n"
                msg += f"    Condizione: {cond[:100]}\n"

        notify_telegram(msg)

    print(f"\n   Totale: {evaluated} soluzioni valutate")
    print(f"   GO: {len(go_solutions)}, Conditional: {len(conditional_solutions)}, No-go: {evaluated - len(go_solutions) - len(conditional_solutions)}")
    print("Feasibility Engine v1.0 completato.")

    return {
        "status": "completed",
        "evaluated": evaluated,
        "go": len(go_solutions),
        "conditional_go": len(conditional_solutions),
        "no_go": evaluated - len(go_solutions) - len(conditional_solutions),
    }


if __name__ == "__main__":
    run()
