"""
brAIn module: execution/builder.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import os, json, time, re, uuid
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, SUPABASE_ACCESS_TOKEN, DB_PASSWORD, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json
from execution.project import (_github_project_api, _commit_to_project_repo,
    _get_telegram_group_id, _create_forum_topic, _send_to_topic, _slugify,
    _create_supabase_project, get_project_db)


def run_spec_generator(project_id):
    """Genera SPEC.md per il progetto.
    Legge TUTTI i dati ESCLUSIVAMENTE da Supabase (projects + solutions + problems).
    NON usa sessioni Telegram, contesti conversazionali o dati esterni alla DB.
    Fail esplicito se bos_id o solution mancano — mai generare da dati vuoti.
    """
    start = time.time()
    logger.info(f"[SPEC] Avvio per project_id={project_id}")

    # 1. Carica progetto da DB
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            logger.error(f"[SPEC] Project {project_id} non trovato in DB")
            return {"status": "error", "error": f"project {project_id} not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    logger.info(f"[SPEC] Generando SPEC per progetto: {project.get('name', 'N/A')!r} (id={project_id})")

    solution_id = project.get("bos_id")
    github_repo = project.get("github_repo", "")
    bos_score = float(project.get("bos_score") or 0)

    # 2. VALIDAZIONE ESPLICITA: bos_id obbligatorio
    if not solution_id:
        err = (f"project {project_id} ha bos_id=NULL — "
               "impossibile generare SPEC senza BOS associato dal database")
        logger.error(f"[SPEC] {err}")
        return {"status": "error", "error": err}

    # 3. Carica soluzione BOS da DB — UNICA fonte di verità
    try:
        sol = supabase.table("solutions").select("*").eq("id", int(solution_id)).execute()
        if not sol.data:
            err = f"solution {solution_id} non trovata in DB (bos_id di project {project_id})"
            logger.error(f"[SPEC] {err}")
            return {"status": "error", "error": err}
        solution = sol.data[0]
    except Exception as e:
        return {"status": "error", "error": f"solution load error: {e}"}

    logger.info(f"[SPEC] Solution caricata: id={solution_id} title={solution.get('title','')[:60]!r}")

    # 4. Carica problema associato dalla DB (se problem_id disponibile)
    problem = {}
    problem_id = solution.get("problem_id")
    if problem_id:
        try:
            prob = supabase.table("problems").select("*").eq("id", int(problem_id)).execute()
            if prob.data:
                problem = prob.data[0]
                logger.info(f"[SPEC] Problem caricato: id={problem_id} title={problem.get('title','')[:60]!r}")
            else:
                logger.warning(f"[SPEC] Problem {problem_id} non trovato in DB — sezione PROBLEMA limitata")
        except Exception as e:
            logger.warning(f"[SPEC] Problem load error: {e}")
    else:
        logger.warning(f"[SPEC] Solution {solution_id} ha problem_id=NULL — sezione PROBLEMA derivata dalla soluzione")

    # 5. Carica feasibility scores dalla DB
    feasibility_details = ""
    try:
        fe = supabase.table("solution_scores").select("*").eq("solution_id", int(solution_id)).execute()
        if fe.data:
            feasibility_details = json.dumps(fe.data[0], default=str)[:600]
            logger.info(f"[SPEC] Feasibility scores caricati per solution {solution_id}")
    except Exception as e:
        logger.warning(f"[SPEC] Feasibility load: {e}")

    # 6. Estrai campi — SOLO da DB, nessun fallback a contesti esterni
    sol_title       = solution.get("title") or project.get("name") or "MVP"
    sol_description = solution.get("description") or ""
    sol_sector      = solution.get("sector") or ""
    sol_sub_sector  = solution.get("sub_sector") or ""
    sol_market      = str(solution.get("market_analysis") or "")[:400]
    sol_feasibility = float(solution.get("feasibility_score") or bos_score)
    sol_revenue     = solution.get("revenue_model") or ""
    sol_advantage   = solution.get("competitive_advantage") or ""
    sol_target      = solution.get("target_customer") or ""

    prob_title       = problem.get("title") or ""
    prob_description = problem.get("description") or ""
    prob_target      = problem.get("target_customer") or sol_target
    prob_geography   = problem.get("target_geography") or ""
    prob_urgency     = str(problem.get("urgency") or "")
    prob_evidence    = (problem.get("evidence") or "")[:300]
    prob_why_now     = (problem.get("why_now") or "")[:300]

    logger.info(
        f"[SPEC] Dati pronti per Claude: sol={sol_title!r:.60} prob={prob_title!r:.40} "
        f"has_problem={bool(problem)} has_feasibility={bool(feasibility_details)}"
    )

    # 7. Ricerca competitiva via Perplexity (unico dato esterno al DB)
    competitor_query = (f"competitor analysis '{sol_title}' settore '{sol_sector}' "
                        f"— top solutions, pricing, market size 2026")
    competitor_info = search_perplexity(competitor_query) or "Dati competitivi non disponibili."

    # 8. User prompt — SOLO dati da DB, nessun contesto sessione
    user_prompt = f"""Genera il SPEC.md per questo progetto.
