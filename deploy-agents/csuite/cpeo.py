"""CPeO — Chief People Officer. Dominio: team, manager, collaboratori, cultura."""
from core.base_chief import BaseChief
from core.config import supabase


class CPeO(BaseChief):
    name = "CPeO"
    domain = "people"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il CPeO di brAIn. Genera un briefing people settimanale includendo: "
        "1) Manager di cantiere attivi e loro progetti, "
        "2) Revenue share distribuito o in accumulazione, "
        "3) Performance manager (feedback inviati, reattività), "
        "4) Nuovi collaboratori onboardati, "
        "5) Raccomandazioni team e incentivi."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            r = supabase.table("project_members").select(
                "telegram_username,role,project_id,active,added_at"
            ).eq("active", True).execute()
            ctx["active_managers"] = r.data or []
        except Exception:
            ctx["active_managers"] = []
        try:
            r = supabase.table("manager_revenue_share").select(
                "manager_username,share_pct,project_id,active"
            ).eq("active", True).execute()
            ctx["revenue_shares"] = r.data or []
        except Exception:
            ctx["revenue_shares"] = []
        return ctx
