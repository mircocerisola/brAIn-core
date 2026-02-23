"""
brAIn Finance Agent v1.0
METABOLISM — Monitora costi, burn rate, proiezioni mensili, alert budget.
Legge da agent_logs, salva in finance_metrics, notifica via Telegram.
"""

import os
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
import requests

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Costanti
MONTHLY_BUDGET_EUR = 1000.0
DEFAULT_USD_TO_EUR = 0.92
DAILY_COST_ALERT_USD = 5.0
BUDGET_ALERT_PCT = 0.70  # 70%


def get_usd_to_eur_rate():
    """Legge tasso USD->EUR da org_config, fallback a default."""
    try:
        result = supabase.table("org_config").select("value").eq("key", "usd_to_eur_rate").execute()
        if result.data:
            return float(json.loads(result.data[0]["value"]))
    except Exception:
        pass
    return DEFAULT_USD_TO_EUR


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


def get_daily_costs(date_str):
    """Aggrega costi da agent_logs per un giorno specifico (YYYY-MM-DD)."""
    day_start = f"{date_str}T00:00:00+00:00"
    day_end = f"{date_str}T23:59:59+00:00"

    try:
        result = supabase.table("agent_logs") \
            .select("agent_id, cost_usd, tokens_input, tokens_output, status") \
            .gte("created_at", day_start) \
            .lte("created_at", day_end) \
            .execute()
        logs = result.data or []
    except Exception as e:
        print(f"[ERROR] Lettura agent_logs: {e}")
        return None

    if not logs:
        return {
            "date": date_str,
            "total_cost_usd": 0.0,
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "cost_by_agent": {},
            "calls_by_agent": {},
        }

    total_cost = 0.0
    total_calls = 0
    successful = 0
    failed = 0
    tokens_in = 0
    tokens_out = 0
    cost_by_agent = {}
    calls_by_agent = {}

    for log in logs:
        agent = log.get("agent_id", "unknown")
        cost = float(log.get("cost_usd", 0) or 0)
        total_cost += cost
        total_calls += 1
        tokens_in += int(log.get("tokens_input", 0) or 0)
        tokens_out += int(log.get("tokens_output", 0) or 0)

        if log.get("status") == "success":
            successful += 1
        else:
            failed += 1

        cost_by_agent[agent] = cost_by_agent.get(agent, 0.0) + cost
        calls_by_agent[agent] = calls_by_agent.get(agent, 0) + 1

    # Arrotonda costi per agente
    cost_by_agent = {k: round(v, 6) for k, v in cost_by_agent.items()}

    return {
        "date": date_str,
        "total_cost_usd": round(total_cost, 6),
        "total_calls": total_calls,
        "successful_calls": successful,
        "failed_calls": failed,
        "total_tokens_in": tokens_in,
        "total_tokens_out": tokens_out,
        "cost_by_agent": cost_by_agent,
        "calls_by_agent": calls_by_agent,
    }


def get_month_costs(year, month):
    """Costi totali del mese corrente fino ad oggi."""
    first_day = f"{year}-{month:02d}-01"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        result = supabase.table("agent_logs") \
            .select("cost_usd") \
            .gte("created_at", f"{first_day}T00:00:00+00:00") \
            .lte("created_at", f"{today}T23:59:59+00:00") \
            .execute()
        logs = result.data or []
    except Exception as e:
        print(f"[ERROR] Lettura costi mese: {e}")
        return 0.0

    return sum(float(l.get("cost_usd", 0) or 0) for l in logs)


def calculate_projection(month_cost_usd, days_elapsed, days_in_month):
    """Proiezione lineare del costo mensile."""
    if days_elapsed <= 0:
        return 0.0
    daily_avg = month_cost_usd / days_elapsed
    return round(daily_avg * days_in_month, 4)


