"""
brAIn — execution/pipeline.py
Pipeline Lean Startup: CSO→smoke test→GO Mirco→COO→launch.
Step guard bloccante, blocker, phase card, LOC counter, dedup, smoke design.
"""
from __future__ import annotations
import hashlib, json, time
from datetime import datetime, timezone
from typing import Optional
import requests as _requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger

SEP = "\u2501" * 15

# ── Pipeline steps — ordine OBBLIGATORIO sequenziale bloccante ────────────────

# CSO TERRITORY: validazione mercato PRIMA di scrivere codice
# COO TERRITORY: build SOLO dopo GO di Mirco su smoke test

PIPELINE_STEPS = [
    # --- CSO TERRITORY ---
    "problem_identified",
    "solution_hypothesized",
    "bos_pending",
    "bos_approved",
    "smoke_test_designing",
    "smoke_test_running",
    "smoke_test_results_ready",
    "smoke_go",             # Mirco dice GO
    # "smoke_nogo",         # terminal — progetto archiviato (non nella sequenza)
    # --- COO TERRITORY ---
    "spec_pending",
    "spec_approved",
    "legal_pending",
    "legal_approved",
    "build_running",
    "qa_testing",
    "launched",
    "metrics_review",
]

_STEP_INDEX = {s: i for i, s in enumerate(PIPELINE_STEPS)}

# Terminal states (non nella sequenza, possono essere impostati in qualsiasi momento)
TERMINAL_STATES = {"smoke_nogo", "archived", "killed"}

CSO_TERRITORY = {
    "problem_identified", "solution_hypothesized", "bos_pending", "bos_approved",
    "smoke_test_designing", "smoke_test_running", "smoke_test_results_ready",
    "smoke_go", "smoke_nogo",
}

COO_TERRITORY = {
    "spec_pending", "spec_approved", "legal_pending", "legal_approved",
    "build_running", "qa_testing", "launched", "metrics_review",
}


def get_territory(step: str) -> str:
    """Ritorna 'cso' o 'coo' per lo step dato."""
    if step in CSO_TERRITORY:
        return "cso"
    if step in COO_TERRITORY:
        return "coo"
    return "unknown"


def advance_pipeline_step(project_id: int, new_step: str) -> bool:
    """Aggiorna pipeline_step con validazione sequenziale stretta.
    Nessuno step puo essere saltato. Ritorna False se step non valido o fuori ordine.
    """
    # Terminal states: sempre permessi
    if new_step in TERMINAL_STATES:
        try:
            supabase.table("projects").update({
                "pipeline_step": new_step,
                "pipeline_territory": get_territory(new_step),
            }).eq("id", project_id).execute()
            logger.info(f"[PIPELINE] {project_id} -> {new_step} (terminal)")
            return True
        except Exception as e:
            logger.warning(f"[PIPELINE] advance_step terminal {project_id} -> {new_step}: {e}")
            return False

    if new_step not in _STEP_INDEX:
        logger.warning(f"[PIPELINE] step sconosciuto: {new_step}")
        return False

    # Verifica step attuale e che il nuovo sia il prossimo nella sequenza
    try:
        r = supabase.table("projects").select("pipeline_step,name,topic_id").eq("id", project_id).execute()
        if not r.data:
            return False
        current = r.data[0].get("pipeline_step")
        name = r.data[0].get("name", f"Progetto {project_id}")
        topic_id = r.data[0].get("topic_id")

        cur_idx = _STEP_INDEX.get(current, -1)
        new_idx = _STEP_INDEX.get(new_step, -1)

        # Permetti avanzamento solo al prossimo step o allo stesso step (idempotente)
        if new_idx > cur_idx + 1 and cur_idx >= 0:
            steps_skipped = PIPELINE_STEPS[cur_idx + 1:new_idx]
            logger.warning(
                f"[PIPELINE] BLOCCO {name}: tentativo di saltare step! "
                f"current={current} -> richiesto={new_step}, skippati={steps_skipped}"
            )
            # Alert nel topic cantiere
            group_id = _get_group_id()
            if group_id and topic_id:
                _send_topic_raw(group_id, topic_id,
                    f"\u26d4 PIPELINE BLOCCATA — {name}\n"
                    f"{SEP}\n"
                    f"Tentativo di saltare step!\n"
                    f"Step attuale: {current}\n"
                    f"Step richiesto: {new_step}\n"
                    f"Step mancanti: {', '.join(steps_skipped)}\n"
                    f"{SEP}\n"
                    f"Completa TUTTI i passi precedenti prima di procedere.")
            return False

        territory = get_territory(new_step)
        supabase.table("projects").update({
            "pipeline_step": new_step,
            "pipeline_territory": territory,
        }).eq("id", project_id).execute()
        logger.info(f"[PIPELINE] {name} {current} -> {new_step} (territory: {territory})")
        return True
    except Exception as e:
        logger.warning(f"[PIPELINE] advance_step {project_id} -> {new_step}: {e}")
        return False


