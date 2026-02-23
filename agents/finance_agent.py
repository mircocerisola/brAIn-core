"""
brAIn Finance Agent v1.0
METABOLISM — Monitora costi, genera report, alert soglie.

Non usa Claude: le aggregazioni sono puro Python/SQL.
Zero costi API per questo agente.
"""

import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

DEFAULT_THRESHOLDS = {
    "daily_total_usd": 5.0,
    "daily_per_agent_usd": 3.0,
    "weekly_total_usd": 25.0,
    "monthly_budget_eur": 1000,
    "eur_usd_rate": 1.08,
    "cost_per_run_max_usd": 0.5,
}


def _iso(dt):
    """Converte datetime in stringa ISO."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _day_str(dt):
    """Restituisce stringa YYYY-MM-DD."""
    return dt.strftime("%Y-%m-%d")


def _date_str(dt, pattern):
    """Wrapper per strftime."""
    return dt.strftime(pattern)


def get_config():
    thresholds = DEFAULT_THRESHOLDS.copy()
    try:
        result = supabase.table("org_config").select("key,value").execute()
        config = {row["key"]: row["value"] for row in result.data}
        if "budget_monthly_eur" in config:
            thresholds["monthly_budget_eur"] = float(config["budget_monthly_eur"])
        if "max_cost_per_run_usd" in config:
            thresholds["cost_per_run_max_usd"] = float(config["max_cost_per_run_usd"])
    except Exception as e:
        print("[WARN] Impossibile leggere org_config, uso default: " + str(e))
    return thresholds


def get_costs_since(since_dt):
    try:
        result = supabase.table("agent_logs") \
            .select("agent_id,action,cost_usd,tokens_input,tokens_output,status,created_at") \
            .gte("created_at", _iso(since_dt)) \
            .gt("cost_usd", 0) \
            .order("created_at", desc=True) \
            .limit(500) \
            .execute()
        return result.data
    except Exception as e:
        print("[ERROR] Recupero costi fallito: " + str(e))
        return []


def aggregate_by_agent(logs):
    agg = {}
    for log in logs:
        agent = log.get("agent_id", "unknown")
        if agent not in agg:
            agg[agent] = {
                "cost_usd": 0.0,
                "tokens_input": 0,
                "tokens_output": 0,
                "calls": 0,
                "errors": 0,
            }
        agg[agent]["cost_usd"] += float(log.get("cost_usd", 0))
        agg[agent]["tokens_input"] += int(log.get("tokens_input", 0))
        agg[agent]["tokens_output"] += int(log.get("tokens_output", 0))
        agg[agent]["calls"] += 1
        if log.get("status") == "error":
            agg[agent]["errors"] += 1
    return agg


def aggregate_by_day(logs):
    daily = {}
    for log in logs:
        day = log.get("created_at", "")[:10]
        if day not in daily:
            daily[day] = 0.0
        daily[day] += float(log.get("cost_usd", 0))
    return dict(sorted(daily.items()))


def check_alerts(thresholds, daily_costs, agent_costs):
    alerts = []
    now = datetime.now(timezone.utc)
    today = _day_str(now)

    today_cost = daily_costs.get(today, 0.0)
    if today_cost > thresholds["daily_total_usd"]:
        alerts.append(
            "ALERT COSTI: oggi spesi $" + str(round(today_cost, 2))
            + " (soglia $" + str(thresholds["daily_total_usd"]) + ")"
        )

    for agent, data in agent_costs.items():
        if data["cost_usd"] > thresholds["daily_per_agent_usd"]:
            alerts.append(
                "ALERT AGENTE: " + agent + " ha speso $" + str(round(data["cost_usd"], 2))
                + " oggi (soglia $" + str(thresholds["daily_per_agent_usd"]) + ")"
            )

    cutoff_7d = _day_str(now - timedelta(days=7))
    last_7_days = sum(
        cost for day, cost in daily_costs.items() if day >= cutoff_7d
    )
    monthly_projection_usd = (last_7_days / 7) * 30
    monthly_budget_usd = thresholds["monthly_budget_eur"] * thresholds["eur_usd_rate"]

    if monthly_projection_usd > monthly_budget_usd * 0.8:
        pct = round((monthly_projection_usd / monthly_budget_usd) * 100)
        alerts.append(
            "ALERT BUDGET: proiezione mensile $" + str(round(monthly_projection_usd, 2))
            + " = " + str(pct) + "% del budget ($" + str(round(monthly_budget_usd, 2)) + ")"
        )

    return alerts, monthly_projection_usd


def generate_report(days=7):
    thresholds = get_config()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    logs = get_costs_since(since)
    if not logs:
        return {
            "status": "ok",
            "message": "Nessun costo registrato negli ultimi " + str(days) + " giorni.",
            "alerts": [],
        }

    agent_costs = aggregate_by_agent(logs)
    daily_costs = aggregate_by_day(logs)
    total_cost = sum(float(log.get("cost_usd", 0)) for log in logs)
    total_calls = len(logs)

    today = _day_str(now)
    today_logs = [l for l in logs if l.get("created_at", "")[:10] == today]
    today_agent_costs = aggregate_by_agent(today_logs)

    alerts, monthly_projection = check_alerts(thresholds, daily_costs, today_agent_costs)

    lines = []
    lines.append("REPORT COSTI - ultimi " + str(days) + " giorni")
    lines.append("Periodo: " + _date_str(since, "%d/%m") + " - " + _date_str(now, "%d/%m/%Y"))
    lines.append("")
    lines.append("Totale: $" + str(round(total_cost, 2)) + " USD (" + str(total_calls) + " chiamate)")
    lines.append("Media giornaliera: $" + str(round(total_cost / max(days, 1), 2)))
    lines.append("Proiezione mensile: $" + str(round(monthly_projection, 2)))
    monthly_budget_usd = thresholds["monthly_budget_eur"] * thresholds["eur_usd_rate"]
    lines.append("Budget mensile: $" + str(round(monthly_budget_usd, 2)) + " (" + str(int(thresholds["monthly_budget_eur"])) + " EUR)")
    lines.append("")

    lines.append("PER AGENTE:")
    sorted_agents = sorted(agent_costs.items(), key=lambda x: x[1]["cost_usd"], reverse=True)
    for agent, data in sorted_agents:
        pct = round((data["cost_usd"] / total_cost * 100)) if total_cost > 0 else 0
        total_tokens = data["tokens_input"] + data["tokens_output"]
        line = "  " + agent + ": $" + str(round(data["cost_usd"], 2)) + " (" + str(pct) + "%) - " + str(data["calls"]) + " chiamate - " + str(total_tokens) + " token"
        lines.append(line)
        if data["errors"] > 0:
            lines.append("    (" + str(data["errors"]) + " errori)")

    lines.append("")
    lines.append("PER GIORNO:")
    for day, cost in daily_costs.items():
        lines.append("  " + day + ": $" + str(round(cost, 2)))

    if alerts:
        lines.append("")
        for alert in alerts:
            lines.append("!! " + alert)

    report_text = "\n".join(lines)

    try:
        supabase.table("agent_logs").insert({
            "agent_id": "finance_agent",
            "action": "generate_report",
            "layer": 6,
            "input_summary": "Report " + str(days) + " giorni",
            "output_summary": "Totale $" + str(round(total_cost, 2)) + ", " + str(len(alerts)) + " alert",
            "model_used": "none",
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_usd": 0,
            "duration_ms": 0,
            "status": "success",
        }).execute()
    except Exception as e:
        print("[WARN] Log fallito: " + str(e))

    return {
        "status": "ok",
        "report_text": report_text,
        "total_cost_usd": round(total_cost, 4),
        "monthly_projection_usd": round(monthly_projection, 2),
        "alerts": alerts,
        "agent_breakdown": agent_costs,
        "daily_breakdown": daily_costs,
    }


def check_run_cost(agent_id, cost_usd):
    thresholds = get_config()
    max_cost = thresholds["cost_per_run_max_usd"]
    if cost_usd > max_cost:
        return {
            "allowed": False,
            "message": agent_id + " ha speso $" + str(round(cost_usd, 4)) + " in una run (max $" + str(max_cost) + ")",
        }
    return {"allowed": True}


def run():
    print("Finance Agent avviato...")
    result = generate_report(days=7)

    if result.get("report_text"):
        print(result["report_text"])
    else:
        print(result.get("message", "Nessun dato."))

    if result.get("alerts"):
        print("\n" + str(len(result["alerts"])) + " ALERT attivi.")

    print("\nFinance Agent completato.")
    return result


if __name__ == "__main__":
    run()
