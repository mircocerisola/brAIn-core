"""
brAIn Command Center v3.0
Bot Telegram unificato — unico punto di contatto per Mirco.
Modello: Sonnet 4.5 (intelligente, contestuale, COO-level).
Funzioni: query DB, problemi, soluzioni, costi, alert, vocali, foto, chat, /code.
v3.0: Sonnet, storia 25 turni con tool results, session context, smart prompting.
"""

import os
import json
import re
import time
import logging
import asyncio
import threading
import base64
from datetime import datetime, timedelta
from aiohttp import web
from dotenv import load_dotenv
import anthropic
import requests as http_requests
from supabase import create_client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
AGENTS_RUNNER_URL = os.environ.get("AGENTS_RUNNER_URL", "")

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

AUTHORIZED_USER_ID = None

# ---- CHAT HISTORY PERSISTENTE (Supabase) ----
CHAT_HISTORY_TABLE = "chat_history"
MAX_DB_MESSAGES = 10  # ultimi messaggi da caricare dal DB
SUMMARY_INTERVAL = 20  # ogni N messaggi utente genera un riassunto
USD_TO_EUR = 0.92

# Session context per chat_id — traccia ultimo problema/soluzione discussi (in-memory, ricostruito da DB)
_session_context = {}  # chat_id -> {"last_problem_id":, "last_solution_id":, "last_shown_ids":, ...}
_chat_history_available = None  # None=not checked yet, True/False

# Buffer messaggi per Forum Topic — usato per contesto risposte brevi + episodic memory trigger
_topic_recent_msgs = {}  # "chat_id:thread_id" -> [{"role": "user/bot", "text": str}, ...]
_TOPIC_BUFFER_SIZE = 10
_topic_msg_count = {}   # scope_key -> count totale messaggi user (per trigger episodio ogni 10)
_SPEC_KEYWORDS = frozenset({
    "spec", "modifica", "funzionalit", "aggiungi", "cambia", "rimuovi", "togli",
    "aggiorna", "integra", "api", "dashboard", "report", "feature",
    "pagina", "schermata", "login", "notifica", "email", "webhook",
    "struttura", "architettura", "kpi", "stack", "gtm", "implementa", "sezione",
    "metti", "inserisci", "elimina", "sostituisci",
})
_SHORT_AFFIRMATIVES = frozenset({
    "si", "sì", "ok", "va bene", "certo", "esatto", "perfetto",
    "fatto", "procedi", "vai", "dai", "sure", "yes", "yep", "ok go",
})


def _topic_buffer_add(chat_id, thread_id, text, role="user"):
    key = f"{chat_id}:{thread_id}"
    buf = _topic_recent_msgs.setdefault(key, [])
    buf.append({"role": role, "text": text})
    _topic_recent_msgs[key] = buf[-_TOPIC_BUFFER_SIZE:]

    # L1: Persisti su Supabase in background (fire-and-forget)
    def _persist():
        try:
            supabase.table("topic_conversation_history").insert({
                "scope_id": key,
                "role": role,
                "text": text[:2000],
            }).execute()
        except Exception:
            pass
    threading.Thread(target=_persist, daemon=True).start()

    # Trigger episodic memory ogni 10 messaggi user
    if role == "user":
        count = _topic_msg_count.get(key, 0) + 1
        _topic_msg_count[key] = count
        if count % 10 == 0:
            # Crea episodio con gli ultimi messaggi
            snapshot = list(buf)
            threading.Thread(
                target=_trigger_create_episode,
                args=(key, snapshot),
                daemon=True,
            ).start()


def _topic_buffer_get_recent(chat_id, thread_id, n=5):
    """Ultimi n messaggi del topic ESCLUSO l'ultimo appena aggiunto (= contesto precedente)."""
    key = f"{chat_id}:{thread_id}"
    msgs = _topic_recent_msgs.get(key, [])
    # esclude l'ultimo (il messaggio corrente) per vedere cosa c'era prima
    prior = msgs[:-1] if len(msgs) > 1 else []
    return prior[-n:]


def _trigger_create_episode(scope_id: str, messages: list):
    """Background: chiama agents-runner per creare un episodio riassuntivo."""
    if not AGENTS_RUNNER_URL:
        return
    try:
        oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
        headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
        http_requests.post(
            f"{AGENTS_RUNNER_URL}/memory/create-episode",
            json={"scope_type": "topic", "scope_id": scope_id, "messages": messages},
            headers=headers,
            timeout=30,
        )
    except Exception as e:
        logger.debug(f"[MEMORY] create-episode trigger error: {e}")


def _trigger_extract_facts(message: str, chief_id: str):
    """Background: chiama agents-runner per estrarre fatti semantici dal messaggio."""
    if not AGENTS_RUNNER_URL or not message or len(message.strip()) < 20:
        return
    try:
        oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
        headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
        http_requests.post(
            f"{AGENTS_RUNNER_URL}/memory/extract-facts",
            json={"message": message[:1000], "chief_id": chief_id},
            headers=headers,
            timeout=20,
        )
    except Exception as e:
        logger.debug(f"[MEMORY] extract-facts trigger error: {e}")


def _is_short_affirmative(msg):
    return msg.lower().strip() in _SHORT_AFFIRMATIVES


def _context_is_spec_discussion(msgs):
    """True se i messaggi recenti riguardano modifiche alla SPEC."""
    if not msgs:
        return False
    combined = " ".join(m.get("text", "").lower() for m in msgs[-4:])
    return any(kw in combined for kw in _SPEC_KEYWORDS)


def _extract_spec_modification(msgs):
    """Estrae la descrizione della modifica dal contesto recente (ultimo messaggio utente non affermativo)."""
    for m in reversed(msgs):
        text = m.get("text", "").strip()
        if m.get("role") == "user" and not _is_short_affirmative(text) and len(text) > 5:
            return text[:200]
    return "modifica discussa nel topic"

# ---- NOTIFICHE INTELLIGENTI ----
# Quando Mirco ha mandato un messaggio negli ultimi 90s, le notifiche background vanno in coda.
# Vengono inviate raggruppate dopo 2 minuti di silenzio. Solo CRITICAL interrompono.
_last_mirco_message_time = 0.0  # timestamp ultimo messaggio di Mirco
_notification_queue = []  # lista di messaggi in coda
_notification_lock = threading.Lock()
MIRCO_ACTIVE_WINDOW = 120  # secondi (2 minuti)
NOTIFICATION_BATCH_DELAY = 120  # secondi di silenzio prima di inviare coda

# ---- CODA AZIONI PRIORITIZZATA ----
ACTION_QUEUE_TABLE = "action_queue"
_current_action = {}  # chat_id -> action dict (azione in attesa di risposta)

# ---- CODE AGENT ----
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "mircocerisola/brAIn-core"
GITHUB_API = "https://api.github.com"
CODE_AGENT_MODEL = "claude-sonnet-4-6"

pending_deploys = {}  # chat_id -> {"files": [...], "summary": ..., "timestamp": ...}

CODE_AGENT_PROMPT = """Sei il Code Agent di brAIn. Scrivi codice Python per il progetto brAIn.

STRUTTURA REPO:
- agents/: agenti Python (world_scanner.py, solution_architect.py, feasibility_engine.py, etc.)
- deploy/: command_center_unified.py + Dockerfile per bot Telegram
- deploy-agents/: agents_runner.py + Dockerfile per agenti schedulati
- config/: configurazione
- CLAUDE.md: DNA dell'organismo

STACK: Python 3.11, Claude API (anthropic), Supabase (supabase-py), aiohttp, requests, python-telegram-bot.
Cloud Run EU Frankfurt, Dockerfile slim.

REGOLE:
- Scrivi codice Python completo e funzionante, non frammenti
- Rispetta lo stile del codice esistente (logging, supabase client, error handling)
- Non eliminare funzionalita' esistenti
- Usa le stesse librerie gia' presenti
- Ogni file deve essere completo — se modifichi un file esistente, includi TUTTO il contenuto

Per ogni file da creare o modificare, rispondi con JSON:
{"files":[{"path":"agents/nome_file.py","content":"contenuto completo del file","action":"create o update"}],"summary":"cosa hai fatto in 2 frasi"}
SOLO JSON."""

# ---- VOICE TRANSCRIPTION (Google Speech-to-Text) ----

def transcribe_voice(audio_bytes):
    try:
        url = "https://speech.googleapis.com/v1/speech:recognize"
        token_url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
        token_r = http_requests.get(token_url, headers={"Metadata-Flavor": "Google"}, timeout=5)
        if token_r.status_code != 200:
            return None
        access_token = token_r.json()["access_token"]
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        payload = {
            "config": {
                "encoding": "OGG_OPUS",
                "sampleRateHertz": 48000,
                "languageCode": "it-IT",
                "alternativeLanguageCodes": ["en-US"],
                "model": "latest_long",
                "enableAutomaticPunctuation": True,
            },
            "audio": {"content": audio_b64},
        }
        r = http_requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if r.status_code == 200:
            result = r.json()
            if "results" in result:
                return " ".join(
                    alt["transcript"]
                    for res in result["results"]
                    for alt in res.get("alternatives", [])[:1]
                ).strip()
        return None
    except Exception as e:
        logger.error(f"[VOICE] {e}")
        return None


# ---- SYSTEM PROMPT ----

def build_system_prompt(chat_id=None, conversation_summary=None):
    ctx = get_minimal_context()
    session = ""
    if chat_id and chat_id in _session_context:
        sc = _session_context[chat_id]
        parts = []
        if sc.get("last_problem_id"):
            parts.append(f"Ultimo problema discusso: ID {sc['last_problem_id']}")
        if sc.get("last_solution_id"):
            parts.append(f"Ultima soluzione discussa: ID {sc['last_solution_id']}")
        if sc.get("last_shown_ids"):
            parts.append(f"Ultimi ID mostrati: {sc['last_shown_ids']}")
        if sc.get("last_command"):
            parts.append(f"Ultimo comando: {sc['last_command']}")
        if parts:
            session = "\nCONTESTO SESSIONE:\n" + "\n".join(parts) + "\n"

    summary_section = ""
    if conversation_summary:
        summary_section = f"\nRIASSUNTO CONVERSAZIONI PRECEDENTI:\n{conversation_summary}\n"

    oggi = datetime.now().strftime("%d/%m/%Y")

    return f"""Oggi e' il {oggi}.

Sei il COO di brAIn — organismo AI-native. Unico punto di contatto per Mirco (CEO).
brAIn scansiona problemi globali, genera soluzioni, le testa sul mercato, scala quelle che funzionano.

PERSONALITA':
- Parli SEMPRE in italiano, diretto, zero fuffa. Testo piano, MAI Markdown.
- Sei un COO che conosce ogni dettaglio dell'organizzazione.
- Proattivo: se Mirco approva, agisci subito. Se chiede dati, fai la query. Mai dire "non ho accesso".
- UNA domanda alla volta. Frasi corte. Mai ripetere cose gia' dette.

REGOLA CRITICA — RISPONDI ALLA DOMANDA SPECIFICA:
- Se Mirco chiede di UN problema specifico, rispondi SOLO su quello. NON elencare tutti i problemi.
- Se Mirco chiede "parlami del problema X" o "che ne pensi di X" — usa search_problems per trovarlo, poi rispondi su QUELLO.
- Se Mirco fa una domanda specifica (es. "come funziona lo scanner?") rispondi a QUELLA domanda, non cambiare argomento.
- NON rispondere mai con una lista di problemi se Mirco non ha chiesto una lista.
- Mai rispondere con informazioni generiche quando la domanda e' specifica.

REGOLA CRITICA — MAI CHIEDERE ID:
- NON chiedere MAI a Mirco un ID numerico. Mirco non sa gli ID.
- Se Mirco menziona un problema con nome/parole chiave, usa search_problems per trovarlo.
- Se dal contesto sai quale problema/soluzione sta discutendo, usa quello direttamente.
- Quando Mirco dice "il primo", "quello", "spiegami meglio" — CAPISCI dal CONTESTO SESSIONE sotto.

REGOLA CRITICA — MAI RIPETERE CONFERME:
- Se nella conversazione hai GIA' confermato un'azione (es. "problema approvato", "Solution Architect notificato"), NON ripeterla.
- Ogni risposta deve aggiungere informazioni NUOVE. Mai riformulare la stessa cosa con parole diverse.
- Se Mirco dice "ok" dopo una conferma, vai avanti con la prossima azione o chiedi cosa vuole fare, non ripetere.

REGOLA CRITICA — VERIFICA SEMPRE NEL DB, MAI SUPPOSIZIONI:
- Quando Mirco chiede lo stato di qualcosa (es. "aspetto il BOS", "ci sono soluzioni?", "a che punto siamo?"):
  1. Fai SUBITO una query al database (query_supabase o search_problems)
  2. Se il dato esiste → mostralo direttamente con i dettagli
  3. Se NON esiste → dillo chiaramente: "Non ancora pronto" o "Non trovato nel DB"
  4. MAI rispondere con frasi vaghe tipo "arrivera' quando l'agente avra' completato" SENZA aver prima verificato
- Questo vale per: soluzioni, problemi, scan, report, qualsiasi dato

CONTESTO CONVERSAZIONE:
- Se Mirco risponde "si", "ok", "avanti", "approva" — AGISCI senza chiedere altro.
- Se dice un numero — interpretalo come ID dal contesto.
- NON chiedere informazioni che hai gia' nella conversazione o nel contesto sessione.

TOOL: Hai accesso al database. Usa i tool SEMPRE per dati freschi. NON inventare numeri.
- Usa search_problems quando Mirco menziona un problema per nome/parole chiave/settore.
- Usa query_supabase per query precise con filtri.
- Quando Mirco chiede stato/risultati, fai PRIMA la query, poi rispondi.

COSTI: Rispondi SEMPRE in euro (EUR). Tasso: 1 USD = 0.92 EUR. Mostra dollari solo se Mirco lo chiede esplicitamente.

FORMATO PROBLEMI (lista):
1. TITOLO (italiano, max 8 parole)
   Score: 0.72 | Settore | Urgenza | Status
Per ogni problema mostra ID, titolo, score, settore, urgenza.

FORMATO PROBLEMA SINGOLO (elevator):
TITOLO
Score: 0.72 | Settore | Urgenza
Il dolore: una frase che fa sentire il problema
Chi soffre: target specifico
Mercato: dimensione/valore

FORMATO SOLUZIONI:
TITOLO
Score: 0.68 | BOS: 0.74
Cosa fa | Per chi | Revenue | Costo mensile | TTM

DEEP DIVE: solo se Mirco chiede "approfondisci" o "dettagli". Max 15 righe.

PIPELINE AUTOMATICA (v3.4):
La pipeline ora e' completamente automatica: Scanner -> SA -> FE -> BOS. Mirco riceve notifica SOLO per il BOS finale se supera soglia_bos. Non ci sono piu azioni review_problem/review_solution/review_feasibility. L'unica azione manuale e' approve_bos (si/no/dettagli).

FLUSSO:
- "problemi": query_supabase, table=problems, select=id,title,weighted_score,sector,urgency,status, order_by=weighted_score, order_desc=true, limit=10. (mostra solo active per default)
- "approva" / "si vai": risponde SOLO a azione BOS in coda (approve_bos). MAI approve_problem automatico.
- "rifiuta" / "skip" / "no": rifiuta SOLO azione BOS in coda.
- "soluzioni": query_supabase table=solutions. (mostra solo active per default)
- "mostrami archiviati" / "vedi archivio": query_supabase con filters=status_detail=archived per vedere problemi/soluzioni archiviati.
- "seleziona": select_solution.
- "stato" / "status": get_system_status. Mostra SEMPRE attivi + archiviati, mai solo "0" senza contesto.
- "costi": get_cost_report.
- "cercami un problema": trigger_scan (scan normale).
- "cercami un problema nelle migliori fonti": trigger_scan con use_top=true.
- "cercami un problema su [fonte]": trigger_scan con source_name='nome fonte'.
- "cercami un problema nel settore [X]": trigger_scan con sector_filter='settore'.
- Problema per nome: search_problems con parole chiave.
- "come vanno le fonti" / "report fonti" / "fonti attive": get_sources_report.
- "riattiva fonte [nome]": reactivate_source con source_name='nome'.
- "soglie" / "thresholds": query_supabase table=pipeline_thresholds, select=*, order_by=id, order_desc=true, limit=1.
- "modifica soglia X a Y": modify_thresholds con soglia e valore.
REGOLA CRITICA — PIPELINE AUTOMATICA:
MAI chiedere a Mirco di approvare problemi o soluzioni. La pipeline li processa automaticamente.
Se mostri problemi o soluzioni -> lista informativa pura. MAI "quale approvo?" o "vuoi approfondire?" o "quale procedo?".
L'UNICA approvazione manuale e' il BOS finale quando la pipeline lo propone.
approve_problem esiste SOLO per override manuale esplicito tipo "approva problema 42".

NOTA STATUS_DETAIL: problems e solutions hanno status_detail (active/archived/rejected). Default sempre active. Per vedere archiviati o rifiutati, specifica filters=status_detail=archived o filters=status_detail=rejected.
CONTEGGI TRASPARENTI: quando Mirco chiede quanti problemi/soluzioni ci sono, rispondi SEMPRE con attivi + archiviati. Es: "Problemi attivi: 0 (archiviati: 117)". Se tutti i dati sono in archivio, spiega esplicitamente che il DB è stato ripulito e il sistema sta raccogliendo nuovi dati.

REGOLA CRITICA — MAI INVENTARE AGENTI O RUOLI:
Gli agenti reali di brAIn sono SOLO: world_scanner, solution_architect, spec_generator, knowledge_keeper, capability_scout, validation_agent, command_center (tu).
MAI citare agenti, ruoli o figure che non esistono nel sistema: "Business Operator", "Growth Hacker", "Marketing Manager", "Sales Agent", "Finance Officer", "Project Manager", "Customer Success" o qualsiasi altra figura inventata.
Se non sai come gestire una richiesta, chiedi direttamente a Mirco cosa vuole fare. Non inventare processi o figure.

REGOLA CRITICA — NEI TOPIC CANTIERE:
Quando sei in un topic di Cantiere e Mirco risponde con una parola breve ("si", "ok", "perfetto", ecc.), leggi il CONTESTO TOPIC sotto per capire a cosa sta rispondendo.
- Se stava discutendo di modifiche SPEC → interpreta come conferma della modifica
- MAI interpretare una risposta breve come "ok lancio" se il contesto era una discussione tecnica
- Il lancio richiede sempre un comando esplicito ("ok lanciamo", "lancia", "lanciamo")

{ctx}{session}{summary_section}"""


