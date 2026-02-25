"""
brAIn Feasibility Engine v2.0
THINKING — Valuta fattibilita' economica e tecnica delle soluzioni proposte.
v2.0: scoring non-lineare (^1.5), 7 parametri BOS ridefiniti, soglie a step calibrate.
"""

import os
import json
import math
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

HAIKU_MODEL = "claude-haiku-4-5"


FEASIBILITY_PROMPT = """Sei il Feasibility Engine di brAIn, un'organizzazione AI-native.
Valuti la fattibilita' economica e tecnica di soluzioni AI con MASSIMO realismo.

VINCOLI DELL'ORGANIZZAZIONE:
- 1 persona, 20h/settimana, competenza tecnica minima
- Budget: 1000 EUR/mese totale, primo progetto SOTTO 200 EUR/mese
- Stack: Claude API (Haiku/Sonnet), Supabase (PostgreSQL + pgvector), Python, Google Cloud Run, Telegram Bot
- Obiettivo: revenue entro 3 mesi, marginalita' alta priorita' assoluta
- Vantaggio: AI-native, zero overhead umano, velocita' di build

REGOLE DI REALISMO:
- Sii PESSIMISTA su revenue (non ottimista). Dimezza la stima iniziale.
- Sii PESSIMISTA su timeline (20h/settimana con competenza minima = lento).
- Se il mercato ha >5 competitor attivi e non abbiamo un vantaggio 10x chiaro, di' NO_GO.
- Se i costi superano 200 EUR/mese, segnalalo come BLOCCO.

1. COSTO MVP:
   - dev_hours: ore di sviluppo MVP (20h/settimana, competenza minima)
   - dev_cost_eur: dev_hours * 0 EUR (noi usiamo agenti AI, costo opportunita' teorico)
   - api_monthly_eur: costo mensile API (Claude, Perplexity, altri)
   - hosting_monthly_eur: Cloud Run + Supabase (gia' pagato, incrementale ~0-20 EUR)
   - other_monthly_eur: altri costi ricorrenti
   - total_mvp_cost_eur: costo una-tantum API durante sviluppo
   - total_monthly_cost_eur: costi operativi mensili a regime

2. TIMELINE:
   - weeks_to_mvp: settimane per MVP funzionante (20h/settimana, competenza minima)
   - weeks_to_revenue: settimane per primo pagamento reale

3. REVENUE (a 6 mesi dal lancio, scenario PESSIMISTA come base):
   - pessimistic_monthly_eur: 3-5 clienti paganti, prezzo basso
   - realistic_monthly_eur: 15-30 clienti, prezzo medio
   - optimistic_monthly_eur: trazione forte, prezzo pieno
   - pricing_model: subscription | pay_per_use | freemium_premium | one_time | transactional
   - price_point_eur: prezzo per cliente/mese

4. MARGINALITA':
   - monthly_margin_pessimistic: revenue pessimista - costi mensili
   - monthly_margin_realistic: revenue realistico - costi mensili
   - monthly_margin_optimistic: revenue ottimista - costi mensili
   - margin_percentage_realistic: (margine realistico / revenue realistico) * 100
   - breakeven_months: mesi per recuperare total_mvp_cost_eur (scenario realistico)

5. COMPETITION:
   - competition_score: 0=nessun competitor, 1=mercato saturo
   - direct_competitors: numero competitor diretti con feature identiche
   - indirect_competitors: numero alternative indirette
   - our_advantage: vantaggio specifico vs competitor (deve essere 10x su almeno 1 dimensione)

6. GO/NO-GO:
   - decision: "GO", "CONDITIONAL_GO", o "NO_GO"
   - confidence: 0.0-1.0 certezza della raccomandazione
   - reasoning: 2-3 frasi max
   - conditions: se CONDITIONAL_GO, cosa deve succedere prima
   - biggest_risk: rischio principale
   - biggest_opportunity: opportunita' principale

7. BOS FEASIBILITY SCORES (0.0-1.0 per ogni parametro, scala SEVERA):
   - mvp_cost_score: <200 EUR/mese = 1.0, 200-500 EUR/mese = 0.8, 500-2000 = 0.5, >2000 = 0.2
   - time_to_market: <1 settimana = 1.0, 1-2 settimane = 0.8, 1 mese = 0.5, 2 mesi = 0.3, >2 mesi = 0.1
   - ai_buildability: costruibile interamente con Claude Code + agenti AI = 1.0, richiede dev umano = 0.4, richiede team = 0.0
   - margin_potential: >80% margine = 1.0, 50-80% = 0.7, 20-50% = 0.4, <20% = 0.1
   - market_access: 100 clienti via SEO/community/content senza paid ads = 1.0, solo partnership = 0.5, solo cold outreach = 0.3, enterprise sales = 0.0
   - recurring_revenue: SaaS mensile = 1.0, abbonamento annuale = 0.8, pay_per_use frequente = 0.6, one_shot = 0.2
   - scalability: 100->10.000 clienti senza costi proporzionali = 1.0, richiede support umano = 0.4, richiede team = 0.0

Rispondi SOLO con JSON:
{"mvp_cost":{"dev_hours":0,"dev_cost_eur":0,"api_monthly_eur":0,"hosting_monthly_eur":0,"other_monthly_eur":0,"total_mvp_cost_eur":0,"total_monthly_cost_eur":0},"timeline":{"weeks_to_mvp":0,"weeks_to_revenue":0},"revenue":{"pessimistic_monthly_eur":0,"realistic_monthly_eur":0,"optimistic_monthly_eur":0,"pricing_model":"","price_point_eur":0},"margin":{"monthly_margin_pessimistic":0,"monthly_margin_realistic":0,"monthly_margin_optimistic":0,"margin_percentage_realistic":0,"breakeven_months":0},"competition":{"competition_score":0.0,"direct_competitors":0,"indirect_competitors":0,"our_advantage":""},"recommendation":{"decision":"GO","confidence":0.0,"reasoning":"","conditions":"","biggest_risk":"","biggest_opportunity":""},"bos_feasibility":{"mvp_cost_score":0.0,"time_to_market":0.0,"ai_buildability":0.0,"margin_potential":0.0,"market_access":0.0,"recurring_revenue":0.0,"scalability":0.0}}
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
        return result.data or []
    except Exception as e:
        print(f"[ERROR] Recupero soluzioni: {e}")
        return []


def get_solution_scores(solution_id):
    try:
        result = supabase.table("solution_scores").select("*").eq("solution_id", solution_id).execute()
        if result.data:
            return result.data[0]
    except Exception:
        pass
    return {}


def research_competition(solution_title, sector):
    query = f"{solution_title} competitors alternatives market size pricing {sector} 2026"
    return search_perplexity(query)


def analyze_solution(solution, problem, scores, competition_research):
    approach = solution.get("approach", "")
    if isinstance(approach, str):
        try:
            approach_data = json.loads(approach)
            approach_text = json.dumps(approach_data, indent=2, ensure_ascii=False)
        except Exception:
            approach_text = approach
    else:
        approach_text = json.dumps(approach, indent=2, ensure_ascii=False)

    # Includi i nuovi campi MVP della soluzione
    mvp_features = solution.get("mvp_features", "")
    if isinstance(mvp_features, str):
        try:
            mvp_features_text = json.dumps(json.loads(mvp_features), ensure_ascii=False)
        except:
            mvp_features_text = mvp_features
    else:
        mvp_features_text = json.dumps(mvp_features, ensure_ascii=False) if mvp_features else ""

    context = (
        f"SOLUZIONE: {solution['title']}\n"
        f"Descrizione: {solution.get('description', '')}\n"
        f"Approccio: {approach_text}\n"
        f"Settore: {solution.get('sector', '')} / {solution.get('sub_sector', '')}\n"
        f"Value Proposition: {solution.get('value_proposition', '')}\n"
        f"Customer Segment: {solution.get('customer_segment', '')}\n"
        f"Revenue Model: {solution.get('revenue_model', '')}\n"
        f"Price Point: {solution.get('price_point', '')}\n"
        f"Distribution Channel: {solution.get('distribution_channel', '')}\n"
        f"MVP Features: {mvp_features_text}\n"
        f"MVP Build Time (SA stima): {solution.get('mvp_build_time', '')} giorni\n"
        f"MVP Cost EUR (SA stima): {solution.get('mvp_cost_eur', '')} EUR\n"
        f"Unfair Advantage: {solution.get('unfair_advantage', '')}\n"
        f"Competitive Gap: {solution.get('competitive_gap', '')}\n\n"
        f"PROBLEMA ORIGINALE: {problem.get('title', '')}\n"
        f"Target Customer: {problem.get('target_customer', problem.get('who_is_affected', ''))}\n"
        f"Target Geography: {problem.get('target_geography', '')}\n"
        f"Pain Intensity: {problem.get('pain_intensity', '')}/5\n"
        f"Evidence: {problem.get('evidence', '')}\n"
        f"Why Now: {problem.get('why_now', '')}\n"
        f"Score problema: {problem.get('weighted_score', '')}\n\n"
        f"SCORE SOLUTION ARCHITECT:\n"
        f"Feasibility: {scores.get('feasibility_score', 'N/A')}\n"
        f"Impact: {scores.get('impact_score', 'N/A')}\n"
        f"Complexity: {scores.get('complexity', 'N/A')}\n"
    )

    if competition_research:
        context += f"\nRICERCA COMPETITIVA:\n{competition_research}\n"

    start = time.time()
    try:
        response = claude.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2048,
            system=FEASIBILITY_PROMPT,
            messages=[{"role": "user", "content": f"Valuta questa soluzione con MASSIMO realismo. SOLO JSON:\n\n{context}"}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        supabase.table("agent_logs").insert({
            "agent_id": "feasibility_engine",
            "action": "analyze_feasibility_v2",
            "layer": 2,
            "input_summary": f"Feasibility v2: {solution['title'][:100]}",
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
    """
    Calcola score composito 0-1 dai BOS feasibility scores.
    Applica scala non-lineare (^1.5) per evitare clustering alto:
    - score grezzo 0.9 -> 0.85
    - score grezzo 0.7 -> 0.59
    - score grezzo 0.5 -> 0.35
    - score grezzo 0.3 -> 0.16
    """
    if not analysis:
        return 0.0

    bos = analysis.get("bos_feasibility", {})

    # 7 parametri BOS v2.0 con pesi
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
        val = max(0.0, min(1.0, val))
        raw_score += val * weight

    # Applica moltiplicatore decisione
    rec = analysis.get("recommendation", {})
    decision = rec.get("decision", "NO_GO")
    confidence = float(rec.get("confidence", 0.5))

    if decision == "NO_GO":
        raw_score *= 0.5
    elif decision == "CONDITIONAL_GO":
        raw_score *= max(0.7, confidence)
    # GO: nessuna penalità

    # Scala non-lineare: penalizza mediocri, premia eccellenti
    final_score = raw_score ** 1.5

    return round(max(0.0, min(1.0, final_score)), 4)


def save_feasibility(solution_id, analysis, feasibility_score):
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
    print("Feasibility Engine v2.0 avviato...")

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

        problem = sol.get("problems", {}) or {}
        if not problem:
            problem = {"title": "", "description": ""}

        scores = get_solution_scores(sol["id"])

        print(f"      Ricerca competitiva...")
        competition = research_competition(title, sector)

        print(f"      Analisi fattibilita'...")
        analysis = analyze_solution(sol, problem, scores, competition)

        if not analysis:
            print(f"      [ERROR] Analisi fallita, skip")
            continue

        feasibility_score = calculate_feasibility_score(analysis)

        saved = save_feasibility(sol["id"], analysis, feasibility_score)
        if saved:
            evaluated += 1

            decision = analysis.get("recommendation", {}).get("decision", "NO_GO")
            margin_real = analysis.get("margin", {}).get("monthly_margin_realistic", 0)
            weeks = analysis.get("timeline", {}).get("weeks_to_mvp", "?")
            monthly_cost = analysis.get("mvp_cost", {}).get("total_monthly_cost_eur", "?")

            # Mostra distribuzione BOS
            bos = analysis.get("bos_feasibility", {})
            bos_str = " | ".join([f"{k[:4]}:{v:.2f}" for k, v in bos.items()])

            print(f"      Score: {feasibility_score:.3f} | {decision}")
            print(f"      BOS: {bos_str}")
            print(f"      Margine: {margin_real} EUR/mese | MVP: {weeks} sett. | Costi: {monthly_cost} EUR/mese")

            if decision == "GO":
                go_solutions.append({"title": title, "score": feasibility_score, "analysis": analysis})
            elif decision == "CONDITIONAL_GO":
                conditional_solutions.append({"title": title, "score": feasibility_score, "analysis": analysis})

        time.sleep(1)

    # Notifica Telegram
    if go_solutions or conditional_solutions:
        msg = f"FEASIBILITY ENGINE v2.0 — {evaluated} soluzioni valutate\n\n"

        if go_solutions:
            msg += "GO:\n"
            for s in sorted(go_solutions, key=lambda x: x["score"], reverse=True):
                a = s["analysis"]
                rev = a.get("revenue", {}).get("realistic_monthly_eur", 0)
                cost = a.get("mvp_cost", {}).get("total_monthly_cost_eur", 0)
                weeks = a.get("timeline", {}).get("weeks_to_mvp", "?")
                msg += f"  [{s['score']:.3f}] {s['title']}\n"
                msg += f"    Rev: {rev} EUR/mese, Costi: {cost} EUR/mese, MVP: {weeks} sett.\n"

        if conditional_solutions:
            msg += "\nCONDITIONAL GO:\n"
            for s in sorted(conditional_solutions, key=lambda x: x["score"], reverse=True):
                a = s["analysis"]
                cond = a.get("recommendation", {}).get("conditions", "")
                msg += f"  [{s['score']:.3f}] {s['title']}\n"
                msg += f"    Condizione: {cond[:100]}\n"

        notify_telegram(msg)

    print(f"\n   Totale: {evaluated} soluzioni valutate")
    print(f"   GO: {len(go_solutions)}, Conditional: {len(conditional_solutions)}, No-go: {evaluated - len(go_solutions) - len(conditional_solutions)}")
    print("Feasibility Engine v2.0 completato.")

    return {
        "status": "completed",
        "evaluated": evaluated,
        "go": len(go_solutions),
        "conditional_go": len(conditional_solutions),
        "no_go": evaluated - len(go_solutions) - len(conditional_solutions),
    }


if __name__ == "__main__":
    run()
