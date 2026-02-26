"""COO — Chief Operations & Revenue Officer. Dominio: operazioni, cantieri, pipeline, prodotto, revenue."""
from core.base_chief import BaseChief
from core.config import supabase


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
        # Cantieri attivi
        try:
            r = supabase.table("projects").select("id,name,status,build_phase,created_at") \
                .neq("status", "archived").execute()
            ctx["active_projects"] = r.data or []
        except Exception:
            ctx["active_projects"] = []
        # Action queue pending
        try:
            r = supabase.table("action_queue").select("action_type,status") \
                .eq("status", "pending").execute()
            ctx["pending_actions"] = len(r.data or [])
        except Exception:
            ctx["pending_actions"] = 0
        # Prodotti live (ex-CPO)
        try:
            r = supabase.table("projects").select("id,name,status,build_phase") \
                .in_("status", ["build_complete", "launch_approved", "live"]).execute()
            ctx["products_live"] = r.data or []
        except Exception:
            ctx["products_live"] = []
        # KPI recenti (ex-CPO)
        try:
            r = supabase.table("kpi_daily").select("project_id,metric_name,value,recorded_at") \
                .order("recorded_at", desc=True).limit(20).execute()
            ctx["recent_kpis"] = r.data or []
        except Exception:
            ctx["recent_kpis"] = []
        return ctx

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
                    age = (datetime.now(timezone.utc) - parse_dt(created)).days
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
                    age = (datetime.now(timezone.utc) - parse_dt(updated)).days
                    if age > 5:
                        anomalies.append({
                            "type": "stale_build",
                            "description": f"Cantiere {row.get('name','?')} in {row.get('status','?')} da {age} giorni senza aggiornamenti",
                            "severity": "high",
                        })
        except Exception:
            pass
        return anomalies
