"""
brAIn module: memory/recycler.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, search_perplexity
from core.templates import now_rome


def run_idea_recycler():
    """Rivaluta problemi e soluzioni archiviate."""
    logger.info("Idea Recycler starting...")

    try:
        archived = supabase.table("problems").select("id, title, sector, weighted_score, created_at") \
            .eq("status_detail", "archived").order("weighted_score", desc=True).limit(10).execute()
        archived = archived.data or []
    except:
        archived = []

    if not archived:
        return {"status": "no_archived", "recycled": 0}

    recycled = 0
    for problem in archived:
        age_days = (now_rome() - datetime.fromisoformat(problem["created_at"].replace("Z", "+00:00"))).days
        if age_days < 14:
            continue

        title = problem["title"]
        sector = problem.get("sector", "")
        result = search_perplexity(f"{title} new developments changes 2026")

        if result and ("growing" in result.lower() or "increasing" in result.lower() or "new" in result.lower()):
            try:
                supabase.table("reevaluation_log").insert({
                    "problem_id": problem["id"],
                    "reason": "Periodic recycler - potential relevance change",
                    "new_data": result[:500],
                }).execute()

                emit_event("idea_recycler", "problem_may_be_relevant", "command_center",
                    {"problem_id": str(problem["id"]), "title": title})

                recycled += 1
            except:
                pass
        time.sleep(1)

    log_to_supabase("idea_recycler", "recycle", 5,
        f"Rivalutati {len(archived)} problemi", f"{recycled} potenzialmente rilevanti",
        "none")

    return {"status": "completed", "recycled": recycled}


# ============================================================
# TARGETED SCAN — scansione mirata su fonte/settore specifico
# ============================================================

