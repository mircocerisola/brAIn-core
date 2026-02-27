"""CMO — Chief Marketing Officer. Dominio: marketing, brand, growth, conversion."""
import json
import requests as _requests
from typing import Optional
from core.base_chief import BaseChief
from core.config import supabase, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome


class CMO(BaseChief):
    name = "CMO"
    chief_id = "cmo"
    domain = "marketing"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il CMO di brAIn. Genera un briefing marketing settimanale includendo: "
        "1) Status brand identity progetti attivi, "
        "2) Metriche chiave (visite, conversioni, CAC se disponibili), "
        "3) Canali con miglior performance, "
        "4) Raccomandazioni content/growth per settimana."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            r = supabase.table("brand_assets").select("project_id,brand_name,tagline,status") \
                .order("created_at", desc=True).limit(5).execute()
            ctx["brand_assets"] = r.data or []
        except Exception:
            ctx["brand_assets"] = []
        try:
            r = supabase.table("marketing_reports").select("*") \
                .order("recorded_at", desc=True).limit(5).execute()
            ctx["marketing_reports"] = r.data or []
        except Exception:
            ctx["marketing_reports"] = []
        return ctx

    def load_marketing_data(self):
        """Carica dati marketing reali da Supabase."""
        data = {"active_projects": [], "brand_briefs": [], "active_smokes": []}
        try:
            r = supabase.table("projects").select(
                "id,name,slug,brand_name,brand_email,brand_domain,pipeline_step,status,smoke_test_method"
            ).not_.in_("status", "archived,killed,failed").order("created_at", desc=True).limit(10).execute()
            data["active_projects"] = r.data or []
        except Exception as e:
            logger.warning("[CMO] load active_projects: %s", e)
        try:
            r = supabase.table("solutions").select(
                "id,title,brand_brief,status"
            ).neq("brand_brief", "").not_.is_("brand_brief", "null").order("created_at", desc=True).limit(5).execute()
            data["brand_briefs"] = r.data or []
        except Exception as e:
            logger.warning("[CMO] load brand_briefs: %s", e)
        try:
            r = supabase.table("projects").select(
                "id,name,brand_name,pipeline_step,smoke_test_method"
            ).eq("pipeline_step", "smoke_test_running").limit(5).execute()
            data["active_smokes"] = r.data or []
        except Exception as e:
            logger.warning("[CMO] load active_smokes: %s", e)
        return data

    def generate_brief_report(self) -> Optional[str]:
        """Report CMO con dati reali. Omette sezioni senza dati."""
        mkt_data = self.load_marketing_data()

        # Se nessun dato reale, non generare report vuoto
        if not any(mkt_data.values()):
            logger.info("[CMO] Nessun dato marketing, skip report")
            return None

        # Costruisci contesto solo con dati reali
        ctx_parts = []
        if mkt_data["active_smokes"]:
            smoke_lines = []
            for s in mkt_data["active_smokes"]:
                smoke_lines.append(
                    "- " + (s.get("brand_name") or s.get("name", "?")) +
                    " | metodo: " + (s.get("smoke_test_method") or "?") +
                    " | step: " + (s.get("pipeline_step") or "?")
                )
            ctx_parts.append("SMOKE TEST ATTIVI:\n" + "\n".join(smoke_lines))

        if mkt_data["brand_briefs"]:
            brief_lines = []
            for b in mkt_data["brand_briefs"]:
                brief_lines.append(
                    "- " + (b.get("title") or "?")[:60] +
                    " | brief: " + (b.get("brand_brief") or "")[:80]
                )
            ctx_parts.append("IDENTITA' IN SVILUPPO:\n" + "\n".join(brief_lines))

        if mkt_data["active_projects"]:
            proj_lines = []
            for p in mkt_data["active_projects"]:
                proj_lines.append(
                    "- " + (p.get("name") or "?")[:50] +
                    " | brand: " + (p.get("brand_name") or "N/A") +
                    " | step: " + (p.get("pipeline_step") or "?")
                )
            ctx_parts.append("PROGETTI ATTIVI:\n" + "\n".join(proj_lines))

        ctx = "\n\n".join(ctx_parts)
        today_str = now_rome().strftime("%d %b").lstrip("0")

        prompt = (
            "Sei il CMO di brAIn. Genera un report marketing breve (max 10 righe, italiano).\n"
            "Dominio: marketing\n"
            "Data: " + today_str + "\n"
            "Dati disponibili:\n" + ctx + "\n\n"
            "FORMATO OBBLIGATORIO (Telegram, NO Markdown):\n"
            "- Prima riga: emoji + CMO + ' · ' + data\n"
            "- Separatore: ───────────\n"
            "- Sezioni: emoji + TITOLO MAIUSCOLO\n"
            "- Dati concreti su righe separate con numeri reali\n"
            "- Chiudi con separatore ───────────\n"
            "- VIETATO: ** grassetto **, ## titoli\n"
            "- Se c'e' uno smoke test attivo, e' la sezione PRINCIPALE.\n"
            "- Mostra SOLO sezioni con dati reali. Zero fuffa."
        )

        try:
            text = self.call_claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=400)
        except Exception as e:
            logger.warning("[CMO] generate_brief_report call_claude error: %s", e)
            return None

        # Salva in chief_decisions
        try:
            supabase.table("chief_decisions").insert({
                "chief_domain": self.domain,
                "decision_type": "brief_report",
                "summary": text[:200] if text else "",
                "full_text": text or "",
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception:
            pass

        # Invia al topic CMO
        self._send_report_to_topic(text)

        return text

    def _send_report_to_topic(self, text):
        """Invia report al Forum Topic #marketing."""
        if not TELEGRAM_BOT_TOKEN or not text:
            return
        try:
            topic_r = supabase.table("org_config").select("value").eq("key", "chief_topic_cmo").execute()
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not topic_r.data or not group_r.data:
                return
            topic_id = int(topic_r.data[0]["value"])
            group_id = int(group_r.data[0]["value"])
            _requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                json={"chat_id": group_id, "message_thread_id": topic_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            logger.warning("[CMO] send_report_to_topic: %s", e)
