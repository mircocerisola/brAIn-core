"""CLO — Chief Legal Officer. Dominio: legale, compliance, contratti, rischi normativi.
v5.32: generate_legal_documents() — Privacy Policy, Cookie Policy, ToS, AI Disclosure.
       legal_gate_check() — nessun MVP online senza approvazione CLO.
"""
import json
import requests as _requests
from core.base_chief import BaseChief
from core.config import supabase, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome
from csuite.utils import fmt

# Documenti legali obbligatori prima del go-live
REQUIRED_LEGAL_DOCS = ["privacy_policy", "cookie_policy", "terms_of_service", "ai_disclosure"]


class CLO(BaseChief):
    name = "CLO"
    chief_id = "clo"
    domain = "legal"
    default_model = "claude-sonnet-4-6"
    MY_DOMAIN = ["legale", "compliance", "contratti", "gdpr", "privacy",
                 "rischio legale", "normativa", "ai act", "termini"]
    MY_REFUSE_DOMAINS = ["codice", "marketing", "finanza", "vendite", "hr", "dns", "deploy"]
    briefing_prompt_template = (
        "Sei il CLO di brAIn. Genera un briefing legale settimanale includendo: "
        "1) Violazioni etiche rilevate (ethics_violations), "
        "2) Progetti con review legale pendente, "
        "3) Nuove normative UE rilevanti (AI Act, GDPR updates), "
        "4) Rischi legali per progetti in corso, "
        "5) Raccomandazioni compliance."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            r = supabase.table("ethics_violations").select("project_id,principle_id,severity,blocked") \
                .eq("resolved", False).order("created_at", desc=True).limit(10).execute()
            ctx["open_violations"] = r.data or []
        except Exception:
            ctx["open_violations"] = []
        try:
            r = supabase.table("legal_reviews").select(
                "project_id,status,risks_found,created_at"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["legal_reviews"] = r.data if r.data else "nessun dato ancora registrato"
        except Exception:
            ctx["legal_reviews"] = "nessun dato ancora registrato"
        try:
            r = supabase.table("projects").select(
                "id,name,status,legal_status"
            ).neq("status", "archived").execute()
            ctx["projects_legal_status"] = r.data or []
        except Exception:
            ctx["projects_legal_status"] = []
        try:
            r = supabase.table("agent_logs").select("action,status,error").eq(
                "agent_id", "ethics_monitor"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["ethics_monitor_log"] = r.data or []
        except Exception:
            ctx["ethics_monitor_log"] = []
        return ctx


    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> list:
        """CLO: ethics violations, legal reviews, compliance log — giorno precedente."""
        sections = []

        # 1. Ethics violations (giorno precedente)
        try:
            r = supabase.table("ethics_violations").select(
                "project_id,principle_id,severity,blocked,resolved"
            ).gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).execute()
            if r.data:
                blocked = sum(1 for v in r.data if v.get("blocked"))
                unresolved = sum(1 for v in r.data if not v.get("resolved"))
                viol_lines = "\n".join(
                    f"  [{v.get('severity','?')}] proj #{v.get('project_id','?')} | {v.get('principle_id','?')}"
                    f"{' | BLOCCATO' if v.get('blocked') else ''}"
                    for v in r.data[:5]
                )
                sections.append(
                    f"\U0001f6ab VIOLATIONS ({len(r.data)} | {blocked} bloccate | {unresolved} aperte)\n{viol_lines}"
                )
        except Exception as e:
            logger.warning("[CLO] ethics_violations error: %s", e)

        # 2. Legal reviews (giorno precedente)
        try:
            r = supabase.table("legal_reviews").select(
                "project_id,status,risks_found,created_at"
            ).gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).limit(5).execute()
            if r.data:
                lr_lines = "\n".join(
                    f"  proj #{row.get('project_id','?')} | {row.get('status','?')} | rischi: {row.get('risks_found','?')}"
                    for row in r.data
                )
                sections.append(f"\U0001f4cb LEGAL REVIEWS ({len(r.data)})\n{lr_lines}")
        except Exception as e:
            logger.warning("[CLO] legal_reviews error: %s", e)

        # 3. Log ethics monitor (giorno precedente)
        try:
            r = supabase.table("agent_logs").select("action,status,error") \
                .eq("agent_id", "ethics_monitor") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).limit(5).execute()
            if r.data:
                errors = [l for l in r.data if l.get("status") == "error"]
                em_lines = "\n".join(
                    f"  {log.get('action','?')[:50]} [{log.get('status','?')}]"
                    for log in r.data[:5]
                )
                err_note = f" | {len(errors)} errori" if errors else ""
                sections.append(f"\U0001f916 ETHICS MONITOR ({len(r.data)}{err_note})\n{em_lines}")
        except Exception as e:
            logger.warning("[CLO] ethics_monitor error: %s", e)

        return sections

    def check_anomalies(self):
        anomalies = []
        try:
            r = supabase.table("ethics_violations").select("id").eq("blocked", True) \
                .eq("resolved", False).execute()
            blocked_count = len(r.data or [])
            if blocked_count > 0:
                anomalies.append({
                    "type": "ethics_blocked_projects",
                    "description": str(blocked_count) + " progetti bloccati per violazioni etiche non risolte",
                    "severity": "critical",
                })
        except Exception:
            pass
        return anomalies

    #
    # v5.32: LEGAL GATE — nessun MVP/landing online senza approvazione CLO
    #

    def legal_gate_check(self, project_id):
        """Verifica se tutti i documenti legali obbligatori sono approvati.
        Ritorna {"approved": True/False, "missing": [lista doc mancanti]}.
        """
        try:
            r = supabase.table("project_assets").select("asset_type,content") \
                .eq("project_id", project_id) \
                .in_("asset_type", REQUIRED_LEGAL_DOCS).execute()
            found = set()
            for row in (r.data or []):
                if row.get("content"):
                    found.add(row["asset_type"])
        except Exception as e:
            logger.warning("[CLO] legal_gate_check DB: %s", e)
            return {"approved": False, "missing": list(REQUIRED_LEGAL_DOCS), "error": str(e)}

        missing = [doc for doc in REQUIRED_LEGAL_DOCS if doc not in found]
        approved = len(missing) == 0

        # Controlla anche legal_status del progetto
        if approved:
            try:
                r = supabase.table("projects").select("legal_status") \
                    .eq("id", project_id).execute()
                if r.data:
                    legal_status = r.data[0].get("legal_status", "")
                    if legal_status != "approved":
                        approved = False
                        missing.append("legal_review_approval")
            except Exception:
                pass

        return {"approved": approved, "missing": missing}

    def generate_legal_documents(self, project_id, thread_id=None):
        """Genera tutti i documenti legali obbligatori per un progetto.
        Privacy Policy, Cookie Policy, ToS, AI Disclosure.
        Invia card approvazione a Mirco.
        """
        logger.info("[CLO] generate_legal_documents per project #%d", project_id)

        # Leggi dati progetto
        brand = ""
        email = ""
        domain_name = ""
        description = ""
        topic_id = thread_id
        try:
            r = supabase.table("projects").select(
                "brand_name,name,brand_email,brand_domain,topic_id,description"
            ).eq("id", project_id).execute()
            if r.data:
                p = r.data[0]
                brand = p.get("brand_name") or p.get("name", "Progetto")
                email = p.get("brand_email") or ""
                domain_name = p.get("brand_domain") or ""
                description = p.get("description") or ""
                if not topic_id:
                    topic_id = p.get("topic_id")
        except Exception as e:
            return {"error": str(e)}

        if not brand:
            return {"error": "brand non trovato per project #" + str(project_id)}

        # Notifica
        if topic_id:
            self._send_to_topic(topic_id,
                fmt("clo", "Documenti legali in generazione",
                    "Sto preparando Privacy Policy, Cookie Policy, ToS e AI Disclosure per " + brand + "."))

        # Genera ogni documento
        docs_generated = []
        for doc_type in REQUIRED_LEGAL_DOCS:
            doc_content = self._generate_single_legal_doc(
                doc_type, brand, email, domain_name, description
            )
            if doc_content:
                # Salva in project_assets
                try:
                    supabase.table("project_assets").upsert({
                        "project_id": project_id,
                        "asset_type": doc_type,
                        "content": doc_content,
                        "filename": brand.lower().replace(" ", "-") + "-" + doc_type.replace("_", "-") + ".html",
                        "updated_at": now_rome().isoformat(),
                    }).execute()
                    docs_generated.append(doc_type)
                except Exception as e:
                    logger.warning("[CLO] save %s: %s", doc_type, e)

        # Invia card approvazione
        if topic_id and docs_generated:
            self._send_legal_approval_card(project_id, brand, docs_generated, topic_id)

        logger.info("[CLO] Documenti legali generati: %d/%d per project #%d",
                    len(docs_generated), len(REQUIRED_LEGAL_DOCS), project_id)
        return {
            "status": "ok",
            "project_id": project_id,
            "docs_generated": docs_generated,
            "missing": [d for d in REQUIRED_LEGAL_DOCS if d not in docs_generated],
        }

    def _generate_single_legal_doc(self, doc_type, brand, email, domain_name, description):
        """Genera un singolo documento legale via Claude."""
        doc_labels = {
            "privacy_policy": "Privacy Policy",
            "cookie_policy": "Cookie Policy",
            "terms_of_service": "Terms of Service (Termini e Condizioni)",
            "ai_disclosure": "AI Disclosure (Informativa sull'uso di AI)",
        }
        label = doc_labels.get(doc_type, doc_type)

        prompt = (
            "Sei un avvocato esperto in diritto digitale europeo (GDPR, AI Act, ePrivacy).\n"
            "Genera un documento '" + label + "' completo per:\n"
            "Brand: " + brand + "\n"
            "Email: " + email + "\n"
            "Dominio: " + domain_name + "\n"
            "Descrizione servizio: " + (description or "Servizio SaaS") + "\n\n"
            "REQUISITI:\n"
            "- Conforme GDPR (EU 2016/679) e AI Act (EU 2024/1689)\n"
            "- Lingua: italiano\n"
            "- Formato: HTML semplice (h2 per sezioni, p per testo, ul/li per elenchi)\n"
            "- Includi: titolare trattamento, base giuridica, diritti interessato, contatti DPO\n"
            "- Data: " + now_rome().strftime("%d/%m/%Y") + "\n"
            "- Rispondi SOLO con il documento HTML, nient'altro."
        )

        try:
            doc = self.call_claude(prompt, model="claude-sonnet-4-6", max_tokens=4000)
            logger.info("[CLO] %s generata (%d chars)", label, len(doc or ""))
            return doc
        except Exception as e:
            logger.warning("[CLO] generate %s error: %s", doc_type, e)
            return None

    def _send_legal_approval_card(self, project_id, brand, docs_generated, topic_id):
        """Invia card con lista documenti + bottoni [Approva][Vedi][Modifica]."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])

            doc_labels = {
                "privacy_policy": "Privacy Policy",
                "cookie_policy": "Cookie Policy",
                "terms_of_service": "Terms of Service",
                "ai_disclosure": "AI Disclosure",
            }
            docs_list = "\n".join(
                "\u2705 " + doc_labels.get(d, d) for d in docs_generated
            )
            text = fmt("clo", "Documenti legali " + brand,
                       docs_list + "\n\nApprovi i documenti legali?")

            markup = {"inline_keyboard": [
                [
                    {"text": "\u2705 Approva tutti", "callback_data": "legal_docs_approve:" + str(project_id)},
                    {"text": "\U0001f4c4 Vedi", "callback_data": "legal_docs_view:" + str(project_id)},
                ],
                [
                    {"text": "\u270f\ufe0f Modifica", "callback_data": "legal_docs_modify:" + str(project_id)},
                ],
            ]}

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
            logger.info("[CLO] Legal approval card inviata per project #%d", project_id)
        except Exception as e:
            logger.warning("[CLO] send_legal_approval_card: %s", e)

    def handle_legal_docs_approve(self, project_id):
        """Callback: Mirco approva tutti i documenti legali."""
        logger.info("[CLO] Legal docs approvati per project #%d", project_id)
        try:
            supabase.table("projects").update({
                "legal_status": "approved",
            }).eq("id", project_id).execute()
        except Exception as e:
            logger.warning("[CLO] legal_status update: %s", e)
            return {"error": str(e)}

        # Crea agent_event per COO
        try:
            supabase.table("agent_events").insert({
                "event_type": "legal_approved",
                "agent_from": "clo",
                "agent_to": "coo",
                "payload": json.dumps({"project_id": project_id}),
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception:
            pass

        return {"status": "ok", "project_id": project_id}

    def handle_legal_docs_view(self, project_id, topic_id=None):
        """Callback: invia documenti legali come file."""
        try:
            r = supabase.table("project_assets").select("asset_type,content,filename") \
                .eq("project_id", project_id) \
                .in_("asset_type", REQUIRED_LEGAL_DOCS).execute()
            if not r.data:
                return {"error": "nessun documento trovato"}

            for doc in r.data:
                content = doc.get("content", "")
                if content and topic_id:
                    # Invia come testo (troncato)
                    label = doc.get("asset_type", "").replace("_", " ").title()
                    preview = content[:3000]
                    if len(content) > 3000:
                        preview += "\n\n[...troncato]"
                    self._send_to_topic(topic_id, fmt("clo", label, preview))

            return {"status": "ok", "docs_count": len(r.data)}
        except Exception as e:
            return {"error": str(e)}

    def _send_to_topic(self, topic_id, text):
        """Invia messaggio a topic specifico."""
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
            logger.warning("[CLO] send_to_topic: %s", e)
