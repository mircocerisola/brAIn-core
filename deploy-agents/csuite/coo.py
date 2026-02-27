"""COO — Chief Operations & Revenue Officer. Dominio: operazioni, cantieri, pipeline, prodotto, revenue."""
from core.base_chief import BaseChief
from core.config import supabase
from core.templates import now_rome


class COO(BaseChief):
    name = "COO"
    domain = "ops"
    chief_id = "coo"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il COO di brAIn — Chief Operations & Revenue Officer. "
        "Genera un briefing operativo settimanale includendo: "
        "1) Status cantieri attivi (fase, blocchi, build_phase), "
        "2) Prodotti live e metriche chiave (KPI, conversione, smoke test), "
        "3) Pipeline problemi→soluzioni→BOS (velocità, colli di bottiglia), "
        "4) SLA rispettati/violati e action_queue pending, "
        "5) Manager di cantiere attivi e loro performance, "
        "6) Azioni operative e di prodotto prioritarie."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        # Cantieri attivi — FULL data (v5.11)
        try:
            r = supabase.table("projects").select(
                "id,name,status,build_phase,pipeline_step,pipeline_territory,"
                "pipeline_locked,topic_id,smoke_test_url,created_at,updated_at"
            ).neq("status", "archived").execute()
            ctx["active_projects"] = r.data or []
        except Exception as e:
            ctx["active_projects"] = f"errore lettura DB: {e}"
        # Action queue pending — FULL details (v5.11)
        try:
            r = supabase.table("action_queue").select(
                "id,action_type,title,description,project_id,status,created_at"
            ).eq("status", "pending").order("created_at", desc=True).execute()
            ctx["pending_actions"] = r.data or []
        except Exception as e:
            ctx["pending_actions"] = f"errore lettura DB: {e}"
        # Prodotti live (ex-CPO)
        try:
            r = supabase.table("projects").select("id,name,status,build_phase") \
                .in_("status", ["build_complete", "launch_approved", "live"]).execute()
            ctx["products_live"] = r.data or []
        except Exception as e:
            ctx["products_live"] = f"errore lettura DB: {e}"
        # KPI recenti (ex-CPO)
        try:
            r = supabase.table("kpi_daily").select("project_id,metric_name,value,recorded_at") \
                .order("recorded_at", desc=True).limit(20).execute()
            ctx["recent_kpis"] = r.data or []
        except Exception as e:
            ctx["recent_kpis"] = f"errore lettura DB: {e}"
        # Agent logs recenti (v5.11) — ultimi 20
        try:
            r = supabase.table("agent_logs").select(
                "agent_id,action,status,cost_usd,created_at"
            ).order("created_at", desc=True).limit(20).execute()
            ctx["recent_logs"] = r.data or []
        except Exception as e:
            ctx["recent_logs"] = f"errore lettura DB: {e}"
        return ctx


    def _daily_report_emoji(self) -> str:
        return "\u2699\ufe0f"

    def _get_daily_report_sections(self, since_24h: str) -> list:
        """COO: azioni queue, log agenti, task completati — ultime 24h."""
        sections = []

        # 1. Azioni queue create nelle ultime 24h
        try:
            r = supabase.table("action_queue").select("id,action_type,title,status") \
                .gte("created_at", since_24h).order("created_at", desc=True).execute()
            if r.data:
                by_status = {}
                for a in r.data:
                    s = a.get("status", "?")
                    by_status[s] = by_status.get(s, 0) + 1
                status_lines = "\n".join(f"  {s}: {cnt}" for s, cnt in by_status.items())
                sections.append(f"\U0001f4cb ACTION QUEUE ({len(r.data)} azioni)\n{status_lines}")
        except Exception as e:
            logger.warning("[COO] action_queue error: %s", e)

        # 2. Log agenti nelle ultime 24h
        try:
            r = supabase.table("agent_logs").select("agent_id,action,status,cost_usd") \
                .gte("created_at", since_24h).execute()
            if r.data:
                by_agent = {}
                errors = 0
                for log in r.data:
                    a = log.get("agent_id", "?")
                    by_agent[a] = by_agent.get(a, 0) + 1
                    if log.get("status") == "error":
                        errors += 1
                top5 = sorted(by_agent.items(), key=lambda x: x[1], reverse=True)[:5]
                agent_lines = "\n".join(f"  {a}: {n} azioni" for a, n in top5)
                err_note = f" | {errors} errori" if errors > 0 else ""
                sections.append(f"\U0001f4dd LOG AGENTI ({len(r.data)} totale{err_note})\n{agent_lines}")
        except Exception as e:
            logger.warning("[COO] agent_logs error: %s", e)

        # 3. Progetti aggiornati nelle ultime 24h
        try:
            r = supabase.table("projects").select("id,name,pipeline_step,status") \
                .gte("updated_at", since_24h).neq("status", "archived").execute()
            if r.data:
                proj_lines = "\n".join(
                    f"  {row.get('name','?')[:40]} → {row.get('pipeline_step') or row.get('status','?')}"
                    for row in r.data[:5]
                )
                sections.append(f"\U0001f3d7 CANTIERI AGGIORNATI ({len(r.data)})\n{proj_lines}")
        except Exception as e:
            logger.warning("[COO] projects error: %s", e)

        # 4. KPI registrati nelle ultime 24h
        try:
            r = supabase.table("kpi_daily").select("project_id,metric_name,value") \
                .gte("recorded_at", since_24h).limit(10).execute()
            if r.data:
                kpi_lines = "\n".join(
                    f"  #{row.get('project_id','?')} {row.get('metric_name','?')}: {row.get('value','?')}"
                    for row in r.data[:5]
                )
                sections.append(f"\U0001f4ca KPI REGISTRATI ({len(r.data)})\n{kpi_lines}")
        except Exception as e:
            logger.warning("[COO] kpi_daily error: %s", e)

        return sections

    def check_anomalies(self):
        anomalies = []
        # Azioni stale
        try:
            from datetime import datetime, timezone
            r = supabase.table("action_queue").select("created_at").eq("status", "pending").execute()
            for row in (r.data or []):
                created = row.get("created_at", "")
                if created:
                    from dateutil.parser import parse as parse_dt
                    age = (now_rome() - parse_dt(created)).days
                    if age > 7:
                        anomalies.append({
                            "type": "stale_action",
                            "description": f"Azione pending da {age} giorni",
                            "severity": "medium",
                        })
                        break
        except Exception:
            pass
        # Cantieri bloccati
        try:
            r = supabase.table("projects") \
                .select("id,name,status,updated_at") \
                .in_("status", ["review_phase1", "review_phase2", "review_phase3"]).execute()
            for row in (r.data or []):
                updated = row.get("updated_at", "")
                if updated:
                    from datetime import datetime, timezone
                    from dateutil.parser import parse as parse_dt
                    age = (now_rome() - parse_dt(updated)).days
                    if age > 5:
                        anomalies.append({
                            "type": "stale_build",
                            "description": f"Cantiere {row.get('name','?')} in {row.get('status','?')} da {age} giorni senza aggiornamenti",
                            "severity": "high",
                        })
        except Exception:
            pass
        return anomalies
