"""COO — Chief Operating Officer. Dominio: operazioni, progetti, cantieri, pipeline."""
from core.base_chief import BaseChief
from core.config import supabase


class COO(BaseChief):
    name = "COO"
    domain = "ops"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il COO di brAIn. Genera un briefing operativo settimanale includendo: "
        "1) Status cantieri attivi (fase, blocchi), "
        "2) Pipeline problemi→soluzioni→BOS (velocità, colli di bottiglia), "
        "3) SLA rispettati/violati, "
        "4) Manager di cantiere attivi e loro performance, "
        "5) Azioni operative prioritarie."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            r = supabase.table("projects").select("id,name,status,build_phase,created_at") \
                .neq("status", "archived").execute()
            ctx["active_projects"] = r.data or []
        except Exception:
            ctx["active_projects"] = []
        try:
            r = supabase.table("action_queue").select("action_type,status") \
                .eq("status", "pending").execute()
            ctx["pending_actions"] = len(r.data or [])
        except Exception:
            ctx["pending_actions"] = 0
        return ctx

    def check_anomalies(self):
        anomalies = []
        try:
            from datetime import datetime, timezone, timedelta
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
        return anomalies