def check_pipeline_step(project_id: int, required_step: str,
                        group_id=None, topic_id=None) -> bool:
    """Verifica che pipeline_step sia >= required_step. Se no, manda alert nel topic."""
    try:
        r = supabase.table("projects").select("pipeline_step,name").eq("id", project_id).execute()
        if not r.data:
            return False
        current = r.data[0].get("pipeline_step") or "problem_identified"
        name = r.data[0].get("name", f"Progetto {project_id}")

        # Terminal states: bloccano sempre
        if current in TERMINAL_STATES:
            logger.warning(f"[PIPELINE] {name} in stato terminale: {current}")
            return False

        cur_idx = _STEP_INDEX.get(current, 0)
        req_idx = _STEP_INDEX.get(required_step, 0)
        if cur_idx < req_idx:
            logger.warning(f"[PIPELINE] {name} blocco: current={current} richiesto={required_step}")
            if group_id and topic_id:
                _send_topic_raw(group_id, topic_id,
                    f"\u26d4 Cantiere {name}: step fuori ordine.\n"
                    f"Step attuale: {current}\nStep richiesto: {required_step}\n"
                    f"Completa i passi precedenti prima di procedere.")
            return False
        return True
    except Exception as e:
        logger.warning(f"[PIPELINE] check_step error: {e}")
        return True  # fail-open: non blocca per errori di rete


def _get_group_id():
    """Legge telegram_group_id da org_config."""
    try:
        r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
        if r.data:
            val = r.data[0]["value"]
            if isinstance(val, (int, float)):
                return int(val)
            return json.loads(str(val))
    except Exception:
        pass
    return None


# ── Dedup send ─────────────────────────────────────────────────────────────────
_dedup_cache: dict = {}   # (group_id, topic_id, hash) -> timestamp
_DEDUP_TTL = 60           # secondi


def _send_topic_raw(group_id, topic_id, text, reply_markup=None):
    """Invia Telegram senza dedup (uso interno)."""
    if not TELEGRAM_BOT_TOKEN:
        return
    chat_id = group_id if group_id else None
    if not chat_id:
        return
    payload = {"chat_id": chat_id, "text": text[:4096]}
    if topic_id:
        payload["message_thread_id"] = topic_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        _requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload, timeout=10,
        )
    except Exception as e:
        logger.warning(f"[PIPELINE_SEND] {e}")


def send_topic_dedup(group_id, topic_id, text, reply_markup=None):
    """Invia nel topic con dedup 60s: stesso contenuto -> skip silenzioso."""
    h = hashlib.md5(text[:500].encode()).hexdigest()
    key = (group_id, topic_id, h)
    now = time.time()
    if now - _dedup_cache.get(key, 0) < _DEDUP_TTL:
        logger.debug(f"[DEDUP] skip duplicato topic={topic_id}")
        return
    _dedup_cache[key] = now
    if len(_dedup_cache) > 200:
        old = [k for k, t in _dedup_cache.items() if now - t > _DEDUP_TTL * 2]
        for k in old:
            _dedup_cache.pop(k, None)
    _send_topic_raw(group_id, topic_id, text, reply_markup)


# ── LOC counter ────────────────────────────────────────────────────────────────
def count_lines_of_code(code_text: str) -> int:
    """Conta righe non vuote nel testo di codice."""
    return sum(1 for line in code_text.split("\n") if line.strip())


def update_project_loc(project_id: int, new_files_loc: int, new_files_count: int,
                       files_added: list, cost_usd: float, pipeline_step: str):
    """Aggiorna lines_of_code, files_count e scrive project_report."""
    try:
        r = supabase.table("projects").select("lines_of_code,files_count").eq("id", project_id).execute()
        current_loc = (r.data[0].get("lines_of_code") or 0) if r.data else 0
        current_files = (r.data[0].get("files_count") or 0) if r.data else 0
        total_loc = current_loc + new_files_loc
        total_files = current_files + new_files_count
        supabase.table("projects").update({
            "lines_of_code": total_loc,
            "files_count": total_files,
            "last_code_update": datetime.now(timezone.utc).isoformat(),
        }).eq("id", project_id).execute()
        supabase.table("project_reports").insert({
            "project_id": project_id,
            "pipeline_step": pipeline_step,
            "lines_of_code": total_loc,
            "files_count": total_files,
            "files_added": files_added,
            "cost_usd": round(cost_usd, 6),
        }).execute()
        return total_loc
    except Exception as e:
        logger.warning(f"[PIPELINE] update_loc {project_id}: {e}")
        return new_files_loc


# ── Phase card with Haiku explanation ─────────────────────────────────────────
def generate_phase_card(project_name: str, fase_n: int, fase_desc: str,
                        code_output: str, spec_md: str, stack: list,
                        total_loc: int, files_list: str) -> str:
    """Genera card fase build con spiegazione semplice + istruzioni test via Haiku."""
    spec_excerpt = (spec_md or "")[:1500]
    code_excerpt = (code_output or "")[:2000]
    stack_str = ", ".join(stack) if stack else "Python, Supabase"

    prompt = (
        f"Sei il COO di brAIn. Il build agent ha completato la Fase {fase_n} ({fase_desc}) "
        f"del progetto '{project_name}' (stack: {stack_str}).\n\n"
        f"SPEC (estratto):\n{spec_excerpt}\n\n"
        f"Codice generato (estratto):\n{code_excerpt}\n\n"
        f"Scrivi una card di aggiornamento per il fondatore con ESATTAMENTE questo formato "
        f"(usa separatori, max 12 righe totali, italiano semplice, ZERO jargon tecnico):\n\n"
        f"Cosa abbiamo costruito:\n[spiegazione in 2 righe, linguaggio semplice]\n\n"
        f"Come funziona:\n[2 righe, spiega come se il cliente avesse 50 anni e non sa cosa e' un API]\n\n"
        f"Testalo adesso:\n[istruzioni concrete in 2-3 passi]"
    )

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        explanation = resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"[PHASE_CARD] Haiku error: {e}")
        explanation = (
            f"Cosa abbiamo costruito:\nFase {fase_n} - {fase_desc}\n\n"
            f"Testalo adesso:\nControlla i file sul repo GitHub."
        )

    card = (
        f"Fase {fase_n} completata\n"
        f"{fase_desc}\n"
        f"{SEP}\n"
        f"{explanation}\n"
        f"{SEP}\n"
        f"File creati:\n{files_list}\n"
        f"Codice totale: {total_loc} righe"
    )
    return card