def get_minimal_context():
    try:
        p_active = supabase.table("problems").select("id", count="exact").eq("status_detail", "active").execute()
        p_archived = supabase.table("problems").select("id", count="exact").eq("status_detail", "archived").execute()
        p_new = supabase.table("problems").select("id", count="exact").eq("status_detail", "active").eq("status", "new").execute()
        p_approved = supabase.table("problems").select("id", count="exact").eq("status_detail", "active").eq("status", "approved").execute()
        s_active = supabase.table("solutions").select("id", count="exact").eq("status_detail", "active").execute()
        s_archived = supabase.table("solutions").select("id", count="exact").eq("status_detail", "archived").execute()
        # Costi ultimi 24h
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        costs = supabase.table("agent_logs").select("cost_usd").gte("created_at", yesterday).execute()
        cost_24h_eur = round(sum(float(c.get("cost_usd", 0) or 0) for c in (costs.data or [])) * USD_TO_EUR, 4)
        # Ultimi eventi pipeline
        events = supabase.table("agent_events").select("event_type,status,created_at") \
            .order("created_at", desc=True).limit(3).execute()
        ev_summary = ""
        if events.data:
            ev_parts = [f"{e['event_type']}({e['status']})" for e in events.data]
            ev_summary = f" Pipeline recente: {', '.join(ev_parts)}."

        p_active_n = p_active.count or 0
        p_archived_n = p_archived.count or 0
        s_active_n = s_active.count or 0
        s_archived_n = s_archived.count or 0

        archive_note = ""
        if p_active_n == 0 and p_archived_n > 0:
            archive_note = (
                f"\nNOTA IMPORTANTE: il DB è stato ripulito. I dati precedenti sono in archivio "
                f"({p_archived_n} problemi, {s_archived_n} soluzioni). "
                f"Il sistema sta raccogliendo nuovi dati con i criteri aggiornati."
            )

        return (
            f"\nSTATO ATTUALE: {p_active_n} problemi attivi (archiviati: {p_archived_n}), "
            f"{p_new.count or 0} nuovi, {p_approved.count or 0} approvati. "
            f"{s_active_n} soluzioni attive (archiviate: {s_archived_n}). "
            f"Costi 24h: {cost_24h_eur} EUR.{ev_summary}{archive_note}\n"
        )
    except:
        return ""


# ---- TOOLS ----

TOOLS = [
    {
        "name": "query_supabase",
        "description": "Query SELECT su Supabase. Solo lettura. Usa per cercare dati specifici su problemi, soluzioni, costi, fonti, knowledge, agenti.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Nome tabella"},
                "select": {"type": "string", "description": "Colonne da selezionare (comma separated)"},
                "filters": {"type": "string", "description": "Filtri: col=val, col.gte=val, col.lte=val separati da virgola"},
                "order_by": {"type": "string"},
                "order_desc": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
            "required": ["table", "select"],
        },
    },
    {
        "name": "get_system_status",
        "description": "Stato completo: conteggi problemi/soluzioni, agenti attivi, errori recenti, costi 24h.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_cost_report",
        "description": "Report costi per agente negli ultimi N giorni.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Giorni da analizzare (default 7)"},
            },
        },
    },
    {
        "name": "approve_problem",
        "description": "Override manuale: approva un problema specifico. USA SOLO se Mirco chiede esplicitamente 'approva problema ID'. MAI invocare in autonomia.",
        "input_schema": {
            "type": "object",
            "properties": {
                "problem_id": {"type": "integer", "description": "ID del problema"},
            },
            "required": ["problem_id"],
        },
    },
    {
        "name": "reject_problem",
        "description": "Rifiuta un problema — cambia status a rejected.",
        "input_schema": {
            "type": "object",
            "properties": {
                "problem_id": {"type": "integer", "description": "ID del problema"},
            },
            "required": ["problem_id"],
        },
    },
    {
        "name": "select_solution",
        "description": "Seleziona una soluzione per lancio — cambia status a selected.",
        "input_schema": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer", "description": "ID della soluzione"},
            },
            "required": ["solution_id"],
        },
    },
    {
        "name": "trigger_scan",
        "description": (
            "Lancia scan via agents-runner (autenticato). "
            "Varianti: "
            "scan normale → usa topic; "
            "migliori fonti → usa use_top=true; "
            "fonte specifica → usa source_name='nome parziale'; "
            "settore specifico → usa sector_filter='settore'. "
            "Usa quando Mirco chiede 'cercami un problema', 'scansiona', 'cerca su [fonte/settore]', 'migliori fonti'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Keywords da scansionare (scan normale)"},
                "source_name": {"type": "string", "description": "Nome parziale fonte specifica (es. 'Reddit', 'Hacker News')"},
                "use_top": {"type": "boolean", "description": "Se true, usa le top 3 fonti per relevance_score"},
                "sector_filter": {"type": "string", "description": "Settore da filtrare (es. 'healthcare', 'fintech', 'education')"},
            },
        },
    },
    {
        "name": "search_problems",
        "description": "Cerca problemi per parole chiave nel titolo o descrizione. Usa quando Mirco menziona un problema con nome parziale, parole vaghe, settore o descrizione. NON chiedere mai l'ID, cerca direttamente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string", "description": "Parole chiave da cercare (italiano o inglese)"},
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "modify_thresholds",
        "description": "Modifica manualmente una soglia della pipeline automatica. Usa quando Mirco vuole cambiare soglia_problema, soglia_soluzione, soglia_feasibility o soglia_bos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "soglia": {"type": "string", "description": "Quale soglia modificare: problema, soluzione, feasibility, bos"},
                "valore": {"type": "number", "description": "Nuovo valore tra 0.0 e 1.0"},
            },
            "required": ["soglia", "valore"],
        },
    },
    {
        "name": "get_sources_report",
        "description": "Mostra report delle fonti di scan suddivise per categoria: ottime (>0.7), media (0.4-0.7), deboli (<0.4). Usa quando Mirco chiede 'come vanno le fonti', 'report fonti', 'che fonti abbiamo', 'fonti attive'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reactivate_source",
        "description": "Riattiva una fonte di scan archiviata. Usa quando Mirco dice 'riattiva fonte [nome]' o 'sblocca fonte [nome]'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_name": {"type": "string", "description": "Nome (parziale) della fonte da riattivare"},
            },
            "required": ["source_name"],
        },
    },
]


def execute_tool(tool_name, tool_input):
    try:
        if tool_name == "query_supabase":
            return supabase_query(tool_input)
        elif tool_name == "get_system_status":
            return get_system_status()
        elif tool_name == "get_cost_report":
            return get_cost_report(tool_input.get("days", 7))
        elif tool_name == "approve_problem":
            return approve_problem(tool_input["problem_id"])
        elif tool_name == "reject_problem":
            return reject_problem(tool_input["problem_id"])
        elif tool_name == "select_solution":
            return select_solution(tool_input["solution_id"])
        elif tool_name == "trigger_scan":
            return trigger_scan(
                topic=tool_input.get("topic"),
                source_name=tool_input.get("source_name"),
                use_top=tool_input.get("use_top", False),
                sector_filter=tool_input.get("sector_filter"),
            )
        elif tool_name == "search_problems":
            return search_problems(tool_input["keywords"])
        elif tool_name == "modify_thresholds":
            return modify_thresholds(tool_input["soglia"], tool_input["valore"])
        elif tool_name == "get_sources_report":
            return get_sources_report()
        elif tool_name == "reactivate_source":
            return reactivate_source(tool_input["source_name"])
        else:
            return f"Tool sconosciuto: {tool_name}"
    except Exception as e:
        return f"ERRORE {tool_name}: {e}"


ALLOWED_TABLES = [
    "problems", "solutions", "agent_logs", "org_knowledge", "scan_sources",
    "capability_log", "org_config", "solution_scores", "agent_events",
    "reevaluation_log", "authorization_matrix", "finance_metrics", "action_queue",
    "pipeline_thresholds", "source_thresholds",
]


def supabase_query(params):
    table = params["table"]
    if table not in ALLOWED_TABLES:
        return f"BLOCCATO: tabella '{table}' non accessibile."
    try:
        q = supabase.table(table).select(params["select"])
        filters_str = params.get("filters", "") or ""
        # Applica filtro status_detail=active di default per problems/solutions
        # a meno che il filtro non specifichi già un valore di status_detail
        if table in ("problems", "solutions") and "status_detail" not in filters_str:
            q = q.eq("status_detail", "active")
        if filters_str:
            for f in filters_str.split(","):
                f = f.strip()
                if ".gte=" in f:
                    col, val = f.split(".gte=")
                    q = q.gte(col.strip(), val.strip())
                elif ".lte=" in f:
                    col, val = f.split(".lte=")
                    q = q.lte(col.strip(), val.strip())
                elif ".ilike=" in f:
                    col, val = f.split(".ilike=", 1)
                    q = q.ilike(col.strip(), val.strip())
                elif "=" in f:
                    col, val = f.split("=", 1)
                    q = q.eq(col.strip(), val.strip())
        if params.get("order_by"):
            q = q.order(params["order_by"], desc=params.get("order_desc", True))
        q = q.limit(min(params.get("limit", 20), 50))
        result = q.execute()
        if result.data:
            return json.dumps(result.data, indent=2, default=str, ensure_ascii=False)[:4000]
        return "Nessun risultato."
    except Exception as e:
        return f"Errore query: {e}"


def search_problems(keywords):
    """Cerca problemi per parole chiave nel titolo o descrizione con ILIKE."""
    try:
        pattern = f"%{keywords}%"
        r1 = supabase.table("problems") \
            .select("id,title,weighted_score,sector,urgency,status,status_detail,description") \
            .eq("status_detail", "active") \
            .ilike("title", pattern) \
            .order("weighted_score", desc=True).limit(5).execute()
        r2 = supabase.table("problems") \
            .select("id,title,weighted_score,sector,urgency,status,status_detail,description") \
            .eq("status_detail", "active") \
            .ilike("description", pattern) \
            .order("weighted_score", desc=True).limit(5).execute()
        seen = set()
        results = []
        for row in (r1.data or []) + (r2.data or []):
            if row["id"] not in seen:
                seen.add(row["id"])
                results.append(row)
        if not results:
            return f"Nessun problema trovato per '{keywords}'."
        return json.dumps(results[:5], indent=2, default=str, ensure_ascii=False)[:4000]
    except Exception as e:
        return f"Errore ricerca: {e}"


def get_system_status():
    try:
        status = {}
        problems = supabase.table("problems").select("id,status").eq("status_detail", "active").execute()
        problems_archived = supabase.table("problems").select("id", count="exact").eq("status_detail", "archived").execute()
        solutions = supabase.table("solutions").select("id").eq("status_detail", "active").execute()
        solutions_archived = supabase.table("solutions").select("id", count="exact").eq("status_detail", "archived").execute()
        status["problemi_attivi"] = len(problems.data) if problems.data else 0
        status["problemi_archiviati"] = problems_archived.count or 0
        status["problemi_nuovi"] = len([p for p in (problems.data or []) if p.get("status") == "new"])
        status["problemi_approvati"] = len([p for p in (problems.data or []) if p.get("status") == "approved"])
        status["soluzioni_attive"] = len(solutions.data) if solutions.data else 0
        status["soluzioni_archiviate"] = solutions_archived.count or 0
        # Nota se DB vuoto ma archivio pieno
        if status["problemi_attivi"] == 0 and status["problemi_archiviati"] > 0:
            status["nota"] = (
                f"DB ripulito. Archivio: {status['problemi_archiviati']} problemi, "
                f"{status['soluzioni_archiviate']} soluzioni. Raccolta nuovi dati in corso."
            )

        logs = supabase.table("agent_logs").select("agent_id,action,status,created_at") \
            .order("created_at", desc=True).limit(15).execute()
        if logs.data:
            agents = {}
            for l in logs.data:
                if l["agent_id"] not in agents:
                    agents[l["agent_id"]] = {
                        "ultima_azione": l["action"],
                        "stato": l["status"],
                        "quando": l["created_at"],
                    }
            status["agenti"] = agents

        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        costs = supabase.table("agent_logs").select("cost_usd").gte("created_at", yesterday).execute()
        status["costi_24h_eur"] = round(
            sum(float(c.get("cost_usd", 0) or 0) for c in (costs.data or [])) * USD_TO_EUR, 4
        )

        errors = supabase.table("agent_logs").select("agent_id,error,created_at") \
            .eq("status", "error").order("created_at", desc=True).limit(3).execute()
        status["errori_recenti"] = errors.data or []

        return json.dumps(status, indent=2, default=str, ensure_ascii=False)
    except Exception as e:
        return f"Errore: {e}"


def get_cost_report(days=7):
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        logs = supabase.table("agent_logs") \
            .select("agent_id,cost_usd,tokens_input,tokens_output,model_used") \
            .gte("created_at", since).limit(500).execute()
        if not logs.data:
            return f"Nessun dato ultimi {days} giorni."
        by_agent = {}
        total_usd = 0
        for l in logs.data:
            aid = l["agent_id"]
            cost = float(l.get("cost_usd", 0) or 0)
            total_usd += cost
            if aid not in by_agent:
                by_agent[aid] = {"eur": 0, "calls": 0}
            by_agent[aid]["eur"] += cost * USD_TO_EUR
            by_agent[aid]["calls"] += 1
        for a in by_agent:
            by_agent[a]["eur"] = round(by_agent[a]["eur"], 4)
        return json.dumps(
            {"giorni": days, "totale_eur": round(total_usd * USD_TO_EUR, 4), "per_agente": by_agent},
            indent=2, ensure_ascii=False,
        )
    except Exception as e:
        return f"Errore: {e}"


def approve_problem(problem_id):
    try:
        check = supabase.table("problems").select("id,title,status,sector").eq("id", problem_id).execute()
        if not check.data:
            return f"Problema ID {problem_id} non trovato."
        if check.data[0]["status"] == "approved":
            return f"Problema ID {problem_id} gia' approvato."

        supabase.table("problems").update({"status": "approved"}).eq("id", problem_id).execute()

        title = check.data[0]["title"]
        sector = check.data[0].get("sector", "")

        # Notifica Solution Architect via agent_events
        try:
            supabase.table("agent_events").insert({
                "event_type": "problem_approved",
                "source_agent": "command_center",
                "target_agent": "solution_architect",
                "payload": json.dumps({"problem_id": problem_id}),
                "priority": "high",
                "status": "pending",
            }).execute()
        except:
            pass

        # Self-improvement: salva preferenza
        try:
            supabase.table("agent_events").insert({
                "event_type": "mirco_feedback",
                "source_agent": "command_center",
                "payload": json.dumps({
                    "type": "problem", "action": "approved",
                    "item_id": str(problem_id), "sector": sector,
                    "reason": f"Approvato: {title[:100]}",
                }),
                "priority": "normal",
                "status": "pending",
            }).execute()
        except:
            pass

        return f"Problema '{title}' (ID {problem_id}) APPROVATO. Solution Architect notificato."
    except Exception as e:
        return f"Errore approvazione: {e}"


def reject_problem(problem_id):
    try:
        check = supabase.table("problems").select("id,title,status,sector").eq("id", problem_id).execute()
        if not check.data:
            return f"Problema ID {problem_id} non trovato."

        supabase.table("problems").update({"status": "rejected", "status_detail": "rejected"}).eq("id", problem_id).execute()
        title = check.data[0]["title"]
        sector = check.data[0].get("sector", "")

        # Self-improvement: salva preferenza rifiuto
        try:
            supabase.table("agent_events").insert({
                "event_type": "mirco_feedback",
                "source_agent": "command_center",
                "payload": json.dumps({
                    "type": "problem", "action": "rejected",
                    "item_id": str(problem_id), "sector": sector,
                    "reason": f"Rifiutato: {title[:100]}",
                }),
                "priority": "normal",
                "status": "pending",
            }).execute()
        except:
            pass

        return f"Problema '{title}' (ID {problem_id}) RIFIUTATO."
    except Exception as e:
        return f"Errore rifiuto: {e}"


