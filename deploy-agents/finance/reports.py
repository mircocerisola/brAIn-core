"""
brAIn module: finance/reports.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re, math
from datetime import datetime, timezone, timedelta
from calendar import monthrange
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, COMMAND_CENTER_URL, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id


def _get_rome_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Rome")
    except Exception:
        return timezone(timedelta(hours=1))


def _format_rome_time(ts_str):
    """Converte timestamp UTC in HH:MM Europe/Rome."""
    if not ts_str:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        rome = dt.astimezone(_get_rome_tz())
        return rome.strftime("%H:%M")
    except Exception:
        return str(ts_str)[:16]


def _make_bar(value, max_value, length=5):
    """Barra proporzionale ▓░ di lunghezza fissa."""
    if max_value <= 0:
        return "░" * length
    filled = max(0, min(length, round(value / max_value * length)))
    return "▓" * filled + "░" * (length - filled)


def _shorten_agent_name(name):
    mapping = {
        "world_scanner": "World Scanner",
        "solution_architect": "Solution Arch.",
        "spec_generator": "Spec Generator",
        "build_agent": "Build Agent",
        "knowledge_keeper": "Knowledge Keeper",
        "command_center": "Command Center",
        "daily_report": "Daily Report",
        "cost_report": "Cost Report",
        "activity_report": "Activity Report",
        "validation_agent": "Validation",
        "capability_scout": "Cap. Scout",
        "bos_calculator": "BOS Calc.",
    }
    return mapping.get(name, name[:18])


def _get_period_cost(since_iso, until_iso=None):
    """Costi in EUR e breakdown per agente per il periodo dato. Ritorna (total_eur, {agent: eur})."""
    usd_to_eur = finance_get_usd_to_eur()
    try:
        q = supabase.table("agent_logs").select("agent_id,cost_usd").gte("created_at", since_iso)
        if until_iso:
            q = q.lte("created_at", until_iso)
        logs = q.execute().data or []
    except Exception as e:
        logger.warning(f"[PERIOD_COST] {e}")
        return 0.0, {}
    by_agent = {}
    total_usd = 0.0
    for l in logs:
        a = l.get("agent_id", "unknown")
        c = float(l.get("cost_usd", 0) or 0)
        total_usd += c
        by_agent[a] = by_agent.get(a, 0.0) + c
    total_eur = round(total_usd * usd_to_eur, 4)
    by_agent_eur = {k: round(v * usd_to_eur, 4) for k, v in by_agent.items()}
    return total_eur, by_agent_eur


def generate_cost_report_v2():
    """Report costi ogni 4h (ore pari Europe/Rome): ultime 4h / oggi / 7g / mese + top spender."""
    logger.info("[REPORT] Generating cost report...")
    now_utc = datetime.now(timezone.utc)
    rome_tz = _get_rome_tz()
    now_rome = now_utc.astimezone(rome_tz)
    data_it = f"{now_rome.day} {MESI_IT_REPORT[now_rome.month]} {now_rome.year} {now_rome.strftime('%H:%M')}"

    since_4h = (now_utc - timedelta(hours=4)).isoformat()
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    since_7d = (now_utc - timedelta(days=7)).isoformat()
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    cost_4h, agents_4h = _get_period_cost(since_4h)
    cost_today, _ = _get_period_cost(today_start)
    cost_7d, _ = _get_period_cost(since_7d)
    cost_month, _ = _get_period_cost(month_start)

    # Spike detection: ultime 4h vs media (7g / 42 periodi da 4h)
    avg_4h = cost_7d / 42 if cost_7d > 0 else 0
    spike_pct = ((cost_4h - avg_4h) / avg_4h * 100) if avg_4h > 0 and cost_4h > avg_4h * 2 else 0

    sorted_agents = sorted(agents_4h.items(), key=lambda x: x[1], reverse=True)
    top4 = sorted_agents[:4]
    altri_cost = sum(v for _, v in sorted_agents[4:])
    display_agents = top4 + ([("Altri", altri_cost)] if altri_cost > 0 else [])
    max_cost = max((v for _, v in display_agents), default=1) or 1

    sep = "\u2501" * 15
    lines = [
        f"\U0001f4b6 *COSTI brAIn* \u2014 {data_it}",
        sep,
        f"\U0001f550 Ultime 4h:   \u20ac{cost_4h:.2f}",
        f"\U0001f4c5 Oggi:        \u20ac{cost_today:.2f}",
        f"\U0001f4c6 7 giorni:    \u20ac{cost_7d:.2f}",
        f"\U0001f5d3 Mese:        \u20ac{cost_month:.2f}",
        sep,
        "Top spender:",
    ]
    for i, (agent, cost) in enumerate(display_agents):
        prefix = "\u2514" if i == len(display_agents) - 1 else "\u251c"
        short = _shorten_agent_name(agent)
        bar = _make_bar(cost, max_cost)
        lines.append(f"{prefix} {short:<18} \u20ac{cost:.2f}  {bar}")

    if spike_pct >= 100:
        lines.append(sep)
        lines.append(f"\u26a0\ufe0f Spike rilevato: +{spike_pct:.0f}%")

    report = "\n".join(lines)
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\U0001f50d Dettaglio ora", "callback_data": "cost_detail_4h"},
            {"text": "\U0001f4ca 7 giorni", "callback_data": "cost_trend_7d"},
        ]]
    }
    chat_id_report = get_telegram_chat_id()
    if chat_id_report and TELEGRAM_BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id_report, "text": report, "reply_markup": reply_markup, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception as e:
            logger.error(f"[COST_REPORT] Telegram error: {e}")
    log_to_supabase("cost_report", "generate", 0, f"Cost report {data_it}", report[:300], "none")
    return {"status": "ok", "type": "cost", "date": data_it, "text": report}


def generate_activity_report_v2():
    """Report attività ogni 4h (ore dispari Europe/Rome): scanner, pipeline, cantieri."""
    logger.info("[REPORT] Generating activity report...")
    now_utc = datetime.now(timezone.utc)
    rome_tz = _get_rome_tz()
    now_rome = now_utc.astimezone(rome_tz)
    data_it = f"{now_rome.day} {MESI_IT_REPORT[now_rome.month]} {now_rome.year} {now_rome.strftime('%H:%M')}"

    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    since_8h = (now_utc - timedelta(hours=8)).isoformat()

    # --- SCANNER ---
    try:
        probs = supabase.table("problems").select("id,weighted_score").gte("created_at", today_start).execute().data or []
        prob_count = len(probs)
        avg_score = sum(float(p.get("weighted_score", 0) or 0) for p in probs) / prob_count if prob_count else 0.0
    except Exception:
        prob_count = 0; avg_score = 0.0
    try:
        last_scan_res = supabase.table("agent_logs").select("created_at").eq("agent_id", "world_scanner").order("created_at", desc=True).limit(1).execute().data or []
        last_scan_str = _format_rome_time(last_scan_res[0]["created_at"]) if last_scan_res else "\u2014"
    except Exception:
        last_scan_str = "\u2014"

    # --- PIPELINE ---
    try:
        bos_today = supabase.table("solutions").select("id,bos_score").gte("created_at", today_start).not_.is_("bos_score", "null").execute().data or []
        bos_count = len(bos_today)
        avg_bos = sum(float(b.get("bos_score", 0) or 0) for b in bos_today) / bos_count if bos_count else 0.0
    except Exception:
        bos_count = 0; avg_bos = 0.0
    try:
        pending_res = supabase.table("action_queue").select("id").eq("action_type", "approve_bos").eq("status", "pending").execute().data or []
        pending_count = len(pending_res)
    except Exception:
        pending_count = 0

    # --- CANTIERI ---
    try:
        cantieri = supabase.table("projects").select("id,name,status,created_at,build_phase").neq("status", "archived").execute().data or []
    except Exception:
        cantieri = []

    # Scanner silenzioso: nessun problema nelle ultime 8h
    try:
        probs_8h = supabase.table("problems").select("id").gte("created_at", since_8h).execute().data or []
        scanner_silent = len(probs_8h) == 0
    except Exception:
        scanner_silent = False

    sep = "\u2501" * 15
    lines = [
        f"\u2699\ufe0f *ATTIVIT\u00c0 brAIn* \u2014 {data_it}",
        sep,
        "\U0001f50d Scanner",
        f"\u251c Problemi trovati oggi:     {prob_count}",
        f"\u251c Score medio:               {avg_score:.2f}",
        f"\u2514 Ultimo scan:               {last_scan_str}",
        "",
        "\U0001f9e0 Pipeline",
        f"\u251c BOS generati oggi:         {bos_count}",
        f"\u251c Score medio BOS:           {avg_bos:.2f}",
        f"\u2514 In attesa approvazione:    {pending_count}",
        "",
        "\U0001f3d7\ufe0f Cantieri",
    ]
    if cantieri:
        first = cantieri[0]
        last_upd = _format_rome_time(first.get("created_at"))
        lines.append(f"\u251c Attivi:                    {len(cantieri)} \u2014 {first.get('name', '?')[:25]}")
        lines.append(f"\u251c Status:                    {first.get('status', '?')}")
        lines.append(f"\u2514 Creato:                    {last_upd}")
    else:
        lines.append("\u2514 Nessun cantiere attivo")

    if scanner_silent:
        lines.append("")
        lines.append("\u26a0\ufe0f Scanner silenzioso \u2014 verifica")

    report = "\n".join(lines)
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\U0001f4cb Problemi", "callback_data": "act_problemi"},
            {"text": "\U0001f3c6 Top BOS", "callback_data": "act_top_bos"},
            {"text": "\U0001f3d7\ufe0f Cantieri", "callback_data": "act_cantieri"},
        ]]
    }
    chat_id_report = get_telegram_chat_id()
    if chat_id_report and TELEGRAM_BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id_report, "text": report, "reply_markup": reply_markup, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception as e:
            logger.error(f"[ACTIVITY_REPORT] Telegram error: {e}")
    log_to_supabase("activity_report", "generate", 0, f"Activity report {data_it}", report[:300], "none")
    return {"status": "ok", "type": "activity", "date": data_it, "text": report}