FONTE DATI: record Supabase — solutions.id={solution_id}, problems.id={problem_id or 'non collegato'}.
NON inventare dati non presenti qui sotto. Se un campo mostra "(non disponibile)", derivalo logicamente dalla descrizione della soluzione.

=== PROGETTO ===
Nome: {project.get("name") or sol_title}
Slug: {project.get("slug") or ""}
BOS score: {bos_score:.2f}/1.00

=== SOLUZIONE BOS (id={solution_id}) ===
Titolo: {sol_title}
Descrizione: {sol_description[:800]}
Settore: {sol_sector} / {sol_sub_sector}
Target customer: {sol_target or "(vedi problema)"}
Revenue model: {sol_revenue or "da definire in base al settore"}
Vantaggio competitivo: {sol_advantage or "(non disponibile)"}
Market analysis: {sol_market or "(non disponibile)"}
Feasibility score: {sol_feasibility:.2f}/1.00

=== PROBLEMA ORIGINALE (id={problem_id or "non collegato"}) ===
Titolo: {prob_title or "(non disponibile — deriva dalla soluzione)"}
Descrizione: {prob_description[:600] or "(non disponibile)"}
Target: {prob_target or "(non disponibile)"}
Geografia: {prob_geography or "(non disponibile)"}
Urgency score: {prob_urgency or "(non disponibile)"}
Evidence: {prob_evidence or "(non disponibile)"}
Why now: {prob_why_now or "(non disponibile)"}

=== ANALISI COMPETITIVA (Perplexity) ===
{competitor_info[:800]}

=== FEASIBILITY DETAILS ===
{feasibility_details or "Non disponibile"}