def save_metrics(daily_data, projection_usd, projection_eur, budget_pct, alerts):
    """Salva metriche giornaliere in finance_metrics."""
    try:
        supabase.table("finance_metrics").insert({
            "report_date": daily_data["date"],
            "total_cost_usd": daily_data["total_cost_usd"],
            "total_cost_eur": round(daily_data["total_cost_usd"] * get_usd_to_eur_rate(), 4),
            "cost_by_agent": json.dumps(daily_data["cost_by_agent"]),
            "calls_by_agent": json.dumps(daily_data["calls_by_agent"]),
            "total_api_calls": daily_data["total_calls"],
            "successful_calls": daily_data["successful_calls"],
            "failed_calls": daily_data["failed_calls"],
            "total_tokens_in": daily_data["total_tokens_in"],
            "total_tokens_out": daily_data["total_tokens_out"],
            "burn_rate_daily_usd": daily_data["total_cost_usd"],
            "projected_monthly_usd": projection_usd,
            "projected_monthly_eur": projection_eur,
            "budget_eur": MONTHLY_BUDGET_EUR,
            "budget_usage_pct": budget_pct,
            "alerts_triggered": json.dumps(alerts),
        }).execute()
        return True
    except Exception as e:
        # Se esiste gia' il record per oggi, aggiorna
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            try:
                supabase.table("finance_metrics").update({
                    "total_cost_usd": daily_data["total_cost_usd"],
                    "total_cost_eur": round(daily_data["total_cost_usd"] * get_usd_to_eur_rate(), 4),
                    "cost_by_agent": json.dumps(daily_data["cost_by_agent"]),
                    "calls_by_agent": json.dumps(daily_data["calls_by_agent"]),
                    "total_api_calls": daily_data["total_calls"],
                    "successful_calls": daily_data["successful_calls"],
                    "failed_calls": daily_data["failed_calls"],
                    "total_tokens_in": daily_data["total_tokens_in"],
                    "total_tokens_out": daily_data["total_tokens_out"],
                    "burn_rate_daily_usd": daily_data["total_cost_usd"],
                    "projected_monthly_usd": projection_usd,
                    "projected_monthly_eur": projection_eur,
                    "budget_eur": MONTHLY_BUDGET_EUR,
                    "budget_usage_pct": budget_pct,
                    "alerts_triggered": json.dumps(alerts),
                }).eq("report_date", daily_data["date"]).execute()
                return True
            except Exception as e2:
                print(f"[ERROR] Update finance_metrics: {e2}")
        else:
            print(f"[ERROR] Salvataggio finance_metrics: {e}")
        return False


def build_daily_report(daily_data, month_total_usd, projection_usd, projection_eur, budget_pct, usd_to_eur):
    """Costruisce il messaggio del report giornaliero."""
    date = daily_data["date"]
    cost_usd = daily_data["total_cost_usd"]
    cost_eur = round(cost_usd * usd_to_eur, 4)
    month_eur = round(month_total_usd * usd_to_eur, 2)

    lines = [
        f"REPORT COSTI {date}",
        "",
        f"Oggi: ${cost_usd:.4f} ({cost_eur:.4f} EUR)",
        f"Chiamate API: {daily_data['total_calls']} ({daily_data['successful_calls']} ok, {daily_data['failed_calls']} errori)",
        f"Token: {daily_data['total_tokens_in']:,} in / {daily_data['total_tokens_out']:,} out",
        "",
    ]

    # Dettaglio per agente
    if daily_data["cost_by_agent"]:
        lines.append("Per agente:")
        sorted_agents = sorted(daily_data["cost_by_agent"].items(), key=lambda x: x[1], reverse=True)
        for agent, agent_cost in sorted_agents:
            calls = daily_data["calls_by_agent"].get(agent, 0)
            lines.append(f"  {agent}: ${agent_cost:.4f} ({calls} chiamate)")
        lines.append("")

    lines.extend([
        f"Mese corrente: ${month_total_usd:.4f} ({month_eur:.2f} EUR)",
        f"Proiezione fine mese: ${projection_usd:.2f} ({projection_eur:.2f} EUR)",
        f"Budget: {MONTHLY_BUDGET_EUR:.0f} EUR | Uso: {budget_pct:.1f}%",
    ])

    # Barra visuale budget
    filled = int(budget_pct / 5)
    bar = "[" + "#" * min(filled, 20) + "." * max(0, 20 - filled) + "]"
    lines.append(bar)

    return "\n".join(lines)