# ── Blocker system ─────────────────────────────────────────────────────────────
def create_build_blocker(project_id: int, problem: str, action: str,
                         time_estimate: str, group_id, topic_id) -> int:
    """Crea azione bloccante in action_queue e manda alert nel topic."""
    action_id = None
    # Recupera user_id di Mirco per il blocker
    mirco_uid = 0
    try:
        uid_r = supabase.table("org_config").select("value").eq("key", "telegram_user_id").execute()
        if uid_r.data:
            mirco_uid = int(uid_r.data[0]["value"])
    except Exception:
        pass
    try:
        r = supabase.table("action_queue").insert({
            "user_id": mirco_uid,
            "action_type": "build_blocker",
            "project_id": project_id,
            "payload": {"problem": problem, "action": action, "time_estimate": time_estimate},
            "status": "pending",
        }).execute()
        if r.data:
            action_id = r.data[0]["id"]
    except Exception as e:
        logger.warning(f"[BLOCKER] action_queue insert: {e}")

    msg = (
        f"AZIONE RICHIESTA — pipeline bloccata\n"
        f"{SEP}\n"
        f"Problema: {problem}\n"
        f"{SEP}\n"
        f"Cosa devi fare:\n{action}\n"
        f"{SEP}\n"
        f"Stima: {time_estimate}"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "Completato",
             "callback_data": f"blocker_done:{project_id}:{action_id or 0}"},
            {"text": "Ho bisogno di aiuto",
             "callback_data": f"blocker_help:{project_id}:{action_id or 0}"},
        ]]
    }
    send_topic_dedup(group_id, topic_id, msg, reply_markup)
    try:
        supabase.table("projects").update({"pipeline_locked": True}).eq("id", project_id).execute()
    except Exception:
        pass
    return action_id or 0


def get_pending_blockers(project_id: int) -> list:
    """Ritorna lista di blockers pendenti per un progetto."""
    try:
        r = supabase.table("action_queue").select("id,payload,status,created_at").eq(
            "project_id", project_id
        ).eq("action_type", "build_blocker").eq("status", "pending").execute()
        return r.data or []
    except Exception:
        return []


def check_and_autostart_smoke(project_id: int) -> dict:
    """Controlla se tutti i blockers sono completati e avvia smoke test automaticamente."""
    pending = get_pending_blockers(project_id)
    if pending:
        titles = [p.get("payload", {}).get("problem", "azione") for p in pending]
        return {"status": "waiting", "pending_count": len(pending), "pending": titles}

    # Tutti completati — verifica pipeline step
    try:
        r = supabase.table("projects").select("pipeline_step,name,topic_id").eq("id", project_id).execute()
        if not r.data:
            return {"status": "error", "error": "project not found"}
        step = r.data[0].get("pipeline_step")
        topic_id = r.data[0].get("topic_id")
        pname = r.data[0].get("name", "")
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if step != "smoke_test_designing":
        return {"status": "skip", "reason": "pipeline_step=" + (step or "null")}

    # Auto-start smoke test
    group_id = _get_group_id()
    if group_id and topic_id:
        _send_topic_raw(group_id, topic_id,
                        "Tutte le azioni completate! Avvio smoke test automaticamente...")

    from execution.smoke import start_smoke_test
    result = start_smoke_test(project_id)
    logger.info("[AUTO_START] Smoke test avviato per project %s: %s", project_id, result.get("status"))
    return {"status": "autostarted", "smoke_result": result}


# ── Metodi disponibili label ──────────────────────────────────────────────────
METHOD_NAMES = {
    "cold_outreach": "Cold Outreach B2B (email/LinkedIn)",
    "landing_page_ads": "Landing Page + Ads (Google/Meta)",
    "concierge": "Concierge MVP (servizio manuale)",
    "pre_order": "Pre-Order / Deposito (landing con prezzo)",
    "paid_ads": "Paid Ads puri (Google Ads intent)",
    "cold_outreach_landing": "Cold Outreach + Landing Page",
}


