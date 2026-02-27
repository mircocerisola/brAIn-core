"""CSO — Chief Strategy Officer. Dominio: strategia, mercati, competizione, opportunita'."""
from core.base_chief import BaseChief
from core.config import supabase, logger


def select_smoke_test_method(solution):
    """Seleziona il metodo smoke test ottimale in base a settore/audience."""
    sector = (solution.get("sector") or "").lower()
    audience = (solution.get("customer_segment") or "").lower()
    solution_type = (solution.get("solution_type") or solution.get("sub_sector") or "").lower()
    market_size = solution.get("market_size") or solution.get("affected_population") or 0
    if isinstance(market_size, str):
        try:
            market_size = int(market_size.replace(",", "").replace(".", ""))
        except Exception:
            market_size = 0

    # B2B con decision maker identificabili -> outreach diretto
    b2b_sectors = (
        "food_tech", "saas", "fintech", "hr_tech", "legal_tech",
        "real_estate", "logistics", "healthcare", "education",
        "hospitality", "restaurant", "retail",
    )
    if sector in b2b_sectors and "business" in audience:
        return "cold_outreach"

    # B2C o audience ampia -> landing page + ads
    if "consumer" in audience or market_size > 100000:
        return "landing_page_ads"

    # Servizio manuale validabile -> concierge MVP
    if "service" in solution_type or "servizio" in solution_type:
        return "concierge"

    # SaaS B2B -> pre-order
    if "saas" in sector or "software" in solution_type:
        return "pre_order"

    # Default B2B -> outreach + landing
    return "cold_outreach_landing"


class CSO(BaseChief):
    name = "CSO"
    chief_id = "cso"
    domain = "strategy"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il CSO di brAIn. Genera un briefing strategico settimanale includendo: "
        "1) Portfolio soluzioni in due sezioni: 'Pipeline sistema' (generate automaticamente) e 'Idee founder' (create da Mirco, NON archiviabili automaticamente), "
        "2) Trend di mercato emersi dai scan, "
        "3) Gap competitivi identificati, "
        "4) Opportunità di pivot o scale, "
        "5) Raccomandazioni priorità prossima settimana."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        # Soluzioni pipeline sistema (source='system' o NULL)
        try:
            r = supabase.table("solutions").select("id,title,bos_score,status,source") \
                .or_("source.eq.system,source.is.null") \
                .order("bos_score", desc=True).limit(5).execute()
            ctx["pipeline_solutions"] = r.data or []
        except Exception:
            ctx["pipeline_solutions"] = []
        # Idee founder (source='founder') — mai archiviabili automaticamente
        try:
            r = supabase.table("solutions").select("id,title,bos_score,status,source") \
                .eq("source", "founder") \
                .order("bos_score", desc=True).limit(10).execute()
            ctx["founder_ideas"] = r.data or []
        except Exception:
            ctx["founder_ideas"] = []
        try:
            r = supabase.table("problems").select("id,title,weighted_score,status") \
                .order("weighted_score", desc=True).limit(5).execute()
            ctx["top_problems"] = r.data or []
        except Exception:
            ctx["top_problems"] = []
        return ctx

    def check_anomalies(self):
        anomalies = []
        try:
            from datetime import datetime, timezone, timedelta
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            r = supabase.table("problems").select("id").gte("created_at", week_ago).execute()
            count = len(r.data or [])
            if count < 10:
                anomalies.append({
                    "type": "low_scan_rate",
                    "description": f"Solo {count} problemi scansionati questa settimana (attesi ≥10)",
                    "severity": "high",
                })
        except Exception:
            pass
        return anomalies
