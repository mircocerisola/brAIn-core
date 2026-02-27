"""CSO — Chief Strategy Officer. Dominio: strategia, mercati, competizione, opportunita'.
v5.15: prospect reali via Perplexity + start_smoke_test autonomo + istruzioni potenziate.
"""
import os
import json
from datetime import timedelta
import requests as _requests
from typing import Dict, List, Optional

from core.base_chief import BaseChief
from core.config import supabase, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome
from core.utils import search_perplexity

_SMOKE_TEST_INSTRUCTIONS = (
    "RESPONSABILITA' DIRETTA E NON DELEGABILE: lo smoke test e' tuo. "
    "Sei responsabile degli step 4-7 della pipeline: "
    "smoke_test_designing, smoke_test_running, smoke_test_results_ready, "
    "smoke_go/pivot/nogo. Quando ricevi QUALSIASI richiesta relativa a smoke test "
    "eseguila immediatamente: non dire 'non e' di mia competenza', non chiedere "
    "permessi, non delegare. Aggiorna pipeline_step in Supabase, "
    "usa Perplexity per trovare prospect reali con email, "
    "genera email template, manda card compatta nel topic cantiere."
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
    # REAL PROSPECT FINDING
    # ============================================================

    def find_real_prospects(self, target, n=50):
        """Cerca ristoranti con email reali via Perplexity. Cerca in piu' citta' se necessario."""
        prospects = []
        cities_tried = []

        # Prima ricerca generica
        query = (
            "Trova " + str(n) + " ristoranti " + target +
            " con email di contatto. Per ogni ristorante elenca: "
            "Nome | Citta | Email | Sito web | Telefono. "
            "Formato: una riga per ristorante, campi separati da |. "
            "Includi SOLO ristoranti con email reale confermata."
        )
        prospects.extend(self._parse_prospect_results(search_perplexity(query)))

        # Se non bastano, cerca per citta' specifiche
        extra_cities = ["Milano", "Torino", "Bergamo", "Brescia", "Novara"]
        for city in extra_cities:
            if len([p for p in prospects if p.get("email")]) >= n:
                break
            query_city = (
                "Trova 15 ristoranti a " + city + " " + target +
                " con email di contatto WhatsApp prenotazioni sito web 2025. "
                "Per ogni ristorante: Nome | Citta | Email | Sito web | Telefono. "
                "Formato: una riga, campi separati da |. Solo con email reale."
            )
            cities_tried.append(city)
            prospects.extend(self._parse_prospect_results(search_perplexity(query_city)))

        # Filtra: solo con email valida, dedup per email
        seen_emails = set()
        valid = []
        for p in prospects:
            email = p.get("email", "")
            if "@" in email and email not in seen_emails:
                seen_emails.add(email)
                valid.append(p)

        logger.info("[CSO] find_real_prospects: %d trovati (%d con email), citta=%s",
                    len(prospects), len(valid), cities_tried)
        return valid[:n]

    def _parse_prospect_results(self, text):
        """Parsa output Perplexity in lista prospect."""
        results = []
        if not text:
            return results
        for line in text.split("\n"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                email = ""
                website = ""
                telefono = ""
                for part in parts:
                    if "@" in part:
                        email = part[:200]
                    elif part.startswith("http") or ".it" in part or ".com" in part:
                        website = part[:200]
                    elif any(c.isdigit() for c in part) and len(part) > 6 and not part[0].isalpha():
                        telefono = part[:50]
                results.append({
                    "nome": parts[0][:100],
                    "citta": parts[1][:50] if len(parts) > 1 else "",
                    "email": email,
                    "website": website,
                    "telefono": telefono,
                })
        return results

    # ============================================================
    # START SMOKE TEST
    # ============================================================

    def start_smoke_test(self, project_id):
        """Avvia smoke test: trova prospect reali, salva in DB, manda card nel cantiere."""
        # Carica progetto
        try:
            r = supabase.table("projects").select("*").eq("id", project_id).execute()
            if not r.data:
                return {"error": "progetto non trovato"}
            project = r.data[0]
        except Exception as e:
            return {"error": str(e)}

        brand_name = project.get("brand_name") or project.get("name", "")
        topic_id = project.get("topic_id")
        bos_id = project.get("bos_id")

        # Target per prospect
        target = "in Lombardia e Piemonte"
        if bos_id:
            try:
                sol = supabase.table("solutions").select("title,customer_segment").eq("id", bos_id).execute()
                if sol.data:
                    target = (sol.data[0].get("customer_segment") or "in Lombardia e Piemonte")
            except Exception:
                pass

        # Trova prospect reali
        prospects = self.find_real_prospects(target, 50)

        # Salva in smoke_test_prospects
        saved = 0
        for p in prospects:
            try:
                supabase.table("smoke_test_prospects").insert({
                    "project_id": project_id,
                    "name": p.get("nome", "")[:100],
                    "company": p.get("nome", "")[:200],
                    "contact": p.get("email", "")[:200],
                    "channel": "email",
                    "status": "pending",
                }).execute()
                saved += 1
            except Exception as e:
                logger.warning("[CSO] prospect insert: %s", e)

        # Aggiorna pipeline
        try:
            supabase.table("projects").update({
                "pipeline_step": "smoke_test_designing",
                "pipeline_locked": False,
            }).eq("id", project_id).execute()
        except Exception:
            pass

        # Manda card nel topic cantiere
        if TELEGRAM_BOT_TOKEN and topic_id:
            self._send_smoke_card(project_id, brand_name, saved, topic_id)

        logger.info("[CSO] start_smoke_test: project=%d prospects=%d", project_id, saved)
        return {"status": "ok", "project_id": project_id, "prospects_saved": saved}

    def _send_smoke_card(self, project_id, brand_name, n_prospect, topic_id):
        """Invia card compatta smoke test nel topic cantiere."""
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])
            sep = "\u2500" * 15
            text = (
                "\U0001f52c SMOKE TEST \u2014 " + brand_name + "\n"
                + sep + "\n"
                + "\U0001f465 " + str(n_prospect) + " prospect con email confermata\n"
                + "\U0001f3af Metodo: cold outreach B2B\n"
                + "\U0001f4cd Lombardia e Piemonte\n"
                + sep
            )
            markup = {"inline_keyboard": [[
                {"text": "\u2705 Avvia outreach", "callback_data": "smoke_design_approve:" + str(project_id)},
                {"text": "\U0001f4c4 Dettaglio", "callback_data": "smoke_detail:" + str(project_id)},
            ]]}
            _requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                json={
                    "chat_id": group_id,
                    "message_thread_id": topic_id,
                    "text": text,
                    "reply_markup": markup,
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning("[CSO] send_smoke_card error: %s", e)

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