# ── CSO Smoke Test Design — COMPLETO ─────────────────────────────────────────
def design_smoke_test(project_id: int) -> dict:
    """CSO progetta il piano smoke test COMPLETO e lo presenta a Mirco.
    Raccoglie TUTTI i dati (prospect reali, email, landing, KPI) PRIMA di mandare il piano.
    Zero N/A ammessi — ogni campo e' popolato con dati reali.
    """
    # Lazy imports per evitare circular
    from execution.smoke import (
        _find_prospects_perplexity, _generate_cold_email_sequence,
        _generate_landing_html, _generate_ads_plan,
    )
    import re

    try:
        r = supabase.table("projects").select(
            "name,spec_md,spec_human_md,topic_id,bos_id,"
            "brand_name,brand_email,brand_domain,smoke_test_method"
        ).eq("id", project_id).execute()
        if not r.data:
            return {"status": "error", "error": "project not found"}
        project = r.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name") or ("Progetto " + str(project_id))
    brand_name = project.get("brand_name") or name
    brand_email = project.get("brand_email") or ""
    brand_domain = project.get("brand_domain") or ""
    method = project.get("smoke_test_method") or "cold_outreach"
    topic_id = project.get("topic_id")
    group_id = _get_group_id()
    method_label = METHOD_NAMES.get(method, method)

    # ── 1. Carica soluzione COMPLETA ──
    solution = {}
    bos_id = project.get("bos_id")
    if bos_id:
        try:
            sol = supabase.table("solutions").select("*").eq("id", bos_id).execute()
            if sol.data:
                solution = sol.data[0]
        except Exception:
            pass

    sol_title = solution.get("title") or name
    sol_desc = (solution.get("description") or "")[:1000]
    sol_sector = solution.get("sector") or ""
    sol_target = solution.get("customer_segment") or ""

    # ── 2. Trova 50 prospect REALI via Perplexity ──
    target_desc = sol_target or sol_title
    if sol_sector:
        target_desc = target_desc + " nel settore " + sol_sector

    logger.info("[SMOKE_DESIGN] Cerco prospect per: %s", target_desc)
    prospects = _find_prospects_perplexity(target_desc, 50)
    prospects_count = len(prospects)
    logger.info("[SMOKE_DESIGN] Trovati %d prospect reali", prospects_count)

    # ── 3. Genera materiali in base al metodo ──
    email_sequence = []
    landing_html = ""
    ads_plan = {}

    if method in ("cold_outreach", "cold_outreach_landing"):
        email_sequence = _generate_cold_email_sequence(brand_name, sol_title, brand_email)
        logger.info("[SMOKE_DESIGN] Email sequence: %d touchpoint", len(email_sequence))

    if method in ("landing_page_ads", "cold_outreach_landing", "pre_order", "paid_ads"):
        landing_html = _generate_landing_html(brand_name, solution)
        logger.info("[SMOKE_DESIGN] Landing HTML: %d chars", len(landing_html))

    if method in ("landing_page_ads", "paid_ads"):
        ads_plan = _generate_ads_plan(brand_name, solution)
        logger.info("[SMOKE_DESIGN] Piano ads generato")

    # ── 4. Genera KPI specifici con Sonnet (basati su dati reali) ──
    kpi_prompt = (
        "Sei il CSO di brAIn. Genera KPI specifici per questo smoke test.\n\n"
        "Progetto: " + brand_name + "\n"
        "Soluzione: " + sol_title + "\n"
        "Settore: " + sol_sector + "\n"
        "Target: " + target_desc + "\n"
        "Metodo: " + method_label + "\n"
        "Prospect trovati: " + str(prospects_count) + "\n"
        "Durata test: 7 giorni\n\n"
        "Rispondi SOLO con JSON:\n"
        '{"kpi_success": "criterio successo con numero esatto basato su '
        + str(prospects_count) + ' prospect",'
        '"kpi_failure": "criterio fallimento con numero esatto",'
        '"duration_days": 7,'
        '"target_description": "descrizione specifica del target basata sui prospect trovati",'
        '"budget_eur": 0,'
        '"reasoning": "perche questi KPI sono realistici per questo settore"}'
    )
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": kpi_prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{[\s\S]*\}', raw)
        kpi_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.warning("[SMOKE_DESIGN] KPI generation: %s", e)
        kpi_data = {}

    kpi_success = kpi_data.get("kpi_success",
                               ">15% risposte positive (" + str(max(prospects_count * 15 // 100, 1)) +
                               " su " + str(prospects_count) + ")")
    kpi_failure = kpi_data.get("kpi_failure",
                               "<5% risposte positive (<" + str(max(prospects_count * 5 // 100, 1)) +
                               " su " + str(prospects_count) + ")")
    duration_days = kpi_data.get("duration_days", 7)
    budget_eur = kpi_data.get("budget_eur", 0)
    if method in ("landing_page_ads", "paid_ads"):
        budget_eur = ads_plan.get("budget_eur", 75) if ads_plan else 75

    # ── 5. Costruisci piano completo ──
    prospect_sample = prospects[:5]
    prospect_sample_lines = ""
    for p in prospect_sample:
        company = p.get("company", "")
        pname = p.get("name", "")
        contact = p.get("contact", "")
        line = "- " + company
        if pname:
            line += " | " + pname
        if contact:
            line += " | " + contact
        prospect_sample_lines += line + "\n"
    if prospects_count > 5:
        prospect_sample_lines += "  [+ " + str(prospects_count - 5) + " altri]\n"

    email_preview = ""
    if email_sequence:
        for em in email_sequence[:3]:
            day_n = str(em.get("day", "?"))
            subj = em.get("subject", "")
            email_preview += "- Giorno " + day_n + ": \"" + subj + "\"\n"

    landing_status = ""
    if landing_html and len(landing_html) > 100:
        landing_status = "Landing page HTML generata (" + str(len(landing_html)) + " chars, pronta per pubblicazione)"
    elif method in ("landing_page_ads", "cold_outreach_landing", "pre_order", "paid_ads"):
        landing_status = "Landing page da generare"

    ads_summary = ""
    if ads_plan:
        channels = ", ".join(ads_plan.get("channels", ["Google Ads"]))
        ads_budget = str(ads_plan.get("budget_eur", 50))
        ads_dur = str(ads_plan.get("duration_days", 5))
        ads_summary = channels + " — EUR" + ads_budget + " per " + ads_dur + " giorni"

    # Costruisci blockers specifici
    blockers = []
    blocker_details = []
    if method in ("cold_outreach", "cold_outreach_landing") and brand_domain:
        blockers.append("Registra dominio " + brand_domain + " e crea " + brand_email)
        blocker_details.append({
            "title": "Registra dominio " + brand_domain + " e crea " + brand_email,
            "steps": (
                "1. Vai su cloudflare.com/domains\n"
                "2. Cerca '" + brand_domain + "' — costo circa EUR10/anno\n"
                "3. Acquista il dominio\n"
                "4. Vai su Email Routing e crea " + brand_email + "\n"
                "5. Torna qui e premi 'Completato'"
            ),
            "time": "10 minuti",
        })
    if method in ("landing_page_ads", "cold_outreach_landing", "pre_order", "paid_ads"):
        blockers.append("Pubblica landing page e condividi URL")
        blocker_details.append({
            "title": "Pubblica landing page e condividi URL",
            "steps": (
                "1. L'HTML e' gia' pronto (generato dal sistema)\n"
                "2. Opzione A: vai su carrd.co → 'New site' → incolla HTML\n"
                "3. Opzione B: crea repo GitHub Pages → carica index.html\n"
                "4. Copia l'URL della pagina pubblicata\n"
                "5. Torna qui e mandami l'URL"
            ),
            "time": "15 minuti",
        })
    if ads_plan:
        kw_list = ads_plan.get("keywords_intent", ads_plan.get("keywords", []))[:5]
        kw_str = ", ".join(kw_list) if kw_list else "keyword del settore"
        blockers.append("Configura campagna ads")
        blocker_details.append({
            "title": "Configura campagna " + ", ".join(ads_plan.get("channels", ["Google Ads"])),
            "steps": (
                "1. Vai su ads.google.com (o Meta Ads)\n"
                "2. Crea campagna con keyword: " + kw_str + "\n"
                "3. Budget: EUR" + str(ads_plan.get("daily_budget_eur", ads_plan.get("budget_eur", 50) // 5)) + "/giorno\n"
                "4. Durata: " + str(ads_plan.get("duration_days", 5)) + " giorni\n"
                "5. Torna qui e conferma"
            ),
            "time": "30 minuti",
        })

    plan = {
        "method": method_label,
        "target_description": kpi_data.get("target_description", target_desc),
        "prospects_count": prospects_count,
        "prospects_sample": prospect_sample,
        "email_sequence": email_sequence,
        "landing_html_generated": bool(landing_html and len(landing_html) > 100),
        "ads_plan": ads_plan,
        "kpi_success": kpi_success,
        "kpi_failure": kpi_failure,
        "duration_days": duration_days,
        "budget_eur": budget_eur,
        "blockers": blockers,
        "blocker_details": blocker_details,
    }

    # ── 6. Salva tutto in DB ──
    try:
        update_data = {
            "smoke_test_plan": json.dumps(plan),
            "smoke_test_kpi": json.dumps({
                "success": kpi_success,
                "failure": kpi_failure,
            }),
            "smoke_test_kpi_target": json.dumps({
                "success": kpi_success,
                "failure": kpi_failure,
            }),
        }
        if landing_html:
            update_data["landing_page_html"] = landing_html
        supabase.table("projects").update(update_data).eq("id", project_id).execute()
    except Exception as e:
        logger.warning("[SMOKE_DESIGN] DB update: %s", e)

    # Avanza pipeline
    advance_pipeline_step(project_id, "smoke_test_designing")

    # ── 7. Costruisci card COMPLETA ──
    blockers_text = ""
    if blockers:
        blockers_text = "\nAZIONI RICHIESTE DA MIRCO:\n"
        for i, b in enumerate(blockers, 1):
            blockers_text += str(i) + ". " + b + "\n"

    budget_line = ""
    if budget_eur and budget_eur > 0:
        budget_line = "Budget stimato: EUR" + str(budget_eur) + "\n"

    card = (
        "PIANO SMOKE TEST — " + brand_name + "\n"
        + SEP + "\n"
        "Metodo: " + method_label + "\n"
        "Target: " + (kpi_data.get("target_description") or target_desc) + "\n"
        "Settore: " + (sol_sector or "non specificato") + "\n\n"
        "PROSPECT TROVATI: " + str(prospects_count) + "/50\n"
        + prospect_sample_lines + "\n"
    )
    if email_preview:
        card += "SEQUENZA EMAIL (" + str(len(email_sequence)) + " touchpoint):\n" + email_preview + "\n"
    if landing_status:
        card += "LANDING PAGE: " + landing_status + "\n"
    if ads_summary:
        card += "ADS: " + ads_summary + "\n"
    card += (
        "\nDurata: " + str(duration_days) + " giorni\n"
        + budget_line
        + "KPI successo: " + kpi_success + "\n"
        "KPI fallimento: " + kpi_failure + "\n"
        + blockers_text
        + SEP
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "Avvia smoke test",
                 "callback_data": "smoke_design_approve:" + str(project_id)},
                {"text": "Modifica piano",
                 "callback_data": "smoke_design_modify:" + str(project_id)},
            ],
            [
                {"text": "Cambia metodo",
                 "callback_data": "smoke_design_method:" + str(project_id)},
                {"text": "Archivia idea",
                 "callback_data": "smoke_design_archive:" + str(project_id)},
            ],
        ]
    }

    if group_id and topic_id:
        _send_topic_raw(group_id, topic_id, card, reply_markup)
    else:
        _send_to_chief_topic("cso", card, reply_markup)

    # ── 8. Crea blockers nel sistema + manda documento operativo ──
    if group_id and topic_id and blocker_details:
        for bd in blocker_details:
            create_build_blocker(
                project_id, bd["title"], bd["steps"], bd["time"],
                group_id, topic_id,
            )
        # Documento operativo come secondo messaggio
        _send_operational_document(
            project_id, brand_name, method, method_label,
            blockers, blocker_details, prospects_count,
            duration_days, email_sequence, landing_html,
            group_id, topic_id,
        )

    logger.info("[SMOKE_DESIGN] Piano COMPLETO generato per %s method=%s prospect=%d (project_id=%s)",
                brand_name, method, prospects_count, project_id)
    return {"status": "ok", "project_id": project_id, "plan": plan, "method": method,
            "prospects_count": prospects_count}


def _send_operational_document(project_id, brand_name, method, method_label,
                               blockers, blocker_details, prospects_count,
                               duration_days, email_sequence, landing_html,
                               group_id, topic_id):
    """Invia documento operativo dettagliato con istruzioni passo-passo per Mirco."""

    # Sezione azioni richieste
    actions_text = ""
    for i, bd in enumerate(blocker_details, 1):
        actions_text += (
            str(i) + ". " + bd["title"] + " — stima " + bd["time"] + "\n"
            "   Come fare:\n"
        )
        for step_line in bd["steps"].split("\n"):
            if step_line.strip():
                actions_text += "   " + step_line + "\n"
        actions_text += "\n"

    # Cosa fara' il sistema
    auto_actions = "- Quando completi tutte le azioni → lo smoke test parte in automatico\n"
    if method in ("cold_outreach", "cold_outreach_landing"):
        auto_actions += (
            "- Il sistema inviera' " + str(prospects_count) + " email ai prospect trovati\n"
            "- Sequenza: " + str(len(email_sequence)) + " touchpoint automatici\n"
        )
    if method in ("landing_page_ads", "cold_outreach_landing", "pre_order", "paid_ads"):
        auto_actions += "- Il sistema monitorera' la landing page e raccogliera' i lead\n"
    auto_actions += (
        "- Ogni giorno riceverai un aggiornamento qui nel cantiere\n"
        "- Dopo " + str(duration_days) + " giorni → analisi automatica + raccomandazione CSO\n"
    )

    # Checklist azioni
    checklist = ""
    for b in blockers:
        checklist += "[ ] " + b + "\n"

    doc = (
        "DOCUMENTO OPERATIVO — SMOKE TEST " + brand_name + "\n"
        + SEP + "\n\n"
        "COSA MI SERVE DA TE (Mirco):\n\n"
        + actions_text
        + "COSA FARA' IL SISTEMA AUTOMATICAMENTE:\n"
        + auto_actions + "\n"
        "TIMELINE:\n"
        "- Giorno 0: completa le azioni qui sopra\n"
        "- Giorno 1-" + str(duration_days) + ": outreach automatico + raccolta dati\n"
        "- Giorno " + str(duration_days + 1) + ": analisi automatica risultati + raccomandazione GO/NO-GO\n\n"
        "STATUS AZIONI:\n"
        + checklist + "\n"
        "REMINDER: se non completi le azioni, riceverai un promemoria ogni 24 ore.\n"
        "Dopo 7 giorni senza azione → alert nel canale #strategy.\n"
        + SEP
    )
    _send_topic_raw(group_id, topic_id, doc)

    # Salva timestamp invio documento nel piano per reminder
    try:
        r = supabase.table("projects").select("smoke_test_plan").eq("id", project_id).execute()
        if r.data:
            plan_stored = json.loads(r.data[0].get("smoke_test_plan") or "{}")
            plan_stored["doc_sent_at"] = datetime.now(timezone.utc).isoformat()
            plan_stored["last_reminder_at"] = datetime.now(timezone.utc).isoformat()
            plan_stored["reminder_count"] = 0
            supabase.table("projects").update({
                "smoke_test_plan": json.dumps(plan_stored),
            }).eq("id", project_id).execute()
    except Exception:
        pass


def send_smoke_daily_update(project_id: int, day: int, total_days: int,
                            contacts_sent: int, total_contacts: int,
                            positive: int) -> None:
    """Invia aggiornamento giornaliero smoke test nel topic cantiere."""
    try:
        r = supabase.table("projects").select("name,topic_id").eq("id", project_id).execute()
        if not r.data:
            return
        name = r.data[0].get("name", "")
        topic_id = r.data[0].get("topic_id")
    except Exception:
        return

    pct = round(positive / max(contacts_sent, 1) * 100)
    msg = (
        f"Giorno {day}/{total_days} — "
        f"{contacts_sent}/{total_contacts} contatti, "
        f"{positive} risposte positive ({pct}%)"
    )
    group_id = _get_group_id()
    if group_id and topic_id:
        _send_topic_raw(group_id, topic_id, msg)


def generate_smoke_results_card(project_id: int, smoke_id: int) -> str:
    """Genera report completo risultati smoke test per Mirco.
    Include dati quantitativi + qualitativi + raccomandazione CSO.
    Avanza pipeline a smoke_test_results_ready.
    """
    try:
        proj = supabase.table("projects").select(
            "name,brand_name,smoke_test_kpi,smoke_test_method,smoke_test_results,topic_id"
        ).eq("id", project_id).execute()
        if not proj.data:
            return ""
        pdata = proj.data[0]
        brand_name = pdata.get("brand_name", pdata.get("name", ""))
        method = pdata.get("smoke_test_method", "cold_outreach")
        kpi = json.loads(pdata.get("smoke_test_kpi") or "{}")
        results = json.loads(pdata.get("smoke_test_results") or "{}")
        topic_id = pdata.get("topic_id")
    except Exception:
        return ""

    # Carica dati smoke test
    try:
        st = supabase.table("smoke_tests").select("*").eq("id", smoke_id).execute()
        if not st.data:
            return ""
        smoke = st.data[0]
    except Exception:
        return ""

    # Metriche
    total = results.get("total_reached", smoke.get("prospects_count", 0))
    positive = results.get("positive", smoke.get("positive_responses", 0))
    negative = results.get("negative", smoke.get("negative_responses", 0))
    no_resp = results.get("no_response", smoke.get("no_response", 0))
    demos = results.get("demo_requests", smoke.get("demo_requests", 0))
    landing_views = results.get("landing_views", smoke.get("landing_visits", 0))
    landing_signups = results.get("landing_signups", smoke.get("forms_compiled", 0))
    total_cost = results.get("total_cost_eur", smoke.get("total_cost_eur", 0))
    cost_per_lead = results.get("cost_per_lead", 0)

    pct_pos = results.get("pct_positive", round(positive / max(total, 1) * 100))
    pct_neg = round(negative / max(total, 1) * 100)
    pct_no = round(no_resp / max(total, 1) * 100)

    kpi_success = kpi.get("success", "N/A")

    # Insights (gia' calcolati da analyze_smoke_results)
    insights = json.loads(smoke.get("spec_insights") or "{}")
    cso_rec = insights.get("recommendation", smoke.get("recommendation", ""))
    cso_reasoning = insights.get("reasoning", smoke.get("cso_recommendation", ""))
    risk_if_go = insights.get("risk_if_go", "")
    opportunity = insights.get("opportunity", "")
    kpi_met = insights.get("kpi_met", pct_pos >= 15)
    kpi_result = "RAGGIUNTO" if kpi_met else "NON RAGGIUNTO"

    # Se non ci sono insights pre-calcolati, genera raccomandazione via Haiku
    if not cso_rec:
        try:
            fb_raw = smoke.get("qualitative_feedback") or "[]"
            if isinstance(fb_raw, str):
                fb_raw = json.loads(fb_raw)
            fb_str = json.dumps(fb_raw[:3], ensure_ascii=False)
            rec_prompt = (
                "Sei il CSO di brAIn. Risultati smoke test per '" + brand_name + "':\n"
                "Prospect: " + str(total) + ", Positive: " + str(positive) +
                " (" + str(pct_pos) + "%), Negative: " + str(negative) +
                ", Demo: " + str(demos) + "\n"
                "KPI successo: " + kpi_success + "\n"
                "Feedback: " + fb_str + "\n\n"
                "Raccomandazione GO/PIVOT/NO-GO in 2 righe con motivazione basata sui dati."
            )
            rec_resp = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": rec_prompt}],
            )
            cso_reasoning = rec_resp.content[0].text.strip()
            cso_rec = "GO" if kpi_met else "NO-GO"
        except Exception:
            cso_reasoning = "KPI " + kpi_result + " (" + str(pct_pos) + "% risposte positive)"
            cso_rec = "GO" if kpi_met else "NO-GO"

    # Salva raccomandazione
    try:
        supabase.table("smoke_tests").update({
            "cso_recommendation": cso_reasoning,
        }).eq("id", smoke_id).execute()
    except Exception:
        pass

    # Feedback qualitativo
    feedback = insights.get("top_feedback", [])
    if not feedback:
        raw_fb = smoke.get("qualitative_feedback") or []
        if isinstance(raw_fb, str):
            raw_fb = json.loads(raw_fb)
        feedback = raw_fb[:3]

    fb_lines = ""
    for i, fb in enumerate(feedback[:3], 1):
        quote = fb if isinstance(fb, str) else str(fb)
        fb_lines += "  " + str(i) + ". \"" + quote + "\"\n"
    if not fb_lines:
        fb_lines = "  (nessun feedback qualitativo raccolto)\n"

    # Obiezioni
    objections = insights.get("objections", [])
    obj_lines = ""
    for obj in objections[:3]:
        obj_lines += "  - " + obj + "\n"

    # Landing page line
    landing_line = ""
    if landing_views or landing_signups:
        landing_ctr = round(landing_signups / max(landing_views, 1) * 100, 1)
        landing_line = (
            "Email/lead raccolti: " + str(landing_signups) + "\n"
            "CTR landing: " + str(landing_ctr) + "%\n"
        )

    cost_line = ""
    if total_cost:
        cost_line = (
            "Costo totale: EUR" + str(total_cost) + "\n"
            "Costo per lead: EUR" + str(cost_per_lead) + "\n"
        )

    method_names = {
        "cold_outreach": "Cold Outreach B2B",
        "landing_page_ads": "Landing Page + Ads",
        "concierge": "Concierge MVP",
        "pre_order": "Pre-Order / Deposito",
        "paid_ads": "Paid Ads",
        "cold_outreach_landing": "Cold Outreach + Landing",
    }

    # Date
    started = smoke.get("started_at", "")
    completed = smoke.get("completed_at", "")
    dates_line = ""
    if started and completed:
        try:
            s = datetime.fromisoformat(started.replace("Z", "+00:00")).strftime("%d/%m")
            c = datetime.fromisoformat(completed.replace("Z", "+00:00")).strftime("%d/%m")
            dates_line = "Durata: " + s + " -> " + c + "\n"
        except Exception:
            pass

    card = (
        "RISULTATI SMOKE TEST — " + brand_name + "\n"
        + SEP + "\n"
        + dates_line
        + "Metodo: " + method_names.get(method, method) + "\n\n"
        "DATI QUANTITATIVI:\n"
        "Prospect raggiunti: " + str(total) + "\n"
        "Risposte positive: " + str(positive) + " (" + str(pct_pos) + "%)\n"
        "Risposte negative: " + str(negative) + " (" + str(pct_neg) + "%)\n"
        "No risposta: " + str(no_resp) + " (" + str(pct_no) + "%)\n"
        "Richieste approfondimento: " + str(demos) + "\n"
        + landing_line
        + cost_line + "\n"
        "SEGNALI QUALITATIVI:\n"
        "Top 3 feedback:\n" + fb_lines + "\n"
    )

    if obj_lines:
        card += "Obiezioni principali:\n" + obj_lines + "\n"

    card += (
        "KPI target: " + kpi_success + " -> " + kpi_result + "\n\n"
        "RACCOMANDAZIONE CSO:\n"
        + cso_rec + " — " + cso_reasoning + "\n"
    )

    if risk_if_go:
        card += "Rischio se vai avanti: " + risk_if_go + "\n"
    if opportunity:
        card += "Opportunita': " + opportunity + "\n"

    card += SEP

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "GO — avvia build",
                 "callback_data": "smoke_go:" + str(project_id) + ":" + str(smoke_id)},
                {"text": "NO-GO — archivia",
                 "callback_data": "smoke_nogo:" + str(project_id) + ":" + str(smoke_id)},
            ],
            [
                {"text": "PIVOT — modifica soluzione",
                 "callback_data": "smoke_pivot:" + str(project_id) + ":" + str(smoke_id)},
            ],
        ]
    }

    # Avanza pipeline
    advance_pipeline_step(project_id, "smoke_test_results_ready")

    group_id = _get_group_id()
    if group_id and topic_id:
        _send_topic_raw(group_id, topic_id, card, reply_markup)

    return card


