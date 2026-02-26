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
    try:
        r = supabase.table("action_queue").insert({
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


# ── CSO Smoke Test Design ─────────────────────────────────────────────────────
def design_smoke_test(project_id: int) -> dict:
    """CSO progetta il piano smoke test e lo presenta a Mirco nel topic cantiere.
    Avanza pipeline a smoke_test_designing.
    """
    try:
        r = supabase.table("projects").select(
            "name,spec_md,spec_human_md,topic_id,bos_id"
        ).eq("id", project_id).execute()
        if not r.data:
            return {"status": "error", "error": "project not found"}
        project = r.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = (project.get("spec_md") or "")[:3000]
    topic_id = project.get("topic_id")
    group_id = _get_group_id()

    # CSO genera piano smoke test con Sonnet
    prompt = (
        f"Sei il CSO (Chief Strategy Officer) di brAIn. Devi progettare uno smoke test "
        f"per validare la domanda reale PRIMA di costruire il prodotto '{name}'.\n\n"
        f"SPEC progetto:\n{spec_md}\n\n"
        f"Rispondi SOLO con JSON valido:\n"
        f'{{"method": "descrizione metodo concreto (es. outreach 50 ristoranti Nord Italia via LinkedIn/email con demo video)",'
        f'"kpi_success": "criterio successo concreto con % (es. 30% risposta positiva, 10% richiesta demo live)",'
        f'"kpi_failure": "criterio fallimento concreto con % (es. <10% risposta, 0 richieste demo)",'
        f'"duration_days": 7,'
        f'"materials_needed": "lista materiali (es. video demo 60s, landing page, script outreach)",'
        f'"prospect_count": 50,'
        f'"target_description": "chi contatteremo (specifico)"}}'
    )

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        import re
        m = re.search(r'\{[\s\S]*\}', raw)
        plan = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.warning(f"[SMOKE_DESIGN] Sonnet error: {e}")
        plan = {
            "method": f"Outreach 50 prospect target via email/LinkedIn",
            "kpi_success": "30% risposta positiva, 10% richiesta demo",
            "kpi_failure": "<10% risposta, 0 richieste demo",
            "duration_days": 7,
            "materials_needed": "Video demo 60s, landing page, script outreach",
            "prospect_count": 50,
            "target_description": "target da definire",
        }

    # Salva piano in DB
    try:
        supabase.table("projects").update({
            "smoke_test_plan": json.dumps(plan),
            "smoke_test_kpi": json.dumps({
                "success": plan.get("kpi_success", ""),
                "failure": plan.get("kpi_failure", ""),
            }),
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.warning(f"[SMOKE_DESIGN] DB update: {e}")

    # Avanza pipeline
    advance_pipeline_step(project_id, "smoke_test_designing")

    # Manda card a Mirco nel topic cantiere
    card = (
        f"SMOKE TEST — {name}\n"
        f"{SEP}\n"
        f"Obiettivo: validare domanda reale prima di costruire\n"
        f"Metodo: {plan.get('method', 'N/A')}\n"
        f"KPI successo: {plan.get('kpi_success', 'N/A')}\n"
        f"KPI fallimento: {plan.get('kpi_failure', 'N/A')}\n"
        f"Durata: {plan.get('duration_days', 7)} giorni\n"
        f"Materiali necessari: {plan.get('materials_needed', 'N/A')}\n"
        f"{SEP}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "Avvia smoke test",
                 "callback_data": f"smoke_design_approve:{project_id}"},
                {"text": "Modifica piano",
                 "callback_data": f"smoke_design_modify:{project_id}"},
            ],
            [
                {"text": "Archivia idea",
                 "callback_data": f"smoke_design_archive:{project_id}"},
            ],
        ]
    }
    if group_id and topic_id:
        _send_topic_raw(group_id, topic_id, card, reply_markup)
    else:
        # Fallback: manda in #strategy
        _send_to_chief_topic("cso", card, reply_markup)

    logger.info(f"[SMOKE_DESIGN] Piano generato per {name} (project_id={project_id})")
    return {"status": "ok", "project_id": project_id, "plan": plan}


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
    Avanza pipeline a smoke_test_results_ready.
    """
    try:
        proj = supabase.table("projects").select("name,smoke_test_kpi,topic_id").eq("id", project_id).execute()
        if not proj.data:
            return ""
        name = proj.data[0].get("name", "")
        kpi = json.loads(proj.data[0].get("smoke_test_kpi") or "{}")
        topic_id = proj.data[0].get("topic_id")
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

    # Carica prospect
    try:
        prospects = supabase.table("smoke_test_prospects").select("status,name").eq("smoke_test_id", smoke_id).execute()
        all_p = prospects.data or []
    except Exception:
        all_p = []

    total = len(all_p)
    positive = smoke.get("positive_responses", 0)
    negative = smoke.get("negative_responses", 0)
    no_resp = smoke.get("no_response", 0)
    demos = smoke.get("demo_requests", 0)
    feedback = smoke.get("qualitative_feedback") or []
    if isinstance(feedback, str):
        feedback = json.loads(feedback)

    pct_pos = round(positive / max(total, 1) * 100)
    pct_neg = round(negative / max(total, 1) * 100)
    pct_no = round(no_resp / max(total, 1) * 100)

    kpi_success = kpi.get("success", "N/A")
    kpi_met = pct_pos >= 30  # default threshold

    # CSO raccomandazione via Haiku
    cso_rec = "GO" if kpi_met else "NO-GO"
    rec_reason = f"KPI raggiunto ({pct_pos}% risposte positive)" if kpi_met else f"KPI non raggiunto ({pct_pos}% < soglia)"
    try:
        rec_prompt = (
            f"Sei il CSO di brAIn. Analizza questi risultati smoke test per '{name}':\n"
            f"Prospect: {total}, Positive: {positive} ({pct_pos}%), Negative: {negative}, Demo: {demos}\n"
            f"KPI successo definito: {kpi_success}\n"
            f"Feedback qualitativo: {json.dumps(feedback[:3], ensure_ascii=False)}\n\n"
            f"Dai una raccomandazione GO o NO-GO in 2 righe con motivazione basata sui dati. "
            f"Solo la raccomandazione, niente altro."
        )
        rec_resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": rec_prompt}],
        )
        cso_rec_text = rec_resp.content[0].text.strip()
    except Exception:
        cso_rec_text = f"Raccomandazione: {cso_rec}. {rec_reason}"

    # Salva raccomandazione
    try:
        supabase.table("smoke_tests").update({
            "cso_recommendation": cso_rec_text,
        }).eq("id", smoke_id).execute()
    except Exception:
        pass

    # Feedback qualitativo top 3
    fb_lines = ""
    for i, fb in enumerate(feedback[:3], 1):
        quote = fb if isinstance(fb, str) else fb.get("quote", str(fb))
        fb_lines += f"  {i}. \"{quote}\"\n"
    if not fb_lines:
        fb_lines = "  (nessun feedback qualitativo raccolto)\n"

    kpi_result = "RAGGIUNTO" if kpi_met else "NON RAGGIUNTO"

    card = (
        f"RISULTATI SMOKE TEST — {name}\n"
        f"{SEP}\n"
        f"Prospect contattati: {total}\n"
        f"Risposte positive: {positive} ({pct_pos}%)\n"
        f"Risposte negative: {negative} ({pct_neg}%)\n"
        f"No risposta: {no_resp} ({pct_no}%)\n"
        f"Richieste demo: {demos}\n"
        f"Feedback qualitativo top 3:\n"
        f"{fb_lines}"
        f"KPI target: {kpi_success}\n"
        f"Risultato: {kpi_result}\n\n"
        f"Raccomandazione CSO: {cso_rec_text}\n"
        f"{SEP}"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "GO — procedi al build",
                 "callback_data": f"smoke_go:{project_id}:{smoke_id}"},
                {"text": "NO-GO — archivia",
                 "callback_data": f"smoke_nogo:{project_id}:{smoke_id}"},
            ],
            [
                {"text": "Pivota — modifica soluzione",
                 "callback_data": f"smoke_pivot:{project_id}:{smoke_id}"},
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
