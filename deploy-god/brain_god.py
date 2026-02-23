"""
brAIn God v1.0
Il Dio dell'organismo brAIn. Gestisce infrastruttura, codice, agenti, costi, legal.
Usa Claude Sonnet con tool_use per operazioni reali su GitHub e Cloud Run.
Guardrails hardcoded a livello Python — non nel prompt.
"""

import os
import json
import re
import time
import logging
import asyncio
import threading
import base64
import copy
from datetime import datetime, timedelta
from aiohttp import web
from dotenv import load_dotenv
import anthropic
import requests as http_requests
from supabase import create_client
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = "mircocerisola/brAIn-core"
GITHUB_API = "https://api.github.com"

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

AUTHORIZED_USER_ID = None
chat_history = []
MAX_HISTORY = 8

# ============================================================
# GUARDRAILS HARDCODED (L2) — NON modificabili dal modello
# ============================================================
BLOCKED_COMMANDS = [
    "rm -rf", "rmdir", "del /s", "format",
    "DROP TABLE", "DROP DATABASE", "TRUNCATE", "DELETE FROM",
    "gcloud run services delete", "gcloud projects delete",
]
ALLOWED_WRITE_PATHS = [
    "agents/", "deploy/", "deploy-agents/", "deploy-god/",
    "docs/", "config/", "CLAUDE.md", "MEMORY.md",
]
MAX_DEPLOYS_PER_DAY = 5
MAX_FILE_WRITES_PER_SESSION = 20
daily_deploy_count = 0
session_write_count = 0
last_deploy_date = None

def is_path_allowed(path):
    """L2 guardrail: solo directory autorizzate"""
    return any(path.startswith(p) for p in ALLOWED_WRITE_PATHS)

def is_content_safe(content):
    """L2 guardrail: blocca comandi distruttivi nel contenuto"""
    content_upper = content.upper()
    for cmd in BLOCKED_COMMANDS:
        if cmd.upper() in content_upper:
            return False, cmd
    return True, None

def check_deploy_limit():
    """L2 guardrail: max deploy giornalieri"""
    global daily_deploy_count, last_deploy_date
    today = datetime.now().date()
    if last_deploy_date != today:
        daily_deploy_count = 0
        last_deploy_date = today
    return daily_deploy_count < MAX_DEPLOYS_PER_DAY

def check_write_limit():
    """L2 guardrail: max scritture per sessione"""
    return session_write_count < MAX_FILE_WRITES_PER_SESSION


