"""CFO — Chief Financial Officer. Dominio: costi, revenue, marginalità, proiezioni."""
import json
from datetime import timedelta
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
            week_ago = (now_rome() - timedelta(days=7)).isoformat()
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

        # Costi ultime 24h con breakdown per Chief e per Progetto
        try:
            ctx["costs_24h"] = self.get_costs_breakdown(hours=24)
        except Exception:
            ctx["costs_24h"] = {}

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


    def get_costs_breakdown(self, hours=24, since=None, until=None):
        """Breakdown costi real-time per Chief e per Progetto da agent_logs.
        Se since/until forniti, usa quelli. Altrimenti rolling hours.
        """
        if since is None:
            since = (now_rome() - timedelta(hours=hours)).isoformat()
        try:
            q = supabase.table("agent_logs").select(
                "agent_id,project_id,model_used,cost_usd,tokens_input,tokens_output"
            ).gte("created_at", since)
            if until:
                q = q.lt("created_at", until)
            r = q.execute()
        except Exception as e:
            logger.warning("[CFO] get_costs_breakdown error: %s", e)
            return {"total_usd": 0, "total_eur": 0, "by_chief": [], "by_project": [], "hours": hours}

        by_chief = {}
        by_project = {}
        by_model = {}
        total = 0.0
        calls = 0
        for row in (r.data or []):
            cost = float(row.get("cost_usd") or 0)
            total += cost
            calls += 1
            agent = row.get("agent_id", "unknown")
            proj = row.get("project_id")
            model = row.get("model_used", "unknown") or "unknown"
            by_chief[agent] = by_chief.get(agent, 0) + cost
            by_model[model] = by_model.get(model, 0) + cost
            if proj:
                by_project[proj] = by_project.get(proj, 0) + cost

        # Risolvi nomi progetto
        project_names = {}
        proj_ids = [p for p in by_project.keys() if p]
        if proj_ids:
            try:
                pr = supabase.table("projects").select("id,name").in_("id", proj_ids).execute()
                for p in (pr.data or []):
                    project_names[p["id"]] = p.get("name", "Progetto #" + str(p["id"]))
            except Exception:
                pass

        by_project_named = []
        for pid, cost in sorted(by_project.items(), key=lambda x: x[1], reverse=True):
            name = project_names.get(pid, "Progetto #" + str(pid))
            by_project_named.append((name, round(cost, 6)))

        return {
            "total_usd": round(total, 6),
            "total_eur": round(total * 0.92, 4),
            "by_chief": sorted(by_chief.items(), key=lambda x: x[1], reverse=True),
            "by_model": sorted(by_model.items(), key=lambda x: x[1], reverse=True),
            "by_project": by_project_named,
            "api_calls": calls,
            "hours": hours,
        }

    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> list:
        """CFO: costi API, breakdown per agente/modello/progetto, anomalie — giorno precedente."""
        sections = []

        # Usa get_costs_breakdown per dati completi (giorno solare precedente)
        breakdown = self.get_costs_breakdown(since=ieri_inizio, until=ieri_fine)

        if breakdown.get("by_chief"):
            top_agents = breakdown["by_chief"][:5]
            agent_lines = "\n".join(
                "  " + a + ": " + chr(0x20ac) + str(round(c * 0.92, 4))
                for a, c in top_agents
            )
            sections.append(chr(0x1f4ca) + " COSTI PER CHIEF\n" + agent_lines)

        if breakdown.get("by_model"):
            top_models = breakdown["by_model"][:3]
            model_lines = "\n".join(
                "  " + m + ": " + chr(0x20ac) + str(round(c * 0.92, 4))
                for m, c in top_models
            )
            sections.append(chr(0x1f916) + " COSTI PER MODELLO\n" + model_lines)

        if breakdown.get("by_project"):
            proj_lines = "\n".join(
                "  " + name + ": " + chr(0x20ac) + str(round(c * 0.92, 4))
                for name, c in breakdown["by_project"][:5]
            )
            sections.append(chr(0x1f4c1) + " COSTI PER PROGETTO\n" + proj_lines)

        # 2. Anomalie costo (ultime 24h)
        anomalies = self.check_anomalies()
        if anomalies:
            anom_lines = "\n".join(
                f"  \u26a0\ufe0f {a.get('description','')[:80]}" for a in anomalies[:3]
            )
            sections.append(f"\U0001f6a8 ANOMALIE\n{anom_lines}")

        # 3. Finance metrics recenti (giorno precedente)
        try:
            r = supabase.table("finance_metrics").select("metric_name,value,created_at") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).limit(5).execute()
            if r.data:
                fm_lines = "\n".join(
                    f"  {row.get('metric_name','?')}: {row.get('value','')}"
                    for row in r.data
                )
                sections.append(f"\U0001f4c8 METRICHE FINANZIARIE\n{fm_lines}")
        except Exception:
            pass

        return sections

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
