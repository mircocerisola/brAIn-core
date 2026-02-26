"""
brAIn module: execution/project.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import os, json, time, re, uuid, base64
from datetime import datetime, timezone, timedelta
import requests

GITHUB_API_BASE_AR = "https://api.github.com"
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, SUPABASE_ACCESS_TOKEN, DB_PASSWORD, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json


def _github_project_api(method, repo, endpoint, data=None):
    """GitHub API per un repo di progetto specifico."""
    if not GITHUB_TOKEN:
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"{GITHUB_API_BASE_AR}/repos/{repo}{endpoint}"
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=30)
        elif method == "PUT":
            r = requests.put(url, headers=headers, json=data, timeout=30)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=data, timeout=30)
        else:
            return None
        if r.status_code in (200, 201):
            return r.json()
        logger.warning(f"[GITHUB_AR] {method} {repo}{endpoint} -> {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"[GITHUB_AR] {e}")
        return None


def _commit_to_project_repo(repo, path, content, message):
    """Committa un file (crea o aggiorna) su un repo di progetto."""
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    existing = _github_project_api("GET", repo, f"/contents/{path}")
    data = {"message": message, "content": content_b64}
    if existing and "sha" in existing:
        data["sha"] = existing["sha"]
    result = _github_project_api("PUT", repo, f"/contents/{path}", data)
    return result is not None


def _create_github_repo(slug, name):
    """Crea repo privato brain-[slug] tramite GitHub API."""
    if not GITHUB_TOKEN:
        logger.warning("[INIT] GITHUB_TOKEN non disponibile")
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    try:
        r = requests.post(
            f"{GITHUB_API_BASE_AR}/user/repos",
            headers=headers,
            json={
                "name": f"brain-{slug}",
                "private": True,
                "description": f"brAIn Project: {name}",
                "auto_init": True,
            },
            timeout=30,
        )
        if r.status_code in (200, 201):
            data = r.json()
            return data.get("full_name")
        logger.warning(f"[INIT] GitHub repo creation -> {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"[INIT] GitHub repo error: {e}")
        return None


def _get_telegram_group_id():
    """Legge telegram_group_id da org_config. Gestisce sia string che int (jsonb)."""
    try:
        r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
        if r.data:
            val = r.data[0]["value"]
            if isinstance(val, (int, float)):
                return int(val)
            return json.loads(str(val))
    except Exception as e:
        logger.warning(f"[GROUP_ID] {e}")
    return None


def _create_forum_topic(group_id, name):
    """Crea Forum Topic nel gruppo Telegram. Ritorna topic_id."""
    if not TELEGRAM_BOT_TOKEN or not group_id:
        return None
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/createForumTopic",
            json={"chat_id": group_id, "name": f"\U0001f3d7\ufe0f Cantiere {name}", "icon_color": 7322096},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("result", {}).get("message_thread_id")
        logger.warning(f"[INIT] createForumTopic -> {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[INIT] Forum topic error: {e}")
    return None


_send_topic_cache: dict = {}   # (group_id, topic_id, hash) → timestamp
_SEND_DEDUP_TTL = 60


def _send_to_topic(group_id, topic_id, text, reply_markup=None):
    """Invia messaggio nel Forum Topic del progetto. Dedup 60s su stesso contenuto."""
    if not TELEGRAM_BOT_TOKEN:
        return
    # Dedup: skip se stesso contenuto mandato negli ultimi 60s
    import hashlib as _hashlib, time as _time
    h = _hashlib.md5((text or "")[:500].encode()).hexdigest()
    _key = (group_id, topic_id, h)
    _now = _time.time()
    if _now - _send_topic_cache.get(_key, 0) < _SEND_DEDUP_TTL and not reply_markup:
        logger.debug(f"[DEDUP] skip topic={topic_id}")
        return
    _send_topic_cache[_key] = _now
    if len(_send_topic_cache) > 300:
        _old = [k for k, t in _send_topic_cache.items() if _now - t > _SEND_DEDUP_TTL * 3]
        for k in _old:
            _send_topic_cache.pop(k, None)

    # Fallback a DM se group non configurato
    chat_id = group_id if group_id else get_telegram_chat_id()
    payload = {"chat_id": chat_id, "text": text}
    if group_id and topic_id:
        payload["message_thread_id"] = topic_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception as e:
        logger.error(f"[TG_TOPIC] {e}")


# Mappa tipo notifica → chiave org_config del topic Chief competente
_NOTIF_TYPE_TO_CHIEF_KEY = {
    "bos": "chief_topic_cso",
    "spec": "chief_topic_coo",      # COO territory (post-GO)
    "legal": "chief_topic_clo",
    "smoke": "chief_topic_cso",      # CSO territory (pre-GO)
    "build": "chief_topic_coo",
    "finance": "chief_topic_cfo",
    "security": "chief_topic_cto",
    "general": "chief_topic_coo",
}


def get_chief_topic_id(notif_type="general"):
    """Ritorna topic_id del topic Chief competente per questo tipo di notifica."""
    cfg_key = _NOTIF_TYPE_TO_CHIEF_KEY.get(notif_type, "chief_topic_coo")
    try:
        r = supabase.table("org_config").select("value").eq("key", cfg_key).execute()
        if r.data:
            val = r.data[0]["value"]
            if isinstance(val, (int, float)):
                return int(val)
            v = str(val).strip()
            return int(v) if v.lstrip("-").isdigit() else None
    except Exception as e:
        logger.warning(f"[CHIEF_TOPIC] {e}")
    return None


def send_project_notification(project_id, message, buttons=None, notif_type="general"):
    """Invia notifica nel topic cantiere (se esiste) o nel topic Chief competente. Mai chat diretta."""
    group_id = _get_telegram_group_id()
    if not group_id:
        return
    topic_id = None
    # 1. Topic cantiere del progetto (se già creato)
    if project_id:
        try:
            r = supabase.table("projects").select("topic_id").eq("id", project_id).execute()
            if r.data:
                topic_id = r.data[0].get("topic_id")
        except Exception:
            pass
    # 2. Fallback: topic Chief competente
    if not topic_id:
        topic_id = get_chief_topic_id(notif_type)
    if topic_id:
        _send_to_topic(group_id, topic_id, message, reply_markup=buttons)
    else:
        logger.warning(f"[NOTIF] Nessun topic per project_id={project_id} notif_type={notif_type}")


def _delete_forum_topic(group_id, topic_id):
    """Elimina un Forum Topic dal gruppo Telegram. Ritorna True se OK."""
    if not TELEGRAM_BOT_TOKEN or not group_id or not topic_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteForumTopic",
            json={"chat_id": group_id, "message_thread_id": topic_id},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"[DELETE_TOPIC] {e}")
        return False


def _slugify(text, max_len=20):
    """Genera slug da testo: lowercase, trattini, max_len chars."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len].rstrip("-")