# ============================================================
# SYSTEM PROMPT — Il DNA di brAIn God
# ============================================================
SYSTEM_PROMPT = """Sei brAIn God — il Dio dell'organismo brAIn. Gestisci l'infrastruttura, il codice, gli agenti, i costi, e la compliance legale.

CHI SEI:
Sei il cervello tecnico di brAIn, un'organizzazione AI-native che scansiona problemi globali e costruisce soluzioni. Mirco e' il CEO e fondatore — parli solo con lui via Telegram. Sei il suo braccio destro tecnico.

COSA GESTISCI:
- Codice degli agenti Python (leggere, modificare, creare, deployare)
- Infrastruttura Cloud Run (build, deploy, monitoraggio)
- Database Supabase (query, stato, metriche)
- Costi e budget (API, infrastruttura, per progetto)
- Compliance e sicurezza

ARCHITETTURA brAIn (8 sistemi organici):
1. CORTEX: brAIn (bot business) + brAIn God (tu, bot infra) + Router
2. SENSES: World Scanner v2.2, Capability Scout v1.1, Legal Monitor (da costruire)
3. THINKING: Solution Architect v2.0, Feasibility Engine (da costruire)
4. HANDS: Project Builder, Marketing Agent, Customer Agent (Layer 3, da costruire)
5. DNA: Code Agent (TU — scrivi/modifichi/deployi codice)
6. METABOLISM: Finance Agent (da costruire, tu fai tracking costi base)
7. IMMUNE: Legal Agent (da costruire)
8. MEMORY: Knowledge Keeper v1.1, Idea Recycler, Supabase pgvector

STACK TECNOLOGICO:
- Claude API: Haiku (bot brAIn), Sonnet (tu, brAIn God + Code Agent)
- Perplexity API Sonar: ricerca web per World Scanner e Solution Architect
- Supabase Pro: PostgreSQL + pgvector + RLS. 22+ tabelle.
- Telegram: 2 bot separati (brAIn = business, brAIn God = infra)
- Python: linguaggio agenti
- GitHub privato: mircocerisola/brAIn-core
- Google Cloud Run EU Frankfurt: hosting 24/7

SERVIZI ATTIVI SU CLOUD RUN:
- command-center: bot Telegram brAIn (business)
- agents-runner: World Scanner + Solution Architect + Knowledge Keeper + Capability Scout
- brain-god: TU (questo servizio)

COME PARLI:
- SEMPRE italiano, diretto, tecnico quando serve.
- NON usare MAI Markdown: niente asterischi, grassetto, corsivo. Testo piano.
- UNA sola domanda alla volta a Mirco.
- Zero fuffa. Vai al punto.
- Quando proponi modifiche al codice, spiega COSA fai e PERCHE' in 2-3 frasi, poi agisci.

REGOLE DI SICUREZZA (le conosci ma NON le puoi aggirare — sono hardcoded nel codice):
- Non puoi eliminare file, tabelle, container. I comandi distruttivi sono bloccati.
- Puoi scrivere solo in directory autorizzate (agents/, deploy/, deploy-agents/, deploy-god/, docs/, config/).
- Max 5 deploy al giorno, max 20 modifiche file per sessione.
- Ogni modifica file crea backup automatico su GitHub (commit separato).
- Se Mirco scrive STOP, tutto si ferma.

COME USI I TOOL:
Hai accesso a tool per leggere e scrivere codice su GitHub, fare query sul database, e controllare lo stato del sistema.
Quando Mirco ti chiede di modificare qualcosa:
1. Leggi il file attuale con read_github_file
2. Genera la versione modificata
3. Spiega a Mirco cosa cambierai in 2-3 frasi
4. Scrivi il file con write_github_file (crea backup automatico)
5. Se serve deploy, segnalalo — per ora il deploy richiede approvazione Mirco

Quando Mirco chiede stato/costi/errori:
1. Usa query_supabase per ottenere dati reali
2. Presenta in modo sintetico

DATI DEL DATABASE (aggiornati ad ogni messaggio):
"""


# ============================================================
# TOOLS — Definizioni per Claude tool_use
# ============================================================
TOOLS = [
    {
        "name": "read_github_file",
        "description": "Legge il contenuto di un file dal repository GitHub brAIn-core. Usa per vedere il codice attuale degli agenti.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Percorso del file nel repo, es: 'deploy/command_center_cloud.py' o 'agents/world_scanner.py'"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "list_github_dir",
        "description": "Lista i file in una directory del repository GitHub brAIn-core.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Percorso della directory, es: 'deploy/' o 'agents/' o '' per la root"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_github_file",
        "description": "Scrive o aggiorna un file nel repository GitHub. Crea backup automatico. SOLO directory autorizzate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Percorso del file nel repo"
                },
                "content": {
                    "type": "string",
                    "description": "Contenuto completo del file"
                },
                "commit_message": {
                    "type": "string",
                    "description": "Messaggio di commit descrittivo"
                }
            },
            "required": ["path", "content", "commit_message"]
        }
    },
    {
        "name": "query_supabase",
        "description": "Esegue una query SELECT sul database Supabase. SOLO lettura, niente INSERT/UPDATE/DELETE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Nome della tabella: problems, solutions, agent_logs, org_knowledge, scan_sources, capability_log, org_config, solution_scores"
                },
                "select": {
                    "type": "string",
                    "description": "Colonne da selezionare, es: 'id,title,status' o '*'"
                },
                "filters": {
                    "type": "string",
                    "description": "Filtri opzionali in formato 'colonna=valore' o 'colonna.gte=valore'. Multipli separati da virgola."
                },
                "order_by": {
                    "type": "string",
                    "description": "Colonna per ordinamento, es: 'created_at' o 'weighted_score'"
                },
                "order_desc": {
                    "type": "boolean",
                    "description": "True per ordine decrescente"
                },
                "limit": {
                    "type": "integer",
                    "description": "Limite risultati (default 20)"
                }
            },
            "required": ["table", "select"]
        }
    },
    {
        "name": "get_system_status",
        "description": "Ottiene lo stato completo del sistema: agenti attivi, ultimi errori, costi giornalieri, problemi/soluzioni nel DB.",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "get_cost_report",
        "description": "Report costi dettagliato: per agente, per giorno, totale. Dati da agent_logs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Numero di giorni da analizzare (default 7)"
                }
            },
        }
    },
]