def handle_smoke_go(project_id: int) -> bool:
    """Mirco dice GO. Avanza a smoke_go, poi a spec_pending. Passa al COO."""
    advance_pipeline_step(project_id, "smoke_go")
    advance_pipeline_step(project_id, "spec_pending")
    try:
        supabase.table("projects").update({
            "pipeline_territory": "coo",
        }).eq("id", project_id).execute()
    except Exception:
        pass
    logger.info(f"[PIPELINE] GO per project {project_id} — passaggio a COO")
    return True


def handle_smoke_nogo(project_id: int) -> bool:
    """Mirco dice NO-GO. Archivia, Idea Recycler rivaluta tra 90 giorni."""
    advance_pipeline_step(project_id, "smoke_nogo")
    try:
        supabase.table("projects").update({
            "status": "archived",
            "pipeline_territory": "cso",
        }).eq("id", project_id).execute()
        # Schedula rivalutazione tra 90 giorni
        supabase.table("action_queue").insert({
            "action_type": "idea_recycle",
            "project_id": project_id,
            "payload": json.dumps({"reason": "smoke_nogo", "recycle_after_days": 90}),
            "status": "scheduled",
        }).execute()
    except Exception as e:
        logger.warning(f"[PIPELINE] nogo archive error: {e}")
    logger.info(f"[PIPELINE] NO-GO per project {project_id} — archiviato")
    return True


