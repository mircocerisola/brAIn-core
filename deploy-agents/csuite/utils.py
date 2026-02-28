"""Utility condivise C-Suite — formato messaggi Telegram unificato + web search."""
import os
import requests as _requests
from core.config import logger

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

CHIEF_ICONS = {
    "cmo": "\U0001f3a8",   # 🎨
    "cso": "\U0001f3af",   # 🎯
    "cto": "\U0001f527",   # 🔧
    "cfo": "\U0001f4ca",   # 📊
    "coo": "\u2699\ufe0f", # ⚙️
    "clo": "\u2696\ufe0f", # ⚖️
    "cpeo": "\U0001f331",  # 🌱
}

CHIEF_NAMES = {
    "cmo": "CMO",
    "cso": "CSO",
    "cto": "CTO",
    "cfo": "CFO",
    "coo": "COO",
    "clo": "CLO",
    "cpeo": "CPeO",
}


def fmt(chief, titolo, contenuto=""):
    """Formato unificato Chief: icona + nome + titolo + contenuto. Zero separatori."""
    icon = CHIEF_ICONS.get(chief, "")
    name = CHIEF_NAMES.get(chief, chief.upper())
    if contenuto:
        return icon + " " + name + "\n" + titolo + "\n\n" + contenuto
    return icon + " " + name + "\n" + titolo


def fmt_task_received(chief, titolo_task, stima="30 secondi"):
    """Messaggio standard 'task ricevuto' con stima tempo."""
    return fmt(chief, "Task ricevuto", "Sto lavorando su: " + titolo_task + "\nTempo stimato: " + stima)


# ============================================================
# WEB SEARCH — Perplexity per tutti i Chief (sync)
# ============================================================

# Trigger che Mirco puo usare per chiedere ricerca online
WEB_SEARCH_TRIGGERS = [
    "cerca online", "vai a vedere online", "cerca su internet",
    "guarda online", "verifica online", "cerca in rete",
    "vai a controllare online", "puoi cercare", "cerca per me",
    "trova online", "controlla online", "vai a vedere",
]


def web_search(query, chief_name="agent"):
    """Ricerca web via Perplexity API. Sync. Usata da tutti i Chief quando serve."""
    if not PERPLEXITY_API_KEY:
        return "Perplexity API key non configurata. Impossibile cercare online."
    try:
        response = _requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": "Bearer " + PERPLEXITY_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Sei un assistente di ricerca. Rispondi in italiano "
                            "in modo diretto e preciso. Cita la fonte quando possibile. "
                            "Massimo 3 paragrafi."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                "max_tokens": 800,
            },
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            result = data["choices"][0]["message"]["content"]
            logger.info("[%s] web_search OK: %d chars", chief_name, len(result))
            return result
        else:
            logger.warning("[%s] web_search status %d", chief_name, response.status_code)
            return "Ricerca fallita (status " + str(response.status_code) + "). Provo con le informazioni che ho."
    except Exception as e:
        logger.warning("[%s] web_search error: %s", chief_name, e)
        return "Errore ricerca online: " + str(e) + ". Procedo con le informazioni disponibili."


def detect_web_search(message):
    """Rileva se un messaggio contiene un trigger di ricerca web.
    Restituisce la query estratta, oppure None.
    """
    msg_lower = message.lower()
    for trigger in WEB_SEARCH_TRIGGERS:
        if trigger in msg_lower:
            # Estrai la query rimuovendo il trigger
            query = msg_lower.replace(trigger, "").strip()
            if not query or len(query) < 5:
                query = message
            return query
    return None