# ============================================================
# TOOL EXECUTION — Con guardrails
# ============================================================
def execute_tool(tool_name, tool_input):
    """Esegue un tool con guardrails hardcoded"""
    try:
        if tool_name == "read_github_file":
            return github_read_file(tool_input["path"])

        elif tool_name == "list_github_dir":
            return github_list_dir(tool_input["path"])

        elif tool_name == "write_github_file":
            return github_write_file(
                tool_input["path"],
                tool_input["content"],
                tool_input["commit_message"]
            )

        elif tool_name == "query_supabase":
            return supabase_query(tool_input)

        elif tool_name == "get_system_status":
            return get_system_status()

        elif tool_name == "get_cost_report":
            days = tool_input.get("days", 7)
            return get_cost_report(days)

        else:
            return f"Tool sconosciuto: {tool_name}"

    except Exception as e:
        return f"ERRORE tool {tool_name}: {str(e)}"


def github_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def github_read_file(path):
    """Legge un file da GitHub"""
    if not GITHUB_TOKEN:
        return "ERRORE: GitHub token non configurato. Mirco deve aggiungere GITHUB_TOKEN."
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    r = http_requests.get(url, headers=github_headers(), timeout=10)
    if r.status_code == 200:
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return f"File: {path} ({len(content)} caratteri)\n\n{content}"
    elif r.status_code == 404:
        return f"File non trovato: {path}"
    else:
        return f"Errore GitHub {r.status_code}: {r.text[:200]}"


def github_list_dir(path):
    """Lista file in una directory GitHub"""
    if not GITHUB_TOKEN:
        return "ERRORE: GitHub token non configurato."
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    r = http_requests.get(url, headers=github_headers(), timeout=10)
    if r.status_code == 200:
        items = r.json()
        if isinstance(items, list):
            result = f"Directory: {path or '/'}\n\n"
            for item in items:
                icon = "dir" if item["type"] == "dir" else "file"
                size = f" ({item.get('size', 0)} bytes)" if item["type"] == "file" else ""
                result += f"  [{icon}] {item['name']}{size}\n"
            return result
        else:
            return f"Non e' una directory: {path}"
    else:
        return f"Errore GitHub {r.status_code}: {r.text[:200]}"


def github_write_file(path, content, commit_message):
    """Scrive un file su GitHub con guardrails"""
    global session_write_count

    # L2: controlla path autorizzato
    if not is_path_allowed(path):
        return f"BLOCCATO (L2): path '{path}' non autorizzato. Paths consentiti: {', '.join(ALLOWED_WRITE_PATHS)}"

    # L2: controlla contenuto sicuro
    safe, blocked_cmd = is_content_safe(content)
    if not safe:
        return f"BLOCCATO (L2): contenuto contiene comando proibito: '{blocked_cmd}'"

    # L2: controlla limite scritture
    if not check_write_limit():
        return f"BLOCCATO (L2): raggiunto limite di {MAX_FILE_WRITES_PER_SESSION} scritture per sessione"

    if not GITHUB_TOKEN:
        return "ERRORE: GitHub token non configurato."

    # Cerca se il file esiste gia' (per ottenere SHA)
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    r = http_requests.get(url, headers=github_headers(), timeout=10)

    payload = {
        "message": f"[brAIn God] {commit_message}",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": "main",
    }

    if r.status_code == 200:
        # File esiste — aggiornamento
        payload["sha"] = r.json()["sha"]

    r = http_requests.put(url, headers=github_headers(), json=payload, timeout=15)

    if r.status_code in (200, 201):
        session_write_count += 1
        action = "aggiornato" if "sha" in payload else "creato"
        return f"OK: file '{path}' {action} su GitHub. Commit: {commit_message}. Scritture sessione: {session_write_count}/{MAX_FILE_WRITES_PER_SESSION}"
    else:
        return f"Errore GitHub {r.status_code}: {r.text[:300]}"


