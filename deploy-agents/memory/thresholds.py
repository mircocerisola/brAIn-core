"""
brAIn module: memory/thresholds.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, search_perplexity, get_pipeline_thresholds
from core.templates import now_rome


def run_action_queue_cleanup():
    """Pulizia settimanale action_queue: rimuove entry pending > 7 giorni e processed > 30 giorni."""
    logger.info("[QUEUE_CLEANUP] Starting action_queue cleanup...")
    try:
        threshold_7d = (now_rome() - timedelta(days=7)).isoformat()
        threshold_30d = (now_rome() - timedelta(days=30)).isoformat()
        # Marca come expired le pending > 7 giorni
        r1 = supabase.table("action_queue").update({"status": "expired"}) \
            .eq("status", "pending").lt("created_at", threshold_7d).execute()
        expired_count = len(r1.data or [])
        # Elimina le processed/expired > 30 giorni
        r2 = supabase.table("action_queue").delete() \
            .in_("status", ["processed", "expired"]).lt("created_at", threshold_30d).execute()
        deleted_count = len(r2.data or [])
        msg = f"Queue cleanup: {expired_count} scadute, {deleted_count} eliminate"
        logger.info(f"[QUEUE_CLEANUP] {msg}")
        notify_telegram(f"\u2699\ufe0f COO\n🧹 {msg}", "info", "queue_cleanup")
        return {"status": "ok", "expired": expired_count, "deleted": deleted_count}
    except Exception as e:
        logger.error(f"[QUEUE_CLEANUP] Error: {e}")
        return {"status": "error", "error": str(e)}


def run_weekly_threshold_update():
    """Aggiorna le soglie della pipeline in base al bos_approval_rate settimanale.
    Chiamato ogni lunedi alle 08:00 via Cloud Scheduler → /thresholds/weekly.
    Target: bos_approval_rate <= 10%."""
    logger.info("[THRESHOLDS] Weekly update starting...")

    thresholds = get_pipeline_thresholds()
    soglia_bos = thresholds["bos"]

    # BOS calcolati nell'ultima settimana
    week_ago = (now_rome() - timedelta(days=7)).isoformat()
    try:
        result = supabase.table("solutions").select("bos_score") \
            .not_.is_("bos_score", "null").gte("created_at", week_ago).execute()
        scores = [float(s["bos_score"]) for s in (result.data or [])]
    except Exception as e:
        logger.error(f"[THRESHOLDS] DB read error: {e}")
        scores = []

    total = len(scores)
    above_threshold = sum(1 for s in scores if s >= soglia_bos) if scores else 0
    bos_approval_rate = round(above_threshold / total * 100, 1) if total > 0 else 0.0

    # Calcola factor di aggiustamento
    factor = 1.0
    reason = f"bos_approval_rate={bos_approval_rate:.1f}% nel target (<=10%), soglie invariate"

    if bos_approval_rate > 20:
        factor = 1.05
        reason = f"bos_approval_rate={bos_approval_rate:.1f}% > 20%, alzo soglie del 5%"
    elif bos_approval_rate > 10:
        factor = 1.02
        reason = f"bos_approval_rate={bos_approval_rate:.1f}% > 10%, alzo soglie del 2%"
    elif total == 0 or bos_approval_rate == 0.0:
        # Controlla se anche la settimana precedente aveva 0 BOS
        try:
            prev_rows = supabase.table("pipeline_thresholds").select("bos_approval_rate") \
                .order("id", desc=True).limit(2).execute()
            prev_data = prev_rows.data or []
            consecutive_zero = (
                len(prev_data) >= 1 and
                (prev_data[0].get("bos_approval_rate") or 0.0) == 0.0
            )
        except:
            consecutive_zero = False

        if consecutive_zero:
            factor = 0.95
            reason = "bos_approval_rate=0% per 2+ settimane consecutive, abbasso soglie del 5%"
        else:
            reason = "bos_approval_rate=0% (prima settimana senza BOS), soglie invariate in attesa"

    new_problema = round(min(0.95, max(0.30, thresholds["problema"] * factor)), 3)
    new_soluzione = round(min(0.95, max(0.30, thresholds["soluzione"] * factor)), 3)
    new_feasibility = round(min(0.95, max(0.30, thresholds["feasibility"] * factor)), 3)
    new_bos = round(min(0.95, max(0.30, thresholds["bos"] * factor)), 3)

    # Salva nuove soglie in DB
    try:
        supabase.table("pipeline_thresholds").insert({
            "soglia_problema": new_problema,
            "soglia_soluzione": new_soluzione,
            "soglia_feasibility": new_feasibility,
            "soglia_bos": new_bos,
            "bos_approval_rate": bos_approval_rate,
            "update_reason": reason,
        }).execute()
    except Exception as e:
        logger.error(f"[THRESHOLDS] Save error: {e}")

    # Report a Mirco con formato standard
    sep = "\u2501" * 15
    changed = factor != 1.0
    msg = (
        f"AGGIORNAMENTO SOGLIE SETTIMANALE\n"
        f"{sep}\n"
        f"BOS approvati questa settimana: {above_threshold}/{total} ({bos_approval_rate:.1f}%)\n"
        f"Soglie aggiornate:\n"
        f"- Problema: {thresholds['problema']:.2f} \u2192 {new_problema:.2f}" + (" (=" if not changed else "") + "\n"
        f"- Soluzione: {thresholds['soluzione']:.2f} \u2192 {new_soluzione:.2f}\n"
        f"- Feasibility: {thresholds['feasibility']:.2f} \u2192 {new_feasibility:.2f}\n"
        f"- BOS: {thresholds['bos']:.2f} \u2192 {new_bos:.2f}\n"
        f"Motivo: {reason}\n"
        f"{sep}\n"
        f"Vuoi modificare manualmente le soglie?"
    )
    notify_telegram("\U0001f3af CSO\n" + msg, level="info", source="threshold_manager")

    log_to_supabase("threshold_manager", "weekly_update", 0,
        f"bos_rate={bos_approval_rate}% total={total}", reason, "none")

    logger.info(f"[THRESHOLDS] Weekly update done. factor={factor} bos_rate={bos_approval_rate}%")
    return {
        "status": "completed",
        "total_bos": total,
        "above_threshold": above_threshold,
        "bos_approval_rate": bos_approval_rate,
        "new_thresholds": {
            "problema": new_problema,
            "soluzione": new_soluzione,
            "feasibility": new_feasibility,
            "bos": new_bos,
        },
        "factor": factor,
        "reason": reason,
    }


# ============================================================
# IDEA RECYCLER
# ============================================================