def select_solution(solution_id):
    try:
        check = supabase.table("solutions").select("id,title,status,sector").eq("id", solution_id).execute()
        if not check.data:
            return f"Soluzione ID {solution_id} non trovata."
        if check.data[0]["status"] == "selected":
            return f"Soluzione ID {solution_id} gia' selezionata."

        supabase.table("solutions").update({"status": "selected"}).eq("id", solution_id).execute()
        title = check.data[0]["title"]
        sector = check.data[0].get("sector", "")

        # Self-improvement: salva preferenza
        try:
            supabase.table("agent_events").insert({
                "event_type": "mirco_feedback",
                "source_agent": "command_center",
                "payload": json.dumps({
                    "type": "solution", "action": "selected",
                    "item_id": str(solution_id), "sector": sector,
                    "reason": f"Selezionata: {title[:100]}",
                }),
                "priority": "normal",
                "status": "pending",
            }).execute()
        except:
            pass

        return f"Soluzione '{title}' (ID {solution_id}) SELEZIONATA per lancio."
    except Exception as e:
        return f"Errore selezione: {e}"


def get_sources_report():
    """Report completo sulle fonti di scan suddiviso per categoria."""
    try:
        sources_result = supabase.table("scan_sources").select(
            "id,name,relevance_score,problems_found,avg_problem_score,status,last_scanned"
        ).order("relevance_score", desc=True).execute()
        all_sources = sources_result.data or []

        # Soglia attuale
        try:
            thresh = supabase.table("source_thresholds").select(
                "dynamic_threshold,updated_at"
            ).order("updated_at", desc=True).limit(1).execute()
            current_threshold = thresh.data[0].get("dynamic_threshold") or 0.35 if thresh.data else 0.35
        except:
            current_threshold = 0.35

        active = [s for s in all_sources if s.get("status") == "active"]
        archived_count = len([s for s in all_sources if s.get("status") != "active"])

        great = [s for s in active if (s.get("relevance_score") or 0) > 0.7]
        average = [s for s in active if 0.4 <= (s.get("relevance_score") or 0) <= 0.7]
        weak = [s for s in active if (s.get("relevance_score") or 0) < 0.4]

        SEP = "━━━━━━━━━━━━━━━"
        NUMERI = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]

        def fmt_src(srcs, limit=5):
            out = []
            for i, s in enumerate(srcs[:limit]):
                num = NUMERI[i] if i < len(NUMERI) else f"{i+1}."
                score = s.get("relevance_score") or 0
                pf = s.get("problems_found") or 0
                out.append(f"{num} {s['name'][:28]} · score: {score:.2f} · problemi: {pf}")
            return out

        lines = ["📡 REPORT FONTI", SEP]
        if great:
            lines.append(f"\n🟢 OTTIME (score > 0.7)")
            lines.extend(fmt_src(great))
        if average:
            lines.append(f"\n🟡 NELLA MEDIA (score 0.4–0.7)")
            lines.extend(fmt_src(average))
        if weak:
            lines.append(f"\n🔴 DEBOLI (score < 0.4)")
            lines.extend(fmt_src(weak))
        lines.append(f"\n📦 ARCHIVIATE: {archived_count} fonti")
        lines.append(f"Soglia archiviazione attuale: {current_threshold:.2f}")
        lines.append(SEP)
        return "\n".join(lines)
    except Exception as e:
        return f"Errore report fonti: {e}"


def reactivate_source(source_name):
    """Riattiva una fonte archiviata."""
    try:
        result = supabase.table("scan_sources").select(
            "id,name,status"
        ).ilike("name", f"%{source_name}%").execute()
        if not result.data:
            return f"Fonte '{source_name}' non trovata."
        source = result.data[0]
        if source.get("status") == "active":
            return f"Fonte '{source['name']}' è già attiva."
        supabase.table("scan_sources").update({
            "status": "active",
            "notes": "Riattivata manualmente da Mirco",
        }).eq("id", source["id"]).execute()
        return f"Fonte '{source['name']}' riattivata."
    except Exception as e:
        return f"Errore riattivazione: {e}"


def modify_thresholds(soglia, valore):
    """Modifica manualmente una soglia della pipeline e inserisce nuova riga in pipeline_thresholds."""
    try:
        valore = float(valore)
        if valore < 0.0 or valore > 1.0:
            return "Valore non valido. Deve essere tra 0.0 e 1.0."

        field_map = {
            "problema": "soglia_problema",
            "soluzione": "soglia_soluzione",
            "feasibility": "soglia_feasibility",
            "bos": "soglia_bos",
        }
        if soglia not in field_map:
            return f"Soglia non valida. Usa: {', '.join(field_map.keys())}"

        # Leggi riga corrente
        current = supabase.table("pipeline_thresholds").select("*").order("id", desc=True).limit(1).execute()
        if current.data:
            row = current.data[0]
            new_row = {
                "soglia_problema": float(row.get("soglia_problema") or 0.65),
                "soglia_soluzione": float(row.get("soglia_soluzione") or 0.70),
                "soglia_feasibility": float(row.get("soglia_feasibility") or 0.70),
                "soglia_bos": float(row.get("soglia_bos") or 0.80),
                "bos_approval_rate": row.get("bos_approval_rate"),
                "update_reason": f"Modifica manuale da Mirco: {field_map[soglia]} = {valore:.3f}",
            }
        else:
            new_row = {
                "soglia_problema": 0.65, "soglia_soluzione": 0.70,
                "soglia_feasibility": 0.70, "soglia_bos": 0.80,
                "update_reason": f"Modifica manuale da Mirco: {field_map[soglia]} = {valore:.3f}",
            }

        new_row[field_map[soglia]] = round(valore, 3)
        supabase.table("pipeline_thresholds").insert(new_row).execute()
        return f"Soglia {soglia} aggiornata a {valore:.3f}. Effettiva dal prossimo ciclo di scan."
    except Exception as e:
        return f"Errore modifica soglia: {e}"


def get_oidc_token(audience):
    """Recupera token OIDC dalla metadata server di Cloud Run per auth service-to-service."""
    try:
        r = http_requests.get(
            f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={audience}",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        if r.status_code == 200:
            return r.text.strip()
    except Exception as e:
        logger.debug(f"[OIDC] metadata server non disponibile (locale?): {e}")
    return None


def trigger_scan(topic=None, source_name=None, use_top=False, sector_filter=None):
    """
    Lancia scan via agents-runner con autenticazione OIDC.
    Varianti:
    - scan normale: solo topic
    - migliori fonti: use_top=True
    - fonte specifica: source_name='nome'
    - settore specifico: sector_filter='settore'
    """
    if not AGENTS_RUNNER_URL:
        return "AGENTS_RUNNER_URL non configurato. Scan non disponibile."

    # Autenticazione OIDC per Cloud Run service-to-service
    token = get_oidc_token(AGENTS_RUNNER_URL)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        if source_name or use_top or sector_filter:
            # Scan mirato su fonte/settore specifico
            payload = {}
            if source_name:
                payload["source_name"] = source_name
            if use_top:
                payload["use_top"] = True
            if sector_filter:
                payload["sector"] = sector_filter
            r = http_requests.post(
                f"{AGENTS_RUNNER_URL}/scanner/targeted",
                json=payload,
                headers=headers,
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                sources_used = data.get("sources_used", [])
                saved = data.get("saved", 0)
                label = f"fonti: {', '.join(sources_used)}" if sources_used else "scan mirato"
                return f"Scan avviato ({label}). Problemi trovati: {saved}"
            return f"Errore scan: HTTP {r.status_code}"
        else:
            # Scan normale con topic
            r = http_requests.post(
                f"{AGENTS_RUNNER_URL}/scanner/custom",
                json={"topic": topic or "specific problems people pay to solve 2026"},
                headers=headers,
                timeout=30,
            )
            if r.status_code == 200:
                return f"Scan avviato per: {topic or 'strategia corrente'}"
            return f"Errore scan: HTTP {r.status_code}"
    except Exception as e:
        return f"Errore scan: {e}"


# ---- CODA AZIONI ----

def enqueue_action(user_id, action_type, title, description, payload=None,
                   priority=5, urgency=5, importance=5):
    """Inserisce un'azione nella coda. Ritorna l'ID dell'azione."""
    try:
        row = {
            "user_id": int(user_id),
            "action_type": action_type,
            "title": title,
            "description": description,
            "payload": json.dumps(payload) if payload else None,
            "priority": priority,
            "urgency": urgency,
            "importance": importance,
            "status": "pending",
        }
        result = supabase.table(ACTION_QUEUE_TABLE).insert(row).execute()
        if result.data:
            action_id = result.data[0]["id"]
            logger.info(f"[ACTION_QUEUE] Azione {action_id} inserita: {title}")
            return action_id
        return None
    except Exception as e:
        logger.error(f"[ACTION_QUEUE] enqueue: {e}")
        return None


def get_next_action(user_id):
    """Ritorna la prossima azione pending con priority_score piu' alto."""
    try:
        result = supabase.table(ACTION_QUEUE_TABLE) \
            .select("*") \
            .eq("user_id", int(user_id)) \
            .eq("status", "pending") \
            .order("priority_score", desc=True) \
            .limit(1).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        logger.error(f"[ACTION_QUEUE] get_next: {e}")
        return None


def count_pending_actions(user_id):
    """Conta azioni pending per un utente."""
    try:
        result = supabase.table(ACTION_QUEUE_TABLE) \
            .select("id", count="exact") \
            .eq("user_id", int(user_id)) \
            .eq("status", "pending") \
            .execute()
        return result.count or 0
    except Exception as e:
        logger.error(f"[ACTION_QUEUE] count: {e}")
        return 0


def complete_action(action_id, new_status="completed"):
    """Marca un'azione come completata o skippata."""
    try:
        supabase.table(ACTION_QUEUE_TABLE).update({
            "status": new_status,
            "completed_at": datetime.now().isoformat(),
        }).eq("id", action_id).execute()
        logger.info(f"[ACTION_QUEUE] Azione {action_id} -> {new_status}")
        return True
    except Exception as e:
        logger.error(f"[ACTION_QUEUE] complete: {e}")
        return False


def list_pending_actions(user_id):
    """Lista compatta azioni pending ordinate per priority_score."""
    try:
        result = supabase.table(ACTION_QUEUE_TABLE) \
            .select("id,action_type,title,priority_score,created_at") \
            .eq("user_id", int(user_id)) \
            .eq("status", "pending") \
            .order("priority_score", desc=True) \
            .limit(20).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"[ACTION_QUEUE] list: {e}")
        return []


def format_action_message(action, pending_count):
    """Formatta un'azione nel formato Telegram standard."""
    action_type = action.get("action_type", "")
    title = action.get("title", "Azione")
    desc = action.get("description", "")
    sep = "\u2501" * 15

    if action_type == "approve_bos":
        # Formato speciale per BOS — 2 righe descrizione essenziale
        payload = action.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except:
                payload = {}
        bos_score = float(payload.get("bos_score", 0))
        sol_title = payload.get("sol_title", "?")
        problem_title = payload.get("problem_title", title.replace("BOS PRONTO \u2014 ", ""))
        desc_lines = "\n".join(desc.split("\n")[:2])
        return (
            f"\u26a1 AZIONE RICHIESTA [{pending_count} in coda]\n"
            f"{sep}\n"
            f"\U0001f3af BOS PRONTO \u2014 {problem_title[:60]}\n"
            f"Score: {bos_score:.2f}/1 | Soluzione: {sol_title[:50]}\n"
            f"{desc_lines[:200]}\n"
            f"{sep}\n"
            f"\u2705 Avvia esecuzione  |  \u274c Scarta  |  \U0001f50d Dettagli"
        )

    # Formato standard per altri tipi
    desc_lines = desc.split("\n")[:3]
    desc_short = "\n".join(desc_lines)
    return (
        f"AZIONE RICHIESTA [{pending_count} in coda]\n"
        f"{'_' * 30}\n"
        f"{title}\n"
        f"{desc_short}\n"
        f"{'_' * 30}\n"
        f"Si  |  No  |  Dettagli"
    )


def send_next_action(chat_id):
    """Invia la prossima azione in coda a Mirco. Ritorna True se inviata."""
    user_id = chat_id
    action = get_next_action(user_id)
    if not action:
        return False

    pending = count_pending_actions(user_id)
    msg = format_action_message(action, pending)
    _current_action[chat_id] = action
    _send_notification_now(msg)
    return True


def handle_action_response(chat_id, response_text):
    """Gestisce la risposta di Mirco a un'azione in coda. Ritorna (handled, reply_text).
    Se _current_action non è in memoria (es. dopo restart CC), carica l'azione pending dal DB."""
    lower = response_text.strip().lower()
    response_keywords = {
        "si", "sì", "ok", "yes", "vai", "approva", "confermo",
        "no", "rifiuta", "skip", "salta", "annulla",
        "dettagli", "spiega", "approfondisci", "dimmi di piu",
    }

    action = _current_action.get(chat_id)

    # Se non c'è azione in memoria ma la risposta è una keyword → cerca nel DB
    if not action and lower in response_keywords:
        db_action = get_next_action(chat_id)
        if db_action:
            _current_action[chat_id] = db_action
            action = db_action

    if not action:
        return False, None
    action_id = action["id"]
    action_type = action.get("action_type", "")
    payload = action.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except:
            payload = {}
    payload = payload or {}

    lower = response_text.strip().lower()

    # Risposte affermative
    if lower in ("si", "sì", "ok", "yes", "vai", "approva", "confermo"):
        complete_action(action_id, "completed")
        del _current_action[chat_id]

        # Esegui operazione in base al tipo
        result_msg = _execute_action(action_type, payload, approved=True)

        # Manda prossima azione automaticamente
        threading.Thread(target=_send_next_action_delayed, args=(chat_id,), daemon=True).start()
        return True, result_msg

    # Risposte negative
    elif lower in ("no", "rifiuta", "skip", "salta", "annulla"):
        complete_action(action_id, "rejected" if lower in ("no", "rifiuta") else "skipped")
        del _current_action[chat_id]

        # Manda prossima azione automaticamente
        threading.Thread(target=_send_next_action_delayed, args=(chat_id,), daemon=True).start()

        if lower in ("no", "rifiuta"):
            result_msg = _execute_action(action_type, payload, approved=False)
            return True, result_msg
        return True, "Azione saltata."

    # Richiesta dettagli
    elif lower in ("dettagli", "spiega", "approfondisci", "dimmi di piu"):
        detail = action.get("description", "Nessun dettaglio aggiuntivo.")
        payload_info = ""
        if payload:
            payload_info = f"\n\nDati: {json.dumps(payload, indent=2, ensure_ascii=False, default=str)[:1000]}"
        return True, f"{detail}{payload_info}"

    return False, None


def _execute_action(action_type, payload, approved):
    """Esegue l'operazione conseguente a un'azione approvata/rifiutata."""
    try:
        if action_type == "approve_bos":
            # Azione principale della pipeline automatica
            sid = payload.get("solution_id")
            pid = payload.get("problem_id")
            if approved and sid:
                # Anti-duplicazione: rigetta silenziosamente se progetto già in corso
                try:
                    dup = supabase.table("projects").select("id,status").eq("bos_id", int(sid)).execute()
                    if dup.data and dup.data[0].get("status") not in ("new", "init", "failed"):
                        logger.info(f"[EXECUTE_ACTION] BOS {sid} già processato (status={dup.data[0].get('status')}), skip silenzioso")
                        return None
                except Exception as e:
                    logger.warning(f"[EXECUTE_ACTION] Duplicate BOS check error: {e}")

                # Seleziona la soluzione per esecuzione e aggiorna problema ad approved
                result = select_solution(int(sid))
                if pid:
                    try:
                        supabase.table("problems").update({"status": "approved"}).eq("id", int(pid)).execute()
                    except:
                        pass
                # Triggera Layer 3: init_project in background
                if AGENTS_RUNNER_URL:
                    def _trigger_init():
                        try:
                            oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
                            headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
                            http_requests.post(
                                f"{AGENTS_RUNNER_URL}/project/init",
                                json={"solution_id": sid},
                                headers=headers,
                                timeout=5,
                            )
                        except Exception as e:
                            logger.warning(f"[EXECUTE_ACTION] /project/init trigger error: {e}")
                    threading.Thread(target=_trigger_init, daemon=True).start()
                return f"BOS approvato. {result}\nAvvio Layer 3: spec, landing page, review in preparazione."
            elif not approved and sid:
                try:
                    supabase.table("solutions").update({"status": "archived"}).eq("id", int(sid)).execute()
                    if pid:
                        supabase.table("problems").update({"status": "archived"}).eq("id", int(pid)).execute()
                except:
                    pass
                return "BOS scartato. Soluzione e problema archiviati."

        elif action_type == "approve_problem":
            pid = payload.get("problem_id")
            if pid:
                if approved:
                    return approve_problem(int(pid))
                else:
                    return reject_problem(int(pid))

        elif action_type == "review_solution":
            sid = payload.get("solution_id")
            if sid and approved:
                return select_solution(int(sid))
            elif sid and not approved:
                return f"Soluzione ID {sid} non selezionata."

        elif action_type == "confirm_deploy":
            if approved:
                return "Deploy confermato."
            else:
                return "Deploy annullato."

        # Tipo generico
        if approved:
            return "Azione confermata."
        else:
            return "Azione rifiutata."
    except Exception as e:
        return f"Errore esecuzione azione: {e}"


def _send_next_action_delayed(chat_id):
    """Attende 2 secondi e poi invia la prossima azione."""
    time.sleep(2)
    had_action = send_next_action(chat_id)
    if not had_action:
        _send_notification_now("Nessuna altra azione in coda.")


# ---- LOGGING ----

def log_to_supabase(agent_id, action, input_summary, output_summary, model_used,
                     tokens_in=0, tokens_out=0, cost=0, duration_ms=0,
                     status="success", error=None):
    def _log():
        try:
            supabase.table("agent_logs").insert({
                "agent_id": agent_id,
                "action": action,
                "layer": 0,
                "input_summary": (input_summary or "")[:500],
                "output_summary": (output_summary or "")[:500],
                "model_used": model_used,
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "status": status,
                "error": error,
            }).execute()
        except Exception as e:
            logger.error(f"[LOG] {e}")
    threading.Thread(target=_log, daemon=True).start()


# ---- CLAUDE (Sonnet 4.5 + tool_use) ----

MODEL = "claude-sonnet-4-6"
COST_INPUT_PER_M = 3.0
COST_OUTPUT_PER_M = 15.0
MAX_TOOL_LOOPS = 5

# Keywords che richiedono risposte piu' lunghe
_LONG_KEYWORDS = {"problemi", "soluzioni", "stato", "status", "costi", "costs", "report", "lista", "tutti", "tutto"}


def _serialize_content(content):
    """Serializza content blocks Claude in formato JSON-safe per la storia."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        result = []
        for block in content:
            if isinstance(block, dict):
                result.append(block)
            elif hasattr(block, "model_dump"):
                result.append(block.model_dump())
            elif hasattr(block, "text"):
                result.append({"type": "text", "text": block.text})
            else:
                result.append({"type": "text", "text": str(block)})
        return result
    if hasattr(content, "model_dump"):
        return content.model_dump()
    return str(content)


def _check_chat_history_table():
    """Verifica se la tabella chat_history esiste. Cacha solo successi, riprova su fallimento."""
    global _chat_history_available
    if _chat_history_available is True:
        return True
    try:
        supabase.table(CHAT_HISTORY_TABLE).select("id").limit(1).execute()
        _chat_history_available = True
        logger.info("[CHAT_HISTORY] Tabella disponibile")
        return True
    except Exception as e:
        logger.warning(f"[CHAT_HISTORY] Tabella non disponibile: {e}")
        return False


def _save_chat_message(chat_id, role, content):
    """Salva un messaggio nella tabella chat_history. role: user/assistant/summary."""
    if not _check_chat_history_table():
        return
    try:
        content_str = json.dumps(content, default=str, ensure_ascii=False) if not isinstance(content, str) else content
        cid = int(chat_id) if str(chat_id).lstrip('-').isdigit() else 0
        supabase.table(CHAT_HISTORY_TABLE).insert({
            "chat_id": cid,
            "role": role,
            "content": content_str,
        }).execute()
    except Exception as e:
        logger.error(f"[CHAT_HISTORY] save: {e}")


def _load_chat_context(chat_id):
    """Carica contesto da DB: ultimo summary + ultimi 30 messaggi."""
    summary_text = ""
    messages = []
    if not _check_chat_history_table():
        return summary_text, messages
    try:
        cid = int(chat_id) if str(chat_id).lstrip('-').isdigit() else 0
        # 1. Ultimo summary
        s = supabase.table(CHAT_HISTORY_TABLE) \
            .select("content") \
            .eq("chat_id", cid) \
            .eq("role", "summary") \
            .order("created_at", desc=True) \
            .limit(1).execute()
        if s.data:
            summary_text = s.data[0]["content"]

        # 2. Ultimi 10 messaggi (non summary)
        rows = supabase.table(CHAT_HISTORY_TABLE) \
            .select("role,content") \
            .eq("chat_id", cid) \
            .neq("role", "summary") \
            .order("created_at", desc=True) \
            .limit(MAX_DB_MESSAGES).execute()

        if rows.data:
            for row in reversed(rows.data):
                try:
                    content = json.loads(row["content"])
                except (json.JSONDecodeError, TypeError):
                    content = row["content"]
                messages.append({"role": row["role"], "content": content})
    except Exception as e:
        logger.error(f"[CHAT_HISTORY] load: {e}")
    return summary_text, messages


def _count_user_messages_since_summary(chat_id):
    """Conta messaggi utente dall'ultimo summary."""
    if not _check_chat_history_table():
        return 0
    try:
        cid = int(chat_id) if str(chat_id).lstrip('-').isdigit() else 0
        # Timestamp ultimo summary
        s = supabase.table(CHAT_HISTORY_TABLE) \
            .select("created_at") \
            .eq("chat_id", cid) \
            .eq("role", "summary") \
            .order("created_at", desc=True) \
            .limit(1).execute()

        q = supabase.table(CHAT_HISTORY_TABLE) \
            .select("id", count="exact") \
            .eq("chat_id", cid) \
            .eq("role", "user")

        if s.data:
            q = q.gt("created_at", s.data[0]["created_at"])

        result = q.execute()
        return result.count or 0
    except Exception as e:
        logger.error(f"[CHAT_HISTORY] count: {e}")
        return 0


def _generate_and_save_summary(chat_id):
    """Genera un riassunto compatto della conversazione con Haiku e salvalo."""
    if not _check_chat_history_table():
        return
    try:
        cid = int(chat_id) if str(chat_id).lstrip('-').isdigit() else 0
        # Carica messaggi dall'ultimo summary (max 60 righe)
        s = supabase.table(CHAT_HISTORY_TABLE) \
            .select("created_at") \
            .eq("chat_id", cid) \
            .eq("role", "summary") \
            .order("created_at", desc=True) \
            .limit(1).execute()

        q = supabase.table(CHAT_HISTORY_TABLE) \
            .select("role,content") \
            .eq("chat_id", cid) \
            .neq("role", "summary") \
            .order("created_at", desc=True) \
            .limit(60)

        if s.data:
            q = q.gt("created_at", s.data[0]["created_at"])

        rows = q.execute()
        if not rows.data or len(rows.data) < 5:
            return

        # Costruisci testo per il riassunto
        lines = []
        for row in reversed(rows.data):
            role = row["role"]
            raw = row["content"]
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    texts = [b.get("text", "") for b in parsed if isinstance(b, dict) and b.get("type") == "text"]
                    text = " ".join(texts)[:200] if texts else raw[:200]
                elif isinstance(parsed, str):
                    text = parsed[:200]
                else:
                    text = str(parsed)[:200]
            except:
                text = raw[:200]
            lines.append(f"{role}: {text}")

        conversation_text = "\n".join(lines)[:3000]

        # Genera con Haiku (economico)
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system="Genera un riassunto compatto della conversazione in italiano. Includi: temi discussi, decisioni prese, problemi/soluzioni approvati/rifiutati con ID, preferenze espresse da Mirco. Max 250 parole. Testo piano, no markdown.",
            messages=[{"role": "user", "content": f"Riassumi:\n\n{conversation_text}"}],
        )
        summary_text = resp.content[0].text

        # Logga costo summary
        s_cost = (resp.usage.input_tokens * 1.0 + resp.usage.output_tokens * 5.0) / 1_000_000
        log_to_supabase("command_center", "summary_generation", f"chat_id={chat_id}",
                        summary_text[:200], "claude-haiku-4-5-20251001",
                        resp.usage.input_tokens, resp.usage.output_tokens, s_cost, 0)

        # Salva come summary (role="summary" per distinguerlo)
        _save_chat_message(chat_id, "summary", summary_text)
        logger.info(f"[SUMMARY] Generato per chat_id={chat_id}")
    except Exception as e:
        logger.error(f"[SUMMARY] {e}")


