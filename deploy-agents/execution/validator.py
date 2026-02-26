"""
brAIn module: execution/validator.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import os, json, time, re, uuid
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, SUPABASE_ACCESS_TOKEN, DB_PASSWORD, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json
from execution.project import (get_project_db, _send_to_topic, _commit_to_project_repo,
    _get_telegram_group_id, _github_project_api, SPEC_SYSTEM_PROMPT_AR)
from execution.builder import FASE_DESCRIPTIONS, enqueue_spec_review_action
try:
    from intelligence.memory import update_project_episode as _update_project_episode
except Exception:
    def _update_project_episode(*args, **kwargs): pass
try:
    from execution.pipeline import (
        advance_pipeline_step, generate_phase_card,
        count_lines_of_code, update_project_loc, send_smoke_proposal,
    )
except Exception as _pimp_err:
    logger.warning(f"[VALIDATOR] pipeline import: {_pimp_err}")
    def advance_pipeline_step(*a, **kw): pass
    def generate_phase_card(*a, **kw): return ""
    def count_lines_of_code(t): return len([l for l in t.split("\n") if l.strip()])
    def update_project_loc(*a, **kw): return 0
    def send_smoke_proposal(*a, **kw): pass


VALIDATION_SYSTEM_PROMPT_AR = """Sei il Validation Agent di brAIn. Analizza le metriche di un progetto MVP e dai un verdetto SCALE/PIVOT/KILL.

VERDETTI:
- SCALE: metriche sopra target, crescita positiva, continua a investire
- PIVOT: metriche sotto target ma segnali positivi, cambia approccio
- KILL: metriche pessime, nessun segnale, chiudi il progetto

FORMATO RISPOSTA (testo piano, no markdown):
VERDETTO: [SCALE/PIVOT/KILL]

Analisi:
[2-3 righe su cosa stanno dicendo le metriche]

Azione raccomandata:
[1 riga concreta su cosa fare questa settimana]

Sii onesto e diretto. Se i dati sono scarsi, dillo esplicitamente."""


def run_validation_agent():
    """Report settimanale SCALE/PIVOT/KILL per tutti i progetti in stato 'validating'."""
    start = time.time()
    logger.info("[VALIDATION] Avvio ciclo settimanale")

    group_id = _get_telegram_group_id()
    chat_id = get_telegram_chat_id()

    try:
        projects_result = supabase.table("projects").select("*").eq("status", "validating").execute()
        projects = projects_result.data or []
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if not projects:
        logger.info("[VALIDATION] Nessun progetto in stato validating")
        return {"status": "ok", "projects_analyzed": 0}

    total_tokens_in = total_tokens_out = 0
    analyzed = 0
    sep = "\u2501" * 15

    for project in projects:
        project_id = project["id"]
        name = project.get("name", f"Progetto {project_id}")
        topic_id = project.get("topic_id")

        try:
            metrics_result = supabase.table("project_metrics").select("*")\
                .eq("project_id", project_id)\
                .order("week", desc=True)\
                .limit(4)\
                .execute()
            metrics = list(reversed(metrics_result.data or []))
        except:
            metrics = []

        kpis = project.get("kpis") or {}
        if isinstance(kpis, str):
            try:
                kpis = json.loads(kpis)
            except:
                kpis = {}

        primary_kpi = kpis.get("primary", "customers")
        target_w4 = kpis.get("target_week4", 0)
        target_w12 = kpis.get("target_week12", 0)
        revenue_target = kpis.get("revenue_target_month3_eur", 0)

        metrics_lines = []
        total_revenue = 0.0
        for m in metrics:
            metrics_lines.append(
                f"Week {m['week']}: customers={m.get('customers_count', 0)}, "
                f"revenue={m.get('revenue_eur', 0):.2f} EUR, "
                f"{m.get('key_metric_name', primary_kpi)}={m.get('key_metric_value', 0)}"
            )
            total_revenue += float(m.get("revenue_eur", 0) or 0)

        current_week = max((m["week"] for m in metrics), default=0)

        user_prompt = f"""Progetto: {name}