Genera il SPEC.md completo seguendo esattamente la struttura richiesta."""

    tokens_in = tokens_out = 0
    spec_md = ""
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            system=SPEC_SYSTEM_PROMPT_AR,
            messages=[{"role": "user", "content": user_prompt}],
        )
        spec_md = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[SPEC] Claude error: {e}")
        log_to_supabase("spec_generator", "spec_generate", 3, f"project={project_id}", str(e),
                        "claude-sonnet-4-6", 0, 0, 0, int((time.time() - start) * 1000), "error", str(e))
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    stack = []
    kpis = {}
    try:
        match = _re.search(r'<!-- JSON_SPEC:\s*(.*?)\s*:JSON_SPEC_END -->', spec_md, _re.DOTALL)
        if match:
            spec_meta = json.loads(match.group(1))
            stack = spec_meta.get("stack", [])
            kpis = spec_meta.get("kpis", {})
            if spec_meta.get("mvp_build_time_days"):
                kpis["mvp_build_time_days"] = spec_meta["mvp_build_time_days"]
            if spec_meta.get("mvp_cost_eur"):
                kpis["mvp_cost_eur"] = spec_meta["mvp_cost_eur"]
    except Exception as e:
        logger.warning(f"[SPEC] JSON extraction error: {e}")

    # MACRO-TASK 4: genera SPEC_HUMAN via Haiku
    spec_human_md = ""
    try:
        human_resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=SPEC_HUMAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": spec_md[:4000]}],
        )
        spec_human_md = human_resp.content[0].text.strip()
        cost += (human_resp.usage.input_tokens * 0.8 + human_resp.usage.output_tokens * 4.0) / 1_000_000
        logger.info(f"[SPEC] SPEC_HUMAN generata: {len(spec_human_md)} chars")
    except Exception as e:
        logger.warning(f"[SPEC] SPEC_HUMAN generation error: {e}")

    try:
        supabase.table("projects").update({
            "spec_md": spec_md,
            "spec_human_md": spec_human_md or None,
            "stack": json.dumps(stack) if stack else None,
            "kpis": json.dumps(kpis) if kpis else None,
            "status": "spec_generated",
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[SPEC] DB update error: {e}")

    if github_repo:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        # Commit come SPEC_CODE.md (versione tecnica per AI agents)
        ok = _commit_to_project_repo(
            github_repo, "SPEC_CODE.md", spec_md,
            f"feat: SPEC_CODE.md rigenerato da brAIn — {ts}",
        )
        if ok:
            logger.info(f"[SPEC] SPEC_CODE.md committato su {github_repo}")
        # Mantieni anche SPEC.md per compatibilità backward
        _commit_to_project_repo(github_repo, "SPEC.md", spec_md,
                                f"feat: SPEC.md sync — {ts}")
        if spec_human_md:
            _commit_to_project_repo(github_repo, "SPEC_HUMAN.md", spec_human_md,
                                    f"feat: SPEC_HUMAN.md generato — {ts}")

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("spec_generator", "spec_generate", 3,
                    f"project={project_id} solution={solution_id} problem={problem_id}",
                    f"SPEC {len(spec_md)} chars stack={stack}",
                    "claude-sonnet-4-6", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[SPEC] Completato project={project_id} in {duration_ms}ms spec_len={len(spec_md)}")
    return {"status": "ok", "project_id": project_id, "spec_length": len(spec_md),
            "solution_id": solution_id, "problem_id": problem_id,
            "stack": stack, "kpis": kpis, "cost_usd": round(cost, 5)}


# ---- LANDING PAGE GENERATOR (inlined) ----

LP_SYSTEM_PROMPT_AR = """Sei un designer/copywriter esperto. Genera HTML single-file per una landing page MVP.

REQUISITI:
- HTML completo (<!DOCTYPE html> ... </html>), CSS inline nel <style>, nessuna dipendenza esterna
- Mobile-first, responsive, caricamento istantaneo
- Colori: bianco + un colore primario coerente col settore
- Font: system-ui / -apple-system (nessun Google Fonts)
- NO JavaScript complesso

STRUTTURA OBBLIGATORIA:
1. Hero section: headline + sottotitolo + CTA button
2. 3 benefit cards con icona emoji + titolo + descrizione 1 riga
3. Social proof placeholder: "[NUMERO] clienti gia' iscritti"
4. Form contatto: nome + email + messaggio + button
5. Footer: "Prodotto da brAIn — AI-native organization"

REGOLE COPYWRITING:
- Headline: beneficio concreto in < 8 parole (NON il nome del prodotto)
- CTA: verbo d'azione + beneficio
- Nessun gergo tecnico

Rispondi SOLO con il codice HTML, senza spiegazioni, senza blocchi markdown."""


def run_landing_page_generator(project_id):
    """Genera HTML landing page e salva in projects.landing_page_html."""
    start = time.time()
    logger.info(f"[LP] Avvio per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", "MVP")
    spec_md = project.get("spec_md", "")

    solution_desc = ""
    target_customer = ""
    problem_desc = ""
    bos_id = project.get("bos_id")
    if bos_id:
        try:
            sol = supabase.table("solutions").select("title,description,problem_id").eq("id", bos_id).execute()
            if sol.data:
                solution_desc = sol.data[0].get("description", "")[:400]
                prob_id = sol.data[0].get("problem_id")
                if prob_id:
                    prob = supabase.table("problems").select("title,description,target_customer").eq("id", prob_id).execute()
                    if prob.data:
                        target_customer = prob.data[0].get("target_customer", "")
                        problem_desc = prob.data[0].get("description", "")[:300]
        except Exception as e:
            logger.warning(f"[LP] Solution/problem load: {e}")

    user_prompt = f"""Progetto: {name}
