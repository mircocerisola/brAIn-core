"""
brAIn Spec Generator
Genera SPEC.md a 10 sezioni per un progetto MVP.
Ottimizzato per AI coding agents (Claude Code).
"""

import os
import re
import json
import time
import base64
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic
import requests
from supabase import create_client

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API = "https://api.github.com"

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8000


def _search_perplexity(query):
    try:
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"},
            json={"model": "sonar", "messages": [{"role": "user", "content": query}], "max_tokens": 500},
            timeout=30,
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"[SPEC] Perplexity error: {e}")
    return None


def _github_api(method, repo, endpoint, data=None):
    """GitHub API su un repo specifico."""
    if not GITHUB_TOKEN:
        return None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"{GITHUB_API}/repos/{repo}{endpoint}"
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
        logger.warning(f"[GITHUB] {method} {repo}{endpoint} -> {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"[GITHUB] {e}")
        return None


def _commit_file_to_repo(repo, path, content, message):
    """Committa un file su GitHub (crea o aggiorna)."""
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    existing = _github_api("GET", repo, f"/contents/{path}")
    data = {"message": message, "content": content_b64}
    if existing and "sha" in existing:
        data["sha"] = existing["sha"]
    result = _github_api("PUT", repo, f"/contents/{path}", data)
    return result is not None


def _log(action, input_summary, output_summary, model, tokens_in, tokens_out, cost, duration_ms, status="success", error=None):
    try:
        supabase.table("agent_logs").insert({
            "agent_id": "spec_generator",
            "action": action,
            "layer": 3,
            "input_summary": (input_summary or "")[:500],
            "output_summary": (output_summary or "")[:500],
            "model_used": model,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "status": status,
            "error": error,
        }).execute()
    except Exception as e:
        logger.error(f"[LOG] {e}")


SPEC_SYSTEM_PROMPT = """Sei l'Architect di brAIn, un'organizzazione AI-native che costruisce prodotti con marginalita' alta.
Genera un SPEC.md COMPLETO e OTTIMIZZATO PER AI CODING AGENTS (Claude Code).

REGOLE:
- Ogni fase di build: task ATOMICI con comandi copy-paste pronti
- Nessuna ambiguita' tecnica: endpoint, schemi DB, variabili d'ambiente tutti espliciti
- Stack: Python + Supabase + Google Cloud Run (sempre, salvo eccezioni giustificate)
- Costo infrastruttura target: < 50 EUR/mese
- Deploy target: Google Cloud Run europe-west3, Container Docker

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


def run(project_id):
    """Genera SPEC.md per il progetto. Salva in DB e committa su GitHub."""
    start = time.time()
    logger.info(f"[SPEC] Avvio per project_id={project_id}")

    # 1. Carica dati dal DB
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            logger.error(f"[SPEC] Progetto {project_id} non trovato")
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
        solution_id = project.get("bos_id")
        slug = project.get("slug", "")
        github_repo = project.get("github_repo", "")
    except Exception as e:
        logger.error(f"[SPEC] DB load error: {e}")
        return {"status": "error", "error": str(e)}

    # Soluzione
    solution = {}
    problem = {}
    feasibility_details = ""
    bos_score = project.get("bos_score", 0) or 0

    if solution_id:
        try:
            sol = supabase.table("solutions").select("*").eq("id", solution_id).execute()
            if sol.data:
                solution = sol.data[0]
                problem_id = solution.get("problem_id")
                if problem_id:
                    prob = supabase.table("problems").select("*").eq("id", problem_id).execute()
                    if prob.data:
                        problem = prob.data[0]
        except Exception as e:
            logger.warning(f"[SPEC] Solution/problem load: {e}")

    # Feasibility details
    try:
        fe = supabase.table("solution_scores").select("*").eq("solution_id", solution_id).execute()
        if fe.data:
            feasibility_details = json.dumps(fe.data[0], indent=2)
    except:
        pass

    # 2. Ricerca competitor via Perplexity
    sol_title = solution.get("title", project.get("name", "MVP"))
    prob_title = problem.get("title", "")
    competitor_query = f"competitor analysis for '{sol_title}' solving '{prob_title}' — top solutions, pricing, market size 2026"
    competitor_info = _search_perplexity(competitor_query) or "Dati competitivi non disponibili."

    # 3. Costruisci prompt utente
    user_prompt = f"""Genera il SPEC.md per questo progetto:

NOME PROGETTO: {project.get("name", sol_title)}
SLUG: {slug}

PROBLEMA:
Titolo: {prob_title}
Descrizione: {problem.get("description", "")[:600]}
Target: {problem.get("target_customer", "")}
Geografia: {problem.get("target_geography", "")}
Urgency: {problem.get("urgency", "")}
Affected population: {problem.get("affected_population", "")}

SOLUZIONE BOS (score: {bos_score:.2f}/1):
Titolo: {sol_title}
Descrizione: {solution.get("description", "")[:600]}
Settore: {solution.get("sector", "")} / {solution.get("sub_sector", "")}

ANALISI COMPETITIVA (Perplexity):
{competitor_info[:800]}

FEASIBILITY DETAILS:
{feasibility_details[:500] if feasibility_details else "Non disponibile"}

Genera il SPEC.md completo seguendo esattamente la struttura richiesta."""

    # 4. Chiama Claude Sonnet
    tokens_in = tokens_out = 0
    spec_md = ""
    try:
        response = claude.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SPEC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        spec_md = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[SPEC] Claude error: {e}")
        _log("spec_generate", f"project={project_id}", str(e), MODEL, 0, 0, 0,
             int((time.time() - start) * 1000), "error", str(e))
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    # 5. Estrai JSON dal blocco spec
    stack = []
    kpis = {}
    try:
        match = re.search(r'<!-- JSON_SPEC:\s*(.*?)\s*:JSON_SPEC_END -->', spec_md, re.DOTALL)
        if match:
            spec_meta = json.loads(match.group(1))
            stack = spec_meta.get("stack", [])
            kpis = spec_meta.get("kpis", {})
    except Exception as e:
        logger.warning(f"[SPEC] JSON extraction error: {e}")

    # 6. Salva in DB
    try:
        supabase.table("projects").update({
            "spec_md": spec_md,
            "stack": json.dumps(stack) if stack else None,
            "kpis": json.dumps(kpis) if kpis else None,
            "status": "spec_generated",
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[SPEC] DB update error: {e}")

    # 7. Commit SPEC.md su GitHub
    if github_repo:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        committed = _commit_file_to_repo(
            github_repo,
            "SPEC.md",
            spec_md,
            f"feat: SPEC.md generato da brAIn Spec Generator — {ts}",
        )
        if committed:
            logger.info(f"[SPEC] SPEC.md committato su {github_repo}")
        else:
            logger.warning(f"[SPEC] Commit fallito su {github_repo}")

    duration_ms = int((time.time() - start) * 1000)
    _log("spec_generate", f"project={project_id} solution={solution_id}",
         f"SPEC generato {len(spec_md)} chars, stack={stack}", MODEL,
         tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[SPEC] Completato project={project_id} in {duration_ms}ms, {len(spec_md)} chars")
    return {
        "status": "ok",
        "project_id": project_id,
        "spec_length": len(spec_md),
        "stack": stack,
        "kpis": kpis,
        "cost_usd": round(cost, 5),
    }


if __name__ == "__main__":
    import sys
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    result = run(pid)
    print(json.dumps(result, indent=2, ensure_ascii=False))