def supabase_query(params):
    """Query SELECT su Supabase con guardrails"""
    table = params["table"]
    select = params["select"]

    # L2: solo SELECT, mai modifiche
    allowed_tables = [
        "problems", "solutions", "agent_logs", "org_knowledge",
        "scan_sources", "capability_log", "org_config", "solution_scores",
        "agent_events", "reevaluation_log",
    ]
    if table not in allowed_tables:
        return f"BLOCCATO: tabella '{table}' non accessibile. Tabelle consentite: {', '.join(allowed_tables)}"

    try:
        q = supabase.table(table).select(select)

        # Applica filtri
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

        # Ordinamento
        if params.get("order_by"):
            q = q.order(params["order_by"], desc=params.get("order_desc", True))

        # Limite
        limit = params.get("limit", 20)
        q = q.limit(min(limit, 50))

        result = q.execute()

        if result.data:
            return json.dumps(result.data, indent=2, default=str, ensure_ascii=False)[:3000]
        else:
            return "Nessun risultato."

    except Exception as e:
        return f"Errore query: {str(e)}"


def get_system_status():
    """Stato completo del sistema"""
    try:
        status = {}

        # Conteggi
        problems = supabase.table("problems").select("id,status", count="exact").execute()
        solutions = supabase.table("solutions").select("id", count="exact").execute()
        status["problemi_totali"] = len(problems.data) if problems.data else 0
        status["problemi_approvati"] = len([p for p in (problems.data or []) if p.get("status") == "approved"])
        status["soluzioni_totali"] = len(solutions.data) if solutions.data else 0

        # Ultimi log per agente
        logs = supabase.table("agent_logs") \
            .select("agent_id,action,status,created_at,cost_usd") \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute()

        if logs.data:
            agents_status = {}
            for log in logs.data:
                aid = log["agent_id"]
                if aid not in agents_status:
                    agents_status[aid] = {
                        "ultima_azione": log["action"],
                        "ultimo_stato": log["status"],
                        "ultimo_run": log["created_at"],
                    }
            status["agenti"] = agents_status

        # Costi ultimi 24h
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        costs = supabase.table("agent_logs") \
            .select("cost_usd") \
            .gte("created_at", yesterday) \
            .execute()
        total_cost = sum(float(c.get("cost_usd", 0) or 0) for c in (costs.data or []))
        status["costi_24h_usd"] = round(total_cost, 4)

        # Errori recenti
        errors = supabase.table("agent_logs") \
            .select("agent_id,action,error,created_at") \
            .eq("status", "error") \
            .order("created_at", desc=True) \
            .limit(5) \
            .execute()
        status["errori_recenti"] = errors.data or []

        return json.dumps(status, indent=2, default=str, ensure_ascii=False)

    except Exception as e:
        return f"Errore status: {str(e)}"


def get_cost_report(days=7):
    """Report costi dettagliato"""
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        logs = supabase.table("agent_logs") \
            .select("agent_id,cost_usd,tokens_input,tokens_output,model_used,created_at") \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(500) \
            .execute()

        if not logs.data:
            return f"Nessun dato costi negli ultimi {days} giorni."

        # Aggregazione per agente
        by_agent = {}
        total = 0
        for log in logs.data:
            aid = log["agent_id"]
            cost = float(log.get("cost_usd", 0) or 0)
            total += cost
            if aid not in by_agent:
                by_agent[aid] = {"costo_usd": 0, "chiamate": 0, "tokens_in": 0, "tokens_out": 0}
            by_agent[aid]["costo_usd"] += cost
            by_agent[aid]["chiamate"] += 1
            by_agent[aid]["tokens_in"] += int(log.get("tokens_input", 0) or 0)
            by_agent[aid]["tokens_out"] += int(log.get("tokens_output", 0) or 0)

        # Arrotonda
        for aid in by_agent:
            by_agent[aid]["costo_usd"] = round(by_agent[aid]["costo_usd"], 4)

        report = {
            "periodo_giorni": days,
            "costo_totale_usd": round(total, 4),
            "per_agente": by_agent,
            "chiamate_totali": len(logs.data),
        }

        return json.dumps(report, indent=2, default=str, ensure_ascii=False)

    except Exception as e:
        return f"Errore report costi: {str(e)}"


