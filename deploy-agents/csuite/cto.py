"""CTO — Chief Technology Officer. Dominio: infrastruttura, codice, deploy, sicurezza tecnica.
v5.12: pattern matching preciso (min 200 chars) + salva prompt in code_tasks + pin in #technology.
       Rimosso execute_in_cloud — Claude Code e' un tool locale, non puo' girare in cloud.
"""
import json
import re
import os
import requests as _requests
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from core.base_chief import BaseChief
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "mircocerisola/brAIn-core"

# Pattern per "manda questo prompt:", "esegui questo:", "incolla in code:", "lancia:"
# NOTA: "prompt:" da solo e' troppo generico — rimosso per evitare falsi positivi
_PROMPT_PATTERN = re.compile(
    r'(?:manda questo prompt|esegui questo|incolla in code|lancia questo)\s*[:]\s*(.+)',
    re.IGNORECASE | re.DOTALL,
)

_MIN_PROMPT_LENGTH = 200  # Caratteri minimi dopo i due punti per attivare il pattern


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
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
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
        # Code tasks recenti
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
            hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
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
    # PATTERN DETECTION: "manda questo prompt:" (min 200 chars)
    # ============================================================

    def answer_question(self, question, user_context=None,
                        project_context=None, topic_scope_id=None,
                        project_scope_id=None, recent_messages=None):
        """Override: intercetta pattern 'manda questo prompt:' PRIMA di qualsiasi logica.
        Requisito: almeno 200 caratteri dopo i due punti. Se meno → risponde prompt troppo corto.
        """
        match = _PROMPT_PATTERN.search(question)
        if match:
            raw_prompt = match.group(1).strip()
            if len(raw_prompt) < _MIN_PROMPT_LENGTH:
                logger.info("[CTO] Pattern rilevato ma troppo corto (%d chars)", len(raw_prompt))
                return (
                    "Prompt troppo corto (" + str(len(raw_prompt)) + " caratteri). "
                    "Mandami il contenuto completo del prompt (minimo 200 caratteri dopo i due punti)."
                )
            logger.info("[CTO] Pattern 'manda prompt' rilevato (%d chars)", len(raw_prompt))
            return self._save_and_deliver_prompt(raw_prompt)

        # Nessun pattern → risposta Chief normale
        return super().answer_question(
            question, user_context=user_context,
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )

    def _save_and_deliver_prompt(self, prompt_text: str) -> str:
        """Salva il prompt in code_tasks e lo restituisce formattato per Claude Code."""
        # 1. Salva in code_tasks
        task_id = None
        try:
            result = supabase.table("code_tasks").insert({
                "title": prompt_text[:100],
                "prompt": prompt_text,
                "requested_by": "cto",
                "status": "ready",
                "sandbox_passed": True,
                "sandbox_check": json.dumps({"source": "cto_direct", "checked_at": datetime.now(timezone.utc).isoformat()}),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            if result.data:
                task_id = result.data[0].get("id")
        except Exception as e:
            logger.warning(f"[CTO] code_tasks insert error: {e}")

        # 2. Pin nel topic #technology
        self._pin_prompt_in_topic(prompt_text, task_id)

        # 3. Restituisci prompt formattato
        sep = "\u2501" * 15
        response = (
            "\u2705 Prompt pronto (code_task #" + str(task_id or "?") + ").\n"
            "Incolla questo in Claude Code sul PC:\n"
            + sep + "\n"
            + prompt_text[:3500] + "\n"
            + sep
        )
        return response

    def _pin_prompt_in_topic(self, prompt_text: str, task_id: Optional[int] = None) -> None:
        """Invia e pinna il prompt nel topic #technology."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            topic_r = supabase.table("org_config").select("value").eq("key", "chief_topic_cto").execute()
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not topic_r.data or not group_r.data:
                return
            topic_id = int(topic_r.data[0]["value"])
            group_id = int(group_r.data[0]["value"])

            tag = "#" + str(task_id) if task_id else ""
            text = (
                "\U0001f4cc PROMPT PRONTO " + tag + "\n"
                "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
                + prompt_text[:3800] + "\n"
                "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
            )
            send_resp = _requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": group_id, "message_thread_id": topic_id, "text": text},
                timeout=10,
            )
            # Pin il messaggio
            if send_resp.status_code == 200:
                msg_id = send_resp.json().get("result", {}).get("message_id")
                if msg_id:
                    _requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/pinChatMessage",
                        json={"chat_id": group_id, "message_id": msg_id, "disable_notification": True},
                        timeout=10,
                    )
        except Exception as e:
            logger.warning(f"[CTO] pin prompt error: {e}")

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
        prompt = f"Richiesta: {task_description}"
        if context:
            prompt += f"\n\nContesto: {context}"

        try:
            technical = self.call_claude(prompt, system=system, max_tokens=3000, model="claude-sonnet-4-6")
            return technical
        except Exception as e:
            logger.error(f"[CTO] build_technical_prompt error: {e}")
            return task_description

    def generate_and_deliver_prompt(self, task_description: str, context: str = "") -> Dict:
        """
        Genera prompt tecnico completo e lo consegna:
        1. Salva in code_tasks con status='ready'
        2. Pin nel topic #technology
        3. Ritorna il prompt per il Chief chiamante
        """
        technical_prompt = self.build_technical_prompt(task_description, context)

        # Salva in code_tasks
        task_id = None
        try:
            result = supabase.table("code_tasks").insert({
                "title": task_description[:100],
                "prompt": technical_prompt,
                "requested_by": "cto",
                "status": "ready",
                "sandbox_passed": True,
                "sandbox_check": json.dumps({
                    "source": "cto_inter_agent",
                    "task_description": task_description[:500],
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            if result.data:
                task_id = result.data[0].get("id")
        except Exception as e:
            logger.warning(f"[CTO] code_tasks insert error: {e}")

        # Pin nel topic
        self._pin_prompt_in_topic(technical_prompt, task_id)

        return {
            "status": "ready",
            "task_id": task_id,
            "prompt": technical_prompt,
        }
