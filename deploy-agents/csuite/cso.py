"""CSO — Chief Strategy Officer. Dominio: strategia, mercati, competizione, opportunita'.
v5.14: responsabilita' diretta smoke test (step 4-7 pipeline). Mai delegare.
"""
import os
import requests as _requests
from typing import Dict, List, Optional

from core.base_chief import BaseChief
from core.config import supabase, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome

_SMOKE_TEST_INSTRUCTIONS = (
    "Sei responsabile diretto degli step 4-7 della pipeline: "
    "smoke_test_designing, smoke_test_running, smoke_test_results_ready, "
    "smoke_go/pivot/nogo. Quando ricevi una richiesta smoke test: "
    "aggiorna pipeline_step in Supabase, usa Perplexity per trovare prospect reali, "
    "chiama CMO via agent_to_agent_call per landing page, genera email template, "
    "manda card compatta nel topic cantiere. Non dire mai 'non e' di mia competenza' "
    "su questo tema. Non delegare mai lo smoke test ad altri Chief."
)


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

    def build_system_prompt(self, project_context=None,
                            topic_scope_id=None, project_scope_id=None,
                            recent_messages=None):
        base = super().build_system_prompt(
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )
        return base + "\n\n=== RESPONSABILITA' SMOKE TEST ===\n" + _SMOKE_TEST_INSTRUCTIONS

    def check_anomalies(self):
        anomalies = []
        try:
            from datetime import datetime, timezone, timedelta
            week_ago = (now_rome() - timedelta(days=7)).isoformat()
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

    # ============================================================
    # RELAUNCH SMOKE TEST
    # ============================================================

    def send_smoke_relaunch(self, project_id: int, project_name: str = "RestaAI") -> Dict:
        """Aggiorna pipeline_step a smoke_test_designing e manda messaggio in #strategy."""
        # 1. Aggiorna pipeline_step
        try:
            supabase.table("projects").update({
                "pipeline_step": "smoke_test_designing",
                "pipeline_locked": False,
            }).eq("id", project_id).execute()
            logger.info("[CSO] Pipeline reset a smoke_test_designing per project #%d", project_id)
        except Exception as e:
            logger.warning("[CSO] pipeline update error: %s", e)
            return {"error": str(e)}

        # 2. Manda messaggio in #strategy
        if not TELEGRAM_BOT_TOKEN:
            return {"status": "ok", "message_sent": False}
        try:
            topic_r = supabase.table("org_config").select("value").eq("key", "chief_topic_cso").execute()
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not topic_r.data or not group_r.data:
                return {"status": "ok", "message_sent": False}
            topic_id = int(topic_r.data[0]["value"])
            group_id = int(group_r.data[0]["value"])

            sep = "\u2500" * 15
            text = (
                "\U0001f680 RILANCIO SMOKE TEST\n"
                + sep + "\n"
                + "Progetto: " + project_name + "\n"
                + "Pipeline: smoke_test_designing\n"
                + "Azione: avvio processo completo di validazione mercato\n"
                + sep
            )
            _requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                json={"chat_id": group_id, "message_thread_id": topic_id, "text": text},
                timeout=10,
            )
            logger.info("[CSO] Messaggio relaunch smoke inviato in #strategy")
            return {"status": "ok", "message_sent": True}
        except Exception as e:
            logger.warning("[CSO] send_smoke_relaunch message error: %s", e)
            return {"status": "ok", "message_sent": False}
