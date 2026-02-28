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


    def _daily_report_emoji(self) -> str:
        return "\U0001f4e2"

    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> list:
        """CMO: prospect nuovi, smoke test events, brand activity — giorno precedente."""
        sections = []

        # 1. Nuovi prospect (giorno precedente)
        try:
            r = supabase.table("smoke_test_prospects").select("id,name,channel,status") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine).execute()
            if r.data:
                n = len(r.data)
                by_channel = {}
                for p in r.data:
                    ch = p.get("channel", "?")
                    by_channel[ch] = by_channel.get(ch, 0) + 1
                ch_lines = "\n".join(f"  {ch}: {cnt}" for ch, cnt in by_channel.items())
                sections.append(f"\U0001f465 NUOVI PROSPECT ({n})\n{ch_lines}")
        except Exception as e:
            logger.warning("[CMO] prospects error: %s", e)

        # 2. Smoke test events (giorno precedente)
        try:
            r = supabase.table("smoke_test_events").select("event_type,project_id,created_at") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).limit(10).execute()
            if r.data:
                ev_lines = "\n".join(
                    f"  {ev.get('event_type','?')} (proj #{ev.get('project_id','?')})"
                    for ev in r.data[:5]
                )
                sections.append(f"\U0001f52c SMOKE TEST EVENTS ({len(r.data)})\n{ev_lines}")
        except Exception as e:
            logger.warning("[CMO] smoke_test_events error: %s", e)

        # 3. Marketing reports (giorno precedente)
        try:
            r = supabase.table("marketing_reports").select("project_id,channel,recorded_at") \
                .gte("recorded_at", ieri_inizio).lt("recorded_at", ieri_fine) \
                .order("recorded_at", desc=True).limit(5).execute()
            if r.data:
                rep_lines = "\n".join(
                    f"  proj #{row.get('project_id','?')} | {row.get('channel','?')}"
                    for row in r.data
                )
                sections.append(f"\U0001f4c8 REPORT MARKETING ({len(r.data)})\n{rep_lines}")
        except Exception as e:
            logger.warning("[CMO] marketing_reports error: %s", e)

        # 4. Brand assets creati (giorno precedente)
        try:
            r = supabase.table("brand_assets").select("brand_name,project_id,status") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine).execute()
            if r.data:
                ba_lines = "\n".join(
                    f"  {row.get('brand_name','?')} (proj #{row.get('project_id','?')})"
                    for row in r.data[:5]
                )
                sections.append(f"\U0001f3a8 BRAND CREATI ({len(r.data)})\n{ba_lines}")
        except Exception as e:
            logger.warning("[CMO] brand_assets error: %s", e)

        return sections

    def generate_landing_page_html(self, project_id):
        """Genera HTML landing page via Claude per un progetto smoke test. Salva in projects.landing_html."""
        try:
            r = supabase.table("projects").select(
                "id,name,brand_name,brand_email,brand_domain,smoke_test_method"
            ).eq("id", project_id).execute()
            if not r.data:
                return {"error": "progetto non trovato"}
            project = r.data[0]
        except Exception as e:
            return {"error": str(e)}

        brand = project.get("brand_name") or project.get("name", "")
        email = project.get("brand_email") or ""
        domain = project.get("brand_domain") or ""

        prompt = (
            "Genera una landing page HTML completa e moderna per uno smoke test.\n"
            "Brand: " + brand + "\n"
            "Email: " + email + "\n"
            "Dominio: " + domain + "\n\n"
            "Requisiti:\n"
            "- HTML singolo file con CSS inline (no file esterni)\n"
            "- Responsive, mobile-first\n"
            "- Header con logo testuale, hero section con value proposition\n"
            "- CTA prominente (contattaci / richiedi demo)\n"
            "- 3 benefici chiave con icone emoji\n"
            "- Footer con email contatto\n"
            "- Palette: colori moderni e professionali\n"
            "- NESSUN brand 'brAIn' visibile, solo il brand del prodotto\n"
            "- Rispondi SOLO con il codice HTML completo, nient'altro."
        )

        try:
            html = self.call_claude(prompt, model="claude-sonnet-4-6", max_tokens=4000)
        except Exception as e:
            logger.warning("[CMO] generate_landing_page_html error: %s", e)
            return {"error": str(e)}

        # Salva in projects
        try:
            supabase.table("projects").update({
                "landing_html": html,
            }).eq("id", project_id).execute()
        except Exception as e:
            logger.warning("[CMO] save landing_html: %s", e)

        logger.info("[CMO] Landing HTML generata per project #%d (%d chars)", project_id, len(html or ""))
        return {"status": "ok", "project_id": project_id, "html_length": len(html or "")}

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
