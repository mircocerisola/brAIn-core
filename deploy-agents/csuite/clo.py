"""CLO — Chief Legal Officer. Dominio: legale, compliance, contratti, rischi normativi."""
from core.base_chief import BaseChief
from core.config import supabase


class CLO(BaseChief):
    name = "CLO"
    chief_id = "clo"
    domain = "legal"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il CLO di brAIn. Genera un briefing legale settimanale includendo: "
        "1) Violazioni etiche rilevate (ethics_violations), "
        "2) Progetti con review legale pendente, "
        "3) Nuove normative UE rilevanti (AI Act, GDPR updates), "
        "4) Rischi legali per progetti in corso, "
        "5) Raccomandazioni compliance."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            r = supabase.table("ethics_violations").select("project_id,principle_id,severity,blocked") \
                .eq("resolved", False).order("created_at", desc=True).limit(10).execute()
            ctx["open_violations"] = r.data or []
        except Exception:
            ctx["open_violations"] = []
        try:
            r = supabase.table("legal_reviews").select(
                "project_id,status,risks_found,created_at"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["legal_reviews"] = r.data if r.data else "nessun dato ancora registrato"
        except Exception:
            ctx["legal_reviews"] = "nessun dato ancora registrato"
        try:
            r = supabase.table("projects").select(
                "id,name,status,legal_status"
            ).neq("status", "archived").execute()
            ctx["projects_legal_status"] = r.data or []
        except Exception:
            ctx["projects_legal_status"] = []
        try:
            r = supabase.table("agent_logs").select("action,status,error").eq(
                "agent_id", "ethics_monitor"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["ethics_monitor_log"] = r.data or []
        except Exception:
            ctx["ethics_monitor_log"] = []
        return ctx

    def check_anomalies(self):
        anomalies = []
        try:
            r = supabase.table("ethics_violations").select("id").eq("blocked", True) \
                .eq("resolved", False).execute()
            blocked_count = len(r.data or [])
            if blocked_count > 0:
                anomalies.append({
                    "type": "ethics_blocked_projects",
                    "description": f"{blocked_count} progetti bloccati per violazioni etiche non risolte",
                    "severity": "critical",
                })
        except Exception:
            pass
        return anomalies
