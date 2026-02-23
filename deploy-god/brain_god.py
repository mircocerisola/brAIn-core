"""
brAIn God v2.0
Il Dio dell'organismo brAIn. Gestisce infrastruttura, codice, agenti, costi, legal.
Usa Claude Opus 4.6 con tool_use per operazioni reali su GitHub e Cloud Run.
Supporta messaggi vocali via Google Cloud Speech-to-Text.
Legge CLAUDE.md dal repo come DNA permanente.
Auto-deploy con approvazione Mirco.
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
GCP_PROJECT = "brain-core-487914"
GCP_REGION = "europe-west3"
ARTIFACT_REGISTRY = f"{GCP_REGION}-docker.pkg.dev/{GCP_PROJECT}/brain-repo"

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

AUTHORIZED_USER_ID = None
chat_history = []
MAX_HISTORY = 12
pending_deploy = None

# GUARDRAILS HARDCODED (L2)
BLOCKED_COMMANDS = ["rm -rf","rmdir","del /s","format","DROP TABLE","DROP DATABASE","TRUNCATE","DELETE FROM","gcloud run services delete","gcloud projects delete"]
ALLOWED_WRITE_PATHS = ["agents/","deploy/","deploy-agents/","deploy-god/","docs/","config/","CLAUDE.md","MEMORY.md"]
MAX_DEPLOYS_PER_DAY = 5
MAX_FILE_WRITES_PER_SESSION = 20
daily_deploy_count = 0
session_write_count = 0
last_deploy_date = None

def is_path_allowed(path):
    return any(path.startswith(p) for p in ALLOWED_WRITE_PATHS)

def is_content_safe(content):
    content_upper = content.upper()
    for cmd in BLOCKED_COMMANDS:
        if cmd.upper() in content_upper:
            return False, cmd
    return True, None

def check_deploy_limit():
    global daily_deploy_count, last_deploy_date
    today = datetime.now().date()
    if last_deploy_date != today:
        daily_deploy_count = 0
        last_deploy_date = today
    return daily_deploy_count < MAX_DEPLOYS_PER_DAY

def check_write_limit():
    return session_write_count < MAX_FILE_WRITES_PER_SESSION

# CLAUDE.md CACHE
_claude_md_cache = {"content": "", "last_fetch": None}

def get_claude_md():
    now = datetime.now()
    if _claude_md_cache["content"] and _claude_md_cache["last_fetch"]:
        if (now - _claude_md_cache["last_fetch"]).seconds < 300:
            return _claude_md_cache["content"]
    try:
        if not GITHUB_TOKEN:
            return "[CLAUDE.md non disponibile: GitHub token mancante]"
        url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/CLAUDE.md"
        r = http_requests.get(url, headers=github_headers(), timeout=10)
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            _claude_md_cache["content"] = content
            _claude_md_cache["last_fetch"] = now
            return content
        else:
            return f"[CLAUDE.md non trovato: {r.status_code}]"
    except Exception as e:
        return f"[Errore CLAUDE.md: {e}]"

# VOICE TRANSCRIPTION
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
            "config": {"encoding": "OGG_OPUS", "sampleRateHertz": 48000, "languageCode": "it-IT", "alternativeLanguageCodes": ["en-US"], "model": "latest_long", "enableAutomaticPunctuation": True},
            "audio": {"content": audio_b64}
        }
        r = http_requests.post(url, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}, json=payload, timeout=30)
        if r.status_code == 200:
            result = r.json()
            if "results" in result:
                return " ".join(alt["transcript"] for res in result["results"] for alt in res.get("alternatives", [])[:1]).strip()
        return None
    except Exception as e:
        logger.error(f"[VOICE] {e}")
        return None

def github_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

# SYSTEM PROMPT
def build_system_prompt():
    claude_md = get_claude_md()
    db_context = get_db_context()
    return f"""Sei brAIn God v2.0 — il Dio dell'organismo brAIn.

RUOLO: Cervello tecnico e operativo. Gestisci codice, infrastruttura, deploy, costi, sicurezza.
Mirco e' il CEO — parli solo con lui via Telegram.

COME PARLI:
- SEMPRE italiano, diretto, tecnico quando serve.
- NON usare MAI Markdown: niente asterischi, grassetto, corsivo. Testo piano.
- UNA sola domanda alla volta.
- Zero fuffa. Vai al punto.
- Quando fai operazioni, spiega COSA e PERCHE'.
- REGOLA CRITICA: dopo OGNI operazione che fai, rispondi SEMPRE con questo formato:
  FATTO: [cosa hai fatto concretamente]
  RISULTATO: [ok oppure errore + dettaglio]
  PROSSIMO: [cosa fai ora]
