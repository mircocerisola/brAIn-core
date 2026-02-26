"""
brAIn module: intelligence/pipeline.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re, hashlib
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import (log_to_supabase, notify_telegram, extract_json, search_perplexity,
                        get_telegram_chat_id, emit_event, get_pending_events, mark_event_done,
                        get_mirco_preferences, get_sector_preference_modifier,
                        get_pipeline_thresholds, get_scan_strategy, get_scan_schedule_strategy,
                        get_sector_with_fewest_problems, get_last_sector_rotation,
                        get_high_bos_problem_sectors, build_strategy_queries, MIN_SCORE_THRESHOLD)
from intelligence.architect import run_solution_architect
from intelligence.feasibility import run_feasibility_engine, run_bos_endpoint_logic, enqueue_bos_action
from memory.knowledge import run_knowledge_keeper


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
                # Trigger solution generation per problemi con score >= soglia_problema
                problem_ids = payload.get("problem_ids", [])
                for pid in problem_ids:
                    try:
                        prob = supabase.table("problems").select("weighted_score").eq("id", pid).execute()
                        if prob.data and float(prob.data[0].get("weighted_score", 0) or 0) >= MIN_SCORE_THRESHOLD:
                            emit_event("event_processor", "problem_ready", "solution_architect",
                                {"problem_id": str(pid)})
                    except:
                        pass
                mark_event_done(event["id"])

            elif event_type == "problems_found":
                # Notifica pura informativa — pipeline continua in autonomia
                problem_ids = payload.get("problem_ids", [])
                count = payload.get("count", len(problem_ids))
                notify_telegram(f"Scanner: trovati {count} nuovi problemi sopra soglia. Pipeline in elaborazione automatica.")
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

                # Solo AUTO-GO viene processato. REVIEW eliminato — la pipeline
                # decide direttamente tramite enqueue_bos_action se BOS >= soglia_bos.
                if verdict == "AUTO-GO":
                    emit_event("event_processor", "auto_go", "project_builder",
                        {"solution_id": solution_id, "bos": bos_score}, "high")
                # ARCHIVE: nessuna azione

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
                # Tipo obsoleto — la pipeline v2 usa solo approve_bos action.
                # Gestiamo silenziosamente per compatibilità con eventi esistenti in DB.
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
# THRESHOLD MANAGER — Aggiornamento settimanale soglie dinamiche
# ============================================================