Target customer: {target_customer or "professionisti e PMI"}
Problema risolto: {problem_desc or "inefficienza nel flusso di lavoro"}
Soluzione: {solution_desc or spec_md[:300]}
Genera la landing page HTML completa."""

    tokens_in = tokens_out = 0
    html = ""
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system=LP_SYSTEM_PROMPT_AR,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_html = response.content[0].text.strip()
        # Strip markdown code fences se il modello le ha aggiunte
        if raw_html.startswith("```"):
            lines = raw_html.split("\n")
            # rimuovi prima riga (```html o ```) e ultima riga (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
        else:
            lines = raw_html.split("\n")
        html = "\n".join(lines).strip()
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[LP] Claude error: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    try:
        supabase.table("projects").update({"landing_page_html": html}).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[LP] DB update error: {e}")

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("landing_page_generator", "lp_generate", 3,
                    f"project={project_id}", f"HTML {len(html)} chars",
                    "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[LP] Completato project={project_id} in {duration_ms}ms")
    return {"status": "ok", "project_id": project_id, "html_length": len(html), "cost_usd": round(cost, 6)}


# ---- BUILD PROMPT GENERATOR ----

def generate_build_prompt(project_id):
    """Genera il prompt Claude Code, lo salva in DB e avvia il build agent automatico."""
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", "MVP")
    slug = project.get("slug", "")
    github_repo = project.get("github_repo", "")
    stack = project.get("stack") or []
    kpis = project.get("kpis") or {}
    topic_id = project.get("topic_id")

    if isinstance(stack, str):
        try:
            stack = json.loads(stack)
        except:
            stack = []
    if isinstance(kpis, str):
        try:
            kpis = json.loads(kpis)
        except:
            kpis = {}

    # Estrai env vars dal spec_md (sezione 7)
    env_vars_section = ""
    spec_md = project.get("spec_md", "")
    if spec_md:
        match = _re.search(r'## 7\. Variabili d.Ambiente.*?\n(.*?)(?=## 8\.)', spec_md, _re.DOTALL)
        if match:
            env_vars_section = match.group(1).strip()[:800]

    service_name = f"{slug}-mvp" if slug else "mvp"
    artifact_tag = f"europe-west3-docker.pkg.dev/brain-core-487914/brain-repo/{service_name}:latest"

    prompt = f"""Esegui con --dangerously-skip-permissions. Non chiedere autorizzazione per nessuna operazione. Non fermarti per conferme intermedie. Procedi autonomamente fino al completamento.

Repo progetto: brain-{slug} (privato su GitHub mircocerisola)
La SPEC completa e' nel campo spec_md del progetto in Supabase (project_id={project_id}).

Costruisci l'MVP "{name}" seguendo esattamente le Fasi di Build nella sezione 8 del SPEC.md, in ordine (Fase 1 -> Fase 4).

Dopo ogni fase, committa su GitHub con un messaggio descrittivo (es: "feat(fase-1): setup repo e struttura base").

Dopo la Fase 4:
  - Pusha su origin main
  - Build Docker: gcloud builds submit --region=europe-west3 --tag {artifact_tag} .
  - Deploy Cloud Run: gcloud run deploy {service_name} --image {artifact_tag} --region europe-west3 --platform managed --quiet

Env vars necessarie (configurare prima del deploy):
{env_vars_section if env_vars_section else "(vedi sezione 7 del SPEC.md)"}

Stack: {", ".join(stack) if stack else "Python + Supabase + Cloud Run"}
Modelli LLM: usa SEMPRE Claude API (claude-haiku-4-5-20251001 o claude-sonnet-4-6), MAI OpenAI/GPT.