# ---- SUPABASE MANAGEMENT API + GCP SECRET MANAGER ----

def _create_supabase_project(slug):
    """Crea un progetto Supabase via Management API. Best-effort, ritorna (db_url, db_key) o (None, None)."""
    if not SUPABASE_ACCESS_TOKEN:
        logger.warning("[SUPABASE_MGMT] SUPABASE_ACCESS_TOKEN mancante, skip creazione DB separato")
        return None, None
    try:
        resp = requests.post(
            "https://api.supabase.com/v1/projects",
            headers={
                "Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "name": f"brain-{slug}",
                "organization_id": os.getenv("SUPABASE_ORG_ID", ""),
                "plan": "free",
                "region": "eu-central-1",
                "db_pass": _generate_db_pass(),
            },
            timeout=60,
        )
        if resp.status_code not in (200, 201):
            logger.warning(f"[SUPABASE_MGMT] Creazione fallita {resp.status_code}: {resp.text[:200]}")
            return None, None
        data = resp.json()
        project_ref = data.get("id", "")
        db_url = f"postgresql://postgres@db.{project_ref}.supabase.co:5432/postgres"
        # Recupera API key (anon)
        keys_resp = requests.get(
            f"https://api.supabase.com/v1/projects/{project_ref}/api-keys",
            headers={"Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}"},
            timeout=30,
        )
        anon_key = ""
        if keys_resp.status_code == 200:
            for k in keys_resp.json():
                if k.get("name") == "anon":
                    anon_key = k.get("api_key", "")
                    break
        logger.info(f"[SUPABASE_MGMT] Creato brain-{slug} ref={project_ref}")
        return db_url, anon_key
    except Exception as e:
        logger.warning(f"[SUPABASE_MGMT] {e}")
        return None, None


