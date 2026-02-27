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
    _current_project_id: Optional[int] = None

    # Pricing per million tokens (input_rate, output_rate)
    _PRICING = {
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-haiku-4-5-20251001": (0.80, 4.0),
        "claude-opus-4-6": (15.0, 75.0),
    }

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
                text = resp.content[0].text if resp.content else ""
                self._log_api_call(mdl, resp.usage)
                return text
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    raise
        return ""

    def _log_api_call(self, model, usage):
        """Log automatico chiamata API con tokens e costo calcolato."""
        if not usage:
            return
        tokens_in = getattr(usage, "input_tokens", 0)
        tokens_out = getattr(usage, "output_tokens", 0)
        cost = self._calculate_cost(model, tokens_in, tokens_out)
        try:
            row = {
                "agent_id": self.name,
                "action": "api_call",
                "model_used": model,
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "cost_usd": round(cost, 8),
                "status": "success",
                "created_at": now_rome().isoformat(),
            }
            if self._current_project_id:
                row["project_id"] = self._current_project_id
            supabase.table("agent_logs").insert(row).execute()
        except Exception as e:
            logger.warning("[%s] API log failed: %s", self.name, e)

    @staticmethod
    def _calculate_cost(model, tokens_in, tokens_out):
        """Calcola costo USD da tokens. Default: pricing Sonnet."""
        rates = BaseAgent._PRICING.get(model, (3.0, 15.0))
        return (tokens_in * rates[0] + tokens_out * rates[1]) / 1_000_000

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
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "status": status,
                "created_at": now_rome().isoformat(),
            }
            if error:
                row["error"] = error[:500]
            if self._current_project_id:
                row["project_id"] = self._current_project_id
            supabase.table("agent_logs").insert(row).execute()
        except Exception as e:
            logger.warning("[%s] Log failed: %s", self.name, e)
