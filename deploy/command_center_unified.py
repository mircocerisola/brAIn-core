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
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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
MAX_DB_MESSAGES = 30  # ultimi messaggi da caricare dal DB
SUMMARY_INTERVAL = 20  # ogni N messaggi utente genera un riassunto
USD_TO_EUR = 0.92

# Session context per chat_id — traccia ultimo problema/soluzione discussi (in-memory, ricostruito da DB)
_session_context = {}  # chat_id -> {"last_problem_id":, "last_solution_id":, "last_shown_ids":, ...}
_chat_history_available = None  # None=not checked yet, True/False

# ---- NOTIFICHE INTELLIGENTI ----
# Quando Mirco ha mandato un messaggio negli ultimi 90s, le notifiche background vanno in coda.
# Vengono inviate raggruppate dopo 2 minuti di silenzio. Solo CRITICAL interrompono.
_last_mirco_message_time = 0.0  # timestamp ultimo messaggio di Mirco
_notification_queue = []  # lista di messaggi in coda
_notification_lock = threading.Lock()
MIRCO_ACTIVE_WINDOW = 120  # secondi (2 minuti)
NOTIFICATION_BATCH_DELAY = 120  # secondi di silenzio prima di inviare coda

# ---- CODE AGENT ----
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "mircocerisola/brAIn-core"
GITHUB_API = "https://api.github.com"
CODE_AGENT_MODEL = "claude-sonnet-4-5-20250929"

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

CONTESTO CONVERSAZIONE:
- Quando Mirco dice "il primo", "quello", "spiegami meglio" — CAPISCI dal contesto. Usa il CONTESTO SESSIONE sotto.
- Se Mirco risponde "si", "ok", "avanti", "approva" — AGISCI senza chiedere altro.
- Se dice un numero — interpretalo come ID del problema/soluzione dal contesto.
- NON chiedere informazioni che hai gia' nella conversazione o nel contesto sessione.

TOOL: Hai accesso al database. Usa i tool SEMPRE per dati freschi. NON inventare numeri.

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

FLUSSO:
- "problemi": query_supabase, table=problems, select=id,title,weighted_score,sector,urgency,status, order_by=weighted_score, order_desc=true, limit=10.
- "approva" / "si vai": approve_problem con ID dal contesto.
- "rifiuta" / "skip" / "no": reject_problem.
- "soluzioni": query_supabase table=solutions.
- "seleziona": select_solution.
- "stato" / "status": get_system_status.
- "costi": get_cost_report.
- Tema da esplorare: trigger_scan.

