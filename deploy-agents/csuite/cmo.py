"""CMO — Chief Marketing Officer. Dominio: marketing, brand, growth, conversion.
v5.34: plan_paid_ads() — piani Google Ads e Meta Ads per smoke test.
v5.32: CMO non scrive MAI codice. Flow landing: research → mockup image → card approvazione → brief CTO.
"""
import json
import re
import tempfile
import requests as _requests
from typing import Any, Dict, List, Optional
from core.base_chief import BaseChief
from csuite.cultura import CULTURA_BRAIN
from core.config import supabase, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome
from csuite.utils import fmt, fmt_task_received

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

# Keyword che triggerano design_landing_concept / bozza visiva
_BOZZA_KEYWORDS = [
    "bozza", "landing", "visiva", "design", "mockup", "wireframe", "pagina",
    "immagine", "preview", "visual", "genera immagine", "genera logo",
    "crea immagine", "crea logo", "grafica",
]

# Keyword che forzano un NUOVO concept (bypass feedback mode)
_NEW_CONCEPT_KEYWORDS = [
    "rifai", "ricrea", "nuova landing", "nuovo concept", "nuova bozza",
    "ricomincia", "da zero", "rifai da capo", "genera nuova",
]


def format_cmo_message(titolo, contenuto=""):
    """Backward-compat wrapper. Usa fmt('cmo', ...) per nuovo codice."""
    return fmt("cmo", titolo, contenuto)