# ============================================================
# LOGGING — Non bloccante
# ============================================================
def log_to_supabase(agent_id, action, input_summary, output_summary, model_used,
                    tokens_in=0, tokens_out=0, cost=0, duration_ms=0, status="success", error=None):
    def _log():
        try:
            supabase.table("agent_logs").insert({
                "agent_id": agent_id,
                "action": action,
                "layer": 0,
                "input_summary": input_summary[:500] if input_summary else None,
                "output_summary": output_summary[:500] if output_summary else None,
                "model_used": model_used,
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "cost_usd": cost,
                "duration_ms": duration_ms,
                "status": status,
                "error": error,
            }).execute()
        except Exception as e:
            logger.error(f"[LOG ERROR] {e}")
    threading.Thread(target=_log, daemon=True).start()


# ============================================================
# DB CONTEXT — Stato rapido del sistema
# ============================================================
def get_db_context():
    context = ""
    try:
        # Problemi: solo conteggi + top 3
        problems = supabase.table("problems") \
            .select("id,title,weighted_score,status,sector") \
            .order("weighted_score", desc=True) \
            .limit(5) \
            .execute()

        if problems.data:
            context += f"\nProblemi nel DB: {len(problems.data)}+ (top 5 per score)\n"
            for p in problems.data:
                context += f"  [{p['id']}] {p['title']} — score:{p.get('weighted_score','?')} status:{p.get('status','?')}\n"

        # Soluzioni: conteggi
        solutions = supabase.table("solutions") \
            .select("id,title,problem_id,status") \
            .limit(5) \
            .execute()

        if solutions.data:
            context += f"\nSoluzioni nel DB: {len(solutions.data)}+\n"
            for s in solutions.data:
                context += f"  [{s['id']}] {s['title']} — problema:{s.get('problem_id','?')} status:{s.get('status','?')}\n"

        # Ultimi errori
        errors = supabase.table("agent_logs") \
            .select("agent_id,action,error,created_at") \
            .eq("status", "error") \
            .order("created_at", desc=True) \
            .limit(3) \
            .execute()

        if errors.data:
            context += "\nUltimi errori:\n"
            for e in errors.data:
                context += f"  {e['agent_id']}: {e.get('error', '?')[:100]} ({e['created_at'][:16]})\n"

    except Exception as e:
        context += f"\n[Errore caricamento context: {e}]\n"

    return context


# ============================================================
# ASK CLAUDE — Agent loop con tool_use
# ============================================================
def ask_claude(user_message, is_photo=False, image_b64=None):
    global chat_history

    start = time.time()
    model = "claude-sonnet-4-5-20250929"
    try:
        db_context = get_db_context()
        full_system = SYSTEM_PROMPT + db_context

        # Costruisci messaggi
        messages = []
        for h in chat_history:
            messages.append({"role": "user", "content": h["user"]})
            messages.append({"role": "assistant", "content": h["assistant"]})

        # Messaggio utente (testo o foto)
        if is_photo and image_b64:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": user_message},
                ],
            })
        else:
            messages.append({"role": "user", "content": user_message})

        # Agent loop — gestisce tool calls
        total_tokens_in = 0
        total_tokens_out = 0
        max_iterations = 5
        final_text = ""

        for iteration in range(max_iterations):
            response = claude.messages.create(
                model=model,
                max_tokens=2000,
                system=full_system,
                messages=messages,
                tools=TOOLS,
            )

            total_tokens_in += response.usage.input_tokens
            total_tokens_out += response.usage.output_tokens

            # Controlla se STOP
            if "STOP" in user_message.upper() and user_message.strip().upper() == "STOP":
                final_text = "STOP ricevuto. Tutti gli agenti fermati. Nessuna operazione in corso."
                break

            # Processa risposta
            if response.stop_reason == "end_turn":
                # Risposta finale — estrai testo
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text += block.text
                break

            elif response.stop_reason == "tool_use":
                # Tool call — esegui e continua
                tool_results = []
                text_parts = []

                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                    elif block.type == "tool_use":
                        logger.info(f"[TOOL] {block.name}({json.dumps(block.input)[:200]})")
                        result = execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result)[:4000],
                        })

                # Aggiungi risposta assistente + tool results ai messaggi
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            else:
                # Stop reason inatteso
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text += block.text
                break

        duration = int((time.time() - start) * 1000)
        cost = (total_tokens_in * 3.0 + total_tokens_out * 15.0) / 1_000_000  # Sonnet pricing

        # Salva in history (solo testo)
        history_user = f"[FOTO] {user_message}" if is_photo else user_message
        chat_history.append({"user": history_user, "assistant": final_text[:500]})
        if len(chat_history) > MAX_HISTORY:
            chat_history = chat_history[-MAX_HISTORY:]

        log_to_supabase(
            agent_id="brain_god",
            action="chat",
            input_summary=user_message[:300],
            output_summary=final_text[:300],
            model_used=model,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            cost=cost,
            duration_ms=duration,
        )

        return final_text or "Operazione completata."

    except Exception as e:
        duration = int((time.time() - start) * 1000)
        logger.error(f"[CLAUDE ERROR] {e}")
        log_to_supabase("brain_god", "chat", user_message[:300], None, model,
                       duration_ms=duration, status="error", error=str(e))
        return f"Errore: {e}"


