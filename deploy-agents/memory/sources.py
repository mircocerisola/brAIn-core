"""
brAIn module: memory/sources.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import os, json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, search_perplexity
from core.templates import now_rome


def run_targeted_scan(source_name=None, use_top=False, sector=None):
    """
    Scan mirato su una fonte specifica, le top fonti, o un settore.
    Bypassa la rotazione normale del scan_schedule.
    """
    try:
        q = supabase.table("scan_sources").select("*").eq("status", "active")
        if source_name:
            q = q.ilike("name", f"%{source_name}%")
            sources_data = q.execute()
        elif sector:
            # Cerca fonti che coprono quel settore
            q = q.ilike("sectors", f"%{sector}%")
            sources_data = q.order("relevance_score", desc=True).limit(5).execute()
        elif use_top:
            sources_data = q.order("relevance_score", desc=True).limit(3).execute()
        else:
            sources_data = q.order("relevance_score", desc=True).limit(5).execute()
        sources = sources_data.data or []
    except Exception as e:
        logger.error(f"[TARGETED SCAN] Errore fetch fonti: {e}")
        sources = []

    if not sources:
        label = source_name or sector or "top"
        logger.warning(f"[TARGETED SCAN] Nessuna fonte trovata per: {label}")
        return {"status": "no_sources", "saved": 0, "message": f"Nessuna fonte trovata per: {label}"}

    source_names_used = [s["name"] for s in sources]
    logger.info(f"[TARGETED SCAN] Fonti usate: {source_names_used}")

    queries = get_standard_queries(sources)[:4]
    result = run_scan(queries, max_problems=1)

    saved_ids = result.get("saved_ids", [])
    if saved_ids:
        import threading
        threading.Thread(target=run_auto_pipeline, args=(saved_ids,), daemon=True).start()

    return {
        "status": "completed",
        "sources_used": source_names_used,
        "saved": result.get("saved", 0),
        "saved_ids": saved_ids,
    }


# ============================================================
# SOURCE REFRESH
# ============================================================

def run_source_refresh():
    """Aggiorna ranking fonti e cerca nuove fonti."""
    logger.info("Source Refresh starting...")

    try:
        sources = supabase.table("scan_sources").select("*").eq("status", "active").execute()
        sources = sources.data or []
    except:
        sources = []

    updated = 0
    for source in sources:
        last_scanned = source.get("last_scanned")
        problems_found = source.get("problems_found", 0)

        if last_scanned:
            try:
                last_dt = datetime.fromisoformat(last_scanned.replace("Z", "+00:00"))
                days_since = (now_rome() - last_dt).days
            except:
                days_since = 30
        else:
            days_since = 30

        # Penalizza fonti che non producono risultati
        if days_since > 14 and problems_found == 0:
            new_rel = max(0.1, source.get("relevance_score", 0.5) - 0.05)
            try:
                supabase.table("scan_sources").update({
                    "relevance_score": round(new_rel, 4),
                }).eq("id", source["id"]).execute()
                updated += 1
            except:
                pass

    log_to_supabase("source_refresh", "refresh", 1,
        f"{len(sources)} fonti analizzate", f"{updated} aggiornate",
        "none")

    return {"status": "completed", "sources": len(sources), "updated": updated}


# ============================================================
# SOURCES CLEANUP WEEKLY — pulizia fonti con soglie dinamiche
# ============================================================

def run_sources_cleanup_weekly():
    """
    Pulizia fonti settimanale con soglie dinamiche.
    - Archivia il 20% peggiore (per avg_problem_score) tra fonti con 5+ scan
    - Archivia sempre fonti con avg_problem_score < 0.25 dopo 5+ scan
    Eseguita ogni lunedì dal Cloud Scheduler.
    """
    logger.info("Sources cleanup weekly starting...")

    try:
        sources_result = supabase.table("scan_sources").select("*").eq("status", "active").execute()
        all_sources = sources_result.data or []
    except Exception as e:
        logger.error(f"[CLEANUP] Errore fetch fonti: {e}")
        return {"status": "error", "error": str(e)}

    # Fonti qualificate: almeno 5 problemi trovati
    qualified = [s for s in all_sources if (s.get("problems_found") or 0) >= 5]

    archived_sources = []
    dynamic_threshold = None
    absolute_threshold = 0.25

    if qualified:
        # Ordina per avg_problem_score crescente (peggiori prima)
        qualified_sorted = sorted(qualified, key=lambda x: x.get("avg_problem_score") or 0)

        # Calcola soglia: archivia il 20% peggiore
        bottom_count = max(1, int(len(qualified_sorted) * 0.20))
        bottom_sources = qualified_sorted[:bottom_count]

        if bottom_sources:
            dynamic_threshold = bottom_sources[-1].get("avg_problem_score") or 0

        # Archivia: nel 20% peggiore OPPURE sotto soglia assoluta
        for s in qualified_sorted:
            score = s.get("avg_problem_score") or 0
            if s in bottom_sources or score < absolute_threshold:
                try:
                    threshold_used = min(dynamic_threshold or 0, absolute_threshold) if s in bottom_sources else absolute_threshold
                    supabase.table("scan_sources").update({
                        "status": "archived",
                        "notes": f"Archiviata automaticamente {now_rome().strftime('%Y-%m-%d')}: score {score:.2f} (soglia {threshold_used:.2f})",
                    }).eq("id", s["id"]).execute()
                    archived_sources.append({"id": s["id"], "name": s["name"], "score": round(score, 3)})
                    logger.info(f"[CLEANUP] Archiviata: {s['name']} (score {score:.2f})")
                except Exception as e:
                    logger.warning(f"[CLEANUP] Errore archiviazione {s['name']}: {e}")

    # Ricalcola conteggio attive dopo pulizia
    active_count = len(all_sources) - len(archived_sources)

    # Aggiorna source_thresholds
    try:
        supabase.table("source_thresholds").insert({
            "dynamic_threshold": dynamic_threshold,
            "absolute_threshold": absolute_threshold,
            "active_sources_count": active_count,
            "archived_this_week": len(archived_sources),
            "target_active_pct": 0.80,
            "update_reason": "pulizia settimanale automatica",
        }).execute()
    except Exception as e:
        logger.warning(f"[CLEANUP] Errore salvataggio source_thresholds: {e}")

    # Notifiche a Mirco — Fix 3: pulsanti Riattiva/Ok
    SEP = "━━━━━━━━━━━━━━━"
    if archived_sources:
        lines = [f"\U0001f4e6 Pulizia fonti settimanale: {len(archived_sources)} archiviate, soglia: {dynamic_threshold:.2f if dynamic_threshold else 'N/A'}"]
        for a in archived_sources:
            lines.append(f"- {a['name']} (score {a['score']:.2f})")
        src_msg = "\n".join(lines)
        # Pulsanti Riattiva (max 3) + Ok
        src_keyboard_rows = []
        for a in archived_sources[:3]:
            src_keyboard_rows.append([
                {"text": f"\U0001f504 Riattiva: {a['name'][:20]}", "callback_data": f"source_reactivate:{a['id']}"},
            ])
        src_keyboard_rows.append([
            {"text": "\u2705 Ok, capito", "callback_data": "source_archive_ok"},
        ])
        src_reply_markup = {"inline_keyboard": src_keyboard_rows}
        chat_id_src = get_telegram_chat_id()
        if chat_id_src and TELEGRAM_BOT_TOKEN:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id_src, "text": "\u2699\ufe0f COO\n" + src_msg, "reply_markup": src_reply_markup},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"[CLEANUP] notify error: {e}")
                notify_telegram("\u2699\ufe0f COO\n" + src_msg)
        else:
            notify_telegram("\u2699\ufe0f COO\n" + src_msg)

    # Report settimanale soglie
    dt_str = f"{dynamic_threshold:.2f}" if dynamic_threshold is not None else "N/A"
    report = (
        f"📊 AGGIORNAMENTO SOGLIE FONTI\n{SEP}\n"
        f"Fonti attive: {active_count}/{len(all_sources)}\n"
        f"Fonti archiviate questa settimana: {len(archived_sources)}\n"
        f"Soglia dinamica attuale: {dt_str}\n"
        f"Soglia assoluta: {absolute_threshold}\n"
        f"{SEP}"
    )
    notify_telegram("\u2699\ufe0f COO\n" + report)

    log_to_supabase("source_cleanup", "weekly_cleanup", 1,
        f"{len(all_sources)} fonti analizzate",
        f"{len(archived_sources)} archiviate, soglia={dt_str}",
        "none")

    return {
        "status": "completed",
        "total_sources": len(all_sources),
        "archived": len(archived_sources),
        "dynamic_threshold": dynamic_threshold,
        "active_count": active_count,
    }


