"""
brAIn — core/templates.py
Template fissi per card Telegram. Unico formato ammesso in tutto il codebase.
"""

# ── SMOKE TEST CARD ─────────────────────────────────────────────────────────
SMOKE_TEST_CARD_TEMPLATE = (
    "\U0001f52c SMOKE TEST \u2014 {brand_name}\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\U0001f4cb {obiettivo_1_riga}\n"
    "\U0001f465 {n_prospect} prospect | \u23f1\ufe0f {durata} giorni | \U0001f4b6 {budget}\n"
    "\U0001f3af KPI: {kpi_successo}\n"
    "{blockers_line}"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
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
    "\u26a1 CODEACTION \u2014 CTO\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\U0001f4cb {title}\n"
    "\U0001f4c1 File: {main_file}\n"
    "\u23f1\ufe0f Stima: {time_minutes} min\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
)


# ── PROMPT EXECUTION STATUS ─────────────────────────────────────────────────
PROMPT_RUNNING_TEMPLATE = (
    "\u2699\ufe0f Prompt in esecuzione\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\U0001f504 Avviato alle {ora}\n"
    "\U0001f4cb Task: {titolo}\n"
    "\u23f3 Aggiornamento ogni 5 minuti\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
)

PROMPT_PROGRESS_TEMPLATE = (
    "\u23f3 Aggiornamento \u2014 {elapsed} min\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\U0001f504 Prompt ancora in esecuzione\n"
    "\U0001f4cb Task: {titolo}\n"
    "\U0001f550 Avviato alle {ora_avvio}\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
)

PROMPT_DONE_TEMPLATE = (
    "\u2705 Prompt completato\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\U0001f4cb Task: {titolo}\n"
    "\u23f1\ufe0f Durata: {durata_minuti} min\n"
    "{output_section}"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
)

PROMPT_ERROR_TEMPLATE = (
    "\u274c Prompt fallito\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\U0001f4cb Task: {titolo}\n"
    "\u23f1\ufe0f Durata: {durata_minuti} min\n"
    "\u26a0\ufe0f Errore: {errore}\n"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
)
