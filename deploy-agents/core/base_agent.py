"""
brAIn BaseAgent — classe base per tutti gli agenti.
Fornisce: run_with_logging, call_claude, notify_mirco, emit_event, retry, circuit breaker.
"""
from __future__ import annotations
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome


class BaseAgent:
    """Classe base per tutti gli agenti brAIn."""

    name: str = "base_agent"
    default_model: str = "claude-haiku-4-5-20251001"
    max_retries: int = 3
    retry_delay: float = 2.0

    # Circuit breaker
    _failure_count: int = 0
    _circuit_open: bool = False
    _circuit_open_until: float = 0.0
    CIRCUIT_THRESHOLD: int = 5
    CIRCUIT_RESET_SECONDS: int = 300

    def run_with_logging(self, action: str, fn, *args, **kwargs) -> Dict[str, Any]:
        """Esegue fn con logging automatico su agent_logs e gestione errori."""
        start = time.time()
        try:
            result = fn(*args, **kwargs)
            duration_ms = int((time.time() - start) * 1000)
            self._log(action, "success", str(result)[:200], duration_ms=duration_ms)
            self._failure_count = 0
            return result if isinstance(result, dict) else {"status": "ok", "result": result}
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            self._failure_count += 1
            self._log(action, "error", str(e)[:500], duration_ms=duration_ms, error=str(e))
            if self._failure_count >= self.CIRCUIT_THRESHOLD:
                self._circuit_open = True
                self._circuit_open_until = time.time() + self.CIRCUIT_RESET_SECONDS
                logger.warning(f"[{self.name}] Circuit breaker OPEN for {self.CIRCUIT_RESET_SECONDS}s")
            raise

    def call_claude(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        prior_messages: Optional[list] = None,
    ) -> str:
        """Chiama Claude API con retry automatico.
        prior_messages: lista di {role, text} — passata come turns reali ad Anthropic.
        """
        mdl = model or self.default_model
        messages: list = []
        if prior_messages:
            for m in prior_messages:
                role = "assistant" if m.get("role") in ("bot", "assistant") else "user"
                text = (m.get("text") or m.get("content") or "").strip()
                if not text:
                    continue
                # Merge messaggi consecutivi dello stesso ruolo (Anthropic lo richiede)
                if messages and messages[-1]["role"] == role:
                    messages[-1]["content"] += "\n" + text
                else:
                    messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": prompt})
        kwargs: Dict[str, Any] = {"model": mdl, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system

        for attempt in range(self.max_retries):
            try:
                resp = claude.messages.create(**kwargs)
                return resp.content[0].text if resp.content else ""
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    raise
        return ""

    def notify_mirco(self, message: str, level: str = "info") -> None:
        """Invia notifica Telegram a Mirco."""
        from core.utils import notify_telegram
        notify_telegram(message, level=level, source=self.name)

    def emit_event(
        self,
        event_type: str,
        target_agent: Optional[str] = None,
        payload: Optional[Dict] = None,
        priority: str = "normal",
    ) -> None:
        """Emette evento su event bus Supabase."""
        from core.utils import emit_event as _emit
        _emit(self.name, event_type, target_agent=target_agent, payload=payload, priority=priority)

    def is_circuit_open(self) -> bool:
        """True se il circuit breaker è aperto (troppi errori recenti)."""
        if self._circuit_open:
            if time.time() > self._circuit_open_until:
                self._circuit_open = False
                self._failure_count = 0
                logger.info(f"[{self.name}] Circuit breaker reset")
                return False
            return True
        return False

    def _log(
        self,
        action: str,
        status: str,
        output_summary: str,
        input_summary: str = "",
        model_used: str = "none",
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: float = 0.0,
        duration_ms: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Log su agent_logs Supabase."""
        try:
            row = {
                "agent_id": self.name,
                "action": action,
                "layer": 0,
                "input_summary": input_summary[:500],
                "output_summary": output_summary[:500],
                "model_used": model_used,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "status": status,
                "created_at": now_rome().isoformat(),
            }
            if error:
                row["error"] = error[:500]
            supabase.table("agent_logs").insert(row).execute()
        except Exception as e:
            logger.warning(f"[{self.name}] Log failed: {e}")