class CMO(BaseChief):
    name = "CMO"
    chief_id = "cmo"
    domain = "marketing"
    default_model = "claude-sonnet-4-6"
    default_temperature = 0.7  # v5.36: creativo
    MY_DOMAIN = ["marketing", "brand", "landing", "growth", "copy",
                 "bozza", "visual", "logo", "contenuti", "ads"]
    MY_REFUSE_DOMAINS = ["codice", "finanza", "legale", "hr", "dns", "deploy", "infrastruttura"]
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

    #
    # ANSWER_QUESTION — override con logging + keyword trigger
    #

    def answer_question(self, question, user_context=None,
                        project_context=None, topic_scope_id=None,
                        project_scope_id=None, recent_messages=None):
        """Override: logging completo + keyword trigger per bozza visiva.
        v5.36d: distingue nuovo concept da feedback su concept esistente.
        """
        logger.info("[CMO] answer_question ricevuta: %s", question[:200])

        q_lower = question.lower()
        matched_kw = [kw for kw in _BOZZA_KEYWORDS if kw in q_lower]

        if matched_kw:
            # Verifica se e' una richiesta di NUOVO concept o FEEDBACK su esistente
            force_new = any(kw in q_lower for kw in _NEW_CONCEPT_KEYWORDS)

            if not force_new:
                # Controlla se esiste gia' un landing_brief per questo progetto
                _has_brief = self._project_has_landing_brief(project_scope_id)
                if _has_brief:
                    # Concept gia' esistente: tratta come feedback, non rigenerare
                    logger.info("[CMO] Brief esistente, tratto come feedback (keywords: %s)", matched_kw)
                    return super().answer_question(
                        question, user_context=user_context,
                        project_context=project_context,
                        topic_scope_id=topic_scope_id,
                        project_scope_id=project_scope_id,
                        recent_messages=recent_messages,
                    )

            logger.info("[CMO] Keyword bozza + nuovo concept: %s (force=%s)", matched_kw, force_new)
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

    def _project_has_landing_brief(self, project_scope_id):
        """Verifica se esiste gia' un landing_brief in project_assets."""
        pid = None
        if project_scope_id:
            try:
                pid = int(project_scope_id)
            except (ValueError, TypeError):
                pass
        if not pid:
            try:
                r = supabase.table("projects").select("id") \
                    .neq("status", "archived").order("created_at", desc=True).limit(1).execute()
                if r.data:
                    pid = r.data[0]["id"]
            except Exception:
                return False
        if not pid:
            return False
        try:
            r = supabase.table("project_assets").select("id") \
                .eq("project_id", pid).eq("asset_type", "landing_brief").limit(1).execute()
            return bool(r.data)
        except Exception:
            return False

    def _handle_bozza_request(self, question, project_scope_id=None):
        """Gestisce richiesta bozza/landing: estrai project, lancia design_landing_concept."""
        # Cerca project_id
        project_id = None
        thread_id = None

        if project_scope_id:
            try:
                project_id = int(project_scope_id)
            except (ValueError, TypeError):
                pass

        if not project_id:
            try:
                r = supabase.table("projects").select("id") \
                    .not_.in_("status", "archived,killed,failed") \
                    .order("created_at", desc=True).limit(1).execute()
                if r.data:
                    project_id = r.data[0]["id"]
            except Exception as e:
                logger.warning("[CMO] lookup project: %s", e)

        if project_id:
            try:
                r = supabase.table("projects").select("topic_id") \
                    .eq("id", project_id).execute()
                if r.data:
                    thread_id = r.data[0].get("topic_id")
            except Exception:
                pass

        if not project_id:
            return fmt("cmo", "Nessun progetto attivo",
                       "Non ho trovato un progetto attivo per generare il concept.")

        logger.info("[CMO] Avvio design_landing_concept: project_id=%s", project_id)
        result = self.design_landing_concept(project_id, thread_id)

        if result.get("status") == "ok":
            return fmt("cmo", "Concept landing in lavorazione",
                       "Ho inviato il mockup nel topic del progetto con card di approvazione.")
        else:
            return fmt("cmo", "Errore concept",
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

    #
    # DATI MARKETING
    #

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
            "- VIETATO: ** grassetto **, ## titoli, separatori di qualsiasi tipo\n"
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
        """Prospect nuovi, smoke test events, brand activity."""
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

    def design_landing_concept(self, project_id, thread_id=None):
        """v5.32: CMO NON scrive codice. Flow: ricerca → brief design → mockup immagine → card approvazione.
        Dopo approvazione Mirco, il brief va al CTO che scrive l'HTML.
        """
        from csuite.utils import web_search

        try:
            r = supabase.table("projects").select(
                "id,name,brand_name,brand_email,brand_domain,smoke_test_method,topic_id,description,spec_md"
            ).eq("id", project_id).execute()
            if not r.data:
                return {"error": "progetto non trovato"}
            project = r.data[0]
        except Exception as e:
            logger.error("[CMO] design_landing_concept DB error: %s", e)
            return {"error": "Problema tecnico nella lettura del progetto. Segnalato al CTO."}

        brand = project.get("brand_name") or project.get("name", "")
        email = project.get("brand_email") or ""
        domain_name = project.get("brand_domain") or ""
        description = project.get("description") or ""
        topic_id = thread_id or project.get("topic_id")

        # Notifica: sto ricercando
        if topic_id:
            self._send_report_to_topic_id(topic_id,
                fmt("cmo", "Landing concept in lavorazione",
                    "Cerco riferimenti e best practice online. 2-3 minuti."))

        # FASE 1: Ricerca online via Perplexity
        ref_search = web_search(
            "migliori landing page SaaS " + brand + " design moderno 2025 esempi",
            "cmo"
        )
        style_search = web_search(
            "best converting SaaS landing page design trends 2025 technology Italy",
            "cmo"
        )

        # FASE 2: Genera brief design via Claude (NO codice, solo strategia)
        brief_prompt = (
            "Sei il CMO di brAIn, esperto in marketing e brand identity.\n"
            "NON scrivere MAI codice (HTML, CSS, JS). Zero.\n"
            "Genera un BRIEF DI DESIGN per una landing page.\n\n"
            "Brand: " + brand + "\n"
            "Email: " + email + "\n"
            "Dominio: " + domain_name + "\n"
            "Descrizione: " + (description or "Prodotto SaaS innovativo") + "\n\n"
            "Riferimenti trovati:\n" + ref_search + "\n\n"
            "Trend design:\n" + style_search + "\n\n"
            "Il brief deve includere (formato JSON):\n"
            '{"palette": {"primary": "#hex", "secondary": "#hex", "accent": "#hex", "bg": "#hex"},\n'
            ' "fonts": {"heading": "nome font", "body": "nome font"},\n'
            ' "hero": {"headline": "testo", "subheadline": "testo", "cta_text": "testo"},\n'
            ' "sections": [\n'
            '   {"title": "...", "type": "pain_points|features|how_it_works|testimonials|cta",\n'
            '    "items": ["item1", "item2", "item3"]}\n'
            ' ],\n'
            ' "style_notes": "descrizione stile visivo in 2 frasi",\n'
            ' "footer": {"email": "...", "links": ["Privacy", "Terms"]}\n'
            "}\n\n"
            "Rispondi SOLO con il JSON, nient'altro."
        )

        try:
            brief_raw = self.call_claude(brief_prompt, model="claude-sonnet-4-6", max_tokens=2000)
        except Exception as e:
            logger.warning("[CMO] design_landing_concept brief error: %s", e)
            return {"status": "error", "message": "Problema tecnico nella generazione del brief. Ho segnalato il bug al CTO."}

        # Parsa il brief JSON
        brief = self._parse_brief_json(brief_raw)
        if not brief:
            brief = {
                "palette": {"primary": "#0D1117", "accent": "#52B788", "bg": "#ffffff"},
                "hero": {"headline": brand, "subheadline": description[:100], "cta_text": "Richiedi Demo"},
                "sections": [],
                "style_notes": brief_raw[:500] if brief_raw else "",
            }

        # Salva brief in project_assets
        try:
            supabase.table("project_assets").upsert({
                "project_id": project_id,
                "asset_type": "landing_brief",
                "content": json.dumps(brief),
                "filename": brand.lower().replace(" ", "-") + "-brief.json",
                "updated_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[CMO] save landing_brief: %s", e)

        # FASE 3: Genera mockup immagine (AI o Pillow fallback)
        mockup_path = self._generate_landing_mockup(brand, brief, project_id)

        # FASE 4: Invia mockup + card approvazione a Mirco
        if topic_id and mockup_path:
            self._send_landing_approval_card(project_id, brand, brief, mockup_path, topic_id)
        elif topic_id:
            # Nessun mockup: invia solo il brief testuale
            self._send_report_to_topic_id(topic_id,
                fmt("cmo", "Brief landing " + brand,
                    "Palette: " + str(brief.get("palette", {})) + "\n"
                    "Hero: " + str(brief.get("hero", {}).get("headline", "")) + "\n"
                    "Stile: " + str(brief.get("style_notes", ""))[:200]))

        self._log_bozza_learning(project_id)

        # v5.37: Notifica COO per creare TODO list landing flow
        try:
            supabase.table("agent_events").insert({
                "event_type": "landing_concept_created",
                "source_agent": "cmo",
                "target_agent": "coo",
                "payload": json.dumps({"project_id": project_id, "brand": brand}),
                "status": "pending",
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[CMO] landing_concept_created event: %s", e)

        logger.info("[CMO] Landing concept generato per project #%d", project_id)
        return {"status": "ok", "project_id": project_id, "brief": brief}

    def _parse_brief_json(self, raw_text):
        """Estrae JSON dal testo Claude (rimuove markdown fences se presenti)."""
        if not raw_text:
            return None
        clean = raw_text.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            clean = "\n".join(lines)
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            # Cerca JSON nel testo
            m = re.search(r'\{[\s\S]+\}', clean)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        return None

    def _generate_landing_mockup(self, brand, brief, project_id):
        """Genera mockup landing via image generation API. Fallback Pillow."""
        # Prova image generation API
        try:
            from utils.image_generation import generate_image
            hero = brief.get("hero", {})
            headline = hero.get("headline", brand)
            subheadline = hero.get("subheadline", "")
            palette = brief.get("palette", {})
            style = brief.get("style_notes", "modern SaaS")

            img_prompt = (
                "Professional SaaS landing page mockup for '" + brand + "'. "
                "Hero section with headline '" + headline + "'. "
                + ("Subheadline: '" + subheadline + "'. " if subheadline else "")
                + "Color palette: " + str(palette) + ". "
                "Style: " + style[:200] + ". "
                "Clean, modern design. Desktop browser view. High quality UI mockup."
            )
            path = generate_image(img_prompt, size="1792x1024",
                                  filename_prefix="landing_" + str(project_id))
            if path:
                logger.info("[CMO] Mockup AI generato: %s", path)
                return path
        except Exception as e:
            logger.warning("[CMO] image generation fallback: %s", e)

        # Fallback: Pillow bozza visiva
        if _HAS_PILLOW:
            hero = brief.get("hero", {})
            result = self.generate_bozza_visiva(
                brand,
                hero.get("subheadline", "La soluzione intelligente"),
                project_id=project_id,
            )
            return result.get("path")
        return None

    def _send_landing_approval_card(self, project_id, brand, brief, mockup_path, topic_id):
        """Invia mockup + card con bottoni [Approva][Modifica][Rifai]."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])

            hero = brief.get("hero", {})
            caption = fmt("cmo", "Concept landing " + brand,
                "Headline: " + hero.get("headline", brand) + "\n"
                "CTA: " + hero.get("cta_text", "Richiedi Demo") + "\n"
                "Stile: " + str(brief.get("style_notes", ""))[:100] + "\n\n"
                "Approvi questo concept?")

            markup = {"inline_keyboard": [[
                {"text": "\u2705 Approva", "callback_data": "landing_approve:" + str(project_id)},
                {"text": "\u270f\ufe0f Modifica", "callback_data": "landing_modify:" + str(project_id)},
                {"text": "\U0001f504 Rifai", "callback_data": "landing_redo:" + str(project_id)},
            ]]}

            data_payload = {
                "chat_id": group_id,
                "caption": caption,
                "reply_markup": json.dumps(markup),
            }
            if topic_id:
                data_payload["message_thread_id"] = topic_id

            with open(mockup_path, "rb") as f:
                _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendPhoto",
                    data=data_payload,
                    files={"photo": f},
                    timeout=30,
                )
            logger.info("[CMO] Landing approval card inviata (proj=%d)", project_id)
        except Exception as e:
            logger.warning("[CMO] send_landing_approval_card: %s", e)

    def generate_landing_page_html(self, project_id, thread_id=None):
        """DEPRECATED v5.32: usa design_landing_concept(). Mantiene backward compat."""
        logger.info("[CMO] generate_landing_page_html DEPRECATED -> design_landing_concept")
        return self.design_landing_concept(project_id, thread_id)

    def _send_landing_document(self, project_id, brand, html, topic_id):
        """Invia landing HTML come documento Telegram."""
        if not TELEGRAM_BOT_TOKEN or not html:
            return
        import tempfile
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])

            # Scrivi file temporaneo
            filename = brand.lower().replace(" ", "-") + "-landing.html"
            tmp_path = tempfile.gettempdir() + "/" + filename
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(html)

            caption = fmt("cmo", "Landing page " + brand,
                          "HTML pronto per deploy\n" + str(len(html)) + " caratteri")

            data_payload = {"chat_id": group_id, "caption": caption}
            if topic_id:
                data_payload["message_thread_id"] = topic_id

            with open(tmp_path, "rb") as f:
                _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendDocument",
                    data=data_payload,
                    files={"document": (filename, f, "text/html")},
                    timeout=15,
                )
            logger.info("[CMO] Landing document inviato (thread=%s)", topic_id)
        except Exception as e:
            logger.warning("[CMO] send_landing_document: %s", e)

    def _send_report_to_topic_id(self, topic_id, text):
        """Invia report a uno specifico topic ID."""
        if not TELEGRAM_BOT_TOKEN or not text:
            return
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])
            _requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                json={"chat_id": group_id, "message_thread_id": topic_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            logger.warning("[CMO] send_report_to_topic_id: %s", e)

    #
    # BOZZA VISIVA — 1200x675, gradient, 3 card sections
    #

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

            caption = fmt("cmo", "Bozza landing " + project_name)

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

    #
    # BRIEF TECNICO PER CTO
    #

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
                "source_agent": "cmo",
                "target_agent": "cto",
                "payload": json.dumps(payload),
                "created_at": now_rome().isoformat(),
            }).execute()
            logger.info("[CMO] landing_brief_ready pubblicato per project #%d", project_id)
            return {"status": "ok", "event_type": "landing_brief_ready"}
        except Exception as e:
            logger.warning("[CMO] publish_landing_brief: %s", e)
            return {"status": "error", "message": "Problema tecnico nella pubblicazione del brief. Ho segnalato il bug al CTO."}

    #
    # TELEGRAM HELPERS
    #

    def handle_landing_approve(self, project_id):
        """Callback: Mirco approva concept landing → invia brief al CTO."""
        logger.info("[CMO] Landing concept approvato per project #%d", project_id)

        # Leggi brief salvato
        brief = {}
        try:
            r = supabase.table("project_assets").select("content") \
                .eq("project_id", project_id).eq("asset_type", "landing_brief").execute()
            if r.data and r.data[0].get("content"):
                brief = json.loads(r.data[0]["content"])
        except Exception as e:
            logger.warning("[CMO] handle_landing_approve read brief: %s", e)

        # Leggi dati progetto
        brand = ""
        topic_id = None
        try:
            r = supabase.table("projects").select("brand_name,name,topic_id,brand_email,brand_domain") \
                .eq("id", project_id).execute()
            if r.data:
                brand = r.data[0].get("brand_name") or r.data[0].get("name", "")
                topic_id = r.data[0].get("topic_id")
                brief["email"] = r.data[0].get("brand_email") or ""
                brief["domain"] = r.data[0].get("brand_domain") or ""
        except Exception:
            pass

        # Invia brief al CTO via agent_events
        try:
            supabase.table("agent_events").insert({
                "event_type": "landing_brief_approved",
                "source_agent": "cmo",
                "target_agent": "cto",
                "payload": json.dumps({
                    "project_id": project_id,
                    "brand": brand,
                    "brief": brief,
                }),
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[CMO] landing_brief_approved event: %s", e)

        # Conferma a Mirco
        if topic_id:
            self._send_report_to_topic_id(topic_id,
                fmt("cmo", "Concept approvato",
                    "Brief inviato al CTO per la realizzazione HTML.\n"
                    "Il CTO ti inviera' una preview quando pronta."))

        return {"status": "ok", "project_id": project_id}

    def handle_landing_modify(self, project_id, feedback=""):
        """Callback: Mirco invia feedback → aggiorna brief → rigenera mockup → nuova card approvazione.
        v5.37: feedback applicato al brief esistente, non rigenerato da zero.
        """
        logger.info("[CMO] Landing modify: project #%d, feedback=%s", project_id, feedback[:100])
        if not feedback:
            return {"status": "awaiting_feedback", "project_id": project_id}

        # 1. Leggi brief esistente
        existing_brief = {}
        try:
            r = supabase.table("project_assets").select("content") \
                .eq("project_id", project_id).eq("asset_type", "landing_brief").execute()
            if r.data and r.data[0].get("content"):
                existing_brief = json.loads(r.data[0]["content"])
        except Exception as e:
            logger.warning("[CMO] handle_landing_modify read brief: %s", e)

        # 2. Leggi dati progetto
        brand = ""
        topic_id = None
        try:
            r = supabase.table("projects").select("brand_name,name,topic_id") \
                .eq("id", project_id).execute()
            if r.data:
                brand = r.data[0].get("brand_name") or r.data[0].get("name", "")
                topic_id = r.data[0].get("topic_id")
        except Exception:
            pass

        # 3. Notifica
        if topic_id:
            self._send_report_to_topic_id(topic_id,
                fmt("cmo", "Modifiche in corso",
                    "Aggiorno il concept con il tuo feedback. 1-2 minuti."))

        # 4. Applica feedback al brief via Claude
        modify_prompt = (
            "Sei il CMO di brAIn. Devi AGGIORNARE un brief di design landing page.\n"
            "NON scrivere MAI codice (HTML, CSS, JS).\n\n"
            "BRIEF ATTUALE:\n" + json.dumps(existing_brief, indent=2, ensure_ascii=False)[:3000] + "\n\n"
            "FEEDBACK DI MIRCO:\n" + feedback + "\n\n"
            "Applica le modifiche richieste al brief e rispondi SOLO con il JSON aggiornato.\n"
            "Mantieni la stessa struttura JSON. Modifica SOLO cio che Mirco ha chiesto."
        )

        try:
            updated_raw = self.call_claude(modify_prompt, model="claude-sonnet-4-6", max_tokens=2000)
            updated_brief = self._parse_brief_json(updated_raw)
            if not updated_brief:
                updated_brief = existing_brief
        except Exception as e:
            logger.warning("[CMO] modify brief Claude error: %s", e)
            updated_brief = existing_brief

        # 5. Salva brief aggiornato
        try:
            supabase.table("project_assets").upsert({
                "project_id": project_id,
                "asset_type": "landing_brief",
                "content": json.dumps(updated_brief),
                "filename": brand.lower().replace(" ", "-") + "-brief.json",
                "updated_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[CMO] save updated brief: %s", e)

        # 6. Rigenera mockup con brief aggiornato
        mockup_path = self._generate_landing_mockup(brand, updated_brief, project_id)

        # 7. Invia nuova card approvazione
        if topic_id and mockup_path:
            self._send_landing_approval_card(project_id, brand, updated_brief, mockup_path, topic_id)
        elif topic_id:
            self._send_report_to_topic_id(topic_id,
                fmt("cmo", "Brief aggiornato " + brand,
                    "Palette: " + str(updated_brief.get("palette", {})) + "\n"
                    "Hero: " + str(updated_brief.get("hero", {}).get("headline", "")) + "\n"
                    "Approvi questo concept aggiornato?"))

        logger.info("[CMO] Landing modify completato project #%d", project_id)
        return {"status": "ok", "project_id": project_id, "brief": updated_brief}

    def handle_landing_redo(self, project_id):
        """Callback: Mirco chiede di rifare il concept landing da zero."""
        logger.info("[CMO] Landing redo per project #%d", project_id)
        return self.design_landing_concept(project_id)

    #
    # TELEGRAM HELPERS
    #

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

    # ------------------------------------------------------------------
    # v5.34 FIX 6: Paid Ads capabilities
    # ------------------------------------------------------------------

    def plan_paid_ads(self, project_id, thread_id=None):
        """Genera piani per Google Ads e Meta Ads smoke test.
        Ricerca di mercato → budget stimato → creativita → targeting.
        """
        logger.info("[CMO] plan_paid_ads per project_id=%s", project_id)

        # Load progetto
        try:
            r = supabase.table("projects") \
                .select("id,name,brand_name,description,status,spec_md") \
                .eq("id", project_id).execute()
            if not r.data:
                return {"status": "error", "error": "Progetto non trovato"}
            project = r.data[0]
        except Exception as e:
            logger.error("[CMO] plan_paid_ads DB error: %s", e)
            return {"status": "error", "error": "Problema tecnico nella lettura del progetto. Segnalato al CTO."}

        brand = project.get("brand_name") or project.get("name") or "Progetto"

        # Ricerca di mercato via Perplexity
        from csuite.utils import web_search
        market_data = web_search(
            brand + " target audience demographics Italy paid ads benchmark CPC",
            "cmo"
        )

        # Genera piano via Sonnet
        plan_prompt = (
            "Sei il CMO di brAIn. Genera un piano paid ads per smoke test.\n"
            "Progetto: " + brand + "\n"
            "Descrizione: " + (project.get("description") or "N/A")[:300] + "\n\n"
            "Dati di mercato:\n" + market_data[:1000] + "\n\n"
            "Genera piano JSON con:\n"
            "1. google_ads: {budget_daily_eur, keywords[], ad_copy[], targeting, expected_cpc_eur}\n"
            "2. meta_ads: {budget_daily_eur, audiences[], creative_concepts[], placements[], expected_cpm_eur}\n"
            "3. total_budget_30d_eur: numero\n"
            "4. expected_results: {impressions, clicks, conversions}\n"
            "5. recommendations: [lista 3 raccomandazioni]\n\n"
            "Rispondi SOLO con JSON valido."
        )

        try:
            raw = self.call_claude(plan_prompt, max_tokens=2000, model="claude-sonnet-4-6")
            # Pulisci JSON
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            plan = json.loads(cleaned.strip())
        except Exception as e:
            logger.error("[CMO] plan_paid_ads parse error: %s", e)
            plan = {"error": "Impossibile generare piano", "raw": raw[:500] if 'raw' in dir() else ""}

        # Salva in chief_decisions
        try:
            supabase.table("chief_decisions").insert({
                "chief_domain": "marketing",
                "decision_type": "paid_ads_plan",
                "summary": "Piano Paid Ads " + brand,
                "full_text": json.dumps(plan, ensure_ascii=False)[:5000],
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[CMO] save paid_ads decision: %s", e)

        # Invia report al topic
        report_parts = [
            "Progetto: " + brand,
            "",
        ]
        if isinstance(plan, dict) and "error" not in plan:
            ga = plan.get("google_ads", {})
            ma = plan.get("meta_ads", {})
            report_parts.append("GOOGLE ADS")
            report_parts.append("Budget giornaliero: " + str(ga.get("budget_daily_eur", "N/A")) + " EUR")
            report_parts.append("Keywords: " + ", ".join(ga.get("keywords", [])[:5]))
            report_parts.append("CPC stimato: " + str(ga.get("expected_cpc_eur", "N/A")) + " EUR")
            report_parts.append("")
            report_parts.append("META ADS")
            report_parts.append("Budget giornaliero: " + str(ma.get("budget_daily_eur", "N/A")) + " EUR")
            report_parts.append("Audience: " + ", ".join(ma.get("audiences", [])[:3]))
            report_parts.append("CPM stimato: " + str(ma.get("expected_cpm_eur", "N/A")) + " EUR")
            report_parts.append("")
            report_parts.append("Budget totale 30gg: " + str(plan.get("total_budget_30d_eur", "N/A")) + " EUR")
        else:
            report_parts.append("Errore nella generazione del piano.")

        msg = fmt("cmo", "Piano Paid Ads", "\n".join(report_parts))
        if thread_id:
            self._send_report_to_topic_id(thread_id, msg)
        else:
            self._send_report_to_topic(msg)

        logger.info("[CMO] plan_paid_ads completato per %s", brand)
        return {"status": "ok", "project_id": project_id, "plan": plan}