def check_alerts(daily_cost_usd, projection_eur, budget_pct):
    """Controlla soglie e genera alert."""
    alerts = []

    if daily_cost_usd > DAILY_COST_ALERT_USD:
        alerts.append({
            "type": "daily_cost_high",
            "message": f"ALERT: costo giornaliero ${daily_cost_usd:.4f} supera soglia ${DAILY_COST_ALERT_USD}",
            "severity": "high",
        })

    if budget_pct > BUDGET_ALERT_PCT * 100:
        alerts.append({
            "type": "budget_projection_high",
            "message": f"ALERT: proiezione {projection_eur:.2f} EUR supera {BUDGET_ALERT_PCT*100:.0f}% del budget ({MONTHLY_BUDGET_EUR:.0f} EUR)",
            "severity": "high",
        })

    if budget_pct > 90:
        alerts.append({
            "type": "budget_critical",
            "message": f"CRITICO: proiezione al {budget_pct:.1f}% del budget! Rischio sforamento.",
            "severity": "critical",
        })

    return alerts


def run(target_date=None):
    """Esegue il Finance Agent. Se target_date=None, usa ieri."""
    print("Finance Agent v1.0 avviato...")

    now = datetime.now(timezone.utc)

    if target_date:
        date_str = target_date
    else:
        yesterday = now - timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")

    usd_to_eur = get_usd_to_eur_rate()

    # 1. Aggrega costi giornalieri
    daily_data = get_daily_costs(date_str)
    if daily_data is None:
        print("   [ERROR] Impossibile leggere agent_logs")
        return {"status": "error", "error": "agent_logs read failed"}

    print(f"   Costi {date_str}: ${daily_data['total_cost_usd']:.4f} ({daily_data['total_calls']} chiamate)")

    # 2. Costi mese corrente + proiezione
    year = now.year
    month = now.month
    month_total_usd = get_month_costs(year, month)

    days_elapsed = now.day
    if month in (1, 3, 5, 7, 8, 10, 12):
        days_in_month = 31
    elif month == 2:
        days_in_month = 29 if year % 4 == 0 else 28
    else:
        days_in_month = 30

    projection_usd = calculate_projection(month_total_usd, days_elapsed, days_in_month)
    projection_eur = round(projection_usd * usd_to_eur, 4)
    budget_pct = round((projection_eur / MONTHLY_BUDGET_EUR) * 100, 2) if MONTHLY_BUDGET_EUR > 0 else 0

    print(f"   Mese: ${month_total_usd:.4f} | Proiezione: ${projection_usd:.2f} ({projection_eur:.2f} EUR)")
    print(f"   Budget usage: {budget_pct:.1f}%")

    # 3. Check alert
    alerts = check_alerts(daily_data["total_cost_usd"], projection_eur, budget_pct)

    # 4. Salva metriche
    saved = save_metrics(daily_data, projection_usd, projection_eur, budget_pct, alerts)
    print(f"   Metriche salvate: {saved}")

    # 5. Report giornaliero via Telegram
    report = build_daily_report(daily_data, month_total_usd, projection_usd, projection_eur, budget_pct, usd_to_eur)
    notify_telegram(report)
    print("   Report inviato via Telegram")

    # 6. Alert separati (se ci sono)
    for alert in alerts:
        alert_msg = f"{'CRITICO' if alert['severity'] == 'critical' else 'ALERT'} METABOLISM\n\n{alert['message']}"
        notify_telegram(alert_msg)
        print(f"   Alert inviato: {alert['type']}")

    print("Finance Agent v1.0 completato.")
    return {
        "status": "completed",
        "date": date_str,
        "daily_cost_usd": daily_data["total_cost_usd"],
        "month_total_usd": month_total_usd,
        "projection_eur": projection_eur,
        "budget_pct": budget_pct,
        "alerts": len(alerts),
    }


if __name__ == "__main__":
    run()