def _generate_db_pass():
    import secrets, string
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(24))


def _save_gcp_secret(secret_id, value):
    """Salva valore in GCP Secret Manager via REST API. Best-effort."""
    project_num = os.getenv("GCP_PROJECT_NUMBER", "402184600300")
    project_id_gcp = "brain-core-487914"
    # Usa metadata token su Cloud Run
    try:
        meta = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        access_token = meta.json().get("access_token", "")
    except Exception:
        logger.warning(f"[GCP_SECRET] Impossibile ottenere metadata token (locale?)")
        return False
    if not access_token:
        return False
    base = f"https://secretmanager.googleapis.com/v1/projects/{project_id_gcp}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    import base64
    encoded = base64.b64encode(value.encode()).decode()
    # Crea secret se non esiste
    try:
        requests.post(
            f"{base}/secrets",
            headers=headers,
            json={"replication": {"automatic": {}}},
            params={"secretId": secret_id},
            timeout=15,
        )
    except Exception:
        pass
    # Aggiungi versione
    try:
        resp = requests.post(
            f"{base}/secrets/{secret_id}:addVersion",
            headers=headers,
            json={"payload": {"data": encoded}},
            timeout=15,
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"[GCP_SECRET] {e}")
        return False


def get_project_db(project_id):
    """Ritorna connessione psycopg2 al DB separato del progetto, o None."""
    try:
        import psycopg2
        proj = supabase.table("projects").select("db_url,db_key_secret_name").eq("id", project_id).execute()
        if not proj.data:
            return None
        project = proj.data[0]
        db_url = project.get("db_url")
        if not db_url:
            return None
        secret_name = project.get("db_key_secret_name", "")
        # Recupera password da Secret Manager
        db_pass = ""
        if secret_name:
            project_id_gcp = "brain-core-487914"
            try:
                meta = requests.get(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                    headers={"Metadata-Flavor": "Google"}, timeout=5,
                )
                access_token = meta.json().get("access_token", "")
                if access_token:
                    resp = requests.get(
                        f"https://secretmanager.googleapis.com/v1/projects/{project_id_gcp}/secrets/{secret_name}/versions/latest:access",
                        headers={"Authorization": f"Bearer {access_token}"}, timeout=10,
                    )
                    if resp.status_code == 200:
                        import base64
                        db_pass = base64.b64decode(resp.json()["payload"]["data"]).decode()
            except Exception as e:
                logger.warning(f"[GET_PROJECT_DB] secret fetch: {e}")
        conn = psycopg2.connect(db_url.replace("postgresql://postgres@", f"postgresql://postgres:{db_pass}@"),
                                sslmode="require")
        return conn
    except Exception as e:
        logger.warning(f"[GET_PROJECT_DB] {e}")
        return None


# ---- SPEC GENERATOR (inlined) ----

