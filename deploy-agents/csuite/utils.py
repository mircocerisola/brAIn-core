"""Utility condivise C-Suite — formato messaggi Telegram unificato."""

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
