"""CPO — Chief Product Officer. Dominio: prodotto, UX, roadmap, feedback utenti."""
from core.base_chief import BaseChief
from core.config import supabase


class CPO(BaseChief):
    name = "CPO"
    domain = "product"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il CPO di brAIn. Genera un briefing prodotto settimanale includendo: "
        "1) Status MVP dei cantieri attivi, "
        "2) Feedback utenti raccolti (smoke test, retention), "
        "3) Priorità feature per prossima iterazione, "
        "4) Metriche product-market fit disponibili, "
        "5) Raccomandazioni roadmap."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            r = supabase.table("projects").select("id,name,status,build_phase") \
                .in_("status", ["build_complete", "launch_approved", "live"]).execute()
            ctx["products_live"] = r.data or []
        except Exception:
            ctx["products_live"] = []
        return ctx