def _update_session_context(chat_id, messages):
    """Aggiorna session context analizzando i tool results nella conversazione."""
    if chat_id not in _session_context:
        _session_context[chat_id] = {}
    sc = _session_context[chat_id]
    # Analizza ultimi messaggi per estrarre ID
    for msg in messages[-6:]:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    # Tool use: traccia quale tool e' stato chiamato
                    if block.get("type") == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        if name == "approve_problem":
                            sc["last_problem_id"] = inp.get("problem_id")
                            sc["last_command"] = "approve"
                        elif name == "reject_problem":
                            sc["last_problem_id"] = inp.get("problem_id")
                            sc["last_command"] = "reject"
                        elif name == "select_solution":
                            sc["last_solution_id"] = inp.get("solution_id")
                            sc["last_command"] = "select"
                        elif name == "query_supabase":
                            table = inp.get("table", "")
                            if table == "problems":
                                sc["last_command"] = "problemi"
                            elif table == "solutions":
                                sc["last_command"] = "soluzioni"
                    # Tool result: estrai ID mostrati
                    elif block.get("type") == "tool_result":
                        result_text = block.get("content", "")
                        if isinstance(result_text, str) and '"id"' in result_text:
                            try:
                                data = json.loads(result_text)
                                if isinstance(data, list):
                                    ids = [str(item.get("id")) for item in data if "id" in item]
                                    if ids:
                                        sc["last_shown_ids"] = ", ".join(ids[:10])
                            except:
                                pass


def ask_claude(user_message, chat_id=None, is_photo=False, image_b64=None):
    start = time.time()
    cid = chat_id or "default"

    if cid not in _session_context:
        _session_context[cid] = {}

    try:
        # Carica contesto da DB (summary + ultimi 30 messaggi)
        summary_text, history_messages = _load_chat_context(cid)

        system = build_system_prompt(chat_id=cid, conversation_summary=summary_text)

        # Costruisci messaggi dalla storia DB
        messages = list(history_messages)

        # Aggiungi messaggio corrente
        if is_photo and image_b64:
            user_content = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                },
                {"type": "text", "text": user_message},
            ]
        else:
            user_content = user_message

        messages.append({"role": "user", "content": user_content})

        # Token budget: stima rapida (~4 char/token). Se > 2000 token, tronca storico a ultimi 7 msg.
        _est_tokens = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4
        if _est_tokens > 2000 and len(messages) > 7:
            messages = messages[-7:]
            # Garantisce che il primo messaggio sia sempre "user"
            while messages and messages[0]["role"] != "user":
                messages.pop(0)

        # max_tokens dinamico
        lower_msg = user_message.lower()
        max_tokens = 4000 if any(kw in lower_msg for kw in _LONG_KEYWORDS) else 2000

        total_in = 0
        total_out = 0
        final = ""
        all_tool_messages = []  # tool exchanges da salvare in DB

        for _ in range(MAX_TOOL_LOOPS):
            resp = claude.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=TOOLS,
            )
            total_in += resp.usage.input_tokens
            total_out += resp.usage.output_tokens

            if resp.stop_reason == "end_turn":
                for b in resp.content:
                    if hasattr(b, "text"):
                        final += b.text
                break
            elif resp.stop_reason == "tool_use":
                results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        logger.info(f"[TOOL] {b.name}({json.dumps(b.input, ensure_ascii=False)[:100]})")
                        r = execute_tool(b.name, b.input)
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": b.id,
                            "content": str(r)[:8000],
                        })
                serialized_assistant = _serialize_content(resp.content)
                messages.append({"role": "assistant", "content": serialized_assistant})
                messages.append({"role": "user", "content": results})
                all_tool_messages.append({"role": "assistant", "content": serialized_assistant})
                all_tool_messages.append({"role": "user", "content": results})
            else:
                for b in resp.content:
                    if hasattr(b, "text"):
                        final += b.text
                break

        dur = int((time.time() - start) * 1000)
        cost = (total_in * COST_INPUT_PER_M + total_out * COST_OUTPUT_PER_M) / 1_000_000

        # Salva in DB nell'ordine corretto: user -> tool exchanges -> assistant finale
        # Per foto: salva solo il caption, non il base64
        user_to_save = f"[FOTO] {user_message}" if is_photo else _serialize_content(user_content)
        _save_chat_message(cid, "user", user_to_save)
        for tm in all_tool_messages:
            _save_chat_message(cid, tm["role"], tm["content"])
        _save_chat_message(cid, "assistant", final)

        # Aggiorna session context dai messaggi correnti
        _update_session_context(cid, messages)

        # Check se serve generare un summary (ogni 20 messaggi utente, in background)
        def _maybe_summarize():
            count = _count_user_messages_since_summary(cid)
            if count >= SUMMARY_INTERVAL:
                _generate_and_save_summary(cid)
        threading.Thread(target=_maybe_summarize, daemon=True).start()

        log_to_supabase(
            "command_center", "chat", user_message[:300], final[:300],
            MODEL, total_in, total_out, cost, dur,
        )

        # Se final e' vuoto (es. dopo tool loops che non producono testo), forza una risposta
        if not final.strip():
            try:
                followup = claude.messages.create(
                    model=MODEL, max_tokens=1000, system=system,
                    messages=messages,
                )
                for b in followup.content:
                    if hasattr(b, "text"):
                        final += b.text
                total_in += followup.usage.input_tokens
                total_out += followup.usage.output_tokens
            except:
                pass
        return final or "Ho eseguito le operazioni richieste."
    except Exception as e:
        dur = int((time.time() - start) * 1000)
        log_to_supabase(
            "command_center", "chat", user_message[:300], None,
            MODEL, duration_ms=dur, status="error", error=str(e),
        )
        return f"Errore: {e}"


def clean_reply(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'```[\w]*\n?', '', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text.strip()


# ---- CARD FORMAT HELPERS (FIX 4) ----
_SEP = "\u2501" * 15


def _make_card(emoji, title, context, body_lines, footer=None):
    """Costruisce testo card con formato standard brAIn.
    Header: [emoji] *TITOLO* — contesto
    Sep, body con ├/└, sep, footer opzionale.
    """
    lines = [f"{emoji} *{title}*" + (f" \u2014 {context}" if context else ""), _SEP]
    for i, line in enumerate(body_lines):
        if not line:
            lines.append("")
            continue
        prefix = "\u2514" if i == len(body_lines) - 1 else "\u251c"
        if line.startswith("└") or line.startswith("├") or line.startswith("━"):
            lines.append(line)
        else:
            lines.append(f"{prefix} {line}")
    lines.append(_SEP)
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def _call_agents_runner_sync(endpoint, body=None):
    """Chiama agents_runner endpoint con OIDC auth. Usato per report on-demand."""
    if not AGENTS_RUNNER_URL:
        return None
    try:
        oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
        headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
        resp = http_requests.post(
            f"{AGENTS_RUNNER_URL}{endpoint}",
            json=body or {},
            headers=headers,
            timeout=15,
        )
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        logger.warning(f"[CALL_AR] {endpoint}: {e}")
        return None


def is_mirco_active():
    """True se Mirco ha mandato un messaggio negli ultimi 90 secondi."""
    return (time.time() - _last_mirco_message_time) < MIRCO_ACTIVE_WINDOW


def queue_or_send_notification(message, is_critical=False):
    """Se Mirco e' attivo, mette in coda. Se CRITICAL, invia subito."""
    if is_critical or not is_mirco_active():
        _send_notification_now(message)
    else:
        with _notification_lock:
            _notification_queue.append(message)


def _send_notification_now(message, parse_mode="Markdown"):
    """Invia notifica Telegram immediatamente. Usa formato card se non già formattato."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = AUTHORIZED_USER_ID
    if not token or not chat_id:
        return
    # Se non è già una card (non contiene ━), wrappa in card
    if _SEP not in message and not message.startswith("📊") and not message.startswith("💶") and not message.startswith("⚙️"):
        emoji = "\U0001f514"  # 🔔
        first_line = message.split("\n")[0][:80]
        rest_lines = message.split("\n")[1:]
        body = rest_lines if rest_lines else []
        message = _make_card(emoji, "NOTIFICA brAIn", first_line, body) if body else f"\U0001f514 *{first_line}*"
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": parse_mode},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"[NOTIFY] {e}")


def flush_notification_queue():
    """Invia tutte le notifiche in coda come messaggio unico."""
    with _notification_lock:
        if not _notification_queue:
            return
        messages = list(_notification_queue)
        _notification_queue.clear()

    if len(messages) == 1:
        _send_notification_now(messages[0])
    else:
        combined = f"NOTIFICHE ({len(messages)})\n\n" + "\n---\n".join(messages)
        # Tronca se troppo lungo
        if len(combined) > 4000:
            combined = combined[:3990] + "\n..."
        _send_notification_now(combined)


def _notification_flusher_loop():
    """Thread background che controlla e invia la coda ogni 30 secondi."""
    while True:
        time.sleep(30)
        try:
            silence = time.time() - _last_mirco_message_time
            if silence >= NOTIFICATION_BATCH_DELAY and _notification_queue:
                flush_notification_queue()
        except:
            pass


# Avvia flusher in background
threading.Thread(target=_notification_flusher_loop, daemon=True).start()


# ---- GITHUB API + CODE AGENT ----

def github_api(method, endpoint, data=None):
    """Helper per chiamate GitHub API."""
    if not GITHUB_TOKEN:
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}{endpoint}"
    try:
        if method == "GET":
            r = http_requests.get(url, headers=headers, timeout=30)
        elif method == "PUT":
            r = http_requests.put(url, headers=headers, json=data, timeout=30)
        elif method == "POST":
            r = http_requests.post(url, headers=headers, json=data, timeout=30)
        else:
            return None
        if r.status_code in (200, 201):
            return r.json()
        logger.warning(f"[GITHUB] {method} {endpoint} → {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"[GITHUB] {e}")
        return None


def github_get_file(path):
    """Recupera contenuto e SHA di un file da GitHub."""
    result = github_api("GET", f"/contents/{path}")
    if result and "content" in result:
        content = base64.b64decode(result["content"]).decode("utf-8")
        return content, result["sha"]
    return None, None


def github_commit_files(files, message):
    """Committa uno o piu' file su GitHub via Contents API."""
    committed = []
    for f in files:
        path = f["path"]
        content = f["content"]
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        existing = github_api("GET", f"/contents/{path}")
        data = {"message": message, "content": content_b64}
        if existing and "sha" in existing:
            data["sha"] = existing["sha"]

        result = github_api("PUT", f"/contents/{path}", data)
        if not result:
            return False, f"Errore commit {path}"
        committed.append(path)
    return True, committed


# ---- LAYER 3: FORUM TOPICS + PROJECT MANAGEMENT ----

def lookup_project_by_topic_id(thread_id):
    """Cerca un progetto per topic_id nel DB. Ritorna il record o None."""
    try:
        result = supabase.table("projects").select("*").eq("topic_id", int(thread_id)).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        logger.warning(f"[TOPIC] lookup_project_by_topic_id({thread_id}): {e}")
    return None


def send_spec_chunks(project_id, target_chat_id, thread_id=None):
    """Carica spec_md dal DB e lo invia in chunks (max 3800 chars ciascuno)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    try:
        proj = supabase.table("projects").select("spec_md,name").eq("id", project_id).execute()
        if not proj.data or not proj.data[0].get("spec_md"):
            http_requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": target_chat_id, "text": "SPEC non ancora disponibile per questo progetto."},
                timeout=10,
            )
            return
        spec_md = proj.data[0]["spec_md"]
        name = proj.data[0].get("name", "")
        # Header
        payload = {"chat_id": target_chat_id, "text": f"SPEC.md — {name}:"}
        if thread_id:
            payload["message_thread_id"] = thread_id
        http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
        # Chunks
        chunk_size = 3800
        for i in range(0, len(spec_md), chunk_size):
            chunk_payload = {"chat_id": target_chat_id, "text": spec_md[i:i + chunk_size]}
            if thread_id:
                chunk_payload["message_thread_id"] = thread_id
            http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=chunk_payload, timeout=10)
    except Exception as e:
        logger.error(f"[SPEC_CHUNKS] {e}")


def build_approved_action(project_id, target_chat_id, thread_id=None):
    """Esegue le azioni quando Mirco approva il build della SPEC."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    try:
        # Aggiorna status
        supabase.table("projects").update({"status": "build_approved"}).eq("id", project_id).execute()

        # Notifica
        confirm_payload = {"chat_id": target_chat_id, "text": "Build approvato. Generando prompt Claude Code..."}
        if thread_id:
            confirm_payload["message_thread_id"] = thread_id
        if token:
            http_requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=confirm_payload, timeout=10,
            )

        # Chiama agents_runner per generare build prompt
        if AGENTS_RUNNER_URL:
            oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
            headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
            http_requests.post(
                f"{AGENTS_RUNNER_URL}/project/build_prompt",
                json={"project_id": str(project_id)},
                headers=headers,
                timeout=10,
            )
    except Exception as e:
        logger.error(f"[BUILD_APPROVED] {e}")


