"""CMO — Chief Marketing Officer. Dominio: marketing, brand, growth, conversion."""
import json
import requests as _requests
from typing import Optional
from core.base_chief import BaseChief
from core.config import supabase, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False


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
            "- Prima riga ESATTA: \U0001f3a8 CMO\n"
            "- Seconda riga: titolo del report (es: Report Marketing)\n"
            "- Terza riga: vuota\n"
            "- Dalla quarta in poi: contenuto con dati concreti\n"
            "- VIETATO: ** grassetto **, ## titoli, --- trattini, ___ separatori\n"
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

    # ============================================================
    # FIX 2A: BOZZA VISIVA RAPIDA (Pillow PNG, zero HTML)
    # ============================================================

    def generate_bozza_visiva(self, project_id, variant=1):
        """Genera bozza visiva PNG per landing page. Zero HTML, solo immagine."""
        if not _HAS_PILLOW:
            logger.warning("[CMO] Pillow non installato, skip bozza visiva")
            return {"error": "pillow non installato"}

        try:
            r = supabase.table("projects").select(
                "id,name,brand_name,brand_email"
            ).eq("id", project_id).execute()
            if not r.data:
                return {"error": "progetto non trovato"}
            project = r.data[0]
        except Exception as e:
            return {"error": str(e)}

        brand = project.get("brand_name") or project.get("name", "Progetto")
        email = project.get("brand_email") or ""

        # Palette varianti
        palettes = [
            {"bg": "#0f1923", "accent": "#25D366", "text": "#ffffff", "section": "#1a2634"},
            {"bg": "#1a1a2e", "accent": "#e94560", "text": "#ffffff", "section": "#16213e"},
            {"bg": "#f8fafc", "accent": "#2563eb", "text": "#0f172a", "section": "#e2e8f0"},
        ]
        pal = palettes[(variant - 1) % len(palettes)]

        W, H = 800, 1200
        img = Image.new("RGB", (W, H), pal["bg"])
        draw = ImageDraw.Draw(img)

        # Font (fallback a default)
        try:
            font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
            font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except Exception:
            font_big = ImageFont.load_default()
            font_med = font_big
            font_sm = font_big

        y = 60
        # Header: brand name
        draw.text((W // 2, y), brand, fill=pal["accent"], font=font_big, anchor="mt")
        y += 80
        # Tagline
        draw.text((W // 2, y), "Il tuo assistente intelligente", fill=pal["text"], font=font_med, anchor="mt")
        y += 60

        # 3 sezioni
        sections_data = [
            ("IL PROBLEMA", "Gestire prenotazioni costa\ntempo e genera errori"),
            ("LA SOLUZIONE", "AI su WhatsApp che gestisce\nprenotazioni 24/7"),
            ("INIZIA ORA", "Richiedi una demo gratuita\n" + email),
        ]
        for title, body in sections_data:
            y += 30
            # Box sezione
            draw.rounded_rectangle(
                [(60, y), (W - 60, y + 200)],
                radius=16, fill=pal["section"]
            )
            draw.text((W // 2, y + 30), title, fill=pal["accent"], font=font_med, anchor="mt")
            draw.text((W // 2, y + 80), body, fill=pal["text"], font=font_sm, anchor="mt")
            y += 210

        # CTA button
        y += 30
        draw.rounded_rectangle(
            [(200, y), (600, y + 60)],
            radius=30, fill=pal["accent"]
        )
        draw.text((W // 2, y + 30), "RICHIEDI DEMO", fill="#ffffff", font=font_med, anchor="mm")

        # Footer
        draw.text((W // 2, H - 40), "Bozza visiva CMO v" + str(variant), fill="#666666", font=font_sm, anchor="mt")

        # Salva PNG
        ts = now_rome().strftime("%Y%m%d_%H%M%S")
        path = "/tmp/bozza_" + str(project_id) + "_" + ts + ".png"
        img.save(path, "PNG")
        logger.info("[CMO] Bozza visiva generata: %s", path)

        # Invia come foto Telegram
        self._send_bozza_photo(project_id, brand, path)

        return {"status": "ok", "path": path, "variant": variant}

    def _send_bozza_photo(self, project_id, brand, path):
        """Invia bozza come foto nel topic cantiere."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            proj_r = supabase.table("projects").select("topic_id").eq("id", project_id).execute()
            if not group_r.data or not proj_r.data:
                return
            group_id = int(group_r.data[0]["value"])
            topic_id = proj_r.data[0].get("topic_id")
            if not topic_id:
                return

            with open(path, "rb") as f:
                _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendPhoto",
                    data={
                        "chat_id": group_id,
                        "message_thread_id": topic_id,
                        "caption": "\U0001f3a8 CMO\nBozza landing " + brand,
                    },
                    files={"photo": f},
                    timeout=15,
                )
            logger.info("[CMO] Bozza foto inviata project #%d", project_id)
        except Exception as e:
            logger.warning("[CMO] send_bozza_photo: %s", e)

    # ============================================================
    # FIX 2B: BRIEF TECNICO PER CTO (dopo approvazione Mirco)
    # ============================================================

    def publish_landing_brief_for_cto(self, project_id, palette, fonts, sections_copy, style_notes="", bozza_path=""):
        """Pubblica evento landing_brief_ready per il CTO via agent_events."""
        payload = {
            "project_id": project_id,
            "palette": palette,
            "fonts": fonts,
            "sections": sections_copy,
            "style_notes": style_notes,
            "bozza_path": bozza_path,
        }
        try:
            supabase.table("agent_events").insert({
                "event_type": "landing_brief_ready",
                "agent_from": "cmo",
                "agent_to": "cto",
                "payload": json.dumps(payload),
                "created_at": now_rome().isoformat(),
            }).execute()
            logger.info("[CMO] landing_brief_ready pubblicato per project #%d", project_id)
            return {"status": "ok", "event_type": "landing_brief_ready"}
        except Exception as e:
            logger.warning("[CMO] publish_landing_brief: %s", e)
            return {"error": str(e)}

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
