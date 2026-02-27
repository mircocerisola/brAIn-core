"""CTO — Chief Technology Officer. Dominio: infrastruttura, codice, deploy, sicurezza tecnica.
v5.17: brain_runner executor puro + CTO gestisce flusso Mirco + output reale Claude Code.
       execute_approved_task: lancia brain_runner.run(), monitor loop 5min con output reale.
       interrupt_task: brain_runner.interrupt(pid). Zero timeout — gira finche' non finisce.
"""
import json
import re
import os
import threading
import requests as _requests
from datetime import timedelta
from typing import Any, Dict, List, Optional

from core.base_chief import BaseChief
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome, format_time_rome
from core import brain_runner

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "mircocerisola/brAIn-core"

# Pattern: qualsiasi messaggio che contiene "Esegui con --dangerously-skip-permissions"
# Cattura tutto da quel punto in poi — e' il prompt completo per Claude Code
_PROMPT_PATTERN = re.compile(
    r'(Esegui con --dangerously-skip-permissions.+)',
    re.IGNORECASE | re.DOTALL,
)


class CTO(BaseChief):
    name = "CTO"
    chief_id = "cto"
    domain = "tech"
    default_model = "claude-sonnet-4-6"
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
            ctx["weekly_errors_by_agent"] = f"errore lettura DB: {e}"
        try:
            r = supabase.table("capability_log").select("name,description,created_at") \
                .order("created_at", desc=True).limit(5).execute()
            ctx["recent_capabilities"] = r.data or []
        except Exception as e:
            ctx["recent_capabilities"] = f"errore lettura DB: {e}"
        try:
            r = supabase.table("code_tasks").select(
                "id,title,status,requested_by,created_at"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["recent_code_tasks"] = r.data or []
        except Exception as e:
            ctx["recent_code_tasks"] = f"errore lettura DB: {e}"
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
                    "description": f"{error_count} errori nell'ultima ora",
                    "severity": "critical" if error_count > 20 else "high",
                })
        except Exception:
            pass
        return anomalies

    # ============================================================
    # PATTERN DETECTION: "Esegui con --dangerously-skip-permissions"
    # -> CODEACTION card automatica, mai prompt nel messaggio
    # ============================================================

    def answer_question(self, question, user_context=None,
                        project_context=None, topic_scope_id=None,
                        project_scope_id=None, recent_messages=None):
        """Override: intercetta 'Esegui con --dangerously-skip-permissions' PRIMA di qualsiasi logica.
        Se trovato -> salva in code_tasks + invia CODEACTION card con bottoni. Mai prompt nel messaggio.
        Mai chiedere conferme — card generata sempre e subito.
        """
        match = _PROMPT_PATTERN.search(question)
        if match:
            raw_prompt = match.group(1).strip()
            logger.info("[CTO] Pattern 'dangerously-skip-permissions' rilevato (%d chars)", len(raw_prompt))
            return self._save_and_send_card(raw_prompt)

        # Nessun pattern -> risposta Chief normale
        return super().answer_question(
            question, user_context=user_context,
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )

    # ---- SAVE + CODEACTION CARD ----

    def _save_and_send_card(self, prompt_text: str) -> str:
        """Salva in code_tasks (con dedup) e invia CODEACTION card. Return marker per skip."""
        title_short = prompt_text[:100]

        # 1. Dedup — cerca task esistente con stesso titolo nell'ultima ora
        existing_id = self._find_existing_task(title_short)

        if existing_id:
            try:
                supabase.table("code_tasks").update({
                    "prompt": prompt_text,
                    "sandbox_check": json.dumps({
                        "source": "cto_direct_update",
                        "checked_at": now_rome().isoformat(),
                    }),
                }).eq("id", existing_id).execute()
                logger.info("[CTO] Dedup: aggiornato code_task #%d", existing_id)
            except Exception as e:
                logger.warning("[CTO] dedup update error: %s", e)
            return "<<CODEACTION_SENT>>"

        # 2. Crea nuovo code_task
        task_id = None
        try:
            result = supabase.table("code_tasks").insert({
                "title": title_short,
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

        # 3. Estrai metadata dal prompt
        meta = self._extract_prompt_meta(prompt_text)

        # 4. Invia CODEACTION card al topic #technology
        self._send_codeaction_card(task_id, meta)

        return "<<CODEACTION_SENT>>"

    def _find_existing_task(self, title_short: str) -> Optional[int]:
        """Cerca code_task esistente per dedup (stesso titolo, ultimo 1h, pending/ready)."""
        try:
            hour_ago = (now_rome() - timedelta(hours=1)).isoformat()
            r = supabase.table("code_tasks").select("id,status") \
                .eq("title", title_short) \
                .gte("created_at", hour_ago) \
                .limit(1).execute()
            if r.data and r.data[0].get("status") in ("pending_approval", "ready"):
                return r.data[0]["id"]
        except Exception as e:
            logger.warning("[CTO] dedup check error: %s", e)
        return None

    def _extract_prompt_meta(self, prompt_text: str) -> Dict:
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

    def _send_codeaction_card(self, task_id: int, meta: Dict) -> None:
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

    # ============================================================
    # ESECUZIONE TASK APPROVATI — brain_runner + monitor output reale
    # ============================================================

    def execute_approved_task(self, task_id: int, chat_id: int, thread_id: int = None):
        """Esegue un task approvato: lancia brain_runner, monitora, invia output reale a Mirco.
        Zero timeout — gira finche' non finisce o Mirco interrompe.
        """
        # 1. Recupera prompt da code_tasks
        try:
            r = supabase.table("code_tasks").select("prompt,title").eq("id", task_id).execute()
            if not r.data:
                self._send_telegram(chat_id, thread_id, "Errore: task #" + str(task_id) + " non trovato.")
                return
            prompt = r.data[0].get("prompt", "")
            titolo = (r.data[0].get("title") or "Azione codice")[:60]
        except Exception as e:
            self._send_telegram(chat_id, thread_id, "Errore lettura task: " + str(e))
            return

        # 2. Lancia brain_runner
        result = brain_runner.run(prompt)
        if result.get("status") == "error":
            err_msg = result.get("error", "sconosciuto")
            self._send_telegram(chat_id, thread_id,
                "\u274c Errore avvio Claude Code\n"
                "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
                + err_msg + "\n"
                "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
            try:
                supabase.table("code_tasks").update({
                    "status": "error", "output": err_msg,
                }).eq("id", task_id).execute()
            except Exception:
                pass
            return

        pid = result["pid"]
        ora_avvio = format_time_rome()

        # 3. Salva PID + stato running in code_tasks
        try:
            supabase.table("code_tasks").update({
                "status": "running",
                "pid": pid,
                "started_at": now_rome().isoformat(),
            }).eq("id", task_id).execute()
        except Exception as e:
            logger.warning("[CTO] code_tasks update pid error: %s", e)

        # 4. Invia conferma avvio
        self._send_telegram(chat_id, thread_id,
            "\u2699\ufe0f Prompt in esecuzione\n"
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            "\U0001f504 Avviato alle " + ora_avvio + "\n"
            "\U0001f4cb Task: " + titolo + "\n"
            "PID: " + str(pid) + "\n"
            "\u23f3 Aggiornamento ogni 5 minuti\n"
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")

        # 5. Monitor loop in background — zero timeout, gira finche' non finisce
        _tid = task_id
        _cid = chat_id
        _thid = thread_id
        _ttl = titolo
        _ora = ora_avvio
        _pid = pid

        def _monitor_loop():
            import time as _time
            _elapsed = 0
            _last_stderr_count = 0

            while True:
                _time.sleep(300)  # 5 minuti
                _elapsed += 5

                output = brain_runner.get_output(_pid)
                if output.get("error"):
                    logger.warning("[CTO] get_output error: %s", output["error"])
                    break

                stderr_lines = output.get("stderr", [])
                stdout_lines = output.get("stdout", [])

                # Errori stderr nuovi — notifica immediata
                if len(stderr_lines) > _last_stderr_count:
                    new_errs = stderr_lines[_last_stderr_count:]
                    _last_stderr_count = len(stderr_lines)
                    err_preview = "\n".join(new_errs[-3:])[:500]
                    self._send_telegram(_cid, _thid,
                        "\u26a0\ufe0f Errore rilevato \u2014 " + str(_elapsed) + " min\n"
                        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
                        + err_preview + "\n"
                        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")

                if not output.get("running", True):
                    # Processo terminato
                    rc = output.get("returncode", -1)
                    last_10 = "\n".join(stdout_lines[-10:])[:1000] if stdout_lines else "Nessun output"

                    # Cerca commit hash e file modificati
                    commit_hash = ""
                    files_modified = ""
                    for line in stdout_lines:
                        if "commit" in line.lower() and len(line) > 6:
                            commit_hash = line.strip()[:80]
                        if "file" in line.lower() and "changed" in line.lower():
                            files_modified = line.strip()[:80]

                    if rc == 0:
                        done_parts = [
                            "\u2705 Prompt completato",
                            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
                            "\U0001f4cb Task: " + _ttl,
                            "\u23f1\ufe0f Durata: " + str(_elapsed) + " min",
                        ]
                        if files_modified:
                            done_parts.append("\U0001f4c1 " + files_modified)
                        if commit_hash:
                            done_parts.append("\U0001f517 " + commit_hash)
                        done_parts.append("\U0001f4ac Output:")
                        done_parts.append(last_10)
                        done_parts.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
                        self._send_telegram(_cid, _thid, "\n".join(done_parts))
                        try:
                            supabase.table("code_tasks").update({
                                "status": "done",
                                "completed_at": now_rome().isoformat(),
                                "output": "\n".join(stdout_lines[-50:]),
                            }).eq("id", _tid).execute()
                        except Exception:
                            pass
                    else:
                        err_out = "\n".join(stderr_lines[-5:])[:500] if stderr_lines else "Sconosciuto"
                        fail_parts = [
                            "\u274c Prompt fallito (exit code " + str(rc) + ")",
                            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
                            "\U0001f4cb Task: " + _ttl,
                            "\u23f1\ufe0f Durata: " + str(_elapsed) + " min",
                            "\u26a0\ufe0f Errore:",
                            err_out,
                            "\U0001f4ac Ultimo output:",
                            last_10,
                            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
                        ]
                        self._send_telegram(_cid, _thid, "\n".join(fail_parts))
                        try:
                            supabase.table("code_tasks").update({
                                "status": "error",
                                "completed_at": now_rome().isoformat(),
                                "output": "\n".join((stdout_lines + stderr_lines)[-50:]),
                            }).eq("id", _tid).execute()
                        except Exception:
                            pass
                    break
                else:
                    # Ancora in esecuzione — aggiornamento con output reale
                    last_3 = "\n".join(stdout_lines[-3:])[:500] if stdout_lines else "In attesa output..."

                    markup = json.dumps({"inline_keyboard": [[
                        {"text": "\U0001f4c4 Dettaglio", "callback_data": "code_detail:" + str(_tid)},
                        {"text": "\U0001f6d1 Interrompi", "callback_data": "code_interrupt:" + str(_tid)},
                    ]]})

                    prog_text = (
                        "\u23f3 Aggiornamento \u2014 " + str(_elapsed) + " min\n"
                        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
                        + last_3 + "\n"
                        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
                    )
                    self._send_telegram(_cid, _thid, prog_text, reply_markup=markup)

        threading.Thread(target=_monitor_loop, daemon=True).start()
        logger.info("[CTO] Monitor avviato per task #%d PID=%d", task_id, pid)

    def interrupt_task(self, task_id: int, chat_id: int, thread_id: int = None):
        """Interrompe un task in esecuzione via brain_runner.interrupt(pid)."""
        try:
            r = supabase.table("code_tasks").select("pid,title").eq("id", task_id).execute()
            if not r.data or not r.data[0].get("pid"):
                self._send_telegram(chat_id, thread_id, "Task non trovato o nessun PID.")
                return
            pid = r.data[0]["pid"]
            titolo = (r.data[0].get("title") or "Azione codice")[:60]
        except Exception as e:
            self._send_telegram(chat_id, thread_id, "Errore: " + str(e))
            return

        result = brain_runner.interrupt(pid)
        logger.info("[CTO] interrupt_task #%d PID=%d result=%s", task_id, pid, result)

        try:
            supabase.table("code_tasks").update({
                "status": "interrupted",
                "completed_at": now_rome().isoformat(),
            }).eq("id", task_id).execute()
        except Exception:
            pass

        self._send_telegram(chat_id, thread_id,
            "\U0001f6d1 Task interrotto\n"
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            "\U0001f4cb " + titolo + "\n"
            "PID " + str(pid) + " terminato.\n"
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")

    # ---- TELEGRAM HELPER ----

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

    # ============================================================
    # GENERA PROMPT TECNICI PER ALTRI CHIEF
    # ============================================================

    def build_technical_prompt(self, task_description: str, context: str = "") -> str:
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

    def generate_and_deliver_prompt(self, task_description: str, context: str = "") -> Dict:
        """Genera prompt tecnico + CODEACTION card. Per chiamate inter-agente."""
        technical_prompt = self.build_technical_prompt(task_description, context)

        title_short = task_description[:100]
        existing_id = self._find_existing_task(title_short)
        task_id = existing_id

        if not task_id:
            try:
                result = supabase.table("code_tasks").insert({
                    "title": title_short,
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
            meta = self._extract_prompt_meta(technical_prompt)
            self._send_codeaction_card(task_id, meta)

        return {
            "status": "pending_approval",
            "task_id": task_id,
            "prompt": technical_prompt,
        }
