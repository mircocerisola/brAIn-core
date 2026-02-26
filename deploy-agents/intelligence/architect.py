"""
brAIn module: intelligence/architect.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re, hashlib
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import (log_to_supabase, notify_telegram, extract_json, search_perplexity,
                        get_telegram_chat_id, emit_event,
                        get_mirco_preferences, get_sector_preference_modifier,
                        get_pipeline_thresholds, get_scan_strategy, get_scan_schedule_strategy,
                        get_sector_with_fewest_problems, get_last_sector_rotation,
                        get_high_bos_problem_sectors, build_strategy_queries)
from intelligence.scanner import RESEARCH_PROMPT, GENERATION_PROMPT, SA_FEASIBILITY_PROMPT


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
            model="claude-sonnet-4-6",
            max_tokens=4000,
            system=gen_prompt,
            messages=[{"role": "user", "content": f"{problem_context}\n\nDOSSIER COMPETITIVO:\n{dossier_text}\n\nGenera 3 soluzioni. SOLO JSON."}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("solution_architect", "generate_unconstrained", 2,
            f"Soluzioni per: {problem['title'][:100]}", reply[:500],
            "claude-sonnet-4-6",
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
                _sa_thresholds = get_pipeline_thresholds()
                if overall < _sa_thresholds["soluzione"]:
                    logger.info(f"[SA] {title[:40]}: overall={overall:.2f} sotto soglia {_sa_thresholds['soluzione']}, salvata ma non prioritaria")
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