KPI primario target: {primary_kpi} — settimana 4: {target_w4}, settimana 12: {target_w12}
Revenue target mese 3: EUR {revenue_target}
Settimana corrente: {current_week}
Metriche: {chr(10).join(metrics_lines) if metrics_lines else "Nessuna metrica"}
Revenue totale: EUR {total_revenue:.2f}
Analizza e dai il verdetto."""

        verdict_text = ""
        try:
            response = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=VALIDATION_SYSTEM_PROMPT_AR,
                messages=[{"role": "user", "content": user_prompt}],
            )
            verdict_text = response.content[0].text.strip()
            total_tokens_in += response.usage.input_tokens
            total_tokens_out += response.usage.output_tokens
        except Exception as e:
            logger.error(f"[VALIDATION] Claude error for {project_id}: {e}")
            continue

        if "KILL" in verdict_text.upper():
            try:
                supabase.table("projects").update({
                    "status": "killed",
                    "notes": f"KILL — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {verdict_text[:200]}",
                }).eq("id", project_id).execute()
                _update_project_episode(project_id, f"Verdetto KILL: {verdict_text[:150]}", "killed", "Progetto archiviato")
            except:
                pass
        else:
            verdict_tag = "SCALE" if "SCALE" in verdict_text.upper() else "PIVOT"
            _update_project_episode(
                project_id,
                f"Verdetto {verdict_tag}: {verdict_text[:150]}",
                "validating",
                f"Seguire raccomandazioni {verdict_tag}",
            )

        report_msg = (
            f"\U0001f4ca REPORT SETTIMANALE\n"
            f"{sep}\n"
            f"\U0001f3d7\ufe0f {name}\n"
            f"{verdict_text}\n"
            f"{sep}"
        )

        # Inline keyboard in base al verdict — Fix 3
        verdict_upper = verdict_text.upper()
        if "SCALE" in verdict_upper:
            val_keyboard = [
                {"text": "\U0001f680 Procedi (SCALE)", "callback_data": f"val_proceed:{project_id}"},
                {"text": "\u23f8\ufe0f Aspetta", "callback_data": f"val_wait:{project_id}"},
            ]
        elif "PIVOT" in verdict_upper:
            val_keyboard = [
                {"text": "\U0001f4a1 Discuti (PIVOT)", "callback_data": f"val_discuss:{project_id}"},
                {"text": "\u23f8\ufe0f Aspetta", "callback_data": f"val_wait:{project_id}"},
            ]
        else:  # KILL
            val_keyboard = [
                {"text": "\U0001f6d1 Procedi (KILL)", "callback_data": f"val_proceed:{project_id}"},
                {"text": "\U0001f4a1 Discuti", "callback_data": f"val_discuss:{project_id}"},
            ]
        val_reply_markup = {"inline_keyboard": [val_keyboard]}

        if group_id and topic_id:
            _send_to_topic(group_id, topic_id, report_msg, reply_markup=val_reply_markup)
        if chat_id and TELEGRAM_BOT_TOKEN:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": report_msg, "reply_markup": val_reply_markup},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"[VALIDATION] Telegram notify error: {e}")

        analyzed += 1
        logger.info(f"[VALIDATION] {name}: report inviato")

    duration_ms = int((time.time() - start) * 1000)
    cost = (total_tokens_in * 0.8 + total_tokens_out * 4.0) / 1_000_000
    log_to_supabase("validation_agent", "validation_weekly", 3,
                    f"{len(projects)} progetti", f"{analyzed} analizzati",
                    "claude-haiku-4-5-20251001", total_tokens_in, total_tokens_out, cost, duration_ms)

    logger.info(f"[VALIDATION] Completato: {analyzed} progetti in {duration_ms}ms")
    return {"status": "ok", "projects_analyzed": analyzed, "cost_usd": round(cost, 6)}


# ---- CONTINUE BUILD AGENT ----

def continue_build_agent(project_id, feedback, current_phase):
    """Genera la fase successiva del build integrando il feedback di Mirco."""
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            logger.error(f"[CONTINUE_BUILD] project {project_id} non trovato")
            return
        project = proj.data[0]
    except Exception as e:
        logger.error(f"[CONTINUE_BUILD] DB load: {e}")
        return

    name = project.get("name", "MVP")
    github_repo = project.get("github_repo", "")
    spec_md = project.get("spec_md", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()
    next_phase = current_phase + 1

    if not github_repo:
        _send_to_topic(group_id, topic_id, f"\u274c Continue build {name}: repo mancante.")
        return

    # Leggi file iterations/ da GitHub per contesto fasi precedenti
    prev_iterations = []
    try:
        contents = _github_project_api("GET", github_repo, "/contents/iterations")
        if contents and isinstance(contents, list):
            for f in sorted(contents, key=lambda x: x.get("name", ""))[:3]:
                file_data = _github_project_api("GET", github_repo, f"/contents/{f['path']}")
                if file_data and file_data.get("content"):
                    import base64 as _b64
                    decoded = _b64.b64decode(file_data["content"]).decode("utf-8", errors="replace")
                    prev_iterations.append(f"### {f['name']}\n{decoded[:2000]}")
    except Exception as e:
        logger.warning(f"[CONTINUE_BUILD] lettura iterations: {e}")

    slug = project.get("slug", "")
    context_prev = "\n\n".join(prev_iterations) if prev_iterations else "Nessuna iterazione precedente trovata."
    fase_desc = FASE_DESCRIPTIONS.get(next_phase, f"Fase {next_phase}")

    build_prompt = f"""Esegui con --dangerously-skip-permissions. Non chiedere autorizzazione per nessuna operazione. Non fermarti per conferme.