REGOLA ASSOLUTA: zero decisioni architetturali autonome.
Committa ogni file creato/modificato — mai lavorare in locale senza committare."""

    # Salva in DB (senza mandarlo su Telegram)
    try:
        supabase.table("projects").update({"build_prompt": prompt}).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[BUILD_PROMPT] DB update error: {e}")

    # Notifica breve: build avviato
    group_id = _get_telegram_group_id()
    _send_to_topic(group_id, topic_id,
                   f"\U0001f6e0\ufe0f Build avviato per \"{name}\".\nGenerando codice MVP in corso...")

    # Avvia build agent in background (non blocca)
    import threading as _threading_build
    _threading_build.Thread(target=run_build_agent, args=(project_id,), daemon=True).start()

    logger.info(f"[BUILD_PROMPT] Generato per project={project_id}, build agent avviato")
    return {"status": "ok", "project_id": project_id, "prompt_length": len(prompt)}


FASE_DESCRIPTIONS = {
    1: "Struttura progetto (main.py, requirements.txt, Dockerfile, .env.example)",
    2: "Logica core e integrazione DB",
    3: "API endpoints e business logic",
    4: "Deploy, monitoring e ottimizzazioni",
}


def run_build_agent(project_id):
    """Build agent autonomo: genera Fase 1 (struttura base), committa su GitHub, notifica per review."""
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return
        project = proj.data[0]
    except Exception as e:
        logger.error(f"[BUILD_AGENT] DB load: {e}")
        return

    name = project.get("name", "MVP")
    github_repo = project.get("github_repo", "")
    spec_md = project.get("spec_md", "")
    stack = project.get("stack") or []
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()

    if isinstance(stack, str):
        try:
            stack = json.loads(stack)
        except:
            stack = []

    if not spec_md or not github_repo:
        _send_to_topic(group_id, topic_id, f"\u274c Build {name}: SPEC o repo mancanti. Impossibile procedere.")
        return

    stack_str = ", ".join(stack) if stack else "Python, Supabase, Cloud Run"

    slug = project.get("slug", "")

    # Genera solo Fase 1: struttura base
    build_prompt = f"""Esegui con --dangerously-skip-permissions. Non chiedere autorizzazione per nessuna operazione. Non fermarti per conferme.

Sei un senior Python developer. Genera il codice per la Fase 1 (struttura base) dell'MVP "{name}".

SPEC.md (estratto):
{spec_md[:5000]}

REQUISITI FASE 1:
- Stack: {stack_str}
- Modelli LLM: usa SEMPRE Claude API (claude-haiku-4-5-20251001 o claude-sonnet-4-6), NON OpenAI/GPT
- Genera: main.py (o app.py), requirements.txt, Dockerfile, .env.example
- Il codice deve essere funzionante e deployabile su Google Cloud Run europe-west3
- Usa Supabase per il database (variabili SUPABASE_URL, SUPABASE_KEY)
- Struttura pulita: solo file essenziali per far partire il progetto

FORMATO OUTPUT per ogni file:
=== FILE: nome_file ===
[contenuto del file]
=== END FILE ===