{ctx}{session}{summary_section}"""


def get_minimal_context():
    try:
        p_count = supabase.table("problems").select("id", count="exact").execute()
        p_new = supabase.table("problems").select("id", count="exact").eq("status", "new").execute()
        p_approved = supabase.table("problems").select("id", count="exact").eq("status", "approved").execute()
        s_count = supabase.table("solutions").select("id", count="exact").execute()
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
        return (
            f"\nSTATO ATTUALE: {p_count.count or 0} problemi "
            f"({p_new.count or 0} nuovi, {p_approved.count or 0} approvati), "
            f"{s_count.count or 0} soluzioni. "
            f"Costi 24h: {cost_24h_eur} EUR.{ev_summary}\n"
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
        "description": "Approva un problema — cambia status a approved e notifica Solution Architect.",
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
        "description": "Lancia scan mirato su un argomento specifico via agents-runner.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Argomento/keywords da scansionare"},
            },
            "required": ["topic"],
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
            return trigger_scan(tool_input["topic"])
        else:
            return f"Tool sconosciuto: {tool_name}"
    except Exception as e:
        return f"ERRORE {tool_name}: {e}"


ALLOWED_TABLES = [
    "problems", "solutions", "agent_logs", "org_knowledge", "scan_sources",
    "capability_log", "org_config", "solution_scores", "agent_events",
    "reevaluation_log", "authorization_matrix", "finance_metrics",
]


def supabase_query(params):
    table = params["table"]
    if table not in ALLOWED_TABLES:
        return f"BLOCCATO: tabella '{table}' non accessibile."
    try:
        q = supabase.table(table).select(params["select"])
        filters_str = params.get("filters", "")
        if filters_str:
            for f in filters_str.split(","):
                f = f.strip()
                if ".gte=" in f:
                    col, val = f.split(".gte=")
                    q = q.gte(col.strip(), val.strip())
                elif ".lte=" in f:
                    col, val = f.split(".lte=")
                    q = q.lte(col.strip(), val.strip())
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


def get_system_status():
    try:
        status = {}
        problems = supabase.table("problems").select("id,status").execute()
        solutions = supabase.table("solutions").select("id").execute()
        status["problemi_totali"] = len(problems.data) if problems.data else 0
        status["problemi_nuovi"] = len([p for p in (problems.data or []) if p.get("status") == "new"])
        status["problemi_approvati"] = len([p for p in (problems.data or []) if p.get("status") == "approved"])
        status["soluzioni_totali"] = len(solutions.data) if solutions.data else 0

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

        supabase.table("problems").update({"status": "rejected"}).eq("id", problem_id).execute()
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


def trigger_scan(topic):
    if not AGENTS_RUNNER_URL:
        return "AGENTS_RUNNER_URL non configurato. Scan non disponibile."
    try:
        r = http_requests.post(
            f"{AGENTS_RUNNER_URL}/scanner/custom",
            json={"topic": topic},
            timeout=5,
        )
        if r.status_code == 200:
            return f"Scan mirato avviato per: {topic}"
        return f"Errore scan: HTTP {r.status_code}"
    except Exception as e:
        return f"Errore scan: {e}"


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

MODEL = "claude-sonnet-4-5-20250929"
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
    """Verifica se la tabella chat_history esiste in Supabase."""
    global _chat_history_available
    if _chat_history_available is not None:
        return _chat_history_available
    try:
        supabase.table(CHAT_HISTORY_TABLE).select("id").limit(1).execute()
        _chat_history_available = True
        logger.info("[CHAT_HISTORY] Tabella disponibile")
    except Exception:
        _chat_history_available = False
        logger.warning("[CHAT_HISTORY] Tabella non trovata — fallback in-memory")
    return _chat_history_available


def _save_chat_message(chat_id, role, content, msg_type="message"):
    """Salva un messaggio nella tabella chat_history."""
    if not _check_chat_history_table():
        return
    try:
        content_str = json.dumps(content, default=str, ensure_ascii=False) if not isinstance(content, str) else content
        supabase.table(CHAT_HISTORY_TABLE).insert({
            "chat_id": str(chat_id),
            "role": role,
            "content": content_str,
            "msg_type": msg_type,
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
        # 1. Ultimo summary
        s = supabase.table(CHAT_HISTORY_TABLE) \
            .select("content") \
            .eq("chat_id", str(chat_id)) \
            .eq("msg_type", "summary") \
            .order("created_at", desc=True) \
            .limit(1).execute()
        if s.data:
            summary_text = s.data[0]["content"]

        # 2. Ultimi 30 messaggi (non summary)
        rows = supabase.table(CHAT_HISTORY_TABLE) \
            .select("role,content") \
            .eq("chat_id", str(chat_id)) \
            .neq("msg_type", "summary") \
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
        # Timestamp ultimo summary
        s = supabase.table(CHAT_HISTORY_TABLE) \
            .select("created_at") \
            .eq("chat_id", str(chat_id)) \
            .eq("msg_type", "summary") \
            .order("created_at", desc=True) \
            .limit(1).execute()

        q = supabase.table(CHAT_HISTORY_TABLE) \
            .select("id", count="exact") \
            .eq("chat_id", str(chat_id)) \
            .eq("msg_type", "message") \
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
        # Carica messaggi dall'ultimo summary (max 60 righe)
        s = supabase.table(CHAT_HISTORY_TABLE) \
            .select("created_at") \
            .eq("chat_id", str(chat_id)) \
            .eq("msg_type", "summary") \
            .order("created_at", desc=True) \
            .limit(1).execute()

        q = supabase.table(CHAT_HISTORY_TABLE) \
            .select("role,content,msg_type") \
            .eq("chat_id", str(chat_id)) \
            .neq("msg_type", "summary") \
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

        # Salva come summary
        _save_chat_message(chat_id, "assistant", summary_text, msg_type="summary")
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
        _save_chat_message(cid, "user", user_to_save, msg_type="message")
        for tm in all_tool_messages:
            _save_chat_message(cid, tm["role"], tm["content"], msg_type="tool")
        _save_chat_message(cid, "assistant", final, msg_type="message")

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

        return final or "Operazione completata."
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


def _send_notification_now(message):
    """Invia notifica Telegram immediatamente."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = AUTHORIZED_USER_ID
    if not token or not chat_id:
        return
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
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
        "brAIn Command Center v3.0 attivo.\n"
        "Sonnet 4.5 — intelligente, contestuale, COO-level.\n"
        "Vocali, foto, dashboard, /code. Scrivimi quello che vuoi."
    )
    log_to_supabase("command_center", "start", f"uid={AUTHORIZED_USER_ID}", "v3.0 sonnet", "none")


async def handle_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler per /code <istruzioni> — Code Agent via Claude Sonnet + GitHub."""
    if not is_authorized(update):
        return
    prompt = update.message.text.replace("/code", "", 1).strip()
    if not prompt:
        await update.message.reply_text(
            "Scrivi /code seguito dalle istruzioni.\n"
            "Esempio: /code aggiungi un endpoint /health che ritorna lo stato degli agenti")
        return
    if not GITHUB_TOKEN:
        await update.message.reply_text("GITHUB_TOKEN non configurato.")
        return

    await update.message.reply_text(f"Ci lavoro con Sonnet: {prompt[:100]}...")
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

    if msg.strip().upper() == "STOP":
        await update.message.reply_text("STOP ricevuto. Tutto fermo.")
        return

    # Check pending deploy
    if chat_id in pending_deploys:
        lower_msg = msg.strip().lower()
        if lower_msg in ("si", "sì", "ok", "vai", "yes", "deploy", "builda", "deploya"):
            deploy_info = pending_deploys.pop(chat_id)
            await update.message.reply_text("Avvio build e deploy...")
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _trigger_build_deploy_sync, chat_id, deploy_info)
            return
        elif lower_msg in ("no", "annulla", "stop", "cancel"):
            pending_deploys.pop(chat_id)
            await update.message.reply_text("Deploy annullato. Il codice resta su GitHub.")
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

        prefix = {
            "critical": "ALERT CRITICO",
            "warning": "ATTENZIONE",
            "info": "INFO",
        }.get(level, "NOTIFICA")

        text = f"[{prefix}] da {source}:\n{message}"

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


# ---- MAIN ----

async def main():
    global tg_app

    logger.info("brAIn Command Center v3.0 — Sonnet 4.5 + Context + Smart Chat")

    tg_app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("code", handle_code_command))
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
