"""CTO — Chief Technology Officer. Dominio: infrastruttura, codice, deploy, sicurezza tecnica.
v5.14: trigger "Esegui con --dangerously-skip-permissions" -> CODEACTION card automatica.
       Titolo = prime 8 parole dopo il flag. Zero prompt nel messaggio, solo card con bottoni.
       Dedup: se code_task esiste per stesso titolo, aggiorna senza re-inviare card.
"""
import json
import re
import os
import requests as _requests
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from core.base_chief import BaseChief
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome

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
        # Strip il prefisso "Esegui con --dangerously-skip-permissions."
        after_flag = re.sub(
            r'(?i)esegui con --dangerously-skip-permissions\.?\s*', '', prompt_text, count=1
        )
        # Strip eventuale "Non chiedere autorizzazione..."
        after_flag = re.sub(
            r'(?i)non chiedere autorizzazione[^.]*\.\s*', '', after_flag, count=1
        )
        # Titolo: prime 8 parole significative
        words = after_flag.strip().split()[:8]
        title = ' '.join(words).rstrip('.:,;').strip() if words else "Azione codice"
        if len(title) > 60:
            title = title[:57] + "..."

        # File: cerca path .py nel prompt
        file_matches = re.findall(r'[\w\-/]+\.py', prompt_text)
        main_file = file_matches[0] if file_matches else "da determinare"

        # Stima basata su lunghezza prompt
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
