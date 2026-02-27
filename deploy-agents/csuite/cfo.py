"""CFO — Chief Financial Officer. Dominio: costi, revenue, marginalità, proiezioni."""
import json
from core.base_chief import BaseChief
from core.config import supabase, logger
from core.templates import now_rome


class CFO(BaseChief):
    name = "CFO"
    chief_id = "cfo"
    domain = "finance"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il CFO di brAIn. Genera un briefing finanziario settimanale includendo: "
        "1) Costi totali settimana vs settimana precedente, "
        "2) Top spender per agente, "
        "3) Proiezione mensile vs budget, "
        "4) Revenue da progetti attivi, "
        "5) Raccomandazioni ottimizzazione costi."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            from datetime import datetime, timezone, timedelta
            week_ago = (now_rome() - timedelta(days=7)).isoformat()
            # FIX 4a: costi per agente E per modello (ultimi 7 giorni)
            r = supabase.table("agent_logs").select("agent_id,model_used,cost_usd") \
                .gte("created_at", week_ago).execute()
            costs_agent = {}
            costs_model = {}
            total = 0.0
            for row in (r.data or []):
                agent = row.get("agent_id", "unknown")
                model = row.get("model_used", "unknown") or "unknown"
                cost = float(row.get("cost_usd") or 0)
                total += cost
                costs_agent[agent] = costs_agent.get(agent, 0) + cost
                costs_model[model] = costs_model.get(model, 0) + cost
            ctx["weekly_costs_by_agent"] = sorted(
                costs_agent.items(), key=lambda x: x[1], reverse=True
            )[:5]
            ctx["weekly_costs_by_model"] = sorted(
                costs_model.items(), key=lambda x: x[1], reverse=True
            )[:5]
            ctx["weekly_total_usd"] = round(total, 4)
            ctx["weekly_total_eur"] = round(total * 0.92, 2)
        except Exception:
            ctx["weekly_costs_by_agent"] = []
            ctx["weekly_costs_by_model"] = []
            ctx["weekly_total_usd"] = 0
            ctx["weekly_total_eur"] = 0

        # FIX 4b: finance_metrics recenti
        try:
            r = supabase.table("finance_metrics").select("*") \
                .order("created_at", desc=True).limit(3).execute()
            ctx["finance_metrics"] = r.data or []
        except Exception:
            ctx["finance_metrics"] = []

        # FIX 4c: costi fissi mensili da org_config
        try:
            r = supabase.table("org_config").select("value") \
                .eq("key", "monthly_fixed_costs").execute()
            if r.data:
                ctx["monthly_fixed_costs"] = json.loads(r.data[0]["value"])
            else:
                default_costs = {
                    "claude_max": 0, "supabase_pro": 25,
                    "perplexity": 0, "cloud_run": 0,
                }
                ctx["monthly_fixed_costs"] = default_costs
                # Upsert per sessioni future
                try:
                    supabase.table("org_config").upsert({
                        "key": "monthly_fixed_costs",
                        "value": json.dumps(default_costs),
                    }).execute()
                except Exception:
                    pass
        except Exception:
            ctx["monthly_fixed_costs"] = {
                "claude_max": 0, "supabase_pro": 25, "perplexity": 0, "cloud_run": 0,
            }

        return ctx

    def check_anomalies(self):
        anomalies = []
        try:
            from datetime import datetime, timezone, timedelta
            yesterday = (now_rome() - timedelta(days=1)).isoformat()
            r = supabase.table("agent_logs").select("cost_usd").gte("created_at", yesterday).execute()
            daily_cost = sum(float(row.get("cost_usd") or 0) for row in (r.data or []))
            budget_daily = 33.0  # €1000/mese / 30 giorni
            if daily_cost > budget_daily * 1.5:
                anomalies.append({
                    "type": "cost_spike",
                    "description": f"Costo giornaliero €{daily_cost:.2f} supera soglia €{budget_daily*1.5:.2f}",
                    "severity": "high",
                })
        except Exception:
            pass
        return anomalies
