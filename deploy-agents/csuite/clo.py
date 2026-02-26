"""CLO — Chief Legal Officer. Dominio: legale, compliance, contratti, rischi normativi."""
from core.base_chief import BaseChief
from core.config import supabase


class CLO(BaseChief):
    name = "CLO"
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
