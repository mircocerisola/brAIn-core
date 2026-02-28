"""CTO — Chief Technology Officer. Dominio: infrastruttura, codice, deploy, sicurezza tecnica.
v5.32: build_landing_from_brief() — riceve brief CMO, genera HTML, screenshot, card approvazione.
v5.18: Claude Code headless in Cloud Run Job. CTO triggera job, monitora output_log da DB.
"""
import json
import re
import os
import threading
import time
import requests as _requests
from datetime import timedelta
from typing import Any, Dict, List, Optional

from core.base_chief import BaseChief
from csuite.cultura import CULTURA_BRAIN
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome, format_time_rome
from csuite.utils import fmt

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "mircocerisola/brAIn-core"

# Cloud Run Job config
_GCP_PROJECT = "brain-core-487914"
_GCP_REGION = "europe-west3"
_JOB_NAME = "brain-code-executor"
_JOBS_API_URL = (
    "https://run.googleapis.com/v2/projects/" + _GCP_PROJECT +
    "/locations/" + _GCP_REGION +
    "/jobs/" + _JOB_NAME + ":run"
)

# Pattern: qualsiasi messaggio che contiene "Esegui con --dangerously-skip-permissions"
_PROMPT_PATTERN = re.compile(
    r'(Esegui con --dangerously-skip-permissions.+)',
    re.IGNORECASE | re.DOTALL,
)


def format_cto_message(titolo, contenuto=""):
    """Backward-compat wrapper. Usa fmt('cto', ...) per nuovo codice."""
    return fmt("cto", titolo, contenuto)


