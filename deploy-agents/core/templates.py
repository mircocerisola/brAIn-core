"""
brAIn — core/templates.py
Template fissi per card Telegram. Unico formato ammesso in tutto il codebase.
Funzioni fuso orario Europe/Rome centralizzate.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

_ROME_TZ = ZoneInfo("Europe/Rome")


def now_rome():
    """Ritorna datetime corrente in fuso orario Europe/Rome."""
    return datetime.now(_ROME_TZ)


def format_time_rome():
    """Ritorna ora corrente HH:MM in fuso orario Rome."""
    return now_rome().strftime("%H:%M")


def format_date_rome():
    """Ritorna data corrente 'dd Mon YYYY' in fuso orario Rome."""
    return now_rome().strftime("%d %b %Y")

# ── SMOKE TEST CARD ─────────────────────────────────────────────────────────
SMOKE_TEST_CARD_TEMPLATE = (
    "\U0001f3af CSO\n"
    "Smoke Test {brand_name}\n\n"
    "{obiettivo_1_riga}\n"
    "{n_prospect} prospect | {durata} giorni | {budget}\n"
    "KPI: {kpi_successo}\n"
    "{blockers_line}"
)


def smoke_test_buttons(project_id):
    """Inline keyboard per smoke test card."""
    return {
        "inline_keyboard": [
            [
                {"text": "\u2705 Avvia",
                 "callback_data": "smoke_design_approve:" + str(project_id)},
                {"text": "\U0001f4c4 Dettaglio",
                 "callback_data": "smoke_detail:" + str(project_id)},
            ],
            [
                {"text": "\u270f\ufe0f Modifica",
                 "callback_data": "smoke_design_modify:" + str(project_id)},
                {"text": "\u274c Archivia",
                 "callback_data": "smoke_design_archive:" + str(project_id)},
            ],
        ]
    }


def format_smoke_test_card(brand_name, obiettivo, n_prospect, durata,
                           budget, kpi_successo, n_azioni=0):
    """Formatta la card smoke test con il template fisso."""
    blockers_line = ""
    if n_azioni and n_azioni > 0:
        blockers_line = (
            "\u26a0\ufe0f Serve da te: " + str(n_azioni)
            + " azione/i prima di partire\n"
        )
    return SMOKE_TEST_CARD_TEMPLATE.format(
        brand_name=brand_name,
        obiettivo_1_riga=obiettivo,
        n_prospect=n_prospect,
        durata=durata,
        budget=budget,
        kpi_successo=kpi_successo,
        blockers_line=blockers_line,
    )


# ── CODEACTION CARD ─────────────────────────────────────────────────────────
CODEACTION_CARD_TEMPLATE = (
    "\U0001f527 CTO\n"
    "Codeaction\n\n"
    "Task: {title}\n"
    "File: {main_file}\n"
    "Stima: {time_minutes} min"
)


# ── PROMPT EXECUTION STATUS ─────────────────────────────────────────────────
PROMPT_RUNNING_TEMPLATE = (
    "\U0001f527 CTO\n"
    "Prompt in esecuzione\n\n"
    "Avviato alle {ora}\n"
    "Task: {titolo}\n"
    "Aggiornamento ogni 5 minuti"
)

PROMPT_PROGRESS_TEMPLATE = (
    "\U0001f527 CTO\n"
    "Aggiornamento {elapsed} min\n\n"
    "Prompt ancora in esecuzione\n"
    "Task: {titolo}\n"
    "Avviato alle {ora_avvio}"
)

PROMPT_DONE_TEMPLATE = (
    "\U0001f527 CTO\n"
    "Prompt completato\n\n"
    "Task: {titolo}\n"
    "Durata: {durata_minuti} min\n"
    "{output_section}"
)

PROMPT_ERROR_TEMPLATE = (
    "\U0001f527 CTO\n"
    "Prompt fallito\n\n"
    "Task: {titolo}\n"
    "Durata: {durata_minuti} min\n"
    "Errore: {errore}"
)
