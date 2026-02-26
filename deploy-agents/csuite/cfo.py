"""CFO — Chief Financial Officer. Dominio: costi, revenue, marginalità, proiezioni."""
from core.base_chief import BaseChief
from core.config import supabase, logger


class CFO(BaseChief):
    name = "CFO"
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
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            r = supabase.table("agent_logs").select("agent_id,cost_usd") \
                .gte("created_at", week_ago).execute()
            costs = {}
            for row in (r.data or []):
                agent = row.get("agent_id", "unknown")
                costs[agent] = costs.get(agent, 0) + float(row.get("cost_usd") or 0)
            ctx["weekly_costs_by_agent"] = sorted(costs.items(), key=lambda x: x[1], reverse=True)[:5]
            ctx["weekly_total_cost"] = sum(costs.values())
        except Exception:
            ctx["weekly_costs_by_agent"] = []
            ctx["weekly_total_cost"] = 0
        return ctx

    def check_anomalies(self):
        anomalies = []
        try:
            from datetime import datetime, timezone, timedelta
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
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