def is_project_collaborator(user_id, project_id):
    """Verifica se user_id è un collaboratore attivo del progetto."""
    try:
        r = supabase.table("project_members").select("id") \
            .eq("telegram_user_id", user_id).eq("project_id", project_id) \
            .eq("active", True).execute()
        return bool(r.data)
    except:
        return False


def try_register_collaborator(user_id, username, project_id):
    """Se esiste una riga project_members con user_id NULL, la aggiorna con user_id e username."""
    try:
        r = supabase.table("project_members").select("id") \
            .is_("telegram_user_id", "null").eq("project_id", project_id) \
            .eq("active", True).execute()
        if r.data:
            row_id = r.data[0]["id"]
            supabase.table("project_members").update({
                "telegram_user_id": user_id,
                "telegram_username": username or "",
            }).eq("id", row_id).execute()
            return True
    except Exception as e:
        logger.warning(f"[REGISTER_COLLAB] {e}")
    return False


def _notify_mirco_silently(project_id, sender_name, message):
    """Invia DM silenzioso a Mirco con un messaggio arrivato da un collaboratore."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    mirco_id = AUTHORIZED_USER_ID
    if not token or not mirco_id:
        return
    try:
        proj = supabase.table("projects").select("name").eq("id", project_id).execute()
        proj_name = proj.data[0]["name"] if proj.data else f"Progetto {project_id}"
    except:
        proj_name = f"Progetto {project_id}"
    text = f"\U0001f4e9 [{sender_name}] nel Cantiere {proj_name}: {message[:200]}"
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": mirco_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"[NOTIFY_MIRCO] {e}")


def handle_launch(project_id, chat_id, thread_id=None):
    """Aggiorna status a launch_approved, notifica nel topic, avvia validation in background."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    try:
        proj = supabase.table("projects").select("name,github_repo,slug").eq("id", project_id).execute()
        project = proj.data[0] if proj.data else {}
        name = project.get("name", f"Progetto {project_id}")
        github_repo = project.get("github_repo", "")
    except:
        name = f"Progetto {project_id}"
        github_repo = ""

    try:
        supabase.table("projects").update({"status": "launch_approved"}).eq("id", project_id).execute()
    except Exception as e:
        logger.warning(f"[LAUNCH] DB update: {e}")

    # Notifica nel topic — Fix 6: no link GitHub 404
    slug = project.get("slug", github_repo.replace("mircocerisola/", "") if github_repo else "")
    launch_card = _make_card(
        "\U0001f680", "LANCIO APPROVATO", name,
        [f"Repo: brain-{slug} (privato)", "Avvia deploy manualmente su Cloud Run."],
    )
    if token:
        payload = {"chat_id": chat_id, "text": launch_card, "parse_mode": "Markdown"}
        if thread_id:
            payload["message_thread_id"] = thread_id
        try:
            http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
        except Exception as e:
            logger.warning(f"[LAUNCH] notifica: {e}")

    # Avvia validation in background
    import threading as _threading
    if AGENTS_RUNNER_URL:
        def _trigger_validation():
            try:
                oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
                headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
                http_requests.post(f"{AGENTS_RUNNER_URL}/validation", headers=headers, timeout=10)
            except Exception as e:
                logger.warning(f"[LAUNCH] validation trigger: {e}")
        _threading.Thread(target=_trigger_validation, daemon=True).start()