Sei un senior Python developer. Continua il build dell'MVP "{name}".

SPEC.md (estratto):
{spec_md[:3000]}

FASI PRECEDENTI (iterazioni su GitHub):
{context_prev}

FEEDBACK DI MIRCO sulla fase {current_phase}:
{feedback}

REQUISITI FASE {next_phase} — {fase_desc}:
- Integra il feedback ricevuto
- Modelli LLM: usa SEMPRE Claude API (claude-haiku-4-5-20251001 o claude-sonnet-4-6), NON OpenAI/GPT
- Usa Supabase per il database

FORMATO OUTPUT per ogni file:
=== FILE: nome_file ===
[contenuto del file]
=== END FILE ===

Genera il codice per la Fase {next_phase}."""

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
        logger.error(f"[CONTINUE_BUILD] Claude error: {e}")
        _send_to_topic(group_id, topic_id, f"\u274c Fase {next_phase} fallita: {e}")
        return

    # Parse e commit dei file generati
    file_pattern = re.compile(r'=== FILE: (.+?) ===\n(.*?)(?==== END FILE ===)', re.DOTALL)
    matches = list(file_pattern.finditer(code_output))
    files_committed = 0

    for match in matches:
        filepath = match.group(1).strip()
        content = match.group(2).strip()
        if content and filepath:
            ok = _commit_to_project_repo(
                github_repo, filepath, content,
                f"feat(fase-{next_phase}): {filepath}",
            )
            if ok:
                files_committed += 1

    if files_committed == 0 and code_output:
        _commit_to_project_repo(github_repo, f"fase_{next_phase}.py", code_output, f"feat(fase-{next_phase}): codice")
        files_committed = 1

    # Salva log iterazione
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H")
    iter_content = f"# Fase {next_phase} — {fase_desc}\n\nData: {datetime.now(timezone.utc).isoformat()}\nFeedback: {feedback}\n\n---\n\n{code_output}"
    _commit_to_project_repo(github_repo, f"iterations/{ts}_fase{next_phase}.md", iter_content, f"log(fase-{next_phase}): iterazione salvata")

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000
    log_to_supabase("build_agent", f"build_fase{next_phase}", 3,
                    f"project={project_id} feedback={feedback[:100]}", f"{files_committed} file committati",
                    "claude-sonnet-4-6", tokens_in, tokens_out, cost, 0)

    # LOC counter + step
    file_list_names = [m.group(1).strip() for m in matches] if matches else [f"fase_{next_phase}.py"]
    new_loc = count_lines_of_code(code_output)
    total_loc = update_project_loc(project_id, new_loc, files_committed, file_list_names, cost, "build_running")
    advance_pipeline_step(project_id, "build_running")

    file_list = "\n".join(f"  \u2022 {f}" for f in file_list_names)

    if next_phase < 4:
        try:
            supabase.table("projects").update({
                "status": f"review_phase{next_phase}",
                "build_phase": next_phase,
            }).eq("id", project_id).execute()
            _update_project_episode(
                project_id,
                f"Build Fase {next_phase} completata ({FASE_DESCRIPTIONS.get(next_phase, '')})",
                f"review_phase{next_phase}",
                f"Mirco revisiona fase {next_phase}",
            )
        except Exception as e:
            logger.warning(f"[CONTINUE_BUILD] DB update: {e}")

        # Phase card con spiegazione Haiku
        result_msg = generate_phase_card(
            name, next_phase, fase_desc, code_output,
            project.get("spec_md", ""), stack, total_loc, file_list,
        )
        if not result_msg:
            result_msg = f"\u256d\u2500\u2500 Fase {next_phase} completata \u2500\u2500\u256e\n{file_list}\n\U0001f4ca Codice: {total_loc} righe"
        reply_markup = {
            "inline_keyboard": [[
                {"text": "\u2705 Continua", "callback_data": f"build_continue:{project_id}:{next_phase}"},
                {"text": "\u270f\ufe0f Modifica", "callback_data": f"build_modify:{project_id}:{next_phase}"},
            ]]
        }
        _send_to_topic(group_id, topic_id, result_msg, reply_markup=reply_markup)

    else:
        # Fase 4 = build completo → advance step + auto-smoke
        advance_pipeline_step(project_id, "build_done")
        try:
            supabase.table("projects").update({
                "status": "build_complete",
                "build_phase": next_phase,
            }).eq("id", project_id).execute()
            _update_project_episode(
                project_id,
                "Build completo (tutte le fasi)",
                "build_complete",
                "Avvio automatico smoke test",
            )
        except Exception as e:
            logger.warning(f"[CONTINUE_BUILD] DB update build_complete: {e}")

        result_msg = (
            f"\U0001f3c1 Build completo \u2014 {name}\n"
            f"{sep}\n"
            f"\U0001f4c1 File ({files_committed}):\n{file_list}\n"
            f"{sep}\n"
            f"\U0001f4c1 Repo: brain-{slug} (privato)\n"
            f"{sep}\n"
            f"\u2705 Avvio smoke test automatico..."
        )
        _send_to_topic(group_id, topic_id, result_msg)

        # Auto-trigger smoke test senza aspettare input Mirco
        try:
            from execution.smoke import run_smoke_test_setup
            run_smoke_test_setup(project_id)
        except Exception as e:
            logger.warning(f"[CONTINUE_BUILD] smoke trigger: {e}")
            _send_to_topic(group_id, topic_id, f"\u26a0\ufe0f Smoke test non avviato: {e}")

    logger.info(f"[CONTINUE_BUILD] Fase {next_phase} completata project={project_id}")


# ---- GENERATE TEAM INVITE LINK ----

def _generate_team_invite_link_sync(project_id):
    """Crea invite link Telegram per il gruppo (member_limit=1, scade 24h). Ritorna URL o None."""
    group_id = _get_telegram_group_id()
    if not group_id or not TELEGRAM_BOT_TOKEN:
        return None
    try:
        expire_date = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/createChatInviteLink",
            json={
                "chat_id": group_id,
                "member_limit": 1,
                "expire_date": expire_date,
            },
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("result", {}).get("invite_link")
        logger.warning(f"[INVITE_LINK] {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[INVITE_LINK] {e}")
    return None


# ---- SPEC UPDATE ----

def run_spec_update(project_id, modification_instruction):
    """Aggiorna lo SPEC di un progetto in base a un'istruzione di modifica."""
    start = time.time()
    logger.info(f"[SPEC_UPDATE] project={project_id} istruzione='{modification_instruction[:80]}'")

    try:
        proj = supabase.table("projects").select("spec_md,name,github_repo,bos_id,bos_score").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    old_spec = project.get("spec_md", "")
    if not old_spec:
        return {"status": "error", "error": "spec_md non disponibile, genera prima la SPEC"}

    update_prompt = f"""Hai questo SPEC.md esistente:

{old_spec[:6000]}

Istruzione di modifica da Mirco:
{modification_instruction}

Applica la modifica richiesta mantenendo la struttura a 10 sezioni e il blocco JSON finale.
Rispondi SOLO con il SPEC.md aggiornato completo."""

    tokens_in = tokens_out = 0
    new_spec = ""
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            system=SPEC_SYSTEM_PROMPT_AR,
            messages=[{"role": "user", "content": update_prompt}],
        )
        new_spec = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    stack = []
    kpis = {}
    try:
        match = re.search(r'<!-- JSON_SPEC:\s*(.*?)\s*:JSON_SPEC_END -->', new_spec, re.DOTALL)
        if match:
            spec_meta = json.loads(match.group(1))
            stack = spec_meta.get("stack", [])
            kpis = spec_meta.get("kpis", {})
    except:
        pass

    try:
        supabase.table("projects").update({
            "spec_md": new_spec,
            "stack": json.dumps(stack) if stack else None,
            "kpis": json.dumps(kpis) if kpis else None,
            "status": "spec_generated",
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[SPEC_UPDATE] DB update error: {e}")

    github_repo = project.get("github_repo", "")
    if github_repo:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _commit_to_project_repo(
            github_repo, "SPEC.md", new_spec,
            f"update: SPEC.md modificato da Mirco — {ts}",
        )

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("spec_generator", "spec_update", 3,
                    f"project={project_id}", f"SPEC aggiornato {len(new_spec)} chars",
                    "claude-sonnet-4-6", tokens_in, tokens_out, cost, duration_ms)

    # Re-enqueue spec review
    enqueue_spec_review_action(project_id)

    return {"status": "ok", "project_id": project_id, "spec_length": len(new_spec), "cost_usd": round(cost, 5)}


# ============================================================
# HTTP ENDPOINTS
# ============================================================

