"""CSO — Chief Strategy Officer. Dominio: strategia, mercati, competizione, opportunita'.
v5.28: plan_smoke_test, delegate_execution_to_coo, analyze_bos_opportunity (strategy-only).
v5.15: prospect reali via Perplexity + start_smoke_test autonomo + istruzioni potenziate.
"""
import os
import json
from datetime import timedelta
import requests as _requests
from typing import Dict, List, Optional

from core.base_chief import BaseChief
from csuite.cultura import CULTURA_BRAIN
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome
from core.utils import search_perplexity
from csuite.utils import fmt

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
    MY_DOMAIN = ["strategia", "mercato", "competizione", "opportunita", "smoke test",
                 "pipeline", "problemi", "soluzioni", "bos", "trend"]
    MY_REFUSE_DOMAINS = ["codice", "finanza", "legale", "hr", "dns", "deploy", "marketing"]
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
                            recent_messages=None, query=None):
        base = super().build_system_prompt(
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
            query=query,
        )
        return base + "\n\nRESPONSABILITA SMOKE TEST\n" + _SMOKE_TEST_INSTRUCTIONS


    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> list:
        """Problemi scansionati, soluzioni generate, smoke test avviati — giorno precedente."""
        sections = []

        # 1. Nuovi problemi (giorno precedente)
        try:
            r = supabase.table("problems").select("id,title,weighted_score,status") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("weighted_score", desc=True).limit(5).execute()
            if r.data:
                pr_lines = "\n".join(
                    f"  [{row.get('status','?')}] {(row.get('title') or '')[:50]} (score {row.get('weighted_score','?')})"
                    for row in r.data
                )
                sections.append(f"\U0001f50d PROBLEMI SCANSIONATI ({len(r.data)})\n{pr_lines}")
        except Exception as e:
            logger.warning("[CSO] problems error: %s", e)

        # 2. Soluzioni generate (giorno precedente)
        try:
            r = supabase.table("solutions").select("id,title,status,bos_score") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).limit(5).execute()
            if r.data:
                sol_lines = "\n".join(
                    f"  [{row.get('status','?')}] {(row.get('title') or '')[:50]}"
                    for row in r.data
                )
                sections.append(f"\U0001f4a1 SOLUZIONI GENERATE ({len(r.data)})\n{sol_lines}")
        except Exception as e:
            logger.warning("[CSO] solutions error: %s", e)

        # 3. Smoke test avviati (giorno precedente)
        try:
            r = supabase.table("smoke_test_prospects").select("project_id,status") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine).execute()
            if r.data:
                by_proj = {}
                for p in r.data:
                    proj = p.get("project_id", "?")
                    by_proj[proj] = by_proj.get(proj, 0) + 1
                smoke_lines = "\n".join(f"  Progetto #{k}: {v} prospect" for k, v in by_proj.items())
                sections.append(f"\U0001f52c SMOKE TEST ({len(r.data)} prospect)\n{smoke_lines}")
        except Exception as e:
            logger.warning("[CSO] smoke_test_prospects error: %s", e)

        # 4. Anomalie pipeline (giorno precedente)
        try:
            r = supabase.table("problems").select("id") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine).execute()
            count_24h = len(r.data or [])
            if count_24h < 3:
                sections.append(
                    f"\u26a0\ufe0f ANOMALIA PIPELINE\n  Solo {count_24h} problemi scansionati nelle ultime 24h (attesi \u22653)"
                )
        except Exception:
            pass

        return sections

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

    #
    # REAL PROSPECT FINDING
    #

    def find_real_prospects(self, target, n=50):
        """DEPRECATED v5.28: usare plan_smoke_test + delegate_execution_to_coo.
        Cerca ristoranti con email reali via Perplexity. Cerca in piu' citta'."""
        logger.info("[CSO] DEPRECATED: find_real_prospects chiamato")
        import re as _re
        prospects = []
        cities_tried = []

        cities = ["Milano", "Roma", "Torino", "Bergamo", "Brescia",
                  "Bologna", "Firenze", "Napoli", "Verona", "Padova"]
        for city in cities:
            if len(prospects) >= n:
                break
            query = (
                "Lista 10 ristoranti a " + city + " " + target +
                " con indirizzo email di contatto reale. "
                "Per OGNI ristorante scrivi esattamente: "
                "NomeRistorante | " + city + " | email@esempio.it | www.sito.it | telefono "
                "Una riga per ristorante, campi separati da |. "
                "Solo ristoranti con email reale verificata dal loro sito web."
            )
            cities_tried.append(city)
            text = search_perplexity(query, max_tokens=2000)
            if not text:
                logger.warning("[CSO] Perplexity vuoto per %s", city)
                continue
            parsed = self._parse_prospect_results(text)
            logger.info("[CSO] %s: %d righe parsate, testo=%d chars",
                        city, len(parsed), len(text))
            prospects.extend(parsed)

        # Dedup per email
        seen = set()
        valid = []
        for p in prospects:
            email = p.get("email", "").lower().strip()
            if "@" in email and email not in seen:
                seen.add(email)
                valid.append(p)

        logger.info("[CSO] find_real_prospects: %d parsati, %d con email, citta=%s",
                    len(prospects), len(valid), cities_tried)
        return valid[:n]

    def _parse_prospect_results(self, text):
        """Parsa output Perplexity in lista prospect. Gestisce pipe, markdown, numeri."""
        import re as _re
        results = []
        if not text:
            return results
        for line in text.split("\n"):
            # Pulisci: rimuovi numerazione iniziale, asterischi, trattini
            clean = _re.sub(r"^\s*[\d]+[\.\)]\s*", "", line)
            clean = _re.sub(r"^\s*[-\*]\s*", "", clean)
            clean = clean.replace("**", "").strip()
            if not clean or len(clean) < 10:
                continue

            # Prova formato pipe
            if "|" in clean:
                parts = [p.strip() for p in clean.split("|")]
                if len(parts) >= 2:
                    entry = self._extract_fields(parts)
                    if entry:
                        results.append(entry)
                    continue

            # Prova formato con separatori vari (-, :, virgole)
            # Cerca almeno una email nella riga
            email_match = _re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", clean)
            if email_match:
                nome = clean[:email_match.start()].rstrip(" -:,").strip()
                nome = _re.sub(r"\s*[,\-:]\s*$", "", nome).strip()
                if nome:
                    results.append({
                        "nome": nome[:100],
                        "citta": "",
                        "email": email_match.group()[:200],
                        "website": "",
                        "telefono": "",
                    })
        return results

    def _extract_fields(self, parts):
        """Estrai nome/citta/email/website/telefono da parti pipe-delimited."""
        import re as _re
        email = ""
        website = ""
        telefono = ""
        nome = parts[0][:100].strip()
        citta = parts[1][:50].strip() if len(parts) > 1 else ""

        for part in parts:
            part = part.strip()
            email_m = _re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", part)
            if email_m and not email:
                email = email_m.group()[:200]
            elif ("http" in part or ".it" in part or ".com" in part) and not website:
                url_m = _re.search(r"https?://[\w./\-]+|www\.[\w./\-]+|[\w\-]+\.(it|com|net|org)", part)
                if url_m:
                    website = url_m.group()[:200]
            elif _re.search(r"\+?\d[\d\s\-/]{7,}", part) and not telefono:
                telefono = part[:50]

        if not nome or nome.lower() in ("nome", "ristorante", "name"):
            return None
        return {
            "nome": nome,
            "citta": citta,
            "email": email,
            "website": website,
            "telefono": telefono,
        }

    #
    # START SMOKE TEST
    #

    def start_smoke_test(self, project_id):
        """DEPRECATED v5.28: usare plan_smoke_test + delegate_execution_to_coo.
        Avvia smoke test: trova prospect reali, salva in DB, manda card nel cantiere."""
        logger.info("[CSO] DEPRECATED: start_smoke_test chiamato")
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
        """Invia card compatta smoke test nel topic #cantiere con template standard."""
        try:
            from core.templates import format_smoke_test_card, smoke_test_buttons
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])

            # Usa topic cantiere (non il topic CSO)
            cantiere_r = supabase.table("org_config").select("value").eq("key", "chief_topic_cantiere").execute()
            target_topic = int(cantiere_r.data[0]["value"]) if cantiere_r.data else topic_id

            text = format_smoke_test_card(
                brand_name=brand_name,
                obiettivo="Validazione mercato cold outreach B2B",
                n_prospect=n_prospect,
                durata=14,
                budget="EUR 0 (outreach)",
                kpi_successo="3+ risposte positive",
            )
            markup = smoke_test_buttons(project_id)

            _requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                json={
                    "chat_id": group_id,
                    "message_thread_id": target_topic,
                    "text": text,
                    "reply_markup": markup,
                },
                timeout=10,
            )
            logger.info("[CSO] Smoke card inviata in #cantiere per project #%d", project_id)
        except Exception as e:
            logger.warning("[CSO] send_smoke_card error: %s", e)

    #
    # RELAUNCH SMOKE TEST
    #

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

            text = (
                "\U0001f3af CSO\n"
                "Rilancio Smoke Test\n\n"
                "Progetto: " + project_name + "\n"
                "Pipeline: smoke_test_designing\n"
                "Azione: avvio processo completo di validazione mercato"
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

    #
    # STRATEGY-ONLY: pianifica senza eseguire
    #

    def plan_smoke_test(self, project_id, thread_id=None):
        """Genera piano smoke test JSON. Zero execution — solo strategia.
        Salva in projects.smoke_test_plan. Delega esecuzione a COO.
        """
        logger.info("[CSO] plan_smoke_test: project_id=%d", project_id)

        # Carica progetto
        try:
            r = supabase.table("projects").select(
                "id,name,brand_name,brand_domain,smoke_test_method"
            ).eq("id", project_id).execute()
            if not r.data:
                return {"error": "progetto non trovato"}
            project = r.data[0]
        except Exception as e:
            return {"error": str(e)}

        brand = project.get("brand_name") or project.get("name", "")
        method = project.get("smoke_test_method") or "cold_outreach"

        # Genera piano con Claude
        plan_prompt = (
            "Sei il CSO di brAIn. Genera un piano smoke test dettagliato.\n"
            "Progetto: " + brand + "\n"
            "Metodo: " + method + "\n\n"
            "Rispondi SOLO JSON:\n"
            '{"method": "...", "target_audience": "...", "kpi": {"metric": "...", "target": "..."},\n'
            ' "duration_days": 14, "budget_eur": 0,\n'
            ' "tasks_for_coo": [\n'
            '   {"assigned_to": "cmo/cto/cso/mirco", "title": "...", "description": "..."}\n'
            ' ]}\n\n'
            "Il piano deve essere concreto, con KPI misurabili e task assegnabili."
        )

        try:
            raw = self.call_claude(plan_prompt, model="claude-sonnet-4-6", max_tokens=1500)
            import re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if m:
                plan = json.loads(m.group(0))
            else:
                plan = {"method": method, "target_audience": "", "kpi": {}, "tasks_for_coo": []}
        except Exception as e:
            logger.warning("[CSO] plan_smoke_test Claude: %s", e)
            plan = {"method": method, "error": str(e)}

        # Salva in DB
        try:
            supabase.table("projects").update({
                "smoke_test_plan": json.dumps(plan),
            }).eq("id", project_id).execute()
        except Exception as e:
            logger.warning("[CSO] save smoke_test_plan: %s", e)

        # Notifica nel topic
        text = fmt("cso", "Piano Smoke Test " + brand,
                   "Metodo: " + plan.get("method", "?") + "\n"
                   "Target: " + plan.get("target_audience", "?") + "\n"
                   "Durata: " + str(plan.get("duration_days", 14)) + " giorni\n"
                   "Task generati: " + str(len(plan.get("tasks_for_coo", []))))
        if thread_id:
            self._send_to_topic(thread_id, text)

        logger.info("[CSO] Piano smoke test generato per project #%d", project_id)
        return {"status": "ok", "project_id": project_id, "plan": plan}

    def _send_to_topic(self, topic_id, text):
        """Invia messaggio nel topic Telegram."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if group_r.data:
                group_id = int(group_r.data[0]["value"])
                _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                    json={"chat_id": group_id, "message_thread_id": topic_id, "text": text},
                    timeout=10,
                )
        except Exception as e:
            logger.warning("[CSO] _send_to_topic: %s", e)

    def delegate_execution_to_coo(self, project_id, thread_id=None):
        """Legge piano da DB, inserisce agent_events per ogni task per il COO."""
        logger.info("[CSO] delegate_execution_to_coo: project_id=%d", project_id)

        try:
            r = supabase.table("projects").select("smoke_test_plan,brand_name,name") \
                .eq("id", project_id).execute()
            if not r.data:
                return {"error": "progetto non trovato"}
            project = r.data[0]
        except Exception as e:
            return {"error": str(e)}

        plan_raw = project.get("smoke_test_plan")
        if not plan_raw:
            return {"error": "nessun piano smoke test salvato"}

        try:
            plan = json.loads(plan_raw) if isinstance(plan_raw, str) else plan_raw
        except Exception:
            return {"error": "piano non parsabile"}

        tasks = plan.get("tasks_for_coo", [])
        events_created = 0
        now = now_rome()

        for task in tasks:
            try:
                supabase.table("agent_events").insert({
                    "event_type": "task_delegation",
                    "agent_from": "cso",
                    "agent_to": "coo",
                    "payload": json.dumps({
                        "project_id": project_id,
                        "assigned_to": task.get("assigned_to", "coo"),
                        "title": task.get("title", ""),
                        "description": task.get("description", ""),
                    }),
                    "created_at": now.isoformat(),
                }).execute()
                events_created += 1
            except Exception as e:
                logger.warning("[CSO] delegate event: %s", e)

        brand = project.get("brand_name") or project.get("name", "")
        text = fmt("cso", "Esecuzione delegata a COO",
                   "Progetto: " + brand + "\n"
                   "Task delegati: " + str(events_created))
        if thread_id:
            self._send_to_topic(thread_id, text)

        logger.info("[CSO] Delegati %d task a COO per project #%d", events_created, project_id)
        return {"status": "ok", "events_created": events_created}

    def analyze_bos_opportunity(self, problem_id, thread_id=None):
        """Analizza opportunita BOS per un problema. Salva in chief_decisions."""
        logger.info("[CSO] analyze_bos_opportunity: problem_id=%d", problem_id)

        try:
            r = supabase.table("problems").select(
                "id,title,description,weighted_score,sector"
            ).eq("id", problem_id).execute()
            if not r.data:
                return {"error": "problema non trovato"}
            problem = r.data[0]
        except Exception as e:
            return {"error": str(e)}

        analysis_prompt = (
            "Sei il CSO di brAIn. Analizza questa opportunita Blue Ocean Strategy.\n"
            "Problema: " + (problem.get("title") or "") + "\n"
            "Descrizione: " + (problem.get("description") or "")[:500] + "\n"
            "Score: " + str(problem.get("weighted_score", 0)) + "\n"
            "Settore: " + (problem.get("sector") or "") + "\n\n"
            "Analizza:\n"
            "1. Spazio di mercato inesplorato\n"
            "2. Fattori da eliminare/ridurre/aumentare/creare (framework BOS)\n"
            "3. Potenziale di differenziazione\n"
            "4. Raccomandazione: proceed/investigate/skip\n\n"
            "Formato: testo diretto, italiano, max 15 righe. Zero markdown."
        )

        try:
            analysis = self.call_claude(analysis_prompt, model="claude-sonnet-4-6", max_tokens=1000)
        except Exception as e:
            logger.warning("[CSO] analyze_bos Claude: %s", e)
            analysis = "Errore analisi: " + str(e)

        # Salva in chief_decisions
        try:
            supabase.table("chief_decisions").insert({
                "chief_domain": "strategy",
                "decision_type": "bos_analysis",
                "summary": "BOS " + (problem.get("title") or "")[:100],
                "full_text": analysis,
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[CSO] save bos_analysis: %s", e)

        text = fmt("cso", "Analisi BOS", analysis[:1000])
        if thread_id:
            self._send_to_topic(thread_id, text)

        return {"status": "ok", "problem_id": problem_id, "analysis": analysis}