def start_team_setup(project_id, chat_id, thread_id=None):
    """Invia messaggio FASE A2 nel topic: aggiungi collaboratori o salta."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    try:
        supabase.table("projects").update({"status": "team_setup"}).eq("id", project_id).execute()
    except Exception as e:
        logger.warning(f"[TEAM_SETUP] DB update: {e}")

    msg = _make_card(
        "\U0001f465", "TEAM SETUP", f"progetto {project_id}",
        ["SPEC validata! Vuoi aggiungere collaboratori al Cantiere?",
         "Potranno scrivere nel topic — ricevi copia di ogni messaggio."],
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\u2795 Aggiungi collaboratore", "callback_data": f"team_add:{project_id}"},
            {"text": "\u23ed\ufe0f Salta, avvia build", "callback_data": f"team_skip:{project_id}"},
        ]]
    }
    payload = {"chat_id": chat_id, "text": msg, "reply_markup": reply_markup, "parse_mode": "Markdown"}
    if thread_id:
        payload["message_thread_id"] = thread_id
    try:
        http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"[TEAM_SETUP] messaggio: {e}")


def trigger_build_start(project_id, chat_id, thread_id=None):
    """Chiama /project/build_prompt su agents_runner con OIDC auth."""
    if AGENTS_RUNNER_URL:
        try:
            oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
            headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
            http_requests.post(
                f"{AGENTS_RUNNER_URL}/project/build_prompt",
                json={"project_id": str(project_id)},
                headers=headers,
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"[BUILD_START] {e}")


async def handle_project_message(update, project):
    """Handler per messaggi arrivati in un Forum Topic di progetto."""
    msg = update.message.text or ""
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    project_id = project["id"]
    name = project.get("name", "")
    project_status = project.get("status", "")
    build_phase = project.get("build_phase", 0) or 0
    user_id = update.effective_user.id if update.effective_user else None
    username = update.effective_user.username or "" if update.effective_user else ""

    # Salva nel buffer topic per contesto conversazione
    if thread_id and msg.strip():
        _topic_buffer_add(chat_id, thread_id, msg, role="user")

    # 1. Identifica mittente
    _is_mirco = is_authorized(update)
    _is_collab = is_project_collaborator(user_id, project_id) if user_id else False

    # 2. Mittente sconosciuto: tenta registrazione collaboratore
    if not _is_mirco and not _is_collab and user_id:
        registered = try_register_collaborator(user_id, username, project_id)
        if registered:
            _is_collab = True
            sender_name = username or str(user_id)
            await update.message.reply_text(
                _make_card("\U0001f3d7\ufe0f", "CANTIERE", name, ["Benvenuto nel cantiere!"]),
                parse_mode="Markdown",
            )
            _notify_mirco_silently(project_id, sender_name, f"[nuovo collaboratore registrato] {msg}")
            return

    # 3. Se collaboratore: gira il messaggio a Mirco silenziosamente
    if _is_collab and not _is_mirco:
        sender_name = username or str(user_id)
        _notify_mirco_silently(project_id, sender_name, msg)

    ctx = _session_context.get(chat_id, {})

    # 4. awaiting_phone: salva telefono + genera invite link
    if ctx.get("awaiting_phone") == project_id and _is_mirco:
        phone = msg.strip()
        _session_context[chat_id]["awaiting_phone"] = None
        if AGENTS_RUNNER_URL:
            try:
                oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
                headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
                resp = http_requests.post(
                    f"{AGENTS_RUNNER_URL}/project/generate_invite",
                    json={"project_id": str(project_id), "phone": phone},
                    headers=headers,
                    timeout=20,
                )
                if resp.status_code == 200:
                    invite_link = resp.json().get("invite_link")
                    if invite_link:
                        await update.message.reply_text(
                            _make_card("\U0001f517", "INVITE LINK", name,
                                       [f"Link: {invite_link}", "Scade in 24h.",
                                        "Vuoi aggiungere altri? Rispondi 'aggiungi' o 'basta'."]),
                            parse_mode="Markdown",
                        )
                    else:
                        await update.message.reply_text(
                            _make_card("\u26a0\ufe0f", "INVITE", name, ["Collaboratore salvato.", "Invite link non generato — controlla permessi bot nel gruppo."]),
                            parse_mode="Markdown",
                        )
                else:
                    await update.message.reply_text(
                        _make_card("\u274c", "ERRORE INVITE", name, [f"Errore: {resp.text[:150]}"]),
                        parse_mode="Markdown",
                    )
            except Exception as e:
                await update.message.reply_text(
                    _make_card("\u274c", "ERRORE", name, [str(e)[:200]]),
                    parse_mode="Markdown",
                )
        else:
            await update.message.reply_text(
                _make_card("\u274c", "ERRORE", "sistema", ["agents\\_runner non raggiungibile."]),
                parse_mode="Markdown",
            )
        return

    # Gestione "aggiungi altri" / "basta" dopo invite
    if _is_mirco:
        msg_lower = msg.lower().strip()
        if msg_lower in ("aggiungi", "aggiungi altro", "aggiungi altri"):
            if chat_id not in _session_context:
                _session_context[chat_id] = {}
            _session_context[chat_id]["awaiting_phone"] = project_id
            await update.message.reply_text(
                _make_card("\U0001f4de", "AGGIUNGI COLLABORATORE", name, ["Inviami il numero di telefono (es. +39...)"]),
                parse_mode="Markdown",
            )
            return
        if msg_lower in ("basta", "ok basta", "nessun altro"):
            import threading as _thr
            _thr.Thread(target=trigger_build_start, args=(project_id, chat_id, thread_id), daemon=True).start()
            await update.message.reply_text(
                _make_card("\U0001f680", "BUILD AVVIATO", name, ["Avvio build in corso\u2026"]),
                parse_mode="Markdown",
            )
            return

    # 4.5 Risposta breve nel topic: risolvi con contesto conversazione
    if thread_id and _is_mirco and _is_short_affirmative(msg):
        recent = _topic_buffer_get_recent(chat_id, thread_id)
        if _context_is_spec_discussion(recent):
            mod_desc = _extract_spec_modification(recent)
            await update.message.reply_text(
                _make_card("\u270f\ufe0f", "SPEC", name, [f"Aggiornamento in corso: {mod_desc[:100]}\u2026"]),
                parse_mode="Markdown",
            )
            if AGENTS_RUNNER_URL:
                try:
                    oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
                    headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
                    http_requests.post(
                        f"{AGENTS_RUNNER_URL}/spec/update",
                        json={"project_id": str(project_id), "modification": mod_desc},
                        headers=headers,
                        timeout=15,
                    )
                    token = os.getenv("TELEGRAM_BOT_TOKEN")
                    if token:
                        markup = {"inline_keyboard": [[
                            {"text": "\U0001f4c4 Vedi SPEC aggiornata", "callback_data": f"spec_download:{project_id}"}
                        ]]}
                        payload = {
                            "chat_id": chat_id,
                            "text": f"\u2705 SPEC aggiornata con: {mod_desc[:120]}\nVuoi vedere il file aggiornato?",
                            "reply_markup": markup,
                            "message_thread_id": thread_id,
                        }
                        http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
                        _topic_buffer_add(chat_id, thread_id, f"SPEC aggiornata con: {mod_desc[:80]}", role="bot")
                except Exception as e:
                    await update.message.reply_text(
                        _make_card("\u274c", "ERRORE SPEC", name, [str(e)[:200]]),
                        parse_mode="Markdown",
                    )
            return

    # 5. "ok lanciamo" / "lancia" / "lanciamo"
    if msg.lower().strip() in ("ok lanciamo", "lancia", "lanciamo", "lancio", "ok lancia"):
        if _is_collab and not _is_mirco:
            # Notifica Mirco con pulsante conferma
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if token and AUTHORIZED_USER_ID:
                try:
                    proj = supabase.table("projects").select("name").eq("id", project_id).execute()
                    proj_name = proj.data[0]["name"] if proj.data else name
                    http_requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": AUTHORIZED_USER_ID,
                            "text": f"Il responsabile vuole lanciare '{proj_name}'. Confermi?",
                            "reply_markup": {"inline_keyboard": [[
                                {"text": "\U0001f680 Lancia", "callback_data": f"launch_confirm:{project_id}"}
                            ]]}
                        },
                        timeout=10,
                    )
                except Exception as e:
                    logger.warning(f"[LAUNCH_NOTIFY] {e}")
            await update.message.reply_text(
                _make_card("\u2709\ufe0f", "LANCIO RICHIESTO", name, ["Ho avvisato Mirco.", "In attesa della sua conferma."]),
                parse_mode="Markdown",
            )
        elif _is_mirco:
            import threading as _thr
            _thr.Thread(target=handle_launch, args=(project_id, chat_id, thread_id), daemon=True).start()
            await update.message.reply_text(
                _make_card("\U0001f680", "LANCIO CONFERMATO", name, ["Avvio in corso\u2026"]),
                parse_mode="Markdown",
            )
        return

    # 6. Feedback durante review fasi
    if project_status in ("review_phase1", "review_phase2", "review_phase3") and (_is_mirco or _is_collab):
        if AGENTS_RUNNER_URL:
            try:
                oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
                headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
                http_requests.post(
                    f"{AGENTS_RUNNER_URL}/project/continue_build",
                    json={"project_id": str(project_id), "feedback": msg, "phase": build_phase},
                    headers=headers,
                    timeout=10,
                )
                await update.message.reply_text(
                    _make_card("\U0001f527", "BUILD", name, ["Aggiornando\u2026 ti avviso appena la prossima fase \u00e8 pronta."]),
                    parse_mode="Markdown",
                )
            except Exception as e:
                await update.message.reply_text(
                    _make_card("\u274c", "ERRORE BUILD", name, [str(e)[:200]]),
                    parse_mode="Markdown",
                )
        return

    # 7. awaiting_spec_edit: comportamento esistente
    if ctx.get("awaiting_spec_edit") == project_id:
        _session_context[chat_id]["awaiting_spec_edit"] = None
        await update.message.reply_text(
            _make_card("\u270f\ufe0f", "SPEC", name, ["Aggiornamento in corso\u2026"]),
            parse_mode="Markdown",
        )
        if AGENTS_RUNNER_URL:
            try:
                oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
                headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
                http_requests.post(
                    f"{AGENTS_RUNNER_URL}/spec/update",
                    json={"project_id": str(project_id), "modification": msg},
                    headers=headers,
                    timeout=10,
                )
                token = os.getenv("TELEGRAM_BOT_TOKEN")
                if token:
                    markup = {"inline_keyboard": [[
                        {"text": "\U0001f4c4 Vedi SPEC aggiornata", "callback_data": f"spec_download:{project_id}"}
                    ]]}
                    payload = {
                        "chat_id": chat_id,
                        "text": f"\u2705 SPEC aggiornata con: {msg[:120]}\nVuoi vedere il file aggiornato?",
                        "reply_markup": markup,
                    }
                    if thread_id:
                        payload["message_thread_id"] = thread_id
                    http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
                    _topic_buffer_add(chat_id, thread_id or 0, f"SPEC aggiornata con: {msg[:80]}", role="bot")
                else:
                    await update.message.reply_text(
                        _make_card("\u270f\ufe0f", "SPEC", name, ["Modifica avviata.", "Ti mando la nuova SPEC review."]),
                        parse_mode="Markdown",
                    )
            except Exception as e:
                await update.message.reply_text(
                    _make_card("\u274c", "ERRORE SPEC", name, [str(e)[:200]]),
                    parse_mode="Markdown",
                )
        return

    # 8. Fallback: routing C-Suite con contesto cantiere
    if _is_mirco or _is_collab:
        # Costruisci project_context
        spec_excerpt = (project.get("spec_human_md") or project.get("spec_md") or "")[:2000]
        project_context = (
            f"Cantiere: {name} | Slug: {project.get('slug','')} | "
            f"Status: {project_status} | Fase build: {build_phase} | "
            f"Settore: {project.get('sector','')} | "
            f"SPEC (estratto): {spec_excerpt}"
        )

        # Classifica richiesta con Haiku → Chief domain
        _classify_prompt = (
            f"Sei un sistema di routing per il C-Suite di brAIn.\n"
            f"Contesto cantiere: {name} ({project_status})\n"
            f"Messaggio: \"{msg}\"\n\n"
            f"In quale dominio Chief rientra questa richiesta?\n"
            f"Rispondi SOLO con una di queste parole: cso|coo|cto|cmo|cfo|clo|cpeo"
        )
        _chief_domain_map = {
            "cso": "strategy", "coo": "ops", "cto": "tech",
            "cmo": "marketing", "cfo": "finance", "clo": "legal", "cpeo": "people",
        }
        _chief_name_map = {
            "cso": "CSO", "coo": "COO", "cto": "CTO",
            "cmo": "CMO", "cfo": "CFO", "clo": "CLO", "cpeo": "CPeO",
        }
        try:
            _claude_client = __import__("anthropic").Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
            _cr = _claude_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": _classify_prompt}],
            )
            _chief_id = _cr.content[0].text.strip().lower().split("|")[0].strip()
            if _chief_id not in _chief_domain_map:
                _chief_id = "coo"  # fallback operativo
        except Exception:
            _chief_id = "coo"

        _domain_target = _chief_domain_map.get(_chief_id, "ops")
        _chief_display = _chief_name_map.get(_chief_id, "COO")
        sep = "\u2501" * 15

        # Chiama Chief via agents-runner con project_context + memoria
        _topic_scope = f"{chat_id}:{thread_id}" if thread_id else f"{chat_id}:main"
        _recent_msgs = _topic_buffer_get_recent(chat_id, thread_id or 0)

        def _project_chief_ask():
            _token = os.getenv("TELEGRAM_BOT_TOKEN")
            if not AGENTS_RUNNER_URL or not _token:
                return
            try:
                oidc_token = get_oidc_token(AGENTS_RUNNER_URL)
                headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
                result = http_requests.post(
                    f"{AGENTS_RUNNER_URL}/csuite/ask",
                    json={
                        "domain": _domain_target,
                        "question": msg,
                        "project_context": project_context,
                        "topic_scope_id": _topic_scope,
                        "project_scope_id": str(project_id),
                        "recent_messages": _recent_msgs,
                    },
                    headers=headers,
                    timeout=45,
                )
                if result.status_code == 200:
                    answer = result.json().get("answer", "")
                    chief_name = result.json().get("chief", _chief_display)
                    card = (
                        f"\U0001f464 {chief_name} risponde:\n"
                        f"{sep}\n"
                        f"{answer[:1200]}"
                    )
                    payload = {"chat_id": chat_id, "text": card}
                    if thread_id:
                        payload["message_thread_id"] = thread_id
                    http_requests.post(
                        f"https://api.telegram.org/bot{_token}/sendMessage",
                        json=payload, timeout=15,
                    )
                    _topic_buffer_add(chat_id, thread_id or 0, answer[:200], role="bot")
                    # L3: estrai fatti semantici dal messaggio di Mirco
                    threading.Thread(
                        target=_trigger_extract_facts,
                        args=(msg, _chief_id),
                        daemon=True,
                    ).start()
            except Exception as e:
                logger.warning(f"[PROJECT_CHIEF_ASK] {e}")

        import threading as _thr2
        _thr2.Thread(target=_project_chief_ask, daemon=True).start()
        await update.message.reply_text(
            _make_card("\U0001f464", _chief_display, name, ["Consultando il Chief in corso\u2026"]),
            parse_mode="Markdown",
        )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler per callback query dei pulsanti inline (spec review, team setup, launch)."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    chat_id = update.effective_chat.id
    thread_id = query.message.message_thread_id if query.message else None

    if data.startswith("spec_download:"):
        project_id = int(data.split(":")[1])
        await query.answer("Invio SPEC...")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            try:
                proj = supabase.table("projects").select("spec_md,name").eq("id", project_id).execute()
                if proj.data and proj.data[0].get("spec_md"):
                    spec_md = proj.data[0]["spec_md"]
                    proj_name = proj.data[0].get("name", f"progetto_{project_id}")
                    safe_name = proj_name.replace(" ", "_").replace("/", "_")[:40]
                    tg_data = {"chat_id": chat_id}
                    if thread_id:
                        tg_data["message_thread_id"] = str(thread_id)
                    http_requests.post(
                        f"https://api.telegram.org/bot{token}/sendDocument",
                        data=tg_data,
                        files={"document": (f"SPEC_{safe_name}.md", spec_md.encode("utf-8"), "text/plain")},
                        timeout=30,
                    )
                else:
                    http_requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": "SPEC non ancora disponibile."},
                        timeout=10,
                    )
            except Exception as e:
                logger.error(f"[SPEC_DOWNLOAD] {e}")

    elif data.startswith("spec_validate:"):
        project_id = int(data.split(":")[1])
        await query.answer("SPEC validata!")
        threading.Thread(target=start_team_setup, args=(project_id, chat_id, thread_id), daemon=True).start()

    elif data.startswith("spec_full:"):
        # MACRO-TASK 4: invia SPEC_CODE.md (versione tecnica completa)
        project_id = int(data.split(":")[1])
        await query.answer("Invio SPEC tecnica...")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            try:
                proj = supabase.table("projects").select("spec_md,name").eq("id", project_id).execute()
                if proj.data and proj.data[0].get("spec_md"):
                    spec_md = proj.data[0]["spec_md"]
                    proj_name = proj.data[0].get("name", f"progetto_{project_id}")
                    safe_name = proj_name.replace(" ", "_").replace("/", "_")[:40]
                    tg_data = {"chat_id": chat_id}
                    if thread_id:
                        tg_data["message_thread_id"] = str(thread_id)
                    http_requests.post(
                        f"https://api.telegram.org/bot{token}/sendDocument",
                        data=tg_data,
                        files={"document": (f"SPEC_CODE_{safe_name}.md", spec_md.encode("utf-8"), "text/plain")},
                        timeout=30,
                    )
                else:
                    http_requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": "SPEC non ancora disponibile."},
                        timeout=10,
                    )
            except Exception as e:
                logger.error(f"[SPEC_FULL] {e}")

    elif data.startswith("spec_edit:"):
        project_id = int(data.split(":")[1])
        await query.answer()
        if chat_id not in _session_context:
            _session_context[chat_id] = {}
        _session_context[chat_id]["awaiting_spec_edit"] = project_id
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            payload = {
                "chat_id": chat_id,
                "text": "Cosa vuoi modificare? (es: architettura, kpi, gtm, stack)",
            }
            if thread_id:
                payload["message_thread_id"] = thread_id
            http_requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload, timeout=10,
            )

    elif data.startswith("team_add:"):
        project_id = int(data.split(":")[1])
        await query.answer()
        if chat_id not in _session_context:
            _session_context[chat_id] = {}
        _session_context[chat_id]["awaiting_phone"] = project_id
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            payload = {"chat_id": chat_id, "text": "Inviami il numero di telefono del collaboratore (es. +39...)"}
            if thread_id:
                payload["message_thread_id"] = thread_id
            http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)

    elif data.startswith("team_skip:"):
        project_id = int(data.split(":")[1])
        await query.answer("Avvio build...")
        threading.Thread(
            target=trigger_build_start,
            args=(project_id, chat_id, thread_id),
            daemon=True,
        ).start()

    elif data.startswith("launch_confirm:"):
        project_id = int(data.split(":")[1])
        await query.answer("Lancio!")
        threading.Thread(
            target=handle_launch,
            args=(project_id, chat_id, thread_id),
            daemon=True,
        ).start()

    # ---- LEGAL AGENT callbacks (MACRO-TASK 2) ----
    elif data.startswith("legal_read:"):
        # Mostra report legale
        parts = data.split(":")
        project_id = int(parts[1])
        review_id = int(parts[2]) if len(parts) > 2 else 0
        await query.answer()
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token and review_id:
            try:
                rev = supabase.table("legal_reviews").select("report_md,green_points,yellow_points,red_points").eq("id", review_id).execute()
                if rev.data:
                    r = rev.data[0]
                    report = r.get("report_md") or ""
                    if not report:
                        green = json.loads(r.get("green_points") or "[]")
                        yellow = json.loads(r.get("yellow_points") or "[]")
                        red = json.loads(r.get("red_points") or "[]")
                        lines = ["Review legale:"]
                        for g in green: lines.append(f"🟢 {g}")
                        for y in yellow: lines.append(f"🟡 {y}")
                        for rd in red: lines.append(f"🔴 {rd}")
                        report = "\n".join(lines)
                    payload = {"chat_id": chat_id, "text": report[:4000]}
                    if thread_id:
                        payload["message_thread_id"] = thread_id
                    http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                       json=payload, timeout=15)
            except Exception as e:
                logger.error(f"[LEGAL_READ] {e}")

    elif data.startswith("legal_proceed:"):
        # Procedi con build (post legal OK)
        project_id = int(data.split(":")[1])
        await query.answer("Avvio build...")
        threading.Thread(target=trigger_build_start, args=(project_id, chat_id, thread_id), daemon=True).start()

    elif data.startswith("legal_block:"):
        # Blocca progetto
        project_id = int(data.split(":")[1])
        await query.answer("Progetto bloccato.")
        try:
            supabase.table("projects").update({"status": "legal_blocked"}).eq("id", project_id).execute()
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if token:
                payload = {"chat_id": chat_id, "text": f"⛔ Progetto {project_id} bloccato per problemi legali."}
                if thread_id:
                    payload["message_thread_id"] = thread_id
                http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
        except Exception as e:
            logger.error(f"[LEGAL_BLOCK] {e}")

    # ---- SMOKE TEST callbacks (MACRO-TASK 3) ----
    elif data.startswith("smoke_approve:"):
        parts = data.split(":")
        project_id = int(parts[1])
        smoke_id = int(parts[2]) if len(parts) > 2 else 0
        await query.answer("Outreach avviato!")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            try:
                supabase.table("smoke_tests").update({"messages_sent": 1}).eq("id", smoke_id).execute()
                payload = {"chat_id": chat_id, "text": "✅ Outreach approvato. Torna qui tra 7 giorni per analizzare i risultati."}
                if thread_id:
                    payload["message_thread_id"] = thread_id
                http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
            except Exception as e:
                logger.error(f"[SMOKE_APPROVE] {e}")

    elif data.startswith("smoke_cancel:"):
        parts = data.split(":")
        project_id = int(parts[1])
        smoke_id = int(parts[2]) if len(parts) > 2 else 0
        await query.answer("Smoke test annullato.")
        try:
            supabase.table("smoke_tests").update({"completed_at": "now()"}).eq("id", smoke_id).execute()
            supabase.table("projects").update({"status": "spec_generated"}).eq("id", project_id).execute()
        except Exception as e:
            logger.error(f"[SMOKE_CANCEL] {e}")

    elif data.startswith("smoke_proceed:"):
        project_id = int(data.split(":")[1])
        await query.answer("Avvio build e marketing...")
        threading.Thread(target=trigger_build_start, args=(project_id, chat_id, thread_id), daemon=True).start()
        def _mkt_smoke_proceed():
            _call_agents_runner_sync("/marketing/run", {"project_id": project_id, "phase": "full"})
        threading.Thread(target=_mkt_smoke_proceed, daemon=True).start()

    elif data.startswith("smoke_spec_insights:"):
        parts = data.split(":")
        project_id = int(parts[1])
        smoke_id = int(parts[2]) if len(parts) > 2 else 0
        await query.answer()
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            try:
                st = supabase.table("smoke_tests").select("spec_insights,conversion_rate,recommendation").eq("id", smoke_id).execute()
                if st.data:
                    insights = json.loads(st.data[0].get("spec_insights") or "{}")
                    conv = st.data[0].get("conversion_rate", 0)
                    rec = st.data[0].get("recommendation", "N/A")
                    lines = [f"📊 Insights Smoke Test — progetto {project_id}",
                             f"Conversione: {conv:.1f}% | Rec: {rec}"]
                    for ins in insights.get("key_insights", [])[:5]:
                        lines.append(f"• {ins}")
                    if insights.get("spec_updates"):
                        lines.append("\nModifiche SPEC suggerite:")
                        for upd in insights["spec_updates"][:3]:
                            lines.append(f"→ {upd}")
                    payload = {"chat_id": chat_id, "text": "\n".join(lines)[:4000]}
                    if thread_id:
                        payload["message_thread_id"] = thread_id
                    http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=15)
            except Exception as e:
                logger.error(f"[SMOKE_INSIGHTS] {e}")

    elif data.startswith("smoke_modify_spec:"):
        project_id = int(data.split(":")[1])
        await query.answer()
        if chat_id not in _session_context:
            _session_context[chat_id] = {}
        _session_context[chat_id]["awaiting_spec_edit"] = project_id
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            payload = {"chat_id": chat_id, "text": "Cosa vuoi modificare nella SPEC? (usa i suggerimenti o descrivi)"}
            if thread_id:
                payload["message_thread_id"] = thread_id
            http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)

    # Build continue/modify (Fix 2)
    elif data.startswith("build_continue:"):
        parts = data.split(":")
        project_id = int(parts[1])
        current_phase = int(parts[2]) if len(parts) > 2 else 1
        await query.answer("Avvio fase successiva...")

        def _continue_build_auto():
            try:
                oidc_token = get_oidc_token(AGENTS_RUNNER_URL) if AGENTS_RUNNER_URL else None
                headers = {"Authorization": f"Bearer {oidc_token}"} if oidc_token else {}
                http_requests.post(
                    f"{AGENTS_RUNNER_URL}/project/continue_build",
                    json={"project_id": project_id, "feedback": "ok, procedi alla fase successiva", "phase": current_phase},
                    headers=headers, timeout=10,
                )
            except Exception as e:
                logger.warning(f"[BUILD_CONTINUE_CB] {e}")

        if AGENTS_RUNNER_URL:
            threading.Thread(target=_continue_build_auto, daemon=True).start()

    elif data.startswith("build_modify:"):
        await query.answer("Scrivi il feedback nel topic del progetto.")

    # ---- REPORT ON-DEMAND callbacks ----
    elif data == "report_cost_ondemand":
        await query.answer("Generazione report costi...")
        _token_rc = os.getenv("TELEGRAM_BOT_TOKEN")
        _cid_rc = chat_id
        def _gen_cost_cb():
            result = _call_agents_runner_sync("/report/cost")
            if result and result.get("text") and _token_rc and _cid_rc:
                http_requests.post(
                    f"https://api.telegram.org/bot{_token_rc}/sendMessage",
                    json={"chat_id": _cid_rc, "text": result["text"], "parse_mode": "Markdown"},
                    timeout=15,
                )
        threading.Thread(target=_gen_cost_cb, daemon=True).start()

    elif data == "report_activ_ondemand":
        await query.answer("Generazione report attività...")
        _token_ra = os.getenv("TELEGRAM_BOT_TOKEN")
        _cid_ra = chat_id
        def _gen_activ_cb():
            result = _call_agents_runner_sync("/report/activity")
            if result and result.get("text") and _token_ra and _cid_ra:
                http_requests.post(
                    f"https://api.telegram.org/bot{_token_ra}/sendMessage",
                    json={"chat_id": _cid_ra, "text": result["text"], "parse_mode": "Markdown"},
                    timeout=15,
                )
        threading.Thread(target=_gen_activ_cb, daemon=True).start()

    # ---- MARKETING callbacks ----
    elif data.startswith("mkt_report:"):
        project_id = int(data.split(":")[1]) if data.split(":")[1] else None
        await query.answer("Generazione report marketing...")
        def _mkt_report_cb():
            _call_agents_runner_sync("/marketing/report", {"project_id": project_id})
        threading.Thread(target=_mkt_report_cb, daemon=True).start()

    elif data.startswith("mkt_brand_kit:"):
        project_id = int(data.split(":")[1]) if data.split(":")[1] else None
        await query.answer("Brand Kit in arrivo...")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token and project_id:
            try:
                r = supabase.table("brand_assets").select("brand_name,tagline,brand_dna_md").eq("project_id", project_id).execute()
                if r.data:
                    a = r.data[0]
                    lines = [f"Nome: {a.get('brand_name','N/A')}", f"Tagline: {a.get('tagline','N/A')}", "",
                             "Brand DNA:", (a.get("brand_dna_md") or "")[:800]]
                    text = "\n".join(lines)[:4000]
                    http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                       json={"chat_id": chat_id, "text": text}, timeout=15)
            except Exception as e:
                logger.warning(f"[MKT_BRAND_KIT] {e}")

    elif data.startswith("mkt_next:"):
        project_id = int(data.split(":")[1]) if data.split(":")[1] else None
        await query.answer("Avvio fase successiva marketing...")
        def _mkt_next_cb():
            _call_agents_runner_sync("/marketing/run", {"project_id": project_id, "phase": "gtm"})
        threading.Thread(target=_mkt_next_cb, daemon=True).start()

    elif data.startswith("mkt_report_detail:"):
        project_id = int(data.split(":")[1]) if data.split(":")[1] else None
        await query.answer("Dettaglio report...")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token and project_id:
            try:
                r = supabase.table("marketing_reports").select("*").eq("project_id", project_id).order("recorded_at", desc=True).limit(1).execute()
                if r.data:
                    rep = r.data[0]
                    text = (f"📊 Report marketing — progetto {project_id}\n"
                            f"Settimana: {rep.get('week_start','N/A')}\n"
                            f"Visite: {rep.get('landing_visits',0)}\n"
                            f"CAC: €{rep.get('cac_eur','N/A')}\n"
                            f"Open rate: {rep.get('email_open_rate','N/A')}%\n"
                            f"Conversione: {rep.get('conversion_rate','N/A')}%")
                    http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                       json={"chat_id": chat_id, "text": text}, timeout=15)
            except Exception as e:
                logger.warning(f"[MKT_REPORT_DETAIL] {e}")

    elif data.startswith("mkt_report_trend:"):
        await query.answer("Trend in sviluppo — dati insufficienti.")

    elif data.startswith("mkt_report_optimize:"):
        project_id = int(data.split(":")[1]) if data.split(":")[1] else None
        await query.answer("Avvio ottimizzazione marketing...")
        def _mkt_opt_cb():
            _call_agents_runner_sync("/marketing/run", {"project_id": project_id, "phase": "retention"})
        threading.Thread(target=_mkt_opt_cb, daemon=True).start()

    # BOS approval inline (Fix 3)
    elif data.startswith("bos_approve:") or data.startswith("bos_reject:"):
        parts = data.split(":")
        approved = data.startswith("bos_approve:")
        action_db_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        await query.answer("Approvato!" if approved else "Rifiutato!")

        try:
            action = None
            if action_db_id:
                ar = supabase.table(ACTION_QUEUE_TABLE).select("*").eq("id", action_db_id).execute()
                action = ar.data[0] if ar.data else None
            if not action:
                action = get_next_action(chat_id)
            if action:
                payload = action.get("payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except:
                        payload = {}
                complete_action(action["id"], "completed" if approved else "rejected")
                current = _current_action.get(chat_id)
                if current and current.get("id") == action["id"]:
                    del _current_action[chat_id]
                result_text = _execute_action("approve_bos", payload, approved)
                token = os.getenv("TELEGRAM_BOT_TOKEN")
                if token and result_text:
                    http_requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": result_text},
                        timeout=10,
                    )
        except Exception as e:
            logger.error(f"[BOS_CALLBACK] {e}")

    elif data.startswith("bos_detail:"):
        action_db_id = int(data.split(":")[1])
        await query.answer()
        try:
            ar = supabase.table(ACTION_QUEUE_TABLE).select("*").eq("id", action_db_id).execute()
            action = ar.data[0] if ar.data else None
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if action and token:
                detail = action.get("description", "Nessun dettaglio.")
                http_requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": detail[:4000]},
                    timeout=10,
                )
        except Exception as e:
            logger.error(f"[BOS_DETAIL] {e}")

    # Validation callbacks SCALE/PIVOT/KILL (Fix 3)
    elif data.startswith("val_proceed:"):
        project_id = int(data.split(":")[1])
        await query.answer("Procedendo...")
        try:
            proj = supabase.table("projects").select("status,name").eq("id", project_id).execute()
            if proj.data:
                current_status = proj.data[0].get("status", "")
                new_status = "scaling" if current_status != "killed" else "killed"
                supabase.table("projects").update({"status": new_status}).eq("id", project_id).execute()
                name = proj.data[0].get("name", f"Progetto {project_id}")
                token = os.getenv("TELEGRAM_BOT_TOKEN")
                if token:
                    http_requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": f"✅ {name}: status aggiornato a '{new_status}'."},
                        timeout=10,
                    )
        except Exception as e:
            logger.error(f"[VAL_PROCEED] {e}")

    elif data.startswith("val_wait:"):
        await query.answer("Ok, continuo a monitorare.")

    elif data.startswith("val_discuss:"):
        project_id = int(data.split(":")[1])
        await query.answer("Apri il topic del progetto per discutere.")

    # Source callbacks (Fix 3)
    elif data.startswith("source_reactivate:"):
        source_id = int(data.split(":")[1])
        await query.answer("Riattivazione in corso...")
        try:
            supabase.table("scan_sources").update({"status": "active", "notes": "Riattivata manualmente da Mirco"}).eq("id", source_id).execute()
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if token:
                http_requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": f"✅ Fonte {source_id} riattivata."},
                    timeout=10,
                )
        except Exception as e:
            logger.error(f"[SOURCE_REACTIVATE] {e}")

    elif data == "source_archive_ok":
        await query.answer("Ok!")

    # Report costi — callbacks
    elif data == "cost_detail_4h":
        await query.answer()
        try:
            from datetime import timedelta
            since_4h = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
            logs = supabase.table("agent_logs").select("agent_id,cost_usd,action").gte("created_at", since_4h).execute().data or []
            by_agent = {}
            for l in logs:
                a = l.get("agent_id", "unknown")
                by_agent[a] = by_agent.get(a, 0.0) + float(l.get("cost_usd", 0) or 0)
            sep = "\u2501" * 15
            lines = ["\U0001f50d *Dettaglio costi ultime 4h*", sep]
            if by_agent:
                for a, c in sorted(by_agent.items(), key=lambda x: x[1], reverse=True):
                    lines.append(f"\u2022 {a}: \u20ac{c * 0.92:.4f}")
            else:
                lines.append("Nessun costo registrato.")
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if token:
                http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            logger.error(f"[COST_DETAIL_4H] {e}")

    elif data == "cost_trend_7d":
        await query.answer()
        try:
            from datetime import timedelta
            lines = ["\U0001f4ca *Trend costi 7 giorni*", "\u2501" * 15]
            now_utc = datetime.now(timezone.utc)
            for d in range(6, -1, -1):
                day = now_utc - timedelta(days=d)
                day_str = day.strftime("%Y-%m-%d")
                day_start = f"{day_str}T00:00:00+00:00"
                day_end = f"{day_str}T23:59:59+00:00"
                logs = supabase.table("agent_logs").select("cost_usd").gte("created_at", day_start).lte("created_at", day_end).execute().data or []
                cost_eur = sum(float(l.get("cost_usd", 0) or 0) for l in logs) * 0.92
                bar_len = min(10, round(cost_eur * 20))
                bar = "\u2588" * bar_len + "\u2591" * (10 - bar_len)
                label = day.strftime("%d/%m")
                lines.append(f"{label} {bar} \u20ac{cost_eur:.2f}")
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if token:
                http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            logger.error(f"[COST_TREND_7D] {e}")

    # Report attività — callbacks
    elif data == "act_problemi":
        await query.answer()
        try:
            prob_res = supabase.table("problems").select("id,title,weighted_score,sector").eq(
                "status", "new").order("weighted_score", desc=True).limit(5).execute()
            probs = prob_res.data or []
            sep = "\u2501" * 15
            if probs:
                lines = [f"\U0001f4cb *Problemi in attesa ({len(probs)})*", sep]
                for p in probs:
                    score = float(p.get("weighted_score") or 0)
                    sector = (p.get("sector") or "?").split("/")[0][:10]
                    lines.append(f"\u2022 [{score:.2f}] {sector} \u2014 {p['title'][:45]}")
            else:
                lines = ["\U0001f4cb Nessun problema in attesa."]
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if token:
                http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            logger.error(f"[ACT_PROBLEMI] {e}")

    elif data == "act_top_bos":
        await query.answer()
        try:
            bos_res = supabase.table("solutions").select("id,title,bos_score,bos_details").not_.is_(
                "bos_score", "null").order("bos_score", desc=True).limit(3).execute()
            bos_list = bos_res.data or []
            sep = "\u2501" * 15
            lines = ["\U0001f3c6 *Top 3 BOS*", sep]
            for s in bos_list:
                bos = float(s.get("bos_score", 0) or 0)
                details = s.get("bos_details", {})
                if isinstance(details, str):
                    try:
                        import json as _json; details = _json.loads(details)
                    except Exception:
                        details = {}
                verdict = details.get("verdict", "?") if isinstance(details, dict) else "?"
                lines.append(f"\u2022 {bos:.2f} \u2014 {verdict} \u2014 {s.get('title','?')[:40]}")
            if not bos_list:
                lines.append("Nessun BOS disponibile.")
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if token:
                http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            logger.error(f"[ACT_TOP_BOS] {e}")

    elif data == "act_cantieri":
        await query.answer()
        try:
            cantieri = supabase.table("projects").select("id,name,status,created_at,build_phase").neq(
                "status", "archived").execute().data or []
            sep = "\u2501" * 15
            if cantieri:
                lines = [f"\U0001f3d7\ufe0f *Cantieri attivi ({len(cantieri)})*", sep]
                for c in cantieri:
                    created = c.get("created_at", "")[:16].replace("T", " ")
                    phase = c.get("build_phase") or 0
                    lines.append(f"\u2022 {c['name'][:30]}")
                    lines.append(f"  Status: {c.get('status','?')} | Fase: {phase} | Creato: {created}")
            else:
                lines = ["\U0001f3d7\ufe0f Nessun cantiere attivo."]
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            if token:
                http_requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            logger.error(f"[ACT_CANTIERI] {e}")

    # Callback legacy (spec_build) mantenuto per compatibilità
    elif data.startswith("spec_build:"):
        project_id = int(data.split(":")[1])
        await query.answer("Avvio build...")
        threading.Thread(
            target=build_approved_action,
            args=(project_id, chat_id, thread_id),
            daemon=True,
        ).start()

    else:
        await query.answer()


def _send_telegram_sync(chat_id, text):
    """Invia messaggio Telegram (sync, per thread background)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"[TG SYNC] {e}")