def handle_smoke_pivot(project_id: int) -> bool:
    """Mirco dice Pivota. Torna a solution_hypothesized con feedback integrato."""
    advance_pipeline_step(project_id, "solution_hypothesized")
    try:
        supabase.table("projects").update({
            "pipeline_territory": "cso",
        }).eq("id", project_id).execute()
    except Exception:
        pass
    logger.info(f"[PIPELINE] PIVOT per project {project_id} — torna a CSO")
    return True


def send_restaurant_reposition(project_id: int) -> bool:
    """Manda le due opzioni di riposizionamento al cantiere ristorante."""
    try:
        r = supabase.table("projects").select("name,topic_id").eq("id", project_id).execute()
        if not r.data:
            return False
        name = r.data[0].get("name", "")
        topic_id = r.data[0].get("topic_id")
    except Exception:
        return False

    msg = (
        f"RIPOSIZIONAMENTO — {name}\n"
        f"{SEP}\n"
        f"Il progetto ha saltato lo smoke test ed e' andato "
        f"direttamente al build. Errore architetturale.\n\n"
        f"Opzione A (raccomandata):\n"
        f"Il build fatto finora diventa un prototipo demo. "
        f"Il CSO usa questo prototipo come materiale per lo smoke test "
        f"(video demo del bot funzionante da mandare ai 50 prospect). "
        f"Poi decidi GO/NO-GO con dati reali.\n\n"
        f"Opzione B:\n"
        f"Si ignora lo smoke test per questo primo cantiere "
        f"dato che e' gia' avanzato, ma la pipeline corretta si applica "
        f"a tutti i cantieri futuri.\n"
        f"{SEP}"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "Opzione A — Smoke test con demo",
             "callback_data": f"restaurant_option_a:{project_id}"},
            {"text": "Opzione B — Continua build",
             "callback_data": f"restaurant_option_b:{project_id}"},
        ]]
    }
    group_id = _get_group_id()
    if group_id and topic_id:
        _send_topic_raw(group_id, topic_id, msg, reply_markup)
        return True
    return False


# ── Smoke test proposal (legacy, rimappato a design) ─────────────────────────
def send_smoke_proposal(project_id: int, group_id, topic_id):
    """Legacy: rimappa a design_smoke_test."""
    return design_smoke_test(project_id)


def _send_to_chief_topic(chief_id: str, text: str, reply_markup=None):
    """Invia messaggio al Forum Topic di un Chief."""
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        topic_key = f"chief_topic_{chief_id}"
        r = supabase.table("org_config").select("value").eq("key", topic_key).execute()
        if not r.data:
            return
        topic_id = int(r.data[0]["value"])
        group_id = _get_group_id()
        if group_id:
            payload = {
                "chat_id": group_id,
                "message_thread_id": topic_id,
                "text": text[:4096],
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            _requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload, timeout=10,
            )
    except Exception as e:
        logger.warning(f"[PIPELINE] _send_to_chief_topic {chief_id}: {e}")