Genera SOLO i file della struttura base."""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            messages=[{"role": "user", "content": build_prompt}],
        )
        code_output = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[BUILD_AGENT] Claude error: {e}")
        _send_to_topic(group_id, topic_id, f"\u274c Build {name} fallito: {e}")
        return

    # Parse e commit dei file generati
    file_pattern = _re.compile(r'=== FILE: (.+?) ===\n(.*?)(?==== END FILE ===)', _re.DOTALL)
    matches = list(file_pattern.finditer(code_output))
    files_committed = 0

    for match in matches:
        filepath = match.group(1).strip()
        content = match.group(2).strip()
        if content and filepath:
            ok = _commit_to_project_repo(
                github_repo, filepath, content,
                f"feat(fase-1): {filepath}",
            )
            if ok:
                files_committed += 1

    # Fallback se nessun file parsato
    if files_committed == 0 and code_output:
        _commit_to_project_repo(github_repo, "main.py", code_output, "feat(fase-1): MVP structure")
        files_committed = 1

    # Salva log iterazione su GitHub
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H")
    iter_content = f"# Fase 1 — {FASE_DESCRIPTIONS[1]}\n\nData: {datetime.now(timezone.utc).isoformat()}\n\nFile generati: {files_committed}\n\n---\n\n{code_output}"
    _commit_to_project_repo(github_repo, f"iterations/{ts}_fase1.md", iter_content, "log(fase-1): iterazione salvata")

    # Aggiorna status e build_phase
    try:
        supabase.table("projects").update({
            "status": "review_phase1",
            "build_phase": 1,
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.warning(f"[BUILD_AGENT] DB update status: {e}")

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000
    log_to_supabase("build_agent", "build_fase1", 3,
                    f"project={project_id}", f"{files_committed} file committati",
                    "claude-sonnet-4-6", tokens_in, tokens_out, cost, 0)

    # Card summary — Fix 2
    file_list = "\n".join([f"  \u2022 {m.group(1).strip()}" for m in matches]) if matches else "  \u2022 main.py (fallback)"
    sep = "\u2501" * 15
    result_msg = (
        f"\u256d\u2500\u2500 Fase 1 completata \u2500\u2500\u256e\n"
        f"\U0001f4e6 {FASE_DESCRIPTIONS[1]}\n"
        f"{sep}\n"
        f"\U0001f4c1 File ({files_committed}):\n{file_list}\n"
        f"{sep}\n"
        f"\U0001f4c1 Repo: brain-{slug} (privato)\n"
        f"{sep}\n"
        f"Come si comporta? Cosa vuoi cambiare?"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\u2705 Continua", "callback_data": f"build_continue:{project_id}:1"},
            {"text": "\u270f\ufe0f Modifica", "callback_data": f"build_modify:{project_id}:1"},
        ]]
    }
    _send_to_topic(group_id, topic_id, result_msg, reply_markup=reply_markup)
    logger.info(f"[BUILD_AGENT] Fase 1 completata project={project_id}, {files_committed} file committati")


# ---- ENQUEUE SPEC REVIEW ACTION ----

def _extract_spec_bullets(spec_md):
    """Estrae 3 bullet points dalla SPEC usando Claude Haiku. Max 60 chars ciascuno."""
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": (
                "Analizza questo SPEC e ritorna SOLO 3 bullet points in italiano, "
                "max 60 caratteri ciascuno, separati da newline. "
                "Solo i 3 bullet, niente altro.\n\n" + spec_md[:3000]
            )}],
        )
        text = response.content[0].text.strip()
        bullets = [b.strip().lstrip("\u2022-* ").strip() for b in text.split("\n") if b.strip()]
        bullets = [b[:60] for b in bullets if b][:3]
        while len(bullets) < 3:
            bullets.append("Vedi SPEC per dettagli")
        return bullets
    except Exception as e:
        logger.warning(f"[SPEC_BULLETS] {e}")
        return ["Vedi SPEC per dettagli"] * 3


def enqueue_spec_review_action(project_id):
    """Inserisce azione spec_review in action_queue e invia al topic con inline keyboard.
    MACRO-TASK 4: usa SPEC_HUMAN se disponibile, altrimenti bullets come fallback.
    """
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return
        project = proj.data[0]
    except Exception as e:
        logger.error(f"[SPEC_REVIEW] DB load: {e}")
        return

    name = project.get("name", f"Progetto {project_id}")
    bos_score = project.get("bos_score", 0) or 0
    slug = project.get("slug", "")
    github_repo = project.get("github_repo", "")
    topic_id = project.get("topic_id")
    spec_md = project.get("spec_md", "")
    spec_human_md = project.get("spec_human_md", "")

    # Inserisci in action_queue
    chat_id = get_telegram_chat_id()
    action_db_id = None
    if chat_id:
        try:
            result = supabase.table("action_queue").insert({
                "user_id": int(chat_id),
                "action_type": "spec_review",
                "title": f"SPEC PRONTA \u2014 {name[:60]}",
                "description": f"BOS score: {bos_score:.2f} | Repo: {github_repo}",
                "payload": json.dumps({
                    "project_id": str(project_id),
                    "slug": slug,
                    "github_repo": github_repo,
                }),
                "priority": 8,
                "urgency": 8,
                "importance": 8,
                "status": "pending",
            }).execute()
            if result.data:
                action_db_id = result.data[0]["id"]
        except Exception as e:
            logger.error(f"[SPEC_REVIEW] action_queue insert: {e}")

    sep = "\u2501" * 15

    # MACRO-TASK 4: usa SPEC_HUMAN se disponibile, altrimenti bullets
    if spec_human_md:
        msg = f"{spec_human_md}\n{sep}"
    else:
        bullets = _extract_spec_bullets(spec_md) if spec_md else ["Vedi SPEC per dettagli"] * 3
        msg = (
            f"\U0001f4cb SPEC pronta \u2014 {name}\n"
            f"Punti chiave:\n"
            f"\u2022 {bullets[0]}\n"
            f"\u2022 {bullets[1]}\n"
            f"\u2022 {bullets[2]}\n"
            f"{sep}"
        )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "\U0001f4c4 Scarica SPEC", "callback_data": f"spec_download:{project_id}"},
                {"text": "\u2705 Valida", "callback_data": f"spec_validate:{project_id}"},
                {"text": "\u270f\ufe0f Modifica", "callback_data": f"spec_edit:{project_id}"},
            ],
        ]
    }

    group_id = _get_telegram_group_id()
    _send_to_topic(group_id, topic_id, msg, reply_markup=reply_markup)

    logger.info(f"[SPEC_REVIEW] Enqueued action_id={action_db_id} per project={project_id}")


# ---- INIT PROJECT ----

def init_project(solution_id):
    """Inizializza progetto da BOS approvato: DB, GitHub repo, Forum Topic, spec, landing, enqueue review."""
    logger.info(f"[INIT] Avvio per solution_id={solution_id}")

    # 1. Carica soluzione e problema
    try:
        sol = supabase.table("solutions").select("*").eq("id", solution_id).execute()
        if not sol.data:
            logger.error(f"[INIT] Solution {solution_id} non trovata")
            return {"status": "error", "error": "solution not found"}
        solution = sol.data[0]
        sol_title = solution.get("title", f"Project {solution_id}")
        bos_score = float(solution.get("bos_score") or 0)
    except Exception as e:
        logger.error(f"[INIT] Solution load error: {e}")
        return {"status": "error", "error": str(e)}

    # 2. Genera slug unico
    base_slug = _slugify(sol_title)
    slug = base_slug
    # Controlla unicita'
    try:
        existing = supabase.table("projects").select("id").eq("slug", slug).execute()
        if existing.data:
            slug = f"{base_slug[:17]}-{solution_id}"
    except:
        pass

    name = sol_title[:80]

    # 3. Crea record in DB
    project_id = None
    try:
        result = supabase.table("projects").insert({
            "name": name,
            "slug": slug,
            "bos_id": int(solution_id),
            "bos_score": bos_score,
            "status": "init",
        }).execute()
        if result.data:
            project_id = result.data[0]["id"]
        else:
            logger.error("[INIT] Inserimento projects fallito")
            return {"status": "error", "error": "db insert failed"}
    except Exception as e:
        logger.error(f"[INIT] DB insert error: {e}")
        return {"status": "error", "error": str(e)}

    logger.info(f"[INIT] Progetto creato: id={project_id} slug={slug}")

    # 3b. MACRO-TASK 1: Crea Supabase project separato (best-effort)
    db_url, db_anon_key = _create_supabase_project(slug)
    if db_url:
        secret_id = f"brain-{slug}-supabase-key"
        _save_gcp_secret(secret_id, db_anon_key or "")
        try:
            supabase.table("projects").update({
                "db_url": db_url,
                "db_key_secret_name": secret_id,
            }).eq("id", project_id).execute()
        except Exception as e:
            logger.warning(f"[INIT] db_url update error: {e}")
        logger.info(f"[INIT] Supabase separato: {db_url[:60]}")
    else:
        logger.info("[INIT] DB separato non creato (best-effort, procedo senza)")

    # 4. Crea GitHub repo
    github_repo = _create_github_repo(slug, name)
    if github_repo:
        try:
            supabase.table("projects").update({"github_repo": github_repo}).eq("id", project_id).execute()
        except:
            pass
        logger.info(f"[INIT] GitHub repo: {github_repo}")
    else:
        logger.warning(f"[INIT] GitHub repo creation fallita, procedo senza")

    # 5. Crea Forum Topic
    group_id = _get_telegram_group_id()
    topic_id = None
    if group_id:
        topic_id = _create_forum_topic(group_id, name)
        if topic_id:
            try:
                supabase.table("projects").update({"topic_id": topic_id}).eq("id", project_id).execute()
            except:
                pass
            logger.info(f"[INIT] Forum Topic creato: topic_id={topic_id}")
            # Messaggio di benvenuto nel topic
            _send_to_topic(group_id, topic_id,
                           f"\U0001f680 Progetto '{name}' avviato!\nBOS score: {bos_score:.2f}\nGenerazione SPEC in corso...")
    else:
        logger.info("[INIT] telegram_group_id non configurato, Forum Topic non creato")

    # 6. Genera SPEC
    spec_result = run_spec_generator(project_id)
    if spec_result.get("status") != "ok":
        logger.error(f"[INIT] Spec generation fallita: {spec_result}")
        if group_id and topic_id:
            _send_to_topic(group_id, topic_id, f"\u26a0\ufe0f Errore generazione SPEC: {spec_result.get('error')}")
        return {"status": "error", "error": "spec generation failed", "detail": spec_result}

    logger.info(f"[INIT] SPEC generata: {spec_result.get('spec_length')} chars")

    # 7. Genera Landing Page
    lp_result = run_landing_page_generator(project_id)
    if lp_result.get("status") == "ok":
        logger.info(f"[INIT] Landing page generata: {lp_result.get('html_length')} chars")
        if group_id and topic_id:
            _send_to_topic(group_id, topic_id, "Landing page HTML generata. Pronta per deploy quando vuoi.")
    else:
        logger.warning(f"[INIT] Landing page generation fallita (non critico): {lp_result}")

    # 8. Enqueue spec review action con inline keyboard
    enqueue_spec_review_action(project_id)

    logger.info(f"[INIT] Completato: project_id={project_id} slug={slug}")
    return {
        "status": "ok",
        "project_id": project_id,
        "slug": slug,
        "github_repo": github_repo,
        "topic_id": topic_id,
    }


# ---- LEGAL AGENT (MACRO-TASK 2) ----

LEGAL_SYSTEM_PROMPT = """Sei il Legal Agent di brAIn, esperto di diritto digitale europeo (GDPR, AI Act, Direttiva E-Commerce, normativa italiana).
Analizza un progetto e valuta i rischi legali per operare in Europa.

RISPOSTA: JSON puro, nessun testo fuori.
{
  "green_points": ["punto OK 1", "punto OK 2"],
  "yellow_points": ["attenzione 1: cosa fare"],
  "red_points": ["blocco critico 1: perche' blocca il lancio"],
  "report_md": "# Review Legale\\n## Punti OK\\n...\\n## Attenzione\\n...\\n## Blocchi\\n...",
  "can_proceed": true
}

REGOLE:
- green_points: aspetti legalmente OK (es: no dati sensibili, B2B chiaro)
- yellow_points: aspetti da sistemare prima del lancio ma non bloccanti
- red_points: problemi che bloccano il lancio (es: raccolta dati senza consenso, attivita' finanziaria non autorizzata)
- can_proceed: false se ci sono red_points, true altrimenti
- Sii concreto: cita norme specifiche (art. GDPR, AI Act art., ecc.)
- Se settore = health/finance/legal: tratta come alta priorita'"""