class CTO(BaseChief):
    name = "CTO"
    chief_id = "cto"
    domain = "tech"
    default_model = "claude-sonnet-4-6"
    MY_DOMAIN = ["codice", "infrastruttura", "deploy", "sicurezza", "architettura",
                 "bug", "cloud run", "docker", "github", "api tecnica"]
    MY_REFUSE_DOMAINS = ["marketing", "finanza", "legale", "hr", "vendite"]
    briefing_prompt_template = (
        "Sei il CTO di brAIn. Genera un briefing tecnico settimanale includendo: "
        "1) Salute dei servizi Cloud Run (uptime, errori), "
        "2) Nuove capability tecnologiche scoperte da Capability Scout, "
        "3) Debito tecnico identificato, "
        "4) Aggiornamenti modelli AI disponibili, "
        "5) Raccomandazioni architetturali."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            week_ago = (now_rome() - timedelta(days=7)).isoformat()
            r = supabase.table("agent_logs").select("agent_id,status,error") \
                .eq("status", "error").gte("created_at", week_ago).execute()
            errors = {}
            for row in (r.data or []):
                agent = row.get("agent_id", "unknown")
                errors[agent] = errors.get(agent, 0) + 1
            ctx["weekly_errors_by_agent"] = sorted(errors.items(), key=lambda x: x[1], reverse=True)[:5]
        except Exception as e:
            ctx["weekly_errors_by_agent"] = "errore lettura DB: " + str(e)
        try:
            r = supabase.table("capability_log").select("name,description,created_at") \
                .order("created_at", desc=True).limit(5).execute()
            ctx["recent_capabilities"] = r.data or []
        except Exception as e:
            ctx["recent_capabilities"] = "errore lettura DB: " + str(e)
        try:
            r = supabase.table("code_tasks").select(
                "id,title,status,requested_by,created_at"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["recent_code_tasks"] = r.data or []
        except Exception as e:
            ctx["recent_code_tasks"] = "errore lettura DB: " + str(e)
        return ctx

    def check_anomalies(self):
        anomalies = []
        try:
            hour_ago = (now_rome() - timedelta(hours=1)).isoformat()
            r = supabase.table("agent_logs").select("id,status").eq("status", "error") \
                .gte("created_at", hour_ago).execute()
            error_count = len(r.data or [])
            if error_count > 10:
                anomalies.append({
                    "type": "high_error_rate",
                    "description": str(error_count) + " errori nell'ultima ora",
                    "severity": "critical" if error_count > 20 else "high",
                })
        except Exception:
            pass
        return anomalies

    #
    # PATTERN DETECTION: "Esegui con --dangerously-skip-permissions"
    # -> CODEACTION card automatica, mai prompt nel messaggio
    #

    def answer_question(self, question, user_context=None,
                        project_context=None, topic_scope_id=None,
                        project_scope_id=None, recent_messages=None):
        """Override: intercetta 'Esegui con --dangerously-skip-permissions' PRIMA di qualsiasi logica."""
        match = _PROMPT_PATTERN.search(question)
        if match:
            raw_prompt = match.group(1).strip()
            logger.info("[CTO] Pattern 'dangerously-skip-permissions' rilevato (%d chars)", len(raw_prompt))
            return self._save_and_send_card(raw_prompt)

        return super().answer_question(
            question, user_context=user_context,
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )

    # SAVE + CODEACTION CARD

    def _save_and_send_card(self, prompt_text):
        """Salva in code_tasks (con dedup) e invia CODEACTION card. Return marker per skip."""
        meta = self._extract_prompt_meta(prompt_text)
        title_clean = meta["title"]

        existing_id = self._find_existing_task(prompt_text)

        if existing_id:
            try:
                supabase.table("code_tasks").update({
                    "prompt": prompt_text,
                    "title": title_clean,
                    "sandbox_check": json.dumps({
                        "source": "cto_direct_update",
                        "checked_at": now_rome().isoformat(),
                    }),
                }).eq("id", existing_id).execute()
                logger.info("[CTO] Dedup: aggiornato code_task #%d", existing_id)
            except Exception as e:
                logger.warning("[CTO] dedup update error: %s", e)
            return "<<CODEACTION_SENT>>"

        task_id = None
        try:
            result = supabase.table("code_tasks").insert({
                "title": title_clean,
                "prompt": prompt_text,
                "requested_by": "cto",
                "status": "pending_approval",
                "sandbox_passed": True,
                "sandbox_check": json.dumps({
                    "source": "cto_direct",
                    "checked_at": now_rome().isoformat(),
                }),
                "created_at": now_rome().isoformat(),
            }).execute()
            if result.data:
                task_id = result.data[0].get("id")
        except Exception as e:
            logger.warning("[CTO] code_tasks insert error: %s", e)
            return "Errore salvataggio code_task."

        if not task_id:
            return "Errore: code_task non creato."

        self._send_codeaction_card(task_id, meta)

        return "<<CODEACTION_SENT>>"

    def _find_existing_task(self, prompt_text):
        """Cerca code_task esistente per dedup (stesso prompt prefix, ultimo 1h, pending/ready)."""
        try:
            hour_ago = (now_rome() - timedelta(hours=1)).isoformat()
            r = supabase.table("code_tasks").select("id,status,prompt") \
                .eq("requested_by", "cto") \
                .gte("created_at", hour_ago) \
                .limit(5).execute()
            prefix = prompt_text[:200]
            for row in (r.data or []):
                if row.get("status") not in ("pending_approval", "ready"):
                    continue
                if (row.get("prompt") or "")[:200] == prefix:
                    return row["id"]
        except Exception as e:
            logger.warning("[CTO] dedup check error: %s", e)
        return None

    def _extract_prompt_meta(self, prompt_text):
        """Estrai titolo (prime 8 parole dopo il flag), file e stima dal prompt."""
        after_flag = re.sub(
            r'(?i)esegui con --dangerously-skip-permissions\.?\s*', '', prompt_text, count=1
        )
        after_flag = re.sub(
            r'(?i)non chiedere autorizzazione[^.]*\.\s*', '', after_flag, count=1
        )
        words = after_flag.strip().split()[:8]
        title = ' '.join(words).rstrip('.:,;').strip() if words else "Azione codice"
        if len(title) > 60:
            title = title[:57] + "..."

        file_matches = re.findall(r'[\w\-/]+\.py', prompt_text)
        main_file = file_matches[0] if file_matches else "da determinare"

        chars = len(prompt_text)
        if chars < 500:
            time_est = 2
        elif chars < 2000:
            time_est = 5
        else:
            time_est = 10

        return {"title": title, "main_file": main_file, "time_minutes": time_est}

    def _send_codeaction_card(self, task_id, meta):
        """Invia CODEACTION card al topic #technology con inline keyboard."""
        if not TELEGRAM_BOT_TOKEN or not task_id:
            return
        try:
            from core.templates import CODEACTION_CARD_TEMPLATE
            topic_r = supabase.table("org_config").select("value").eq("key", "chief_topic_cto").execute()
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not topic_r.data or not group_r.data:
                return
            topic_id = int(topic_r.data[0]["value"])
            group_id = int(group_r.data[0]["value"])

            card_text = CODEACTION_CARD_TEMPLATE.format(
                title=meta.get("title", "Azione codice"),
                main_file=meta.get("main_file", "da determinare"),
                time_minutes=meta.get("time_minutes", 5),
            )
            markup = {"inline_keyboard": [[
                {"text": "\u2705 Approva", "callback_data": "code_approve:" + str(task_id)},
                {"text": "\U0001f4c4 Dettaglio", "callback_data": "code_detail:" + str(task_id)},
            ]]}

            _requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                json={
                    "chat_id": group_id,
                    "message_thread_id": topic_id,
                    "text": card_text,
                    "reply_markup": markup,
                },
                timeout=10,
            )
            logger.info("[CTO] CODEACTION card #%d inviata al topic", task_id)
        except Exception as e:
            logger.warning("[CTO] send_codeaction_card error: %s", e)

    #
    # MESSAGGIO AGGIORNAMENTO — formato fisso 4 righe
    #

    @staticmethod
    def build_update_message(elapsed, output_log):
        """Costruisce messaggio aggiornamento."""
        lines = output_log.split("\n") if output_log else []
        clean = [l.strip() for l in lines
                 if l.strip() and "--dangerously-skip-permissions" not in l]
        last_line = clean[-1] if clean else "In esecuzione \u2014 nessun output ancora"
        if len(last_line) > 200:
            last_line = last_line[:197] + "..."
        return fmt("cto",
            "Aggiornamento " + str(elapsed) + " min",
            last_line
        )

    #
    # ESECUZIONE VIA CLOUD RUN JOB — trigger + monitor da DB
    #

    def _has_running_task(self):
        """Controlla se esiste un task con status running/ready."""
        try:
            r = supabase.table("code_tasks").select("id,title") \
                .in_("status", ["running", "ready"]).limit(1).execute()
            if r.data:
                return r.data[0]
        except Exception as e:
            logger.warning("[CTO] _has_running_task: %s", e)
        return None

    def _count_pending_tasks(self):
        """Conta task in coda (status=queued)."""
        try:
            r = supabase.table("code_tasks").select("id", count="exact") \
                .eq("status", "queued").limit(0).execute()
            return r.count if r.count is not None else 0
        except Exception:
            return 0

    def _dequeue_next_task(self):
        """Prende il prossimo task in coda FIFO e lo esegue."""
        try:
            r = supabase.table("code_tasks").select("id,title") \
                .eq("status", "queued") \
                .order("created_at").limit(1).execute()
            if not r.data:
                logger.info("[CTO] Coda vuota, nessun task da dequeue")
                return
            next_task = r.data[0]
            next_id = next_task["id"]
            next_title = (next_task.get("title") or "Azione codice")[:60]
            logger.info("[CTO] Dequeue task #%d: %s", next_id, next_title)
        except Exception as e:
            logger.warning("[CTO] dequeue read: %s", e)
            return

        # Setta ready e triggera
        try:
            supabase.table("code_tasks").update({"status": "ready"}).eq("id", next_id).execute()
        except Exception as e:
            logger.warning("[CTO] dequeue update ready: %s", e)
            return

        job_ok = self._trigger_cloud_run_job(next_id)
        ora = format_time_rome()

        # Recupera topic CTO per notifica
        group_id, topic_id = None, None
        try:
            gr = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            tr = supabase.table("org_config").select("value").eq("key", "chief_topic_cto").execute()
            if gr.data:
                group_id = int(gr.data[0]["value"])
            if tr.data:
                topic_id = int(tr.data[0]["value"])
        except Exception:
            pass

        if job_ok and group_id and topic_id:
            self._send_telegram(group_id, topic_id,
                fmt("cto",
                    "Task dalla coda avviato",
                    "Task: " + next_title + "\n"
                    "Avviato alle " + ora))
            # Monitor in background
            self._start_monitor(next_id, group_id, topic_id, next_title)

    def execute_approved_task(self, task_id, chat_id, thread_id=None):
        """Approva task: controlla coda, se libero esegue, altrimenti accoda."""

        # 1. Recupera titolo
        titolo = "Azione codice"
        try:
            r = supabase.table("code_tasks").select("title").eq("id", task_id).execute()
            if r.data:
                titolo = (r.data[0].get("title") or "Azione codice")[:60]
        except Exception:
            pass

        # 2. Controlla se c'e' un task gia in esecuzione
        running = self._has_running_task()
        if running:
            # Metti in coda
            try:
                supabase.table("code_tasks").update({
                    "status": "queued",
                }).eq("id", task_id).execute()
            except Exception as e:
                logger.warning("[CTO] update status queued: %s", e)
                self._send_telegram(chat_id, thread_id,
                                    fmt("cto","Errore", "Impossibile accodare task #" + str(task_id)))
                return

            pending_count = self._count_pending_tasks()
            running_title = (running.get("title") or "")[:40]
            self._send_telegram(chat_id, thread_id,
                fmt("cto",
                    "Task in coda",
                    "Task: " + titolo + "\n"
                    "Posizione: " + str(pending_count) + "\n"
                    "In esecuzione: " + running_title))
            logger.info("[CTO] Task #%d accodato (pos=%d), running=#%d",
                        task_id, pending_count, running["id"])
            return

        # 3. Nessun task in esecuzione: esegui subito
        try:
            supabase.table("code_tasks").update({
                "status": "ready",
            }).eq("id", task_id).execute()
        except Exception as e:
            logger.warning("[CTO] update status ready: %s", e)
            self._send_telegram(chat_id, thread_id,
                                fmt("cto","Errore", "Impossibile preparare task #" + str(task_id)))
            return

        # 4. Trigger Cloud Run Job con CODE_TASK_ID
        job_ok = self._trigger_cloud_run_job(task_id)

        # 5. Conferma a Mirco
        ora = format_time_rome()
        if job_ok:
            self._send_telegram(chat_id, thread_id,
                fmt("cto",
                    "Claude Code avviato in cloud",
                    "Task: " + titolo + "\n"
                    "Avviato alle " + ora + "\n"
                    "Esecuzione: Cloud Run Job\n"
                    "Aggiornamento ogni 5 minuti"))
        else:
            self._send_telegram(chat_id, thread_id,
                fmt("cto",
                    "Task in coda",
                    "Task #" + str(task_id) + ": " + titolo + "\n"
                    "Job trigger fallito, il task rimane in stato ready.\n"
                    "Verra' eseguito al prossimo run del job."))

        # 6. Monitor loop in background
        self._start_monitor(task_id, chat_id, thread_id, titolo)

    def _start_monitor(self, task_id, chat_id, thread_id, titolo):
        """Avvia monitor loop per un task in background."""
        _tid = task_id
        _cid = chat_id
        _thid = thread_id
        _ttl = titolo
        _self = self

        def _monitor():
            _elapsed = 0
            while True:
                time.sleep(300)  # 5 minuti
                _elapsed += 5

                try:
                    r = supabase.table("code_tasks").select(
                        "status,output_log,output"
                    ).eq("id", _tid).execute()
                    if not r.data:
                        break
                    task = r.data[0]
                except Exception as e:
                    logger.warning("[CTO] monitor read error: %s", e)
                    continue

                status = task.get("status", "")
                output_log = task.get("output_log") or ""

                # Task terminato — card compatta 4 righe + bottoni
                if status in ("done", "error", "interrupted"):
                    labels = {"done": "Completato", "error": "Fallito", "interrupted": "Interrotto"}
                    label = labels.get(status, "Completato")

                    completion_text = fmt("cto",
                        label,
                        "Task: " + _ttl + " " + str(_elapsed) + " min"
                    )
                    completion_markup = {"inline_keyboard": [[
                        {"text": "\U0001f4c4 Dettaglio", "callback_data": "code_detail:" + str(_tid)},
                        {"text": "\U0001f195 Nuovo task", "callback_data": "code_new:" + str(_tid)},
                    ]]}
                    _self._send_telegram(_cid, _thid, completion_text,
                                        reply_markup=completion_markup)

                    # Auto-dequeue prossimo task in coda
                    _self._dequeue_next_task()
                    break

                # Ancora in esecuzione — aggiornamento 4 righe
                markup = {"inline_keyboard": [[
                    {"text": "\U0001f4c4 Dettaglio", "callback_data": "code_detail:" + str(_tid)},
                    {"text": "\U0001f6d1 Interrompi", "callback_data": "code_interrupt:" + str(_tid)},
                ]]}

                msg = CTO.build_update_message(_elapsed, output_log)
                _self._send_telegram(_cid, _thid, msg, reply_markup=markup)

        threading.Thread(target=_monitor, daemon=True).start()
        logger.info("[CTO] Monitor avviato per task #%d", task_id)

    def interrupt_task(self, task_id, chat_id, thread_id=None):
        """Interrompe un task: setta status=interrupt_requested. Il Job lo rileva e termina."""
        try:
            supabase.table("code_tasks").update({
                "status": "interrupt_requested",
            }).eq("id", task_id).execute()
        except Exception as e:
            self._send_telegram(chat_id, thread_id,
                                fmt("cto","Errore interrupt", str(e)))
            return

        logger.info("[CTO] interrupt_task #%d -> interrupt_requested", task_id)

        titolo = ""
        try:
            r = supabase.table("code_tasks").select("title").eq("id", task_id).execute()
            if r.data:
                titolo = (r.data[0].get("title") or "")[:60]
        except Exception:
            pass

        self._send_telegram(chat_id, thread_id,
            fmt("cto",
                "Interruzione richiesta",
                "Task: " + titolo + "\n"
                "Il job terminera' il processo."))

    # CLOUD RUN JOB TRIGGER

    def _trigger_cloud_run_job(self, task_id):
        """Triggera il Cloud Run Job brain-code-executor con CODE_TASK_ID."""
        try:
            # Access token dal metadata server (solo su Cloud Run)
            token_r = _requests.get(
                "http://metadata.google.internal/computeMetadata/v1/"
                "instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
                timeout=5,
            )
            if token_r.status_code != 200:
                logger.warning("[CTO] metadata token error: %d", token_r.status_code)
                return False
            access_token = token_r.json()["access_token"]

            # Trigger job con override CODE_TASK_ID
            r = _requests.post(
                _JOBS_API_URL,
                headers={
                    "Authorization": "Bearer " + access_token,
                    "Content-Type": "application/json",
                },
                json={
                    "overrides": {
                        "containerOverrides": [{
                            "env": [
                                {"name": "CODE_TASK_ID", "value": str(task_id)},
                            ],
                        }],
                    },
                },
                timeout=30,
            )
            if r.status_code in (200, 201, 202):
                logger.info("[CTO] Cloud Run Job triggered task=%d resp=%d", task_id, r.status_code)
                return True
            else:
                logger.warning("[CTO] Job trigger failed: %d %s", r.status_code, r.text[:300])
                return False
        except Exception as e:
            logger.warning("[CTO] trigger job error: %s", e)
            return False

    # TELEGRAM HELPER

    def _send_telegram(self, chat_id, thread_id, text, reply_markup=None):
        """Helper per inviare messaggio Telegram."""
        if not TELEGRAM_BOT_TOKEN:
            return
        payload = {"chat_id": chat_id, "text": text}
        if thread_id:
            payload["message_thread_id"] = thread_id
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            _requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                json=payload, timeout=10,
            )
        except Exception as e:
            logger.warning("[CTO] telegram send error: %s", e)

    #
    # FIX 3: CONTESTO LIMITATO AL PROGETTO
    #

    def project_context_builder(self, project_id):
        """Costruisce contesto compatto (max 20 righe) per prompt Claude Code su progetto specifico."""
        ctx = {}
        try:
            r = supabase.table("projects").select(
                "name,slug,brand_name,pipeline_step,status,smoke_test_url"
            ).eq("id", project_id).execute()
            if r.data:
                p = r.data[0]
                ctx["project"] = {
                    "name": p.get("name", ""),
                    "slug": p.get("slug", ""),
                    "brand_name": p.get("brand_name", ""),
                    "pipeline_step": p.get("pipeline_step", ""),
                    "status": p.get("status", ""),
                }
        except Exception as e:
            logger.warning("[CTO] project_context_builder project: %s", e)

        try:
            r = supabase.table("project_tasks").select(
                "title,assigned_to,status"
            ).eq("project_id", project_id).eq("assigned_to", "cto").execute()
            ctx["cto_tasks"] = r.data or []
        except Exception as e:
            logger.warning("[CTO] project_context_builder tasks: %s", e)
            ctx["cto_tasks"] = []

        try:
            r = supabase.table("smoke_test_prospects").select("id").eq("project_id", project_id).execute()
            ctx["prospect_count"] = len(r.data or [])
        except Exception:
            ctx["prospect_count"] = 0

        # Formatta compatto (max 20 righe)
        lines = []
        p = ctx.get("project", {})
        lines.append("PROGETTO: " + (p.get("brand_name") or p.get("name", "?")))
        lines.append("Slug: " + p.get("slug", "?"))
        lines.append("Step: " + p.get("pipeline_step", "?"))
        lines.append("Status: " + p.get("status", "?"))
        lines.append("Prospect: " + str(ctx.get("prospect_count", 0)))
        if ctx.get("cto_tasks"):
            lines.append("")
            lines.append("TASK ASSEGNATI")
            for t in ctx["cto_tasks"]:
                lines.append("  [" + t.get("status", "?") + "] " + t.get("title", "?")[:50])
        return {"context_text": "\n".join(lines), "raw": ctx}

    #
    # FIX 4: CARD ANTEPRIMA CON BOTTONE ANNULLA
    #

    def send_preview_card(self, task_id, project_id=None):
        """Invia card anteprima con [Avvia] e [Annulla] prima di eseguire Claude Code."""
        if not TELEGRAM_BOT_TOKEN:
            return

        # Leggi task info
        title = "Azione codice"
        try:
            r = supabase.table("code_tasks").select("title,prompt").eq("id", task_id).execute()
            if r.data:
                title = (r.data[0].get("title") or "Azione codice")[:60]
                prompt = r.data[0].get("prompt") or ""
        except Exception:
            prompt = ""

        meta = self._extract_prompt_meta(prompt) if prompt else {"main_file": "da determinare", "time_minutes": 5}

        card_text = fmt("cto",
            "Prossima azione",
            "Task: " + title + "\n"
            "File coinvolti: " + meta.get("main_file", "da determinare") + "\n"
            "Stima: " + str(meta.get("time_minutes", 5)) + " min"
        )
        markup = {"inline_keyboard": [[
            {"text": "\u25b6\ufe0f Avvia", "callback_data": "code_approve:" + str(task_id)},
            {"text": "\u274c Annulla", "callback_data": "code_cancel_preview:" + str(task_id)},
        ]]}

        # Invia nel topic cantiere se progetto, altrimenti topic CTO
        topic_id = None
        group_id = None
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if group_r.data:
                group_id = int(group_r.data[0]["value"])

            if project_id:
                proj_r = supabase.table("projects").select("topic_id").eq("id", project_id).execute()
                if proj_r.data and proj_r.data[0].get("topic_id"):
                    topic_id = proj_r.data[0]["topic_id"]

            if not topic_id:
                topic_r = supabase.table("org_config").select("value").eq("key", "chief_topic_cto").execute()
                if topic_r.data:
                    topic_id = int(topic_r.data[0]["value"])
        except Exception as e:
            logger.warning("[CTO] send_preview_card topic lookup: %s", e)

        if group_id and topic_id:
            try:
                _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                    json={"chat_id": group_id, "message_thread_id": topic_id, "text": card_text, "reply_markup": markup},
                    timeout=10,
                )
                logger.info("[CTO] Preview card inviata task #%d", task_id)
            except Exception as e:
                logger.warning("[CTO] send_preview_card: %s", e)

    def handle_cancel_preview(self, task_id):
        """Gestisce click su Annulla: task torna pending, notifica COO."""
        try:
            supabase.table("code_tasks").update({"status": "pending"}).eq("id", task_id).execute()
        except Exception as e:
            logger.warning("[CTO] handle_cancel_preview update: %s", e)
            return {"error": str(e)}

        # Notifica COO via agent_events
        try:
            supabase.table("agent_events").insert({
                "event_type": "task_cancelled",
                "agent_from": "cto",
                "agent_to": "coo",
                "payload": json.dumps({"task_id": task_id, "motivo": "annullato da Mirco"}),
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[CTO] handle_cancel_preview event: %s", e)

        logger.info("[CTO] Task #%d annullato da Mirco, COO notificato", task_id)
        return {"status": "cancelled", "task_id": task_id}

    #
    # v5.32: BUILD LANDING DA BRIEF CMO
    #

    def build_landing_from_brief(self, project_id, brief=None, thread_id=None):
        """Riceve brief design dal CMO, genera HTML, screenshot, card approvazione Mirco."""
        logger.info("[CTO] build_landing_from_brief: project #%d", project_id)

        # Leggi brief da project_assets se non passato
        if not brief:
            try:
                r = supabase.table("project_assets").select("content") \
                    .eq("project_id", project_id).eq("asset_type", "landing_brief").execute()
                if r.data and r.data[0].get("content"):
                    brief = json.loads(r.data[0]["content"])
            except Exception as e:
                logger.warning("[CTO] read landing_brief: %s", e)

        if not brief:
            return {"error": "brief non trovato per project #" + str(project_id)}

        # Leggi dati progetto
        brand = ""
        email = ""
        domain_name = ""
        description = ""
        if not thread_id:
            try:
                r = supabase.table("projects").select(
                    "brand_name,name,brand_email,brand_domain,topic_id,description"
                ).eq("id", project_id).execute()
                if r.data:
                    p = r.data[0]
                    brand = p.get("brand_name") or p.get("name", "")
                    email = p.get("brand_email") or brief.get("email", "")
                    domain_name = p.get("brand_domain") or brief.get("domain", "")
                    description = p.get("description") or ""
                    thread_id = thread_id or p.get("topic_id")
            except Exception:
                pass

        if not brand:
            brand = brief.get("brand", "Progetto")

        # Notifica partenza
        if thread_id:
            self._send_telegram_to_topic(thread_id,
                fmt("cto", "Build landing HTML",
                    "Sto generando la landing page per " + brand + " dal brief CMO."))

        # Genera HTML da brief
        palette = brief.get("palette", {})
        hero = brief.get("hero", {})
        sections = brief.get("sections", [])
        fonts = brief.get("fonts", {})
        style_notes = brief.get("style_notes", "")

        sections_text = ""
        for sec in sections:
            sections_text += (
                "\n- Sezione '" + sec.get("title", "") + "' "
                "(tipo: " + sec.get("type", "generic") + "): "
                + ", ".join(sec.get("items", []))
            )

        html_prompt = (
            "Sei un web developer senior. Genera una landing page HTML completa, SINGOLO FILE.\n"
            "Brand: " + brand + "\n"
            "Email contatto: " + email + "\n"
            "Dominio: " + domain_name + "\n"
            "Descrizione: " + (description or "Prodotto SaaS") + "\n\n"
            "BRIEF DESIGN (dal CMO):\n"
            "Palette: primary=" + palette.get("primary", "#0D1117")
            + " accent=" + palette.get("accent", "#52B788")
            + " bg=" + palette.get("bg", "#ffffff") + "\n"
            "Font heading: " + fonts.get("heading", "Inter") + "\n"
            "Font body: " + fonts.get("body", "Inter") + "\n"
            "Hero headline: " + hero.get("headline", brand) + "\n"
            "Hero subheadline: " + hero.get("subheadline", "") + "\n"
            "Hero CTA: " + hero.get("cta_text", "Richiedi Demo") + "\n"
            "Sezioni:" + sections_text + "\n"
            "Stile: " + style_notes[:300] + "\n\n"
            "REQUISITI TECNICI:\n"
            "- CSS inline, responsive mobile-first\n"
            "- Meta SEO + Open Graph\n"
            "- Animazioni CSS sottili (fade-in, hover)\n"
            "- Font: Google Fonts " + fonts.get("heading", "Inter") + "\n"
            "- NESSUN brand brAIn visibile\n"
            "- Deve sembrare una landing professionale startup tech europea 2025\n"
            "- Rispondi SOLO con il codice HTML completo, nient'altro."
        )

        try:
            html = self.call_claude(html_prompt, model="claude-sonnet-4-6", max_tokens=8000)
        except Exception as e:
            logger.warning("[CTO] build_landing HTML generation: %s", e)
            return {"error": str(e)}

        # Salva HTML
        try:
            supabase.table("projects").update({"landing_html": html}).eq("id", project_id).execute()
        except Exception:
            pass
        try:
            supabase.table("project_assets").upsert({
                "project_id": project_id,
                "asset_type": "landing_html",
                "content": html,
                "filename": brand.lower().replace(" ", "-") + "-landing.html",
                "updated_at": now_rome().isoformat(),
            }).execute()
        except Exception:
            pass

        # Screenshot HTML → PNG
        screenshot_path = None
        try:
            from utils.html_screenshot import html_to_screenshot
            screenshot_path = html_to_screenshot(
                html, width=1200, height=900,
                filename_prefix="landing_" + str(project_id)
            )
        except Exception as e:
            logger.warning("[CTO] screenshot error: %s", e)

        # Invia preview + card [Approva Deploy][Modifica]
        if thread_id:
            self._send_landing_preview(project_id, brand, html, screenshot_path, thread_id)

        logger.info("[CTO] Landing HTML generata (%d chars) + screenshot per project #%d",
                    len(html or ""), project_id)
        return {"status": "ok", "project_id": project_id, "html_length": len(html or "")}

    def _send_landing_preview(self, project_id, brand, html, screenshot_path, topic_id):
        """Invia preview landing (screenshot + HTML doc) con bottoni."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])

            # Invia screenshot se disponibile
            if screenshot_path:
                caption = fmt("cto", "Preview landing " + brand,
                              "HTML generato (" + str(len(html or "")) + " chars)\n"
                              "Approvi per il deploy?")
                markup = {"inline_keyboard": [[
                    {"text": "\u2705 Approva deploy", "callback_data": "landing_deploy_approve:" + str(project_id)},
                    {"text": "\u270f\ufe0f Modifica", "callback_data": "landing_deploy_modify:" + str(project_id)},
                ]]}
                data_payload = {
                    "chat_id": group_id,
                    "caption": caption,
                    "reply_markup": json.dumps(markup),
                }
                if topic_id:
                    data_payload["message_thread_id"] = topic_id
                with open(screenshot_path, "rb") as f:
                    _requests.post(
                        "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendPhoto",
                        data=data_payload,
                        files={"photo": f},
                        timeout=30,
                    )

            # Invia anche HTML come documento
            import tempfile
            filename = brand.lower().replace(" ", "-") + "-landing.html"
            tmp_path = tempfile.gettempdir() + "/" + filename
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(html or "")
            doc_data = {"chat_id": group_id, "caption": "File HTML landing " + brand}
            if topic_id:
                doc_data["message_thread_id"] = topic_id
            with open(tmp_path, "rb") as f:
                _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendDocument",
                    data=doc_data,
                    files={"document": (filename, f, "text/html")},
                    timeout=15,
                )
        except Exception as e:
            logger.warning("[CTO] send_landing_preview: %s", e)

    def _send_telegram_to_topic(self, topic_id, text):
        """Helper: invia messaggio a uno specifico topic."""
        if not TELEGRAM_BOT_TOKEN:
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
            logger.warning("[CTO] send_telegram_to_topic: %s", e)

    #
    # GENERA PROMPT TECNICI PER ALTRI CHIEF
    #

    def build_technical_prompt(self, task_description, context=""):
        """Trasforma una richiesta funzionale in prompt tecnico completo per Claude Code."""
        system = (
            "Sei un tech lead senior di brAIn, un organismo AI-native.\n"
            "Trasforma questa richiesta funzionale in un prompt tecnico completo per Claude Code.\n"
            "Il prompt deve:\n"
            "- Iniziare con 'Esegui con --dangerously-skip-permissions.'\n"
            "- Essere preciso, completo, con path file esatti\n"
            "- Specificare COSA modificare e COME\n"
            "- Includere esempi di codice dove necessario\n"
            "- Indicare i file coinvolti\n"
            "- Finire con 'Committa, pusha, builda, deploya.'\n"
            "Stack: Python, Supabase, Cloud Run, Telegram Bot API.\n"
            "Repo: deploy-agents/ (agents-runner), deploy/ (command-center).\n"
            "Rispondi SOLO con il prompt tecnico, nient'altro."
        )
        prompt = "Richiesta: " + task_description
        if context:
            prompt = prompt + "\n\nContesto: " + context

        try:
            technical = self.call_claude(prompt, system=system, max_tokens=3000, model="claude-sonnet-4-6")
            return technical
        except Exception as e:
            logger.error("[CTO] build_technical_prompt error: %s", e)
            return task_description

    def generate_and_deliver_prompt(self, task_description, context=""):
        """Genera prompt tecnico + CODEACTION card. Per chiamate inter-agente."""
        technical_prompt = self.build_technical_prompt(task_description, context)

        meta = self._extract_prompt_meta(technical_prompt)
        existing_id = self._find_existing_task(technical_prompt)
        task_id = existing_id

        if not task_id:
            try:
                result = supabase.table("code_tasks").insert({
                    "title": meta["title"],
                    "prompt": technical_prompt,
                    "requested_by": "cto",
                    "status": "pending_approval",
                    "sandbox_passed": True,
                    "sandbox_check": json.dumps({
                        "source": "cto_inter_agent",
                        "task_description": task_description[:500],
                        "checked_at": now_rome().isoformat(),
                    }),
                    "created_at": now_rome().isoformat(),
                }).execute()
                if result.data:
                    task_id = result.data[0].get("id")
            except Exception as e:
                logger.warning("[CTO] code_tasks insert error: %s", e)

        if task_id and not existing_id:
            self._send_codeaction_card(task_id, meta)

        return {
            "status": "pending_approval",
            "task_id": task_id,
            "prompt": technical_prompt,
        }
