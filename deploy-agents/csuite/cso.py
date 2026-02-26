"""CSO — Chief Strategy Officer. Dominio: strategia, mercati, competizione, opportunità."""
from core.base_chief import BaseChief
from core.config import supabase, logger


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