def _run_code_agent_sync(chat_id, prompt):
    """Genera codice con Claude Sonnet e committa su GitHub."""
    try:
        # 1. Struttura repo
        tree = github_api("GET", "/git/trees/main?recursive=1")
        if not tree:
            _send_telegram_sync(chat_id, "Errore: non riesco a leggere il repo GitHub.")
            return

        py_files = [f["path"] for f in tree.get("tree", [])
                     if f["type"] == "blob" and f["path"].endswith(".py")]
        file_list = "\n".join(py_files)

        # 2. Leggi file rilevanti per contesto (max 5, max 3000 char ciascuno)
        context_files = []
        key_dirs = ["agents/", "deploy/", "deploy-agents/"]
        for fp in py_files:
            if any(fp.startswith(d) for d in key_dirs) and len(context_files) < 5:
                content, _ = github_get_file(fp)
                if content:
                    context_files.append(f"--- {fp} ---\n{content[:3000]}")

        context_text = "\n\n".join(context_files)

        # 3. Chiama Claude Sonnet
        start = time.time()
        response = claude.messages.create(
            model=CODE_AGENT_MODEL,
            max_tokens=8192,
            system=CODE_AGENT_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"FILE NEL REPO:\n{file_list}\n\n"
                    f"CODICE ESISTENTE (estratti):\n{context_text}\n\n"
                    f"RICHIESTA DI MIRCO: {prompt}\n\n"
                    f"Scrivi il codice. SOLO JSON."
                ),
            }],
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        cost = (response.usage.input_tokens * 3.0 + response.usage.output_tokens * 15.0) / 1_000_000
        log_to_supabase("code_agent", "generate_code", prompt[:300], reply[:300],
            CODE_AGENT_MODEL, response.usage.input_tokens, response.usage.output_tokens,
            cost, duration)

        # 4. Parsing risposta
        data = extract_json_from_text(reply)
        if not data or not data.get("files"):
            _send_telegram_sync(chat_id, f"Code Agent non ha generato codice valido.\n\nRisposta: {reply[:500]}")
            return

        # 5. Commit su GitHub
        files = data["files"]
        summary = data.get("summary", "Code Agent: modifiche automatiche")

        success, result = github_commit_files(files, f"[Code Agent] {summary}")

        if success:
            file_names = ", ".join([f["path"] for f in files])
            pending_deploys[chat_id] = {
                "files": [f["path"] for f in files],
                "summary": summary,
                "timestamp": time.time(),
            }
            _send_telegram_sync(chat_id,
                f"Codice scritto e committato su GitHub.\n\n"
                f"File: {file_names}\n"
                f"Cosa: {summary}\n\n"
                f"Vuoi che buildo e deployo?")
        else:
            _send_telegram_sync(chat_id, f"Errore nel commit: {result}")

    except Exception as e:
        logger.error(f"[CODE AGENT] {e}")
        _send_telegram_sync(chat_id, f"Errore Code Agent: {e}")


def extract_json_from_text(text):
    """Estrai JSON da testo (anche con markdown)."""
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except:
        pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except:
                    return None
    return None


def _get_cloud_access_token():
    """Ottieni access token dal metadata server GCE."""
    try:
        r = http_requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()["access_token"]
    except:
        pass
    return None


def _determine_services_to_deploy(files):
    """Determina quali servizi Cloud Run devono essere deployati in base ai file modificati."""
    services = []
    for f in files:
        if f.startswith("deploy-agents/") or f.startswith("agents/"):
            if "agents-runner" not in services:
                services.append("agents-runner")
        if f.startswith("deploy/") and "command_center" in f:
            if "command-center" not in services:
                services.append("command-center")
    return services


def _trigger_build_deploy_sync(chat_id, deploy_info):
    """Triggera build e deploy via Cloud Build API."""
    files = deploy_info.get("files", [])
    services = _determine_services_to_deploy(files)

    if not services:
        _send_telegram_sync(chat_id, "Nessun servizio da deployare per i file modificati.")
        return

    token = _get_cloud_access_token()
    if not token:
        _send_telegram_sync(chat_id,
            f"Non ho accesso al Cloud Build. Servizi da deployare: {', '.join(services)}\n"
            f"Deploya manualmente.")
        return

    project_id = "brain-core-487914"
    region = "europe-west3"

    for service in services:
        if service == "agents-runner":
            dockerfile_dir = "deploy-agents"
        elif service == "command-center":
            dockerfile_dir = "deploy"
        else:
            continue

        image = f"{region}-docker.pkg.dev/{project_id}/brain-repo/{service}:latest"

        build_config = {
            "source": {
                "repoSource": {
                    "projectId": project_id,
                    "repoName": "github_mircocerisola_brain-core",
                    "branchName": "main",
                }
            },
            "steps": [
                {
                    "name": "gcr.io/cloud-builders/docker",
                    "args": ["build", "-t", image, "."],
                    "dir": dockerfile_dir,
                },
                {
                    "name": "gcr.io/cloud-builders/docker",
                    "args": ["push", image],
                },
                {
                    "name": "gcr.io/google.com/cloudsdktool/cloud-sdk",
                    "entrypoint": "gcloud",
                    "args": [
                        "run", "deploy", service,
                        "--image", image,
                        "--region", region,
                        "--platform", "managed",
                        "--quiet",
                    ],
                },
            ],
        }

        try:
            r = http_requests.post(
                f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/builds",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=build_config,
                timeout=30,
            )
            if r.status_code in (200, 201):
                build_data = r.json()
                build_id = build_data.get("metadata", {}).get("build", {}).get("id", "?")
                _send_telegram_sync(chat_id, f"Build avviata per {service} (ID: {build_id[:8]})")
            else:
                _send_telegram_sync(chat_id,
                    f"Errore build {service}: HTTP {r.status_code}\n"
                    f"Controlla i log di Cloud Build.")
        except Exception as e:
            _send_telegram_sync(chat_id, f"Errore build {service}: {e}")


# ---- TELEGRAM HANDLERS ----

tg_app = None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTHORIZED_USER_ID
    AUTHORIZED_USER_ID = update.effective_user.id
    try:
        supabase.table("org_config").upsert(
            {"key": "telegram_user_id", "value": json.dumps(AUTHORIZED_USER_ID),
             "description": "ID Telegram di Mirco"},
            on_conflict="key",
        ).execute()
    except:
        pass
    await update.message.reply_text(
        _make_card(
            "\U0001f9e0", "COMMAND CENTER", "v3.0 attivo",
            [
                "Sonnet 4.5 — intelligente, contestuale, COO-level.",
                "Vocali, foto, report, /code — scrivimi quello che vuoi.",
                "report costi | report attivit\u00e0 | /code <istruzioni>",
            ]
        ),
        parse_mode="Markdown",
    )
    log_to_supabase("command_center", "start", f"uid={AUTHORIZED_USER_ID}", "v3.0 sonnet", "none")