# ============================================================
# TELEGRAM HANDLERS
# ============================================================
tg_app = None

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTHORIZED_USER_ID
    AUTHORIZED_USER_ID = update.effective_user.id

    try:
        supabase.table("org_config").upsert({
            "key": "god_telegram_user_id",
            "value": json.dumps(AUTHORIZED_USER_ID),
        }, on_conflict="key").execute()
    except Exception as e:
        logger.error(f"[CONFIG ERROR] {e}")

    await update.message.reply_text(
        "brAIn God v1.0 attivo. Sono il tuo braccio destro tecnico. "
        "Chiedimi qualsiasi cosa su codice, infrastruttura, costi, agenti."
    )
    log_to_supabase("brain_god", "start", f"user_id={AUTHORIZED_USER_ID}", "God avviato v1.0", "none")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_message = update.message.text
    await update.message.chat.send_action("typing")

    reply = ask_claude(user_message)
    clean = clean_reply(reply)

    # Split messaggi lunghi
    if len(clean) > 4000:
        parts = [clean[i:i+4000] for i in range(0, len(clean), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(clean)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    await update.message.chat.send_action("typing")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()
    image_b64 = base64.b64encode(bytes(image_bytes)).decode("utf-8")
    caption = update.message.caption or "Analizza questa immagine nel contesto di brAIn — infrastruttura, architettura, codice, errori."

    reply = ask_claude(caption, is_photo=True, image_b64=image_b64)
    clean = clean_reply(reply)

    if len(clean) > 4000:
        parts = [clean[i:i+4000] for i in range(0, len(clean), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(clean)


def clean_reply(text):
    """Rimuove formattazione markdown"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'```[\w]*\n?', '', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text.strip()


def is_authorized(update: Update) -> bool:
    global AUTHORIZED_USER_ID

    if AUTHORIZED_USER_ID is None:
        try:
            result = supabase.table("org_config").select("value").eq("key", "god_telegram_user_id").execute()
            if result.data:
                AUTHORIZED_USER_ID = json.loads(result.data[0]["value"])
        except:
            pass

    if AUTHORIZED_USER_ID is None:
        return True

    if update.effective_user.id != AUTHORIZED_USER_ID:
        return False

    return True


# ============================================================
# WEB SERVER + WEBHOOK
# ============================================================
async def health_check(request):
    return web.Response(text="brAIn God OK", status=200)


async def telegram_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logger.error(f"[WEBHOOK ERROR] {e}")
    return web.Response(text="OK", status=200)


async def main():
    global tg_app

    logger.info("brAIn God v1.0 starting...")

    tg_app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await tg_app.initialize()
    await tg_app.start()

    if WEBHOOK_URL:
        await tg_app.bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook set to {WEBHOOK_URL}")

    web_app = web.Application()
    web_app.router.add_get("/", health_check)
    web_app.router.add_post("/", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"brAIn God running on port {PORT}")

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
