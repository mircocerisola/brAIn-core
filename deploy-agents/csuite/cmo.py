"""CMO — Chief Marketing Officer. Dominio: marketing, brand, growth, conversion.
v5.27: format_cmo_message, answer_question override con keyword trigger,
       generate_bozza_visiva riscritta (1200x675, gradient, 3 card).
"""
import json
import re
import tempfile
import requests as _requests
from typing import Any, Dict, List, Optional
from core.base_chief import BaseChief
from core.config import supabase, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

# Keyword che triggerano generate_bozza_visiva
_BOZZA_KEYWORDS = ["bozza", "landing", "visiva", "design", "mockup", "wireframe", "pagina"]


def format_cmo_message(titolo, contenuto=""):
    """Helper formato CMO: icona + nome + titolo + contenuto. Zero separatori."""
    if contenuto:
        return "\U0001f3a8 CMO\n" + titolo + "\n\n" + contenuto
    return "\U0001f3a8 CMO\n" + titolo


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

    # ============================================================
    # ANSWER_QUESTION — override con logging + keyword trigger
    # ============================================================

    def answer_question(self, question, user_context=None,
                        project_context=None, topic_scope_id=None,
                        project_scope_id=None, recent_messages=None):
        """Override: logging completo + keyword trigger per bozza visiva."""
        logger.info("[CMO] answer_question ricevuta: %s", question[:200])

        # Keyword detection per bozza visiva
        q_lower = question.lower()
        matched_kw = [kw for kw in _BOZZA_KEYWORDS if kw in q_lower]
        if matched_kw:
            logger.info("[CMO] Keyword bozza rilevate: %s", matched_kw)
            return self._handle_bozza_request(question, project_scope_id)

        # Risposta standard via BaseChief
        logger.info("[CMO] Nessuna keyword bozza, risposta standard")
        response = super().answer_question(
            question, user_context=user_context,
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )
        logger.info("[CMO] Risposta generata: %d chars", len(response or ""))
        return response

    def _handle_bozza_request(self, question, project_scope_id=None):
        """Gestisce richiesta bozza: estrai project, genera PNG, rispondi."""
        if not _HAS_PILLOW:
            logger.warning("[CMO] Pillow non disponibile per bozza")
            return format_cmo_message("Bozza non disponibile",
                                      "Pillow non installato nel container.")

        # Cerca project_id dal contesto o dal messaggio
        project_id = None
        project_name = ""
        tagline = ""
        thread_id = None

        if project_scope_id:
            try:
                project_id = int(project_scope_id)
            except (ValueError, TypeError):
                pass

        # Se non abbiamo project_id, cerca il progetto attivo piu recente
        if not project_id:
            try:
                r = supabase.table("projects").select(
                    "id,name,brand_name"
                ).not_.in_("status", "archived,killed,failed") \
                    .order("created_at", desc=True).limit(1).execute()
                if r.data:
                    project_id = r.data[0]["id"]
            except Exception as e:
                logger.warning("[CMO] lookup project: %s", e)

        # Carica dati progetto
        if project_id:
            try:
                r = supabase.table("projects").select(
                    "id,name,brand_name,topic_id"
                ).eq("id", project_id).execute()
                if r.data:
                    p = r.data[0]
                    project_name = p.get("brand_name") or p.get("name", "Progetto")
                    thread_id = p.get("topic_id")
            except Exception as e:
                logger.warning("[CMO] load project data: %s", e)

        if not project_name:
            # Estrai dal messaggio
            project_name = self._extract_project_name(question)

        if not tagline:
            tagline = "La soluzione intelligente"

        logger.info("[CMO] Genero bozza: project_id=%s, name=%s", project_id, project_name)

        result = self.generate_bozza_visiva(project_name, tagline, thread_id, project_id)

        if result.get("status") == "ok":
            return format_cmo_message(
                "Bozza visiva generata",
                "Progetto: " + project_name + "\n"
                "File: " + result.get("path", ""))
        else:
            return format_cmo_message(
                "Errore bozza visiva",
                result.get("error", "errore sconosciuto"))

    def _extract_project_name(self, question):
        """Estrai nome progetto dal messaggio (dopo 'per' o 'di')."""
        patterns = [
            r'(?:per|di|del|dello)\s+["\']?([A-Z][a-zA-Z0-9\s]{2,30})',
            r'["\']([A-Z][a-zA-Z0-9\s]{2,30})["\']',
        ]
        for pat in patterns:
            m = re.search(pat, question)
            if m:
                return m.group(1).strip()
        return "Progetto"

    # ============================================================
    # DATI MARKETING
    # ============================================================

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

        if not any(mkt_data.values()):
            logger.info("[CMO] Nessun dato marketing, skip report")
            return None

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

        self._send_report_to_topic(text)
        return text

    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> list:
        """CMO: prospect nuovi, smoke test events, brand activity."""
        sections = []

        try:
            r = supabase.table("smoke_test_prospects").select("id,name,channel,status") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine).execute()
            if r.data:
                n = len(r.data)
                by_channel = {}
                for p in r.data:
                    ch = p.get("channel", "?")
                    by_channel[ch] = by_channel.get(ch, 0) + 1
                ch_str = ", ".join(ch + ": " + str(cnt) for ch, cnt in by_channel.items())
                sections.append("NUOVI PROSPECT (" + str(n) + ")\n  " + ch_str)
        except Exception as e:
            logger.warning("[CMO] prospects error: %s", e)

        try:
            r = supabase.table("smoke_test_events").select("event_type,project_id,created_at") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).limit(10).execute()
            if r.data:
                ev_lines = "\n".join(
                    "  " + ev.get("event_type", "?") + " (proj #" + str(ev.get("project_id", "?")) + ")"
                    for ev in r.data[:5]
                )
                sections.append("SMOKE TEST EVENTS (" + str(len(r.data)) + ")\n" + ev_lines)
        except Exception as e:
            logger.warning("[CMO] smoke_test_events error: %s", e)

        try:
            r = supabase.table("marketing_reports").select("project_id,channel,recorded_at") \
                .gte("recorded_at", ieri_inizio).lt("recorded_at", ieri_fine) \
                .order("recorded_at", desc=True).limit(5).execute()
            if r.data:
                rep_lines = "\n".join(
                    "  proj #" + str(row.get("project_id", "?")) + " | " + str(row.get("channel", "?"))
                    for row in r.data
                )
                sections.append("REPORT MARKETING (" + str(len(r.data)) + ")\n" + rep_lines)
        except Exception as e:
            logger.warning("[CMO] marketing_reports error: %s", e)

        try:
            r = supabase.table("brand_assets").select("brand_name,project_id,status") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine).execute()
            if r.data:
                ba_lines = "\n".join(
                    "  " + str(row.get("brand_name", "?")) + " (proj #" + str(row.get("project_id", "?")) + ")"
                    for row in r.data[:5]
                )
                sections.append("BRAND CREATI (" + str(len(r.data)) + ")\n" + ba_lines)
        except Exception as e:
            logger.warning("[CMO] brand_assets error: %s", e)

        return sections

    def generate_landing_page_html(self, project_id):
        """Genera HTML landing page via Claude per un progetto smoke test."""
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

        try:
            supabase.table("projects").update({
                "landing_html": html,
            }).eq("id", project_id).execute()
        except Exception as e:
            logger.warning("[CMO] save landing_html: %s", e)

        logger.info("[CMO] Landing HTML generata per project #%d (%d chars)", project_id, len(html or ""))
        return {"status": "ok", "project_id": project_id, "html_length": len(html or "")}

    # ============================================================
    # BOZZA VISIVA — 1200x675, gradient, 3 card sections
    # ============================================================

    def generate_bozza_visiva(self, project_name, tagline, thread_id=None, project_id=None):
        """Genera bozza visiva PNG 1200x675 con gradient, 3 card, invia via Telegram."""
        if not _HAS_PILLOW:
            logger.warning("[CMO] Pillow non installato, skip bozza visiva")
            return {"error": "pillow non installato"}

        logger.info("[CMO] generate_bozza_visiva: name=%s, tagline=%s, project_id=%s",
                    project_name, tagline, project_id)

        W, H = 1200, 675
        img = Image.new("RGB", (W, H), "#0f1923")
        draw = ImageDraw.Draw(img)

        # Gradient background (top-to-bottom, dark blue to dark teal)
        for y in range(H):
            ratio = y / H
            r = int(15 + ratio * 10)
            g = int(25 + ratio * 40)
            b = int(35 + ratio * 30)
            draw.line([(0, y), (W, y)], fill=(r, g, b))

        # Font loading con fallback
        font_big = None
        font_med = None
        font_sm = None
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        font_paths_regular = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
        for fp in font_paths:
            try:
                font_big = ImageFont.truetype(fp, 42)
                break
            except Exception:
                continue
        for fp in font_paths_regular:
            try:
                font_med = ImageFont.truetype(fp, 22)
                font_sm = ImageFont.truetype(fp, 16)
                break
            except Exception:
                continue
        if font_big is None:
            font_big = ImageFont.load_default()
        if font_med is None:
            font_med = font_big
        if font_sm is None:
            font_sm = font_big

        accent = "#25D366"
        text_color = "#ffffff"
        card_bg = (26, 38, 52, 200)  # semi-transparent dark

        # Header: brand name centrato
        draw.text((W // 2, 40), project_name, fill=accent, font=font_big, anchor="mt")

        # Tagline sotto il brand
        draw.text((W // 2, 95), tagline, fill=text_color, font=font_med, anchor="mt")

        # Linea accento
        draw.line([(W // 2 - 60, 130), (W // 2 + 60, 130)], fill=accent, width=3)

        # 3 Card sections orizzontali
        cards = [
            ("IL PROBLEMA", ["Gestire prenotazioni", "costa tempo e genera errori"]),
            ("LA SOLUZIONE", ["AI intelligente che", "gestisce tutto 24/7"]),
            ("INIZIA ORA", ["Richiedi una demo", "gratuita oggi"]),
        ]
        card_w = 340
        card_h = 200
        card_y = 160
        gap = (W - 3 * card_w) // 4

        for i, (card_title, card_lines) in enumerate(cards):
            x = gap + i * (card_w + gap)

            # Card background con bordo arrotondato
            draw.rounded_rectangle(
                [(x, card_y), (x + card_w, card_y + card_h)],
                radius=16, fill=(26, 38, 52)
            )
            # Bordo top accent
            draw.rounded_rectangle(
                [(x, card_y), (x + card_w, card_y + 4)],
                radius=2, fill=accent
            )

            # Card title
            draw.text((x + card_w // 2, card_y + 30), card_title,
                      fill=accent, font=font_med, anchor="mt")

            # Card body — una riga alla volta per evitare errore multiline anchor
            line_y = card_y + 80
            for body_line in card_lines:
                draw.text((x + card_w // 2, line_y), body_line,
                          fill=text_color, font=font_sm, anchor="mt")
                line_y += 24

        # CTA button
        btn_w, btn_h = 280, 50
        btn_x = (W - btn_w) // 2
        btn_y = card_y + card_h + 40
        draw.rounded_rectangle(
            [(btn_x, btn_y), (btn_x + btn_w, btn_y + btn_h)],
            radius=25, fill=accent
        )
        draw.text((W // 2, btn_y + btn_h // 2), "RICHIEDI DEMO",
                  fill="#ffffff", font=font_med, anchor="mm")

        # Footer
        footer_text = "Bozza visiva CMO | " + now_rome().strftime("%d/%m/%Y %H:%M")
        draw.text((W // 2, H - 25), footer_text, fill="#666666", font=font_sm, anchor="mm")

        # Salva PNG (tempfile per cross-platform)
        ts = now_rome().strftime("%Y%m%d_%H%M%S")
        pid_str = str(project_id) if project_id else "0"
        tmp_dir = tempfile.gettempdir()
        path = tmp_dir + "/bozza_" + pid_str + "_" + ts + ".png"
        img.save(path, "PNG")
        logger.info("[CMO] Bozza visiva generata: %s (%dx%d)", path, W, H)

        # Invia come foto Telegram
        self._send_bozza_photo_v2(project_name, path, thread_id, project_id)

        # post_task_learning
        self._log_bozza_learning(project_id)

        return {"status": "ok", "path": path, "size": str(W) + "x" + str(H)}

    def _send_bozza_photo_v2(self, project_name, path, thread_id=None, project_id=None):
        """Invia bozza come foto via Telegram. Usa thread_id se dato, altrimenti topic CMO."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])

            # thread_id: parametro diretto > topic progetto > topic CMO
            target_thread = thread_id
            if not target_thread and project_id:
                try:
                    proj_r = supabase.table("projects").select("topic_id").eq("id", project_id).execute()
                    if proj_r.data and proj_r.data[0].get("topic_id"):
                        target_thread = proj_r.data[0]["topic_id"]
                except Exception:
                    pass
            if not target_thread:
                try:
                    topic_r = supabase.table("org_config").select("value").eq("key", "chief_topic_cmo").execute()
                    if topic_r.data:
                        target_thread = int(topic_r.data[0]["value"])
                except Exception:
                    pass

            caption = format_cmo_message("Bozza landing " + project_name)

            data_payload = {"chat_id": group_id, "caption": caption}
            if target_thread:
                data_payload["message_thread_id"] = target_thread

            with open(path, "rb") as f:
                _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendPhoto",
                    data=data_payload,
                    files={"photo": f},
                    timeout=15,
                )
            logger.info("[CMO] Bozza foto inviata (thread=%s)", target_thread)
        except Exception as e:
            logger.warning("[CMO] send_bozza_photo_v2: %s", e)

    def _log_bozza_learning(self, project_id):
        """Registra post_task_learning per competenza visual_design."""
        try:
            from csuite.cpeo import post_task_learning
            post_task_learning("cmo", "generate_bozza_visiva", "success",
                               "visual_design_brand_identity", True)
        except Exception as e:
            logger.warning("[CMO] post_task_learning bozza: %s", e)

    # ============================================================
    # BRIEF TECNICO PER CTO
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

    # ============================================================
    # TELEGRAM HELPERS
    # ============================================================

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
