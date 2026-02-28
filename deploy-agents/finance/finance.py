"""
brAIn module: finance/finance.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re, math
from datetime import datetime, timezone, timedelta
from calendar import monthrange
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, COMMAND_CENTER_URL, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id
from core.templates import now_rome

MONTHLY_BUDGET_EUR = 1000.0
DEFAULT_USD_TO_EUR = 0.92
DAILY_COST_ALERT_USD = 5.0
BUDGET_ALERT_PCT = 70.0
FIXED_COSTS_MONTHLY_EUR = {
    "claude_max": 100.0,
    "supabase": 25.0,
    "perplexity": 15.0,
}
FIXED_COSTS_TOTAL_EUR = sum(FIXED_COSTS_MONTHLY_EUR.values())
FIXED_COSTS_DAILY_EUR = round(FIXED_COSTS_TOTAL_EUR / 30, 2)


def finance_get_usd_to_eur():
    """Ritorna tasso USD→EUR. Cache mensile in exchange_rates. Fallback 0.92."""
    try:
        # Controlla cache in exchange_rates (valida 30 giorni)
        result = supabase.table("exchange_rates").select("rate,fetched_at").eq(
            "from_currency", "USD").eq("to_currency", "EUR").order(
            "fetched_at", desc=True).limit(1).execute()
        if result.data:
            fetched_at = result.data[0]["fetched_at"]
            # Parsifica e controlla se < 30 giorni
            if isinstance(fetched_at, str):
                fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            else:
                fetched_dt = fetched_at
            age_days = (now_rome() - fetched_dt).days
            if age_days < 30:
                return float(result.data[0]["rate"])
    except Exception as e:
        logger.warning(f"[FINANCE] Lettura exchange_rates fallita: {e}")

    # Fetch da frankfurter.app
    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=EUR",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            rate = float(data["rates"]["EUR"])
            # Salva in exchange_rates
            try:
                supabase.table("exchange_rates").insert({
                    "from_currency": "USD",
                    "to_currency": "EUR",
                    "rate": rate,
                }).execute()
            except Exception as save_err:
                logger.warning(f"[FINANCE] Salvataggio exchange_rates fallito: {save_err}")
            logger.info(f"[FINANCE] Tasso USD→EUR aggiornato: {rate}")
            return rate
    except Exception as e:
        logger.warning(f"[FINANCE] Fetch frankfurter.app fallito: {e}")

    return DEFAULT_USD_TO_EUR


def _paginated_logs(select, filters_fn=None, limit_per_page=1000):
    """Fetch all agent_logs with pagination (bypasses 1000-row limit)."""
    all_data = []
    offset = 0
    while True:
        q = supabase.table("agent_logs").select(select)
        if filters_fn:
            q = filters_fn(q)
        result = q.range(offset, offset + limit_per_page - 1).execute()
        if not result.data:
            break
        all_data.extend(result.data)
        if len(result.data) < limit_per_page:
            break
        offset += limit_per_page
    return all_data


def finance_get_daily_costs(date_str):
    """Costi aggregati per un singolo giorno."""
    day_start = f"{date_str}T00:00:00+00:00"
    day_end = f"{date_str}T23:59:59+00:00"
    try:
        logs = _paginated_logs(
            "agent_id,action,cost_usd,tokens_input,tokens_output,model_used,status",
            lambda q: q.gte("created_at", day_start).lte("created_at", day_end),
        )
    except Exception as e:
        logger.error(f"[FINANCE] {e}")
        return None

    total_cost = 0.0
    total_calls = 0
    successful = 0
    failed = 0
    tokens_in = 0
    tokens_out = 0
    cost_by_agent = {}
    calls_by_agent = {}
    cost_by_action = {}
    cost_by_model = {}

    for log in logs:
        agent = log.get("agent_id", "unknown")
        action = log.get("action", "unknown")
        model = log.get("model_used", "unknown")
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
        cost_by_action[action] = cost_by_action.get(action, 0.0) + cost
        cost_by_model[model] = cost_by_model.get(model, 0.0) + cost

    return {
        "date": date_str,
        "total_cost_usd": round(total_cost, 6),
        "total_calls": total_calls,
        "successful_calls": successful,
        "failed_calls": failed,
        "total_tokens_in": tokens_in,
        "total_tokens_out": tokens_out,
        "cost_by_agent": {k: round(v, 6) for k, v in cost_by_agent.items()},
        "calls_by_agent": calls_by_agent,
        "cost_by_action": {k: round(v, 6) for k, v in cost_by_action.items()},
        "cost_by_model": {k: round(v, 6) for k, v in cost_by_model.items()},
    }


def finance_get_range_costs(start_date, end_date):
    """Costi totali per un range di date. Ritorna dict come finance_get_daily_costs."""
    try:
        logs = _paginated_logs(
            "agent_id,action,cost_usd,tokens_input,tokens_output,model_used,status",
            lambda q: q.gte("created_at", f"{start_date}T00:00:00+00:00")
                       .lte("created_at", f"{end_date}T23:59:59+00:00"),
        )
    except:
        return None
    total_cost = sum(float(l.get("cost_usd", 0) or 0) for l in logs)
    cost_by_agent = {}
    for l in logs:
        a = l.get("agent_id", "unknown")
        cost_by_agent[a] = cost_by_agent.get(a, 0.0) + float(l.get("cost_usd", 0) or 0)
    return {
        "total_cost_usd": round(total_cost, 6),
        "total_calls": len(logs),
        "cost_by_agent": {k: round(v, 6) for k, v in cost_by_agent.items()},
    }


def finance_get_all_time_costs():
    """Costi totali DA SEMPRE. Usa agent_logs con paginazione."""
    try:
        logs = _paginated_logs("cost_usd")
    except:
        return 0.0
    return round(sum(float(l.get("cost_usd", 0) or 0) for l in logs), 4)


def finance_get_daily_series(days):
    """Array di costi giornalieri per gli ultimi N giorni. Ritorna [(date_str, cost_usd), ...]."""
    now = now_rome()
    series = []
    for i in range(days, 0, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        costs = finance_get_daily_costs(d)
        series.append((d, costs["total_cost_usd"] if costs else 0.0))
    return series


def finance_get_month_costs(year, month):
    """Costi variabili totali del mese (USD)."""
    first_day = f"{year}-{month:02d}-01"
    _, last = monthrange(year, month)
    last_day = f"{year}-{month:02d}-{last}"
    today = now_rome().strftime("%Y-%m-%d")
    end = min(last_day, today)
    data = finance_get_range_costs(first_day, end)
    return data["total_cost_usd"] if data else 0.0


# ---------- CASH FLOW INTELLIGENCE ----------

def finance_burn_rates():
    """Burn rate: oggi, media 7gg, media 30gg (tutto in USD variabili)."""
    now = now_rome()
    today = now.strftime("%Y-%m-%d")
    today_costs = finance_get_daily_costs(today)
    today_usd = today_costs["total_cost_usd"] if today_costs else 0.0

    # Media 7 giorni
    start_7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    data_7 = finance_get_range_costs(start_7, today)
    avg_7 = round(data_7["total_cost_usd"] / 7, 4) if data_7 else 0.0

    # Media 30 giorni
    start_30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    data_30 = finance_get_range_costs(start_30, today)
    avg_30 = round(data_30["total_cost_usd"] / 30, 4) if data_30 else 0.0

    return {"today_usd": today_usd, "avg_7d_usd": avg_7, "avg_30d_usd": avg_30}


def finance_projections(usd_to_eur):
    """Proiezione costi variabili a 30, 60, 90 giorni (EUR) basata su media 7gg."""
    rates = finance_burn_rates()
    daily = rates["avg_7d_usd"] if rates["avg_7d_usd"] > 0 else rates["avg_30d_usd"]
    daily_eur = daily * usd_to_eur
    return {
        "30d_eur": round(daily_eur * 30 + FIXED_COSTS_TOTAL_EUR, 2),
        "60d_eur": round(daily_eur * 60 + FIXED_COSTS_TOTAL_EUR * 2, 2),
        "90d_eur": round(daily_eur * 90 + FIXED_COSTS_TOTAL_EUR * 3, 2),
        "daily_variable_eur": round(daily_eur, 4),
        "daily_total_eur": round(daily_eur + FIXED_COSTS_DAILY_EUR, 4),
        "burn_rates": rates,
    }


def finance_runway(usd_to_eur):
    """Giorni di runway rimanenti con budget mensile 1000 EUR."""
    now = now_rome()
    year, month = now.year, now.month
    _, days_in_month = monthrange(year, month)
    days_elapsed = now.day

    month_variable_usd = finance_get_month_costs(year, month)
    month_variable_eur = month_variable_usd * usd_to_eur
    # Costi fissi proporzionali ai giorni trascorsi
    month_fixed_eur = FIXED_COSTS_DAILY_EUR * days_elapsed
    month_total_eur = month_variable_eur + month_fixed_eur

    budget_remaining = MONTHLY_BUDGET_EUR - month_total_eur
    daily_total_eur = (month_total_eur / days_elapsed) if days_elapsed > 0 else 0

    if daily_total_eur > 0:
        runway_days = int(budget_remaining / daily_total_eur)
    else:
        runway_days = days_in_month - days_elapsed

    # Proiezione fine mese
    projected_month_eur = round(daily_total_eur * days_in_month, 2) if days_elapsed > 0 else 0
    budget_pct = round((projected_month_eur / MONTHLY_BUDGET_EUR) * 100, 1) if MONTHLY_BUDGET_EUR > 0 else 0

    return {
        "days_remaining": max(runway_days, 0),
        "budget_remaining_eur": round(budget_remaining, 2),
        "month_spent_eur": round(month_total_eur, 2),
        "month_variable_eur": round(month_variable_eur, 2),
        "month_fixed_eur": round(month_fixed_eur, 2),
        "projected_month_eur": projected_month_eur,
        "budget_pct": budget_pct,
        "daily_total_eur": round(daily_total_eur, 4),
    }


# ---------- COST PER VALUE ----------

def finance_cost_per_value(days=30):
    """Costo per problema trovato, soluzione generata, BOS calcolato."""
    now = now_rome()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # Costi per agente nel periodo
    range_data = finance_get_range_costs(since, now.strftime("%Y-%m-%d"))
    agent_costs = range_data["cost_by_agent"] if range_data else {}

    scanner_cost = agent_costs.get("world_scanner", 0)
    sa_cost = agent_costs.get("solution_architect", 0)
    fe_cost = agent_costs.get("feasibility_engine", 0)
    bos_cost = agent_costs.get("bos_scorer", 0)

    # Conteggi valore prodotto
    since_ts = f"{since}T00:00:00+00:00"
    try:
        problems = supabase.table("problems").select("id", count="exact").gte("created_at", since_ts).execute()
        n_problems = problems.count or 0
    except:
        n_problems = 0
    try:
        solutions = supabase.table("solutions").select("id", count="exact").gte("created_at", since_ts).execute()
        n_solutions = solutions.count or 0
    except:
        n_solutions = 0
    try:
        bos_done = supabase.table("solutions").select("id", count="exact") \
            .gte("created_at", since_ts).not_.is_("bos_score", "null").execute()
        n_bos = bos_done.count or 0
    except:
        n_bos = 0

    cost_per_problem = round(scanner_cost / n_problems, 4) if n_problems > 0 else 0
    cost_per_solution = round((sa_cost + fe_cost) / n_solutions, 4) if n_solutions > 0 else 0
    cost_per_bos = round(bos_cost / n_bos, 4) if n_bos > 0 else 0
    total_value_cost = scanner_cost + sa_cost + fe_cost + bos_cost

    # Efficienza per agente: valore prodotto vs costo
    efficiency = {}
    if scanner_cost > 0 and n_problems > 0:
        efficiency["world_scanner"] = {"output": f"{n_problems} problemi", "cost_usd": round(scanner_cost, 4),
                                        "unit_cost": cost_per_problem}
    if sa_cost > 0 and n_solutions > 0:
        efficiency["solution_architect"] = {"output": f"{n_solutions} soluzioni", "cost_usd": round(sa_cost, 4),
                                             "unit_cost": cost_per_solution}
    if bos_cost > 0 and n_bos > 0:
        efficiency["bos_scorer"] = {"output": f"{n_bos} BOS", "cost_usd": round(bos_cost, 4),
                                     "unit_cost": cost_per_bos}

    return {
        "period_days": days,
        "cost_per_problem": cost_per_problem,
        "cost_per_solution": cost_per_solution,
        "cost_per_bos": cost_per_bos,
        "n_problems": n_problems,
        "n_solutions": n_solutions,
        "n_bos": n_bos,
        "total_value_cost": round(total_value_cost, 4),
        "efficiency": efficiency,
    }


# ---------- OTTIMIZZAZIONE ATTIVA ----------

def finance_optimization_suggestions(days=7):
    """Analizza log e suggerisce ottimizzazioni con risparmio stimato."""
    now = now_rome()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        logs = _paginated_logs(
            "agent_id,action,model_used,tokens_input,tokens_output,cost_usd",
            lambda q: q.gte("created_at", f"{since}T00:00:00+00:00")
                       .eq("status", "success"),
        )
    except:
        return []

    suggestions = []
    haiku_cost_in = 1.0 / 1_000_000   # $/token Haiku input
    haiku_cost_out = 5.0 / 1_000_000   # $/token Haiku output
    sonnet_cost_in = 3.0 / 1_000_000
    sonnet_cost_out = 15.0 / 1_000_000

    # 1. Prompt lunghi: token_input > 5000
    long_prompts = [l for l in logs if int(l.get("tokens_input", 0) or 0) > 5000]
    if long_prompts:
        by_action = {}
        for l in long_prompts:
            k = f"{l['agent_id']}/{l['action']}"
            by_action.setdefault(k, []).append(int(l.get("tokens_input", 0) or 0))
        worst = sorted(by_action.items(), key=lambda x: sum(x[1]), reverse=True)[:3]
        for action, tokens_list in worst:
            avg_tokens = sum(tokens_list) // len(tokens_list)
            potential_save = sum(tokens_list) * (sonnet_cost_in - haiku_cost_in) * 0.3  # 30% reduction
            suggestions.append({
                "type": "prompt_lungo",
                "target": action,
                "detail": f"Media {avg_tokens:,} token input ({len(tokens_list)} chiamate)",
                "saving_usd": round(potential_save, 4),
            })

    # 2. Chiamate Sonnet che potrebbero essere Haiku
    sonnet_actions = {}
    for l in logs:
        model = l.get("model_used", "")
        if "sonnet" in model.lower():
            action = f"{l['agent_id']}/{l['action']}"
            t_in = int(l.get("tokens_input", 0) or 0)
            t_out = int(l.get("tokens_output", 0) or 0)
            cost = float(l.get("cost_usd", 0) or 0)
            sonnet_actions.setdefault(action, {"count": 0, "total_cost": 0, "t_in": 0, "t_out": 0})
            sonnet_actions[action]["count"] += 1
            sonnet_actions[action]["total_cost"] += cost
            sonnet_actions[action]["t_in"] += t_in
            sonnet_actions[action]["t_out"] += t_out

    # Azioni Sonnet ad alto volume che NON sono generazione critica
    critical_actions = {"generate", "research", "feasibility", "bos"}
    for action, data in sorted(sonnet_actions.items(), key=lambda x: x[1]["total_cost"], reverse=True)[:3]:
        action_name = action.split("/")[-1] if "/" in action else action
        if not any(c in action_name.lower() for c in critical_actions):
            haiku_equiv = data["t_in"] * haiku_cost_in + data["t_out"] * haiku_cost_out
            saving = data["total_cost"] - haiku_equiv
            if saving > 0.001:
                suggestions.append({
                    "type": "downgrade_haiku",
                    "target": action,
                    "detail": f"{data['count']} chiamate Sonnet, possibile switch a Haiku",
                    "saving_usd": round(saving, 4),
                })

    # 3. Chiamate ridondanti: stesso agente+azione in < 60 secondi (detect from duplicates)
    action_counts = {}
    for l in logs:
        k = f"{l['agent_id']}/{l['action']}"
        action_counts[k] = action_counts.get(k, 0) + 1
    avg_per_action = sum(action_counts.values()) / len(action_counts) if action_counts else 0
    for action, count in action_counts.items():
        if count > avg_per_action * 3 and count > 10:
            per_call_cost = sum(float(l.get("cost_usd", 0) or 0) for l in logs
                                if f"{l['agent_id']}/{l['action']}" == action) / count
            potential_reduction = int(count * 0.3)  # assume 30% are redundant
            suggestions.append({
                "type": "chiamate_ridondanti",
                "target": action,
                "detail": f"{count} chiamate in {days}gg (media {avg_per_action:.0f}), possibile riduzione 30%",
                "saving_usd": round(potential_reduction * per_call_cost, 4),
            })

    total_saving = sum(s["saving_usd"] for s in suggestions)
    if suggestions:
        suggestions.append({"type": "totale", "detail": f"Risparmio stimato totale", "saving_usd": round(total_saving, 4)})

    return suggestions


# ---------- ANOMALY DETECTION ----------

def finance_detect_anomalies():
    """IQR su costi giornalieri + 3-sigma per agente."""
    alerts = []

    # Ultimi 30 giorni di costi giornalieri
    series = finance_get_daily_series(30)
    costs = [c for _, c in series if c > 0]

    if len(costs) >= 7:
        sorted_costs = sorted(costs)
        n = len(sorted_costs)
        q1 = sorted_costs[n // 4]
        q3 = sorted_costs[3 * n // 4]
        iqr = q3 - q1
        upper_fence = q3 + 1.5 * iqr

        # Controlla oggi
        today_cost = series[-1][1] if series else 0
        if today_cost > upper_fence and today_cost > 0:
            alerts.append({
                "type": "iqr_daily",
                "message": f"Costo oggi ${today_cost:.4f} supera soglia IQR ${upper_fence:.4f} (Q3={q3:.4f} + 1.5*IQR={iqr:.4f})",
                "severity": "warning",
            })

    # 3-sigma per agente (ultimi 30gg aggregati per agente per giorno)
    now = now_rome()
    since = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        logs = _paginated_logs(
            "agent_id,cost_usd,created_at",
            lambda q: q.gte("created_at", f"{since}T00:00:00+00:00"),
        )
    except:
        logs = []

    # Aggrega per agente per giorno
    agent_daily = {}
    for l in logs:
        agent = l.get("agent_id", "unknown")
        day = l.get("created_at", "")[:10]
        cost = float(l.get("cost_usd", 0) or 0)
        agent_daily.setdefault(agent, {})
        agent_daily[agent][day] = agent_daily[agent].get(day, 0) + cost

    for agent, daily_map in agent_daily.items():
        values = list(daily_map.values())
        if len(values) < 7:
            continue
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance) if variance > 0 else 0
        threshold = mean + 3 * std

        # Controlla ultimo giorno
        today = now.strftime("%Y-%m-%d")
        today_val = daily_map.get(today, 0)
        if today_val > threshold and std > 0 and today_val > 0.01:
            alerts.append({
                "type": "3sigma_agent",
                "message": f"Agente {agent}: ${today_val:.4f} oggi > 3sigma ${threshold:.4f} (media=${mean:.4f}, std=${std:.4f})",
                "severity": "warning",
            })

    return alerts


# ---------- METRICHE CFO TECH ----------

def finance_cfo_metrics(usd_to_eur):
    """Metriche enterprise: margine operativo, rapporti, unit economics."""
    now = now_rome()
    year, month = now.year, now.month
    _, days_in_month = monthrange(year, month)
    days_elapsed = now.day

    # Costi variabili mese corrente
    variable_usd = finance_get_month_costs(year, month)
    variable_eur = variable_usd * usd_to_eur

    # Costi fissi proporzionali
    fixed_eur = FIXED_COSTS_DAILY_EUR * days_elapsed
    total_eur = variable_eur + fixed_eur

    # Rapporti
    fixed_pct = round((fixed_eur / total_eur) * 100, 1) if total_eur > 0 else 0
    variable_pct = round(100 - fixed_pct, 1)

    # Margine operativo (budget - costi) / budget
    projected_total = round((total_eur / days_elapsed) * days_in_month, 2) if days_elapsed > 0 else 0
    operating_margin = round(((MONTHLY_BUDGET_EUR - projected_total) / MONTHLY_BUDGET_EUR) * 100, 1) if MONTHLY_BUDGET_EUR > 0 else 0

    # Unit economics ultimi 30gg
    cpv = finance_cost_per_value(30)

    # Costo acquisizione per problema (include tutto il pipeline)
    total_pipeline_cost = cpv["total_value_cost"]
    total_output = cpv["n_problems"] + cpv["n_solutions"] + cpv["n_bos"]
    cost_per_output = round(total_pipeline_cost / total_output, 4) if total_output > 0 else 0

    return {
        "operating_margin_pct": operating_margin,
        "fixed_costs_pct": fixed_pct,
        "variable_costs_pct": variable_pct,
        "fixed_eur_month": round(FIXED_COSTS_TOTAL_EUR, 2),
        "variable_eur_month": round((variable_eur / days_elapsed) * days_in_month, 2) if days_elapsed > 0 else 0,
        "projected_total_eur": projected_total,
        "cost_per_problem": cpv["cost_per_problem"],
        "cost_per_solution": cpv["cost_per_solution"],
        "cost_per_bos": cpv["cost_per_bos"],
        "cost_per_pipeline_output": cost_per_output,
        "unit_economics": cpv["efficiency"],
    }


# ---------- PERSISTENCE ----------

def finance_save_metrics(daily_data, projection_usd, projection_eur, budget_pct, alerts, usd_to_eur):
    row = {
        "report_date": daily_data["date"],
        "total_cost_usd": daily_data["total_cost_usd"],
        "total_cost_eur": round(daily_data["total_cost_usd"] * usd_to_eur, 4),
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
    }
    try:
        supabase.table("finance_metrics").insert(row).execute()
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            try:
                del row["report_date"]
                supabase.table("finance_metrics").update(row).eq("report_date", daily_data["date"]).execute()
            except:
                pass


# ---------- REPORT: MATTUTINO (8:00) ----------

def finance_morning_report():
    """Report CFO mattutino: costi ieri, trend, burn rate, runway, alert."""
    logger.info("[FINANCE] Morning report starting...")
    now = now_rome()
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    usd_to_eur = finance_get_usd_to_eur()

    # Costi ieri
    daily = finance_get_daily_costs(yesterday)
    if not daily:
        return {"status": "error", "error": "no data"}

    cost_eur = round(daily["total_cost_usd"] * usd_to_eur, 4)

    # Burn rates e runway
    rates = finance_burn_rates()
    rw = finance_runway(usd_to_eur)

    # Trend: ieri vs media 7gg
    pct_vs_7d = round(((daily["total_cost_usd"] - rates["avg_7d_usd"]) / rates["avg_7d_usd"]) * 100, 1) if rates["avg_7d_usd"] > 0 else 0
    trend_symbol = "+" if pct_vs_7d >= 0 else ""

    # Anomalie
    anomalies = finance_detect_anomalies()

    # Salva metriche
    finance_save_metrics(daily, rw["projected_month_eur"] / usd_to_eur if usd_to_eur > 0 else 0,
                         rw["projected_month_eur"], rw["budget_pct"],
                         anomalies, usd_to_eur)

    lines = [
        f"CFO REPORT {yesterday}",
        "",
        f"Costi API ieri: ${daily['total_cost_usd']:.4f} ({cost_eur:.4f} EUR)",
        f"Chiamate: {daily['total_calls']} ({daily['successful_calls']} ok, {daily['failed_calls']} err)",
    ]

    if daily["cost_by_agent"]:
        for agent, c in sorted(daily["cost_by_agent"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {agent}: ${c:.4f} ({daily['calls_by_agent'].get(agent, 0)} call)")

    lines.extend([
        "",
        f"Burn rate: ${rates['today_usd']:.4f}/oggi | ${rates['avg_7d_usd']:.4f}/7gg | ${rates['avg_30d_usd']:.4f}/30gg",
        f"Trend ieri vs 7gg: {trend_symbol}{pct_vs_7d}%",
        "",
        f"Mese speso: {rw['month_spent_eur']:.2f} EUR (var {rw['month_variable_eur']:.2f} + fissi {rw['month_fixed_eur']:.2f})",
        f"Proiezione mese: {rw['projected_month_eur']:.2f} EUR / Budget: {MONTHLY_BUDGET_EUR:.0f} EUR ({rw['budget_pct']:.1f}%)",
        f"Runway: {rw['days_remaining']} giorni | Rimangono: {rw['budget_remaining_eur']:.2f} EUR",
    ])

    if anomalies:
        lines.append("")
        for a in anomalies:
            lines.append(f"ALERT: {a['message']}")

    report = "\n".join(lines)
    notify_telegram("\U0001f4b0 CFO\n" + report, level="info", source="finance_agent")

    # Alert budget anticipato
    if rw["budget_pct"] > 80:
        days_to_budget = rw["days_remaining"]
        notify_telegram(
            "\U0001f4b0 CFO\n"
            f"ALERT BUDGET: proiezione {rw['projected_month_eur']:.2f} EUR = {rw['budget_pct']:.1f}% del budget. "
            f"Runway: {days_to_budget} giorni.",
            level="warning" if rw["budget_pct"] < 95 else "critical",
            source="finance_agent",
        )

    log_to_supabase("finance_agent", "morning_report", 6,
        f"Morning {yesterday}", f"${daily['total_cost_usd']:.4f}, runway {rw['days_remaining']}d",
        "none", 0, 0, 0, 0)

    logger.info(f"[FINANCE] Morning report done: ${daily['total_cost_usd']:.4f}")
    return {"status": "completed", "date": yesterday, "cost_usd": daily["total_cost_usd"],
            "runway_days": rw["days_remaining"], "budget_pct": rw["budget_pct"]}


# ---------- REPORT: SETTIMANALE (Domenica sera) ----------

def finance_weekly_report():
    """Report CFO settimanale: analisi completa, confronto, ottimizzazioni, anomalie."""
    logger.info("[FINANCE] Weekly report starting...")
    now = now_rome()
    usd_to_eur = finance_get_usd_to_eur()

    # Questa settimana (lun-dom)
    days_back = now.weekday()  # 0=lunedi
    week_start = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    week_end = now.strftime("%Y-%m-%d")
    this_week = finance_get_range_costs(week_start, week_end)

    # Settimana precedente
    prev_start = (now - timedelta(days=days_back + 7)).strftime("%Y-%m-%d")
    prev_end = (now - timedelta(days=days_back + 1)).strftime("%Y-%m-%d")
    prev_week = finance_get_range_costs(prev_start, prev_end)

    tw_cost = this_week["total_cost_usd"] if this_week else 0
    pw_cost = prev_week["total_cost_usd"] if prev_week else 0
    tw_eur = round(tw_cost * usd_to_eur, 2)
    pw_eur = round(pw_cost * usd_to_eur, 2)
    tw_calls = this_week["total_calls"] if this_week else 0
    pw_calls = prev_week["total_calls"] if prev_week else 0

    pct_cost = round(((tw_cost - pw_cost) / pw_cost) * 100, 1) if pw_cost > 0 else 0
    pct_calls = round(((tw_calls - pw_calls) / pw_calls) * 100, 1) if pw_calls > 0 else 0

    # Efficienza
    cpv = finance_cost_per_value(7)
    anomalies = finance_detect_anomalies()
    optimizations = finance_optimization_suggestions(7)
    cfo = finance_cfo_metrics(usd_to_eur)

    lines = [
        f"CFO REPORT SETTIMANALE {week_start} / {week_end}",
        "",
        f"Costi API: ${tw_cost:.4f} ({tw_eur:.2f} EUR) | Chiamate: {tw_calls}",
    ]

    if this_week and this_week["cost_by_agent"]:
        for agent, c in sorted(this_week["cost_by_agent"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {agent}: ${c:.4f}")

    cost_arrow = "+" if pct_cost >= 0 else ""
    calls_arrow = "+" if pct_calls >= 0 else ""
    lines.extend([
        "",
        f"vs settimana precedente: costi {cost_arrow}{pct_cost}% | chiamate {calls_arrow}{pct_calls}%",
        f"  Precedente: ${pw_cost:.4f} ({pw_eur:.2f} EUR) | {pw_calls} chiamate",
        "",
        "EFFICIENZA:",
        f"  Problemi trovati: {cpv['n_problems']} (${cpv['cost_per_problem']:.4f}/problema)",
        f"  Soluzioni generate: {cpv['n_solutions']} (${cpv['cost_per_solution']:.4f}/soluzione)",
        f"  BOS calcolati: {cpv['n_bos']} (${cpv['cost_per_bos']:.4f}/BOS)",
    ])

    if cpv["efficiency"]:
        best = min(cpv["efficiency"].items(), key=lambda x: x[1]["unit_cost"])
        worst = max(cpv["efficiency"].items(), key=lambda x: x[1]["unit_cost"])
        if best[0] != worst[0]:
            lines.append(f"  Piu efficiente: {best[0]} (${best[1]['unit_cost']:.4f}/output)")
            lines.append(f"  Meno efficiente: {worst[0]} (${worst[1]['unit_cost']:.4f}/output)")

    if anomalies:
        lines.append("")
        lines.append("ANOMALIE:")
        for a in anomalies:
            lines.append(f"  {a['message']}")

    if optimizations:
        lines.append("")
        lines.append("OTTIMIZZAZIONI SUGGERITE:")
        for i, opt in enumerate(optimizations, 1):
            if opt["type"] == "totale":
                lines.append(f"  Risparmio stimato totale: ${opt['saving_usd']:.4f}/settimana")
            else:
                lines.append(f"  {i}. [{opt['type']}] {opt['target']}: {opt['detail']} (saving: ${opt['saving_usd']:.4f})")

    lines.extend([
        "",
        "METRICHE CFO:",
        f"  Margine operativo: {cfo['operating_margin_pct']:.1f}%",
        f"  Fissi/Variabili: {cfo['fixed_costs_pct']:.0f}%/{cfo['variable_costs_pct']:.0f}%",
        f"  Costo per output pipeline: ${cfo['cost_per_pipeline_output']:.4f}",
    ])

    report = "\n".join(lines)
    notify_telegram("\U0001f4b0 CFO\n" + report, level="info", source="finance_agent")

    log_to_supabase("finance_agent", "weekly_report", 6,
        f"Weekly {week_start}/{week_end}", f"${tw_cost:.4f} vs ${pw_cost:.4f}",
        "none", 0, 0, 0, 0)

    logger.info(f"[FINANCE] Weekly report done: ${tw_cost:.4f}")
    return {"status": "completed", "week": f"{week_start}/{week_end}",
            "cost_usd": tw_cost, "vs_prev_pct": pct_cost}


# ---------- REPORT: MENSILE (1° del mese) ----------

def finance_monthly_report():
    """Report CFO mensile: trend, piano ottimizzazione, previsione mese successivo."""
    logger.info("[FINANCE] Monthly report starting...")
    now = now_rome()
    usd_to_eur = finance_get_usd_to_eur()

    # Mese precedente
    if now.month == 1:
        prev_year, prev_month = now.year - 1, 12
    else:
        prev_year, prev_month = now.year, now.month - 1

    prev_variable_usd = finance_get_month_costs(prev_year, prev_month)
    prev_variable_eur = round(prev_variable_usd * usd_to_eur, 2)
    prev_total_eur = round(prev_variable_eur + FIXED_COSTS_TOTAL_EUR, 2)

    # Due mesi fa (per confronto)
    if prev_month == 1:
        pp_year, pp_month = prev_year - 1, 12
    else:
        pp_year, pp_month = prev_year, prev_month - 1
    pp_variable_usd = finance_get_month_costs(pp_year, pp_month)
    pp_total_eur = round(pp_variable_usd * usd_to_eur + FIXED_COSTS_TOTAL_EUR, 2)

    pct_vs_prev = round(((prev_total_eur - pp_total_eur) / pp_total_eur) * 100, 1) if pp_total_eur > 0 else 0

    # Costi da sempre
    all_time_usd = finance_get_all_time_costs()
    all_time_eur = round(all_time_usd * usd_to_eur, 2)

    # Ottimizzazioni e metriche
    optimizations = finance_optimization_suggestions(30)
    cfo = finance_cfo_metrics(usd_to_eur)
    cpv = finance_cost_per_value(30)

    # Previsione mese successivo (basata su media ultimi 2 mesi variabili)
    avg_variable = (prev_variable_usd + pp_variable_usd) / 2 if pp_variable_usd > 0 else prev_variable_usd
    forecast_variable_eur = round(avg_variable * usd_to_eur, 2)
    forecast_total_eur = round(forecast_variable_eur + FIXED_COSTS_TOTAL_EUR, 2)

    month_name = f"{prev_year}-{prev_month:02d}"
    lines = [
        f"CFO REPORT MENSILE {month_name}",
        "",
        f"Costi API: ${prev_variable_usd:.4f} ({prev_variable_eur:.2f} EUR)",
        f"Costi fissi: {FIXED_COSTS_TOTAL_EUR:.2f} EUR",
    ]
    for name, cost in FIXED_COSTS_MONTHLY_EUR.items():
        lines.append(f"  {name}: {cost:.2f} EUR")
    lines.extend([
        f"TOTALE: {prev_total_eur:.2f} EUR / Budget: {MONTHLY_BUDGET_EUR:.0f} EUR ({round(prev_total_eur/MONTHLY_BUDGET_EUR*100, 1)}%)",
    ])

    if pp_total_eur > 0:
        trend_arrow = "+" if pct_vs_prev >= 0 else ""
        lines.append(f"vs mese precedente: {trend_arrow}{pct_vs_prev}% ({pp_total_eur:.2f} EUR)")
    lines.append("")

    lines.extend([
        "COSTI DA SEMPRE:",
        f"  API totali: ${all_time_usd:.4f} ({all_time_eur:.2f} EUR)",
        "",
        "UNIT ECONOMICS (30gg):",
        f"  Costo per problema: ${cpv['cost_per_problem']:.4f}",
        f"  Costo per soluzione: ${cpv['cost_per_solution']:.4f}",
        f"  Costo per BOS: ${cpv['cost_per_bos']:.4f}",
        f"  Pipeline: {cpv['n_problems']} problemi -> {cpv['n_solutions']} soluzioni -> {cpv['n_bos']} BOS",
        "",
        "METRICHE CFO:",
        f"  Margine operativo: {cfo['operating_margin_pct']:.1f}%",
        f"  Fissi: {cfo['fixed_costs_pct']:.0f}% | Variabili: {cfo['variable_costs_pct']:.0f}%",
    ])

    if optimizations:
        lines.append("")
        lines.append("PIANO OTTIMIZZAZIONE:")
        for i, opt in enumerate(optimizations, 1):
            if opt["type"] == "totale":
                lines.append(f"  Risparmio stimato: ${opt['saving_usd']:.4f}/mese")
            else:
                lines.append(f"  {i}. {opt['detail']} (saving: ${opt['saving_usd']:.4f})")

    lines.extend([
        "",
        "PREVISIONE MESE PROSSIMO:",
        f"  API: {forecast_variable_eur:.2f} EUR (media 2 mesi)",
        f"  Fissi: {FIXED_COSTS_TOTAL_EUR:.2f} EUR",
        f"  TOTALE previsto: {forecast_total_eur:.2f} EUR",
    ])

    report = "\n".join(lines)
    notify_telegram("\U0001f4b0 CFO\n" + report, level="info", source="finance_agent")

    log_to_supabase("finance_agent", "monthly_report", 6,
        f"Monthly {month_name}", f"Total {prev_total_eur:.2f} EUR",
        "none", 0, 0, 0, 0)

    logger.info(f"[FINANCE] Monthly report done: {prev_total_eur:.2f} EUR")
    return {"status": "completed", "month": month_name, "total_eur": prev_total_eur,
            "forecast_eur": forecast_total_eur}


# ---------- ENTRY POINT PRINCIPALE ----------

def run_finance_agent(target_date=None):
    """Analisi finanziaria completa — usata da /finance endpoint."""
    logger.info("Finance Agent v2.0 — CFO AI starting...")

    now = now_rome()
    if target_date:
        date_str = target_date
    else:
        yesterday = now - timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")

    usd_to_eur = finance_get_usd_to_eur()
    daily = finance_get_daily_costs(date_str)
    if daily is None:
        return {"status": "error", "error": "agent_logs read failed"}

    rw = finance_runway(usd_to_eur)
    rates = finance_burn_rates()
    anomalies = finance_detect_anomalies()
    cpv = finance_cost_per_value(30)
    cfo = finance_cfo_metrics(usd_to_eur)
    optimizations = finance_optimization_suggestions(7)

    # Salva metriche
    finance_save_metrics(daily, rw["projected_month_eur"] / usd_to_eur if usd_to_eur > 0 else 0,
                         rw["projected_month_eur"], rw["budget_pct"], anomalies, usd_to_eur)

    # Alert budget anticipato (se proiezione > 80% budget)
    if rw["budget_pct"] > 80:
        notify_telegram(
            "\U0001f4b0 CFO\n"
            f"ALERT: proiezione mese {rw['projected_month_eur']:.2f} EUR = {rw['budget_pct']:.1f}% budget. "
            f"Runway: {rw['days_remaining']}gg.",
            level="warning" if rw["budget_pct"] < 95 else "critical",
            source="finance_agent",
        )

    # Alert anomalie
    for a in anomalies:
        notify_telegram(f"\U0001f4b0 CFO\nANOMALIA: {a['message']}", level=a["severity"], source="finance_agent")

    log_to_supabase("finance_agent", "full_analysis", 6,
        f"Analysis {date_str}", f"Budget {rw['budget_pct']:.1f}%, runway {rw['days_remaining']}d",
        "none", 0, 0, 0, 0)

    logger.info(f"[FINANCE] Full analysis done: {rw['budget_pct']:.1f}% budget, {rw['days_remaining']}d runway")
    return {
        "status": "completed",
        "date": date_str,
        "daily_cost_usd": daily["total_cost_usd"],
        "runway": rw,
        "burn_rates": rates,
        "anomalies": len(anomalies),
        "cfo_metrics": cfo,
        "cost_per_value": cpv,
        "optimizations": len(optimizations),
    }


# ============================================================
# PARTE 8: SISTEMA REPORT (costi ogni 4h ore pari, attività ore dispari)
# ============================================================

MESI_IT_REPORT = {1:"gen",2:"feb",3:"mar",4:"apr",5:"mag",6:"giu",
                  7:"lug",8:"ago",9:"set",10:"ott",11:"nov",12:"dic"}


