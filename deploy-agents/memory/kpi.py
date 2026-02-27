"""
brAIn module: memory/kpi.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, search_perplexity
from core.templates import now_rome


def update_kpi_daily():
    """Aggiorna kpi_daily per oggi. Chiamare a mezzanotte via Cloud Scheduler → /kpi/update."""
    now_utc = now_rome()
    today = now_utc.strftime("%Y-%m-%d")
    today_start = f"{today}T00:00:00+00:00"

    try:
        prob_res = supabase.table("problems").select("id,weighted_score").gte("created_at", today_start).execute().data or []
        problems_found = len(prob_res)
        avg_problem_score = sum(float(p.get("weighted_score", 0) or 0) for p in prob_res) / problems_found if problems_found else 0.0
    except Exception:
        problems_found = 0; avg_problem_score = 0.0
    try:
        bos_res = supabase.table("solutions").select("id,bos_score").gte("created_at", today_start).not_.is_("bos_score", "null").execute().data or []
        bos_generated = len(bos_res)
        avg_bos_score = sum(float(b.get("bos_score", 0) or 0) for b in bos_res) / bos_generated if bos_generated else 0.0
    except Exception:
        bos_generated = 0; avg_bos_score = 0.0
    try:
        active_cantieri = len(supabase.table("projects").select("id").neq("status", "archived").execute().data or [])
    except Exception:
        active_cantieri = 0
    try:
        mvps_launched = len(supabase.table("projects").select("id").eq("status", "launch_approved").gte("created_at", today_start).execute().data or [])
    except Exception:
        mvps_launched = 0
    cost_today, _ = _get_period_cost(today_start)
    try:
        api_calls = supabase.table("agent_logs").select("id", count="exact").gte("created_at", today_start).execute().count or 0
    except Exception:
        api_calls = 0
    try:
        supabase.table("kpi_daily").upsert({
            "date": today,
            "problems_found": problems_found,
            "avg_problem_score": round(avg_problem_score, 4),
            "bos_generated": bos_generated,
            "avg_bos_score": round(avg_bos_score, 4),
            "mvps_launched": mvps_launched,
            "active_cantieri": active_cantieri,
            "total_cost_eur": round(cost_today, 4),
            "api_calls": api_calls,
        }, on_conflict="date").execute()
        logger.info(f"[KPI] kpi_daily aggiornata per {today}")
    except Exception as e:
        logger.error(f"[KPI] Upsert fallito: {e}")
    return {"status": "ok", "date": today}


# ============================================================
# PARTE 1: EVENT PROCESSOR — cascade completa
# ============================================================