SPEC_SYSTEM_PROMPT_AR = """Sei l'Architect di brAIn, un'organizzazione AI-native che costruisce prodotti con marginalita' alta.
Genera un SPEC.md COMPLETO e OTTIMIZZATO PER AI CODING AGENTS (Claude Code).

REGOLE:
- Ogni fase di build: task ATOMICI con comandi copy-paste pronti
- Nessuna ambiguita' tecnica: endpoint, schemi DB, variabili d'ambiente tutti espliciti
- Stack: Python + Supabase + Google Cloud Run (sempre, salvo eccezioni giustificate)
- Costo infrastruttura target: < 50 EUR/mese
- Deploy target: Google Cloud Run europe-west3, Container Docker
- Modelli LLM: usa SEMPRE Claude API (claude-haiku-4-5-20251001 per task veloci, claude-sonnet-4-6 per task complessi). NON usare mai GPT, OpenAI, Gemini o altri provider LLM.

STRUTTURA OBBLIGATORIA (usa esattamente questi header):

## 1. Sintesi del Progetto
Una frase: cosa fa, per chi, perche' vale.

## 2. Problema e Target Customer
Target specifico (professione + eta' + contesto), geografia, frequenza problema, pain intensity.

## 3. Soluzione Proposta
Funzionalita' core MVP (massimo 3), value proposition in 2 righe, differenziatore chiave.

## 4. Analisi Competitiva e Differenziazione
Top 3 competitor con pricing, nostro vantaggio competitivo concreto.

## 5. Architettura Tecnica e Stack
Diagramma testuale del flusso dati, componenti, API utilizzate, schema DB (tabelle + colonne principali).

## 6. KPI e Metriche di Successo
KPI primario (con target settimana 4 e settimana 12), revenue target mese 3, criteri SCALE/PIVOT/KILL.

## 7. Variabili d'Ambiente Necessarie
Lista completa ENV VAR con descrizione (una per riga, formato KEY=descrizione).

## 8. Fasi di Build MVP
Fase 1: Setup repo e struttura base
Fase 2: Core logic [nome funzionalita']
Fase 3: Interfaccia utente / API
Fase 4: Test, deploy Cloud Run, monitoraggio
Ogni fase: lista task atomici, tempo stimato, comandi chiave.

## 9. Go-To-Market — Primo Cliente
Come acquisire il primo cliente pagante in 14 giorni. Canale specifico, messaggio, pricing iniziale.

## 10. Roadmap Post-MVP
3 iterazioni successive (settimane 4, 8, 12) con funzionalita' e ricavi target.

DOPO LA SEZIONE 10, includi OBBLIGATORIAMENTE questo blocco (NON omettere, NON modificare i marker):

<!-- JSON_SPEC:
{
  "stack": ["elenco", "tecnologie", "usate"],
  "kpis": {
    "primary": "nome KPI principale",
    "target_week4": 0,
    "target_week12": 0,
    "revenue_target_month3_eur": 0
  },
  "mvp_build_time_days": 0,
  "mvp_cost_eur": 0
}
:JSON_SPEC_END -->"""


SPEC_HUMAN_SYSTEM_PROMPT = """Sei un consulente di business che spiega un progetto in modo chiaro a un imprenditore.
Dato un SPEC tecnico, genera una versione leggibile per Mirco (CEO, non tecnico profondo).

FORMATO OBBLIGATORIO (testo piano, NO markdown asterischi, max 900 caratteri totali):

[NOME PROGETTO]
━━━━━━━━━━━━━━━
Problema: [1 riga, cosa risolve]
Target: [chi sono i clienti, 1 riga]
Soluzione: [cosa fa concretamente, 1-2 righe]
━━━━━━━━━━━━━━━
Revenue model: [come guadagna]
Primo cliente: [come acquisire in 14gg, 1 riga]
KPI principale: [metrica + target settimana 4]
━━━━━━━━━━━━━━━
Build: [N giorni] | Costo infra: EUR [N]/mese
Rischio principale: [1 riga onesta]
━━━━━━━━━━━━━━━

Risposta SOLO con questo formato, niente altro, niente introduzioni."""


