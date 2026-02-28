"""
brAIn module: intelligence/feasibility.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re, hashlib
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, COMMAND_CENTER_URL, logger
from core.utils import (log_to_supabase, notify_telegram, extract_json, search_perplexity,
                        get_telegram_chat_id, emit_event,
                        get_mirco_preferences, get_sector_preference_modifier,
                        get_pipeline_thresholds, get_scan_strategy, get_scan_schedule_strategy,
                        get_sector_with_fewest_problems, get_last_sector_rotation,
                        get_high_bos_problem_sectors, build_strategy_queries)
from intelligence.architect import (run_solution_architect, research_problem,
    generate_solutions_unconstrained, assess_feasibility, save_solution_v2)
from core.templates import now_rome


FEASIBILITY_ENGINE_PROMPT = """Sei il Feasibility Engine di brAIn. Valuta la fattibilita' tecnica ed economica di una soluzione MVP.

Rispondi SOLO con JSON valido:
{
  "bos_feasibility": {
    "mvp_cost_score": 0.7,
    "time_to_market": 0.8,
    "ai_buildability": 0.9,
    "margin_potential": 0.7,
    "market_access": 0.6,
    "recurring_revenue": 0.8,
    "scalability": 0.7
  },
  "recommendation": {
    "decision": "GO",
    "confidence": 0.8,
    "rationale": "Motivazione concisa in italiano"
  },
  "risks": ["rischio 1", "rischio 2"],
  "opportunities": ["opportunita 1", "opportunita 2"]
}

SCALE 0.0-1.0 per ogni parametro:
- mvp_cost_score: 1.0 = costo <5k EUR, 0.0 = costo >100k EUR
- time_to_market: 1.0 = build <2 settimane, 0.0 = >6 mesi
- ai_buildability: 1.0 = costruibile al 100% con AI e Python, 0.0 = richiede hardware/infrastruttura fisica
- margin_potential: 1.0 = margine >80%, 0.0 = margine <20%
- market_access: 1.0 = canale diretto chiaro (es. cold email, Telegram), 0.0 = richiede partnership/retail
- recurring_revenue: 1.0 = SaaS mensile, 0.0 = vendita una tantum
- scalability: 1.0 = scala automaticamente senza costi fissi, 0.0 = scala linearmente col personale

decision: "GO" se tutti i parametri chiave sono alti, "CONDITIONAL_GO" se ci sono rischi mitigabili, "NO_GO" se non realizzabile nel budget/tempo.
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
    _pipeline_thresholds = get_pipeline_thresholds()

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

        if feasibility_score < _pipeline_thresholds["feasibility"]:
            logger.info(f"[FE] {title[:40]}: feasibility={feasibility_score:.2f} sotto soglia {_pipeline_thresholds['feasibility']}")

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
        week_ago = (now_rome() - timedelta(days=7)).isoformat()
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
                json={"chat_id": chat_id_direct, "text": "\U0001f3af CSO\n" + msg, "reply_markup": bos_reply_markup},
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