- Mai dire "procedo" senza poi fare. Se dici che fai qualcosa, FALLO subito.
- Se un file e' troppo lungo da leggere, leggilo a pezzi oppure dillo subito e proponi alternativa.
- Non perdere il filo: se Mirco ti chiede di fare 5 cose, falle tutte in ordine e riporta su ognuna.

CAPACITA': leggere/scrivere codice GitHub, query Supabase, stato sistema, costi, proporre deploy.

SICUREZZA (hardcoded, non aggirabili): no eliminazione file/tabelle/container, solo directory autorizzate, max 5 deploy/giorno, STOP ferma tutto.

DEPLOY: usa request_deploy per preparare. Mirco conferma con "si"/"ok". Mai deploy senza approvazione.

DNA DELL'ORGANISMO:
{claude_md}

STATO SISTEMA:
{db_context}"""

# TOOLS
TOOLS = [
    {"name": "read_github_file", "description": "Legge un file dal repo GitHub brAIn-core.", "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Percorso file"}}, "required": ["path"]}},
    {"name": "list_github_dir", "description": "Lista file in una directory del repo.", "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Percorso directory"}}, "required": ["path"]}},
    {"name": "write_github_file", "description": "Scrive/aggiorna un file nel repo. Solo directory autorizzate.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "commit_message": {"type": "string"}}, "required": ["path", "content", "commit_message"]}},
    {"name": "query_supabase", "description": "Query SELECT su Supabase. Solo lettura.", "input_schema": {"type": "object", "properties": {"table": {"type": "string"}, "select": {"type": "string"}, "filters": {"type": "string"}, "order_by": {"type": "string"}, "order_desc": {"type": "boolean"}, "limit": {"type": "integer"}}, "required": ["table", "select"]}},
    {"name": "get_system_status", "description": "Stato completo: agenti, errori, costi, conteggi.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_cost_report", "description": "Report costi per agente e giorno.", "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "request_deploy", "description": "Prepara deploy su Cloud Run. Richiede approvazione Mirco.", "input_schema": {"type": "object", "properties": {"service_name": {"type": "string"}, "description": {"type": "string"}, "dockerfile_dir": {"type": "string"}}, "required": ["service_name", "description", "dockerfile_dir"]}},
]

def execute_tool(tool_name, tool_input):
    try:
        if tool_name == "read_github_file": return github_read_file(tool_input["path"])
        elif tool_name == "list_github_dir": return github_list_dir(tool_input["path"])
        elif tool_name == "write_github_file": return github_write_file(tool_input["path"], tool_input["content"], tool_input["commit_message"])
        elif tool_name == "query_supabase": return supabase_query(tool_input)
        elif tool_name == "get_system_status": return get_system_status()
        elif tool_name == "get_cost_report": return get_cost_report(tool_input.get("days", 7))
        elif tool_name == "request_deploy": return request_deploy(tool_input)
        else: return f"Tool sconosciuto: {tool_name}"
    except Exception as e:
        return f"ERRORE {tool_name}: {e}"

def github_read_file(path):
    if not GITHUB_TOKEN: return "ERRORE: GitHub token mancante."
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    r = http_requests.get(url, headers=github_headers(), timeout=10)
    if r.status_code == 200:
        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        return f"File: {path} ({len(content)} chars)\n\n{content}"
    elif r.status_code == 404: return f"File non trovato: {path}"
    else: return f"Errore GitHub {r.status_code}: {r.text[:200]}"

def github_list_dir(path):
    if not GITHUB_TOKEN: return "ERRORE: GitHub token mancante."
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    r = http_requests.get(url, headers=github_headers(), timeout=10)
    if r.status_code == 200:
        items = r.json()
        if isinstance(items, list):
            result = f"Directory: {path or '/'}\n\n"
            for item in items:
                icon = "dir" if item["type"] == "dir" else "file"
                size = f" ({item.get('size',0)}b)" if item["type"] == "file" else ""
                result += f"  [{icon}] {item['name']}{size}\n"
            return result
        return f"Non e' una directory: {path}"
    return f"Errore GitHub {r.status_code}: {r.text[:200]}"

def github_write_file(path, content, commit_message):
    global session_write_count
    if not is_path_allowed(path): return f"BLOCCATO (L2): path '{path}' non autorizzato."
    # Content safety check solo per file di codice, non per documentazione
    if not path.endswith((".md", ".txt", ".json", ".yml", ".yaml")):
        safe, cmd = is_content_safe(content)
        if not safe: return f"BLOCCATO (L2): comando proibito: '{cmd}'"
    if not check_write_limit(): return f"BLOCCATO (L2): limite scritture raggiunto"
    if not GITHUB_TOKEN: return "ERRORE: GitHub token mancante."
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    r = http_requests.get(url, headers=github_headers(), timeout=10)
    payload = {"message": f"[brAIn God] {commit_message}", "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"), "branch": "main"}
    if r.status_code == 200: payload["sha"] = r.json()["sha"]
    r = http_requests.put(url, headers=github_headers(), json=payload, timeout=15)
    if r.status_code in (200, 201):
        session_write_count += 1
        return f"OK: '{path}' scritto su GitHub. [{session_write_count}/{MAX_FILE_WRITES_PER_SESSION}]"
    return f"Errore GitHub {r.status_code}: {r.text[:300]}"

def supabase_query(params):
    table = params["table"]
    allowed = ["problems","solutions","agent_logs","org_knowledge","scan_sources","capability_log","org_config","solution_scores","agent_events","reevaluation_log","authorization_matrix"]
    if table not in allowed: return f"BLOCCATO: tabella '{table}' non accessibile."
    try:
        q = supabase.table(table).select(params["select"])
        filters_str = params.get("filters", "")
        if filters_str:
            for f in filters_str.split(","):
                f = f.strip()
                if ".gte=" in f:
                    col, val = f.split(".gte="); q = q.gte(col.strip(), val.strip())
                elif ".lte=" in f:
                    col, val = f.split(".lte="); q = q.lte(col.strip(), val.strip())
                elif "=" in f:
                    col, val = f.split("=", 1); q = q.eq(col.strip(), val.strip())
        if params.get("order_by"): q = q.order(params["order_by"], desc=params.get("order_desc", True))
        q = q.limit(min(params.get("limit", 20), 50))
        result = q.execute()
        return json.dumps(result.data, indent=2, default=str, ensure_ascii=False)[:3000] if result.data else "Nessun risultato."
    except Exception as e:
        return f"Errore query: {e}"

def get_system_status():
    try:
        status = {}
        problems = supabase.table("problems").select("id,status").execute()
        solutions = supabase.table("solutions").select("id").execute()
        status["problemi_totali"] = len(problems.data) if problems.data else 0
        status["problemi_approvati"] = len([p for p in (problems.data or []) if p.get("status") == "approved"])
        status["soluzioni_totali"] = len(solutions.data) if solutions.data else 0
        logs = supabase.table("agent_logs").select("agent_id,action,status,created_at").order("created_at", desc=True).limit(15).execute()
        if logs.data:
            agents = {}
            for l in logs.data:
                if l["agent_id"] not in agents: agents[l["agent_id"]] = {"ultima_azione": l["action"], "stato": l["status"], "quando": l["created_at"]}
            status["agenti"] = agents
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        costs = supabase.table("agent_logs").select("cost_usd").gte("created_at", yesterday).execute()
        status["costi_24h_usd"] = round(sum(float(c.get("cost_usd",0) or 0) for c in (costs.data or [])), 4)
        errors = supabase.table("agent_logs").select("agent_id,error,created_at").eq("status","error").order("created_at",desc=True).limit(3).execute()
        status["errori_recenti"] = errors.data or []
        return json.dumps(status, indent=2, default=str, ensure_ascii=False)
    except Exception as e:
        return f"Errore: {e}"

def get_cost_report(days=7):
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        logs = supabase.table("agent_logs").select("agent_id,cost_usd,tokens_input,tokens_output,model_used").gte("created_at", since).limit(500).execute()
        if not logs.data: return f"Nessun dato ultimi {days} giorni."
        by_agent = {}
        total = 0
        for l in logs.data:
            aid = l["agent_id"]; cost = float(l.get("cost_usd",0) or 0); total += cost
            if aid not in by_agent: by_agent[aid] = {"usd": 0, "calls": 0}
            by_agent[aid]["usd"] += cost; by_agent[aid]["calls"] += 1
        for a in by_agent: by_agent[a]["usd"] = round(by_agent[a]["usd"], 4)
        return json.dumps({"giorni": days, "totale_usd": round(total,4), "per_agente": by_agent}, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Errore: {e}"

def request_deploy(params):
    global pending_deploy
    service = params["service_name"]
    if service not in ["command-center","agents-runner","brain-god"]: return "Servizio non valido."
    if not check_deploy_limit(): return "BLOCCATO: limite deploy giornaliero."
    pending_deploy = {"service": service, "description": params["description"], "dockerfile_dir": params["dockerfile_dir"], "image": f"{ARTIFACT_REGISTRY}/{service}:latest"}
    return f"Deploy preparato: {service}. Motivo: {params['description']}. Chiedi conferma a Mirco."

def execute_pending_deploy():
    global pending_deploy, daily_deploy_count
    if not pending_deploy: return "Nessun deploy in attesa."
    service = pending_deploy["service"]
    image = pending_deploy["image"]
    dockerfile_dir = pending_deploy["dockerfile_dir"]
    try:
        token_r = http_requests.get("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token", headers={"Metadata-Flavor": "Google"}, timeout=5)
        if token_r.status_code != 200:
            pending_deploy = None
            return "Errore token GCP."
        access_token = token_r.json()["access_token"]
        build_url = f"https://cloudbuild.googleapis.com/v1/projects/{GCP_PROJECT}/locations/{GCP_REGION}/builds"
        build_payload = {"steps": [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "-t", image, "."], "dir": dockerfile_dir}, {"name": "gcr.io/cloud-builders/docker", "args": ["push", image]}], "source": {"repoSource": {"projectId": GCP_PROJECT, "repoName": "github_mircocerisola_brain-core", "branchName": "main"}}, "images": [image]}
        r = http_requests.post(build_url, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}, json=build_payload, timeout=30)
        if r.status_code not in (200, 201):
            pending_deploy = None
            return f"Build API non disponibile. Mirco esegui:\ncd C:\\brAIn\\{dockerfile_dir}\ngcloud builds submit --tag {image} --region={GCP_REGION}\ngcloud run deploy {service} --image={image} --region {GCP_REGION} --platform managed"
        daily_deploy_count += 1
        log_to_supabase("brain_god", "deploy", f"service={service}", "Build avviato", "none")
        pending_deploy = None
        return f"Build avviato per {service}. Deploy in corso..."
    except Exception as e:
        pending_deploy = None
        return f"Errore deploy: {e}"

def log_to_supabase(agent_id, action, input_summary, output_summary, model_used, tokens_in=0, tokens_out=0, cost=0, duration_ms=0, status="success", error=None):
    def _log():
        try:
            supabase.table("agent_logs").insert({"agent_id": agent_id, "action": action, "layer": 0, "input_summary": (input_summary or "")[:500], "output_summary": (output_summary or "")[:500], "model_used": model_used, "tokens_input": tokens_in, "tokens_output": tokens_out, "cost_usd": cost, "duration_ms": duration_ms, "status": status, "error": error}).execute()
        except Exception as e:
            logger.error(f"[LOG] {e}")
    threading.Thread(target=_log, daemon=True).start()

def get_db_context():
    ctx = ""
    try:
        p = supabase.table("problems").select("id,title,weighted_score,status").order("weighted_score", desc=True).limit(5).execute()
        if p.data:
            ctx += f"\nProblemi ({len(p.data)}+):\n"
            for x in p.data: ctx += f"  [{x['id']}] {x['title']} score:{x.get('weighted_score','?')} {x.get('status','?')}\n"
        s = supabase.table("solutions").select("id,title,status").limit(5).execute()
        if s.data:
            ctx += f"\nSoluzioni ({len(s.data)}+):\n"
            for x in s.data: ctx += f"  [{x['id']}] {x['title']} {x.get('status','?')}\n"
        e = supabase.table("agent_logs").select("agent_id,error,created_at").eq("status","error").order("created_at",desc=True).limit(3).execute()
        if e.data:
            ctx += "\nErrori:\n"
            for x in e.data: ctx += f"  {x['agent_id']}: {(x.get('error','?') or '?')[:80]}\n"
    except Exception as ex:
        ctx += f"\n[Errore context: {ex}]\n"
    return ctx

def ask_claude(user_message, is_photo=False, image_b64=None):
    global chat_history
    start = time.time()
    model = "claude-opus-4-6"
    try:
        system = build_system_prompt()
        messages = []
        for h in chat_history:
            messages.append({"role": "user", "content": h["user"]})
            messages.append({"role": "assistant", "content": h["assistant"]})
        if is_photo and image_b64:
            messages.append({"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}, {"type": "text", "text": user_message}]})
        else:
            messages.append({"role": "user", "content": user_message})
        if user_message.strip().upper() == "STOP":
            return "STOP ricevuto. Tutto fermo."
        total_in = 0; total_out = 0; final = ""
        for _ in range(8):
            resp = claude.messages.create(model=model, max_tokens=8000, system=system, messages=messages, tools=TOOLS)
            total_in += resp.usage.input_tokens; total_out += resp.usage.output_tokens
            if resp.stop_reason == "end_turn":
                for b in resp.content:
                    if hasattr(b, "text"): final += b.text
                break
            elif resp.stop_reason == "tool_use":
                results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        logger.info(f"[TOOL] {b.name}")
                        r = execute_tool(b.name, b.input)
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(r)[:15000]})
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({"role": "user", "content": results})
            else:
                for b in resp.content:
                    if hasattr(b, "text"): final += b.text
                break
        dur = int((time.time()-start)*1000)
        cost = (total_in*15.0+total_out*75.0)/1_000_000
        chat_history.append({"user": f"[FOTO] {user_message}" if is_photo else user_message, "assistant": final[:2000]})
        if len(chat_history) > MAX_HISTORY: chat_history = chat_history[-MAX_HISTORY:]
        log_to_supabase("brain_god","chat",user_message[:300],final[:300],model,total_in,total_out,cost,dur)
        return final or "Operazione completata."
    except Exception as e:
        log_to_supabase("brain_god","chat",user_message[:300],None,model,duration_ms=int((time.time()-start)*1000),status="error",error=str(e))
        return f"Errore: {e}"

# TELEGRAM
tg_app = None

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTHORIZED_USER_ID
    AUTHORIZED_USER_ID = update.effective_user.id
    try: supabase.table("org_config").upsert({"key":"god_telegram_user_id","value":json.dumps(AUTHORIZED_USER_ID)}, on_conflict="key").execute()
    except: pass
    await update.message.reply_text("brAIn God v2.0 attivo. Opus 4.6. Vocali OK. Chiedimi qualsiasi cosa.")
    log_to_supabase("brain_god","start",f"uid={AUTHORIZED_USER_ID}","v2.0","none")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    msg = update.message.text
    global pending_deploy
    if pending_deploy and msg.strip().lower() in ["si","sì","ok","yes","conferma","vai","go"]:
        await update.message.chat.send_action("typing")
        r = execute_pending_deploy()
        await update.message.reply_text(clean_reply(r)); return
    if pending_deploy and msg.strip().lower() in ["no","annulla","stop","cancel"]:
        pending_deploy = None
        await update.message.reply_text("Deploy annullato."); return
    await update.message.chat.send_action("typing")
    reply = clean_reply(ask_claude(msg))
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i+4000])

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    await update.message.chat.send_action("typing")
    photo = update.message.photo[-1]
    f = await context.bot.get_file(photo.file_id)
    img = await f.download_as_bytearray()
    b64 = base64.b64encode(bytes(img)).decode("utf-8")
    caption = update.message.caption or "Analizza questa immagine."
    reply = clean_reply(ask_claude(caption, True, b64))
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i+4000])

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    await update.message.chat.send_action("typing")
    try:
        f = await context.bot.get_file(update.message.voice.file_id)
        audio = await f.download_as_bytearray()
        text = transcribe_voice(bytes(audio))
        if not text:
            await update.message.reply_text("Non ho capito il vocale. Ripeti o scrivi?"); return
        await update.message.reply_text(f'Ho capito: "{text}"')
        await update.message.chat.send_action("typing")
        reply = clean_reply(ask_claude(text))
        for i in range(0, len(reply), 4000):
            await update.message.reply_text(reply[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"Errore vocale: {e}")

def clean_reply(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'```[\w]*\n?', '', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text.strip()

def is_authorized(update):
    global AUTHORIZED_USER_ID
    if AUTHORIZED_USER_ID is None:
        try:
            r = supabase.table("org_config").select("value").eq("key","god_telegram_user_id").execute()
            if r.data: AUTHORIZED_USER_ID = json.loads(r.data[0]["value"])
        except: pass
    if AUTHORIZED_USER_ID is None: return True
    return update.effective_user.id == AUTHORIZED_USER_ID

async def health_check(request):
    return web.Response(text="brAIn God v2.0 OK", status=200)

async def telegram_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logger.error(f"[WEBHOOK] {e}")
    return web.Response(text="OK", status=200)

async def main():
    global tg_app
    logger.info("brAIn God v2.0 — Opus 4.6 + Voice + CLAUDE.md + Auto-Deploy")
    tg_app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    tg_app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await tg_app.initialize()
    await tg_app.start()
    if WEBHOOK_URL:
        await tg_app.bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook: {WEBHOOK_URL}")
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/", telegram_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Running on :{PORT}")
    try:
        while True: await asyncio.sleep(3600)
    except: pass
    finally:
        await tg_app.stop(); await tg_app.shutdown(); await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
