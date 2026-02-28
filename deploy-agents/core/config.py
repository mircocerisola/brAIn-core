"""
brAIn Core Config — shared state per tutti i moduli.
Importare da qui: supabase, claude, TELEGRAM_BOT_TOKEN, logger, ecc.
"""
import os
import sys
import time
import random
import logging
import threading
from dotenv import load_dotenv
from supabase import create_client, ClientOptions
import anthropic
import requests as _requests

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brain")

# v5.36: timeout 10s su PostgREST per evitare hang indefiniti
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY"),
    options=ClientOptions(postgrest_client_timeout=10),
)

# ---------------------------------------------------------------------------
# Claude wrapper: retry + logging + caching automatici per TUTTE le chiamate.
# Quando call_claude() di BaseAgent chiama claude.messages.create(), setta
# _claude_managed.active = True per saltare il wrapper (ha gia' retry+log).
# Le 48+ chiamate dirette (execution/, intelligence/, marketing/, ecc.)
# ottengono retry + logging + prompt caching gratis senza modifiche.
# ---------------------------------------------------------------------------
_claude_managed = threading.local()

_PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "claude-opus-4-6": (15.0, 75.0),
}

_raw_claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


class _RetryLoggedMessages:
    """Wrapper su messages API: aggiunge retry, logging, caching per chiamate dirette."""

    def __init__(self, messages_api):
        self._api = messages_api

    def create(self, **kwargs):
        # Se call_claude() ha gia' il controllo, passthrough
        if getattr(_claude_managed, "active", False):
            return self._api.create(**kwargs)

        # Prompt caching per chiamate dirette (se system e' stringa)
        sys_val = kwargs.get("system")
        if sys_val and isinstance(sys_val, str):
            kwargs["system"] = [
                {"type": "text", "text": sys_val, "cache_control": {"type": "ephemeral"}}
            ]

        # Retry con jitter (3 tentativi)
        for attempt in range(3):
            try:
                resp = self._api.create(**kwargs)
                self._log(kwargs.get("model", "unknown"), resp.usage)
                return resp
            except Exception:
                if attempt < 2:
                    time.sleep(2.0 * (2 ** attempt) + random.uniform(0, 1))
                else:
                    raise
        return None  # unreachable

    def _log(self, model, usage):
        if not usage:
            return
        tokens_in = getattr(usage, "input_tokens", 0)
        tokens_out = getattr(usage, "output_tokens", 0)
        rates = _PRICING.get(model, (3.0, 15.0))
        cost = (tokens_in * rates[0] + tokens_out * rates[1]) / 1_000_000
        # Identifica il modulo chiamante
        agent_id = "direct_call"
        try:
            frame = sys._getframe(2)
            fn = frame.f_code.co_filename.replace("\\", "/")
            agent_id = fn.split("/")[-1].replace(".py", "")
        except Exception:
            pass
        try:
            supabase.table("agent_logs").insert({
                "agent_id": agent_id,
                "action": "api_call",
                "model_used": model,
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "cost_usd": round(cost, 8),
                "status": "success",
            }).execute()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._api, name)


class _WrappedClaude:
    """Proxy su anthropic.Anthropic con messages.create() wrappato."""

    def __init__(self, client):
        self._client = client
        self.messages = _RetryLoggedMessages(client.messages)

    def __getattr__(self, name):
        return getattr(self._client, name)


claude = _WrappedClaude(_raw_claude)

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
COMMAND_CENTER_URL = os.getenv("COMMAND_CENTER_URL", "")
SUPABASE_ACCESS_TOKEN = os.getenv("SUPABASE_ACCESS_TOKEN", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Mutable state (usare _state["key"] per leggere/scrivere)
_state = {
    "TELEGRAM_CHAT_ID": None,
}

requests = _requests
