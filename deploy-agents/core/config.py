"""
brAIn Core Config — shared state per tutti i moduli.
Importare da qui: supabase, claude, TELEGRAM_BOT_TOKEN, logger, ecc.
"""
import os
import logging
from dotenv import load_dotenv
from supabase import create_client
import anthropic
import requests as _requests

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brain")

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

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
