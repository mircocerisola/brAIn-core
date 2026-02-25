"""
brAIn Solution Architect Agent v1.4
Layer 2 — Genera soluzioni SOLO per i problemi approvati da Mirco.
"""

import os
import json
import time
from dotenv import load_dotenv
import anthropic
from supabase import create_client

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

GENERATION_PROMPT = """Sei il Solution Architect di brAIn.
Genera 2 soluzioni concrete per il problema dato.

Vincoli:
- 1 umano (20h/settimana, competenza tecnica minima)
- Budget: 1000 euro/mese totale, primo progetto sotto 200 euro/mese
- Stack: Claude API, Supabase, Python, Cloud Run, Telegram
- No-code o low-code preferito
- Revenue entro 3 mesi
- Marginalita alta e' la priorita assoluta

Usa i dati qualitativi del problema (chi e' colpito, esempi, perche conta) per costruire soluzioni che rispondano davvero al bisogno.

Per ogni soluzione specifica anche:
- sector: macro-settore (es. food, health, finance)
- sub_sector: sotto-livello specifico (es. food/delivery, finance/compliance)

BOS SOLUTION QUALITY SCORES (0.0-1.0 per ognuno):
- uniqueness: quanto e' unica questa soluzione rispetto a quelle esistenti
- moat_potential: potenziale di creare un vantaggio difendibile (network effect, dati proprietari)
- value_multiplier: quanto valore genera rispetto al prezzo (10x value = 1.0)
- simplicity: quanto e' semplice da usare e capire per il cliente
- revenue_clarity: quanto e' chiaro e diretto il modello di revenue
- ai_nativeness: quanto la soluzione e' nativamente AI (core AI = 1.0)

Rispondi SOLO con JSON:
{"solutions":[{"title":"nome","description":"cosa fa","approach":"come si implementa","sector":"food","sub_sector":"food/waste","feasibility_score":0.8,"impact_score":0.7,"complexity":"low","time_to_market":"2 settimane","nocode_compatible":true,"cost_estimate":"50 euro/mese","revenue_model":"come genera soldi","uniqueness":0.7,"moat_potential":0.6,"value_multiplier":0.8,"simplicity":0.7,"revenue_clarity":0.8,"ai_nativeness":0.9}],"best_pick":"quale delle due e perche"}

IMPORTANTE: complexity DEVE essere esattamente uno tra: low, medium, high.
SOLO JSON."""


def get_approved_problems():
    """Recupera SOLO problemi approvati da Mirco"""
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


def generate_for_problem(problem):
    prompt_text = (
        f"Problema: {problem['title']}\n"
        f"Descrizione: {problem.get('description', '')}\n"
        f"Settore: {problem.get('sector', problem.get('domain', ''))}\n"
        f"Urgenza: {problem.get('urgency', '')}\n"
        f"Score: {problem.get('weighted_score', problem.get('score', ''))}\n"
        f"Mercato: {problem.get('top_markets', '')}\n"
        f"Scope: {problem.get('geographic_scope', '')}\n\n"
        f"Chi e' colpito: {problem.get('who_is_affected', '')}\n"
        f"Esempio reale: {problem.get('real_world_example', '')}\n"
        f"Perche conta: {problem.get('why_it_matters', '')}"
    )

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=GENERATION_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Genera 2 soluzioni per questo problema. SOLO JSON:\n\n{prompt_text}"
            }]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        supabase.table("agent_logs").insert({
            "agent_id": "solution_architect",
            "action": "generate_solutions",
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
            sol_result = supabase.table("solutions").insert({
                "problem_id": problem_id,
                "title": sol.get("title", "Senza titolo"),
                "description": sol.get("description", ""),
                "approach": sol.get("approach", ""),
                "sector": sol.get("sector", problem_sector),
                "sub_sector": sol.get("sub_sector", ""),
                "status": "proposed",
                "created_by": "solution_architect",
            }).execute()

            sol_id = sol_result.data[0]["id"]

            feasibility = float(sol.get("feasibility_score", 0.5))
            impact = float(sol.get("impact_score", 0.5))
            overall = (feasibility + impact) / 2

            supabase.table("solution_scores").insert({
                "solution_id": sol_id,
                "feasibility_score": feasibility,
                "impact_score": impact,
                "cost_estimate": str(sol.get("cost_estimate", "unknown")),
                "complexity": normalize_complexity(sol.get("complexity", "medium")),
                "time_to_market": str(sol.get("time_to_market", "unknown")),
                "nocode_compatible": bool(sol.get("nocode_compatible", True)),
                "overall_score": overall,
                "notes": str(sol.get("revenue_model", "")),
                "scored_by": "solution_architect",
            }).execute()

            saved += 1
            print(f"      [{overall:.2f}] {sol.get('title')} ({sol.get('sub_sector', '')})")

        except Exception as e:
            print(f"   [ERROR] Salvataggio: {e}")

    best = data.get("best_pick", "")
    if best:
        print(f"      Consiglio: {best[:150]}")

    return saved


def run():
    print("Solution Architect v1.4 avviato...")

    problems = get_approved_problems()

    if not problems:
        print("   Nessun problema approvato. Approva dei problemi dal bot Telegram prima.")
        return

    print(f"   {len(problems)} problemi approvati da elaborare\n")

    total_saved = 0
    for i, problem in enumerate(problems, 1):
        score = problem.get("weighted_score") or problem.get("score", 0)
        sector = problem.get("sector", problem.get("domain", "?"))
        print(f"   [{i}/{len(problems)}] {problem['title']} ({sector}, score: {score})")

        analysis = generate_for_problem(problem)
        if analysis:
            saved = save_solutions(analysis, problem["id"], sector)
            total_saved += saved
        else:
            print("      Generazione fallita, passo al prossimo")

        time.sleep(1)

    print(f"\n   Totale: {total_saved} soluzioni salvate")
    print("Solution Architect v1.4 completato.")


if __name__ == "__main__":
    run()