async def handle_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler per /code <istruzioni> — Code Agent via Claude Sonnet + GitHub."""
    if not is_authorized(update):
        return
    prompt = update.message.text.replace("/code", "", 1).strip()
    if not prompt:
        await update.message.reply_text(
            _make_card("\U0001f4bb", "CODE AGENT", "uso",
                       ["Scrivi /code seguito dalle istruzioni.",
                        "Esempio: /code aggiungi endpoint /health"]),
            parse_mode="Markdown",
        )
        return
    if not GITHUB_TOKEN:
        await update.message.reply_text(
            _make_card("\u274c", "CODE AGENT", "errore", ["GITHUB\\_TOKEN non configurato."]),
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        _make_card("\U0001f4bb", "CODE AGENT", "avviato",
                   [f"Lavoro con Sonnet: {prompt[:80]}\u2026"]),
        parse_mode="Markdown",
    )
    chat_id = update.effective_chat.id
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_code_agent_sync, chat_id, prompt)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _last_mirco_message_time
    if not is_authorized(update):
        return
    msg = update.message.text
    chat_id = update.effective_chat.id

    # Traccia ultimo messaggio per notifiche intelligenti
    _last_mirco_message_time = time.time()

    # ---- FORUM TOPIC ROUTING ----
    thread_id = update.message.message_thread_id if update.message else None
    if thread_id:
        project = lookup_project_by_topic_id(thread_id)
        if project:
            await handle_project_message(update, project)
            return

    if msg.strip().upper() == "STOP":
        await update.message.reply_text(
            _make_card("\U0001f6d1", "STOP", "sistema brAIn", ["Tutto fermo. Nessuna operazione attiva."]),
            parse_mode="Markdown",
        )
        return

    lower_msg = msg.strip().lower()

    # ---- C-SUITE ROUTING ----
    _CSUITE_KEYWORDS = {
        "cso": "strategy", "strategia": "strategy", "pivot": "strategy",
        "cfo": "finance", "costi": "finance", "budget": "finance", "burn rate": "finance",
        "cmo": "marketing", "growth": "marketing", "conversione": "marketing",
        "cto": "tech", "architettura": "tech", "deploy": "tech",
        "coo": "ops", "operazioni": "ops", "pipeline": "ops",
        "cpo": "ops", "roadmap": "ops", "ux": "ops",
        "clo": "legal", "legale": "legal", "compliance": "legal", "gdpr": "legal",
        "cpeo": "people", "revenue share": "people",
    }
    _csuite_match = None
    for kw, dom in _CSUITE_KEYWORDS.items():
        if kw in lower_msg:
            _csuite_match = dom
            break

    if _csuite_match and len(lower_msg) > 5:
        # Routing a C-Suite: chiedi al Chief via agents-runner con memoria
        _topic_buffer_add(chat_id, 0, msg, role="user")
        await update.message.reply_text(
            _make_card("🧠", "C-SUITE", f"Chief {_csuite_match.upper()}", ["Consultando il Chief in corso…"]),
            parse_mode="Markdown",
        )
        _domain_cs = _csuite_match
        _question_cs = msg
        _chat_cs = chat_id
        _token_cs = os.getenv("TELEGRAM_BOT_TOKEN")
        _scope_cs = f"{chat_id}:main"
        _recent_cs = _topic_buffer_get_recent(chat_id, 0)
        # Mappa domain → chief_id per extract_facts
        _domain_to_chief = {
            "strategy": "cso", "finance": "cfo", "marketing": "cmo",
            "tech": "cto", "ops": "coo", "legal": "clo", "people": "cpeo",
        }
        _chief_id_cs = _domain_to_chief.get(_domain_cs, "coo")
        def _csuite_ask():
            result = _call_agents_runner_sync("/csuite/ask", {
                "domain": _domain_cs,
                "question": _question_cs,
                "topic_scope_id": _scope_cs,
                "recent_messages": _recent_cs,
            })
            if result and result.get("answer") and _token_cs and _chat_cs:
                answer_text = f"🧠 *{result.get('chief', 'Chief')}*\n\n{result['answer']}"
                http_requests.post(
                    f"https://api.telegram.org/bot{_token_cs}/sendMessage",
                    json={"chat_id": _chat_cs, "text": answer_text[:4000], "parse_mode": "Markdown"},
                    timeout=30,
                )
                _topic_buffer_add(_chat_cs, 0, result["answer"][:200], role="bot")
            # L3: estrai fatti semantici dal messaggio
            _trigger_extract_facts(_question_cs, _chief_id_cs)
        threading.Thread(target=_csuite_ask, daemon=True).start()
        return

    # ---- MARKETING ROUTING ----
    _BRAND_TRIGGERS = {"crea brand", "brand identity", "brand brAIn", "crea brand identity brain", "crea brand identity brAIn"}

    if lower_msg in _BRAND_TRIGGERS or lower_msg.startswith("crea brand identity"):
        await update.message.reply_text(
            _make_card("\U0001f3a8", "BRAND IDENTITY", "avvio in corso", ["Generazione brand DNA, naming, logo\u2026", "Ti notificher\u00f2 al completamento."]),
            parse_mode="Markdown",
        )
        def _mkt_brand_brain():
            _call_agents_runner_sync("/marketing/brand", {"target": "brain"})
        threading.Thread(target=_mkt_brand_brain, daemon=True).start()
        return

    if lower_msg.startswith("marketing ") or lower_msg == "marketing":
        project_name = lower_msg.replace("marketing", "").strip() or None
        project_id = None
        if project_name:
            try:
                r = supabase.table("projects").select("id").ilike("name", f"%{project_name}%").limit(1).execute()
                if r.data:
                    project_id = r.data[0]["id"]
            except Exception:
                pass
        if project_id:
            await update.message.reply_text(
                _make_card("\U0001f680", "MARKETING AVVIATO", f"progetto id={project_id}", [
                    "Fase: full (brand \u2192 GTM \u2192 retention)",
                    "Ti notificher\u00f2 al completamento.",
                ]),
                parse_mode="Markdown",
            )
            def _mkt_run():
                _call_agents_runner_sync("/marketing/run", {"project_id": project_id, "phase": "full"})
            threading.Thread(target=_mkt_run, daemon=True).start()
        else:
            await update.message.reply_text(
                _make_card("\u2139\ufe0f", "MARKETING", "progetto non trovato", [
                    "Specifica nome progetto valido: marketing NomeProgetto",
                ]),
                parse_mode="Markdown",
            )
        return

    # ---- FIX 2/3: ROUTING REPORT ON-DEMAND ----
    _REPORT_COST_TRIGGERS = {"report costi", "costi", "report cost", "cost report", "quanto stiamo spendendo", "costo"}
    _REPORT_ACTIV_TRIGGERS = {"report attività", "attività", "report attivita", "attivita", "status", "stato sistema", "report activity", "activity"}
    _REPORT_GENERIC_TRIGGERS = {"report", "report brAIn", "report brain", "dashboard"}

    if lower_msg in _REPORT_COST_TRIGGERS:
        await update.message.reply_text(
            _make_card("\U0001f4b6", "REPORT COSTI", "in arrivo...", ["Generazione in corso\u2026"]),
            parse_mode="Markdown",
        )
        def _gen_cost():
            _call_agents_runner_sync("/report/cost")
        threading.Thread(target=_gen_cost, daemon=True).start()
        return

    if lower_msg in _REPORT_ACTIV_TRIGGERS:
        await update.message.reply_text(
            _make_card("\u2699\ufe0f", "REPORT ATTIVIT\u00c0", "in arrivo...", ["Generazione in corso\u2026"]),
            parse_mode="Markdown",
        )
        def _gen_activ():
            _call_agents_runner_sync("/report/activity")
        threading.Thread(target=_gen_activ, daemon=True).start()
        return

    if lower_msg in _REPORT_GENERIC_TRIGGERS:
        sep = _SEP
        card_text = (
            f"\U0001f4ca *REPORT DISPONIBILI*\n{sep}\n"
            f"Scegli quale report vuoi vedere:\n{sep}"
        )
        reply_markup = {
            "inline_keyboard": [[
                {"text": "\U0001f4b6 Costi", "callback_data": "report_cost_ondemand"},
                {"text": "\u2699\ufe0f Attivit\u00e0", "callback_data": "report_activ_ondemand"},
            ]]
        }
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            http_requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": card_text, "reply_markup": reply_markup, "parse_mode": "Markdown"},
                timeout=10,
            )
        return

    # ---- COMANDI CODA AZIONI ----
    if lower_msg in ("quante azioni", "quante azioni ho", "quante azioni ho in coda", "azioni in coda", "coda"):
        n = count_pending_actions(chat_id)
        if n == 0:
            await update.message.reply_text(
                _make_card("\U0001f4ec", "CODA AZIONI", "brAIn", ["Nessuna azione in coda."]),
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                _make_card("\U0001f4ec", "CODA AZIONI", "brAIn", [f"{n} azioni in attesa di risposta."]),
                parse_mode="Markdown",
            )
            if chat_id not in _current_action:
                send_next_action(chat_id)
        return

    if lower_msg in ("vedi tutte le azioni", "lista azioni", "tutte le azioni", "mostra azioni"):
        actions = list_pending_actions(chat_id)
        if not actions:
            await update.message.reply_text(
                _make_card("\U0001f4ec", "CODA AZIONI", "brAIn", ["Nessuna azione in coda."]),
                parse_mode="Markdown",
            )
        else:
            body = [f"{i}. {a['title']} (score: {(a.get('priority_score') or 0):.1f})" for i, a in enumerate(actions, 1)]
            await update.message.reply_text(
                _make_card("\U0001f4ec", f"AZIONI IN CODA", f"{len(actions)} totali", body),
                parse_mode="Markdown",
            )
        return

    if lower_msg in ("salta", "salta questa", "salta questa azione", "skip"):
        if chat_id in _current_action:
            action = _current_action.pop(chat_id)
            complete_action(action["id"], "skipped")
            remaining = count_pending_actions(chat_id)
            if remaining > 0:
                await update.message.reply_text(
                    _make_card("\u23ed\ufe0f", "AZIONE SALTATA", "brAIn", [f"Rimanenti: {remaining}"]),
                    parse_mode="Markdown",
                )
                send_next_action(chat_id)
            else:
                await update.message.reply_text(
                    _make_card("\u23ed\ufe0f", "AZIONE SALTATA", "brAIn", ["Nessuna altra azione in coda."]),
                    parse_mode="Markdown",
                )
        else:
            await update.message.reply_text(
                _make_card("\u2139\ufe0f", "NESSUNA AZIONE", "brAIn", ["Nessuna azione attiva da saltare."]),
                parse_mode="Markdown",
            )
        return

    if lower_msg in ("prossima azione", "prossima", "next"):
        if not send_next_action(chat_id):
            await update.message.reply_text(
                _make_card("\U0001f4ec", "CODA AZIONI", "brAIn", ["Nessuna azione in coda."]),
                parse_mode="Markdown",
            )
        return

    # ---- RISPOSTA A AZIONE IN CORSO ----
    if chat_id in _current_action:
        handled, reply_text = handle_action_response(chat_id, msg)
        if handled:
            if reply_text:
                await update.message.reply_text(clean_reply(reply_text))
            return

    # Check pending deploy
    if chat_id in pending_deploys:
        if lower_msg in ("si", "sì", "ok", "vai", "yes", "deploy", "builda", "deploya"):
            deploy_info = pending_deploys.pop(chat_id)
            await update.message.reply_text(
                _make_card("\U0001f680", "DEPLOY AVVIATO", "brAIn", ["Build e deploy in corso\u2026"]),
                parse_mode="Markdown",
            )
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _trigger_build_deploy_sync, chat_id, deploy_info)
            return
        elif lower_msg in ("no", "annulla", "stop", "cancel"):
            pending_deploys.pop(chat_id)
            await update.message.reply_text(
                _make_card("\u274c", "DEPLOY ANNULLATO", "brAIn", ["Il codice resta su GitHub."]),
                parse_mode="Markdown",
            )
            return

    await update.message.chat.send_action("typing")
    reply = clean_reply(ask_claude(msg, chat_id=chat_id))
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i + 4000])


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.chat.send_action("typing")
    chat_id = update.effective_chat.id
    photo = update.message.photo[-1]
    f = await context.bot.get_file(photo.file_id)
    img = await f.download_as_bytearray()
    b64 = base64.b64encode(bytes(img)).decode("utf-8")
    caption = update.message.caption or "Analizza questa immagine e dimmi cosa vedi in ottica brAIn."
    reply = clean_reply(ask_claude(caption, chat_id=chat_id, is_photo=True, image_b64=b64))
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i + 4000])


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.chat.send_action("typing")
    chat_id = update.effective_chat.id
    try:
        f = await context.bot.get_file(update.message.voice.file_id)
        audio = await f.download_as_bytearray()
        text = transcribe_voice(bytes(audio))
        if not text:
            await update.message.reply_text("Non ho capito il vocale. Ripeti o scrivi?")
            return
        reply = clean_reply(ask_claude(text, chat_id=chat_id))
        for i in range(0, len(reply), 4000):
            await update.message.reply_text(reply[i:i + 4000])
    except Exception as e:
        await update.message.reply_text(f"Errore vocale: {e}")


async def handle_command_as_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    text = update.message.text.lower().strip()
    remap = {
        "/problems": "mostrami i problemi nuovi",
        "/solutions": "mostrami le soluzioni",
        "/status": "come sta il sistema?",
        "/costs": "quanto stiamo spendendo?",
        "/help": "cosa sai fare? Includi che il comando /code permette di scrivere codice nel repo.",
    }
    user_message = remap.get(text, text.replace("/", ""))
    await update.message.chat.send_action("typing")
    reply = clean_reply(ask_claude(user_message, chat_id=chat_id))
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i + 4000])


def is_authorized(update):
    global AUTHORIZED_USER_ID
    if AUTHORIZED_USER_ID is None:
        try:
            r = supabase.table("org_config").select("value").eq("key", "telegram_user_id").execute()
            if r.data:
                AUTHORIZED_USER_ID = json.loads(r.data[0]["value"])
        except:
            pass
    if AUTHORIZED_USER_ID is None:
        return True
    return update.effective_user.id == AUTHORIZED_USER_ID


# ---- HTTP ENDPOINTS ----

async def health_check(request):
    return web.Response(text="brAIn Command Center v3.0 OK", status=200)


async def telegram_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logger.error(f"[WEBHOOK] {e}")
    return web.Response(text="OK", status=200)


async def handle_alert(request):
    """Riceve alert da altri agenti. Notifiche intelligenti: CRITICAL subito, altri in coda."""
    try:
        data = await request.json()
        message = data.get("message", "")
        level = data.get("level", "info")
        source = data.get("source", "unknown")

        if not message:
            return web.Response(text="Missing message", status=400)

        # FIX 4: formato card per alert
        level_emoji = {"critical": "\U0001f6a8", "warning": "\u26a0\ufe0f", "info": "\u2139\ufe0f"}.get(level, "\U0001f514")
        level_title = {"critical": "ALERT CRITICO", "warning": "ATTENZIONE", "info": "INFO"}.get(level, "NOTIFICA")
        body_lines = [line for line in message.split("\n") if line.strip()]
        text = _make_card(level_emoji, level_title, source, body_lines)

        is_critical = (level == "critical")
        queue_or_send_notification(text, is_critical=is_critical)

        log_to_supabase(
            "command_center", "alert_forwarded",
            f"{source}: {message[:200]}", f"{'immediate' if is_critical else 'queued'}", "none",
        )
        return web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"[ALERT] {e}")
        return web.Response(text=str(e), status=500)


async def handle_enqueue_action(request):
    """Endpoint per agenti: inserisce un'azione nella coda di Mirco."""
    try:
        data = await request.json()
        action_type = data.get("action_type")
        title = data.get("title")
        description = data.get("description", "")
        payload = data.get("payload")
        priority = data.get("priority", 5)
        urgency = data.get("urgency", 5)
        importance = data.get("importance", 5)

        if not action_type or not title:
            return web.Response(text="Missing action_type or title", status=400)

        # Usa AUTHORIZED_USER_ID o il user_id dal payload
        user_id = data.get("user_id") or AUTHORIZED_USER_ID
        if not user_id:
            return web.Response(text="No user_id available", status=400)

        action_id = enqueue_action(
            user_id, action_type, title, description,
            payload=payload, priority=priority, urgency=urgency, importance=importance,
        )
        if action_id:
            # Se Mirco non ha un'azione attiva, manda subito la prossima
            chat_id = int(user_id)
            if chat_id not in _current_action:
                send_next_action(chat_id)

            return web.json_response({"status": "queued", "action_id": action_id})
        return web.Response(text="Failed to enqueue", status=500)
    except Exception as e:
        logger.error(f"[ENQUEUE] {e}")
        return web.Response(text=str(e), status=500)


async def handle_set_current_action(request):
    """Chiamato da agents_runner dopo aver inserito un'azione BOS in action_queue.
    Carica l'azione in _current_action così Mirco può rispondervi immediatamente."""
    try:
        data = await request.json()
        chat_id_str = data.get("chat_id")
        action_id_str = data.get("action_id")
        if not chat_id_str or not action_id_str:
            return web.Response(text="Missing chat_id or action_id", status=400)
        chat_id = int(chat_id_str)
        result = supabase.table("action_queue").select("*").eq("id", int(action_id_str)).execute()
        if result.data:
            _current_action[chat_id] = result.data[0]
            logger.info(f"[ACTION/SET] current_action impostata per chat_id={chat_id} action_id={action_id_str}")
            return web.json_response({"status": "ok"})
        return web.Response(text="Action not found", status=404)
    except Exception as e:
        logger.error(f"[ACTION/SET] {e}")
        return web.Response(text=str(e), status=500)


# ---- MAIN ----

async def main():
    global tg_app

    logger.info("brAIn Command Center v3.3 — Sonnet 4.5 + Action Queue")

    tg_app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("code", handle_code_command))
    tg_app.add_handler(CallbackQueryHandler(handle_callback_query))
    tg_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    tg_app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    tg_app.add_handler(MessageHandler(filters.COMMAND, handle_command_as_message))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await tg_app.initialize()
    await tg_app.start()

    if WEBHOOK_URL:
        await tg_app.bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook: {WEBHOOK_URL}")

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/", telegram_webhook)
    app.router.add_post("/alert", handle_alert)
    app.router.add_post("/action", handle_enqueue_action)
    app.router.add_post("/action/set", handle_set_current_action)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    logger.info(f"Running on :{PORT}")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await tg_app.stop()
        await tg_app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
