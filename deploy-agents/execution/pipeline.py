"""
brAIn — execution/pipeline.py
Pipeline utilities: step guard, blocker, phase card, LOC counter, dedup, smoke proposal.
"""
from __future__ import annotations
import hashlib, time
from datetime import datetime, timezone
from typing import Optional
import requests as _requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger

# ── Step ordering ──────────────────────────────────────────────────────────────
PIPELINE_STEPS = [
    "spec_pending", "spec_approved",
    "legal_pending", "legal_approved",
    "smoke_pending", "smoke_approved", "smoke_done",
    "build_pending", "build_running", "build_done",
    "launched",
]
_STEP_INDEX = {s: i for i, s in enumerate(PIPELINE_STEPS)}


def advance_pipeline_step(project_id: int, new_step: str) -> bool:
    """Aggiorna pipeline_step. Ritorna False se step non valido."""
    if new_step not in _STEP_INDEX:
        logger.warning(f"[PIPELINE] step sconosciuto: {new_step}")
        return False
    try:
        supabase.table("projects").update({
            "pipeline_step": new_step,
        }).eq("id", project_id).execute()
        return True
    except Exception as e:
        logger.warning(f"[PIPELINE] advance_step {project_id} → {new_step}: {e}")
        return False


def check_pipeline_step(project_id: int, required_step: str,
                        group_id=None, topic_id=None) -> bool:
    """Verifica che pipeline_step sia >= required_step. Se no, manda alert nel topic."""
    try:
        r = supabase.table("projects").select("pipeline_step,name").eq("id", project_id).execute()
        if not r.data:
            return False
        current = r.data[0].get("pipeline_step") or "spec_pending"
        name = r.data[0].get("name", f"Progetto {project_id}")
        cur_idx = _STEP_INDEX.get(current, 0)
        req_idx = _STEP_INDEX.get(required_step, 0)
        if cur_idx < req_idx:
            logger.warning(f"[PIPELINE] {name} blocco: current={current} richiesto={required_step}")
            if group_id and topic_id:
                _send_topic_raw(group_id, topic_id,
                    f"\u26a0\ufe0f Cantiere {name}: step fuori ordine.\n"
                    f"Step attuale: {current}\nStep richiesto: {required_step}\n"
                    f"Completa i passi precedenti prima di procedere.")
            return False
        return True
    except Exception as e:
        logger.warning(f"[PIPELINE] check_step error: {e}")
        return True  # fail-open: non blocca per errori di rete


# ── Dedup send ─────────────────────────────────────────────────────────────────
_dedup_cache: dict = {}   # (group_id, topic_id, hash) → timestamp
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
    """Invia nel topic con dedup 60s: stesso contenuto → skip silenzioso."""
    h = hashlib.md5(text[:500].encode()).hexdigest()
    key = (group_id, topic_id, h)
    now = time.time()
    if now - _dedup_cache.get(key, 0) < _DEDUP_TTL:
        logger.debug(f"[DEDUP] skip duplicato topic={topic_id}")
        return
    _dedup_cache[key] = now
    # Pulisci cache vecchie ogni 200 entries
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
        # leggi valori attuali
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
        # project_report
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
    sep = "\u2501" * 15

    prompt = (
        f"Sei il COO di brAIn. Il build agent ha completato la Fase {fase_n} ({fase_desc}) "
        f"del progetto '{project_name}' (stack: {stack_str}).\n\n"
        f"SPEC (estratto):\n{spec_excerpt}\n\n"
        f"Codice generato (estratto):\n{code_excerpt}\n\n"
        f"Scrivi una card di aggiornamento per il fondatore con ESATTAMENTE questo formato "
        f"(usa ━━━━━ come separatore, max 12 righe totali, italiano semplice, ZERO jargon tecnico):\n\n"
        f"📦 Cosa abbiamo costruito:\n[spiegazione in 2 righe, linguaggio da ristorante non da developer]\n\n"
        f"⚙️ Come funziona:\n[2 righe, spiega come se il cliente avesse 50 anni e non sa cosa è un API]\n\n"
        f"🧪 Testalo adesso:\n[istruzioni concrete in 2-3 passi — es. 'Manda questo messaggio su WhatsApp: ...' "
        f"o 'Apri questa URL: ...' o 'Invia questa email a...']\n\n"
        f"Se il progetto usa WhatsApp, le istruzioni test devono sempre includere un messaggio WhatsApp concreto "
        f"che Mirco può mandare in 30 secondi. Sii specifico e pratico."
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
        explanation = f"📦 Cosa abbiamo costruito:\nFase {fase_n} — {fase_desc}\n\n🧪 Testalo adesso:\nControlla i file sul repo GitHub."

    card = (
        f"\u256d\u2500\u2500 Fase {fase_n} completata \u2500\u2500\u256e\n"
        f"📋 {fase_desc}\n"
        f"{sep}\n"
        f"{explanation}\n"
        f"{sep}\n"
        f"📝 File creati:\n{files_list}\n"
        f"📊 Codice totale: {total_loc} righe"
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

    sep = "\u2501" * 15
    msg = (
        f"\U0001f6a7 AZIONE RICHIESTA\n"
        f"Senza questo non posso continuare\n"
        f"{sep}\n"
        f"Problema: {problem}\n"
        f"{sep}\n"
        f"Cosa devi fare:\n{action}\n"
        f"{sep}\n"
        f"Stima tempo: {time_estimate}"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\u2705 Ho completato l'azione",
             "callback_data": f"blocker_done:{project_id}:{action_id or 0}"},
            {"text": "\u2753 Ho bisogno di aiuto",
             "callback_data": f"blocker_help:{project_id}:{action_id or 0}"},
        ]]
    }
    send_topic_dedup(group_id, topic_id, msg, reply_markup)
    # Blocca pipeline
    try:
        supabase.table("projects").update({"pipeline_locked": True}).eq("id", project_id).execute()
    except Exception:
        pass
    return action_id or 0


# ── Smoke test proposal (lightweight) ─────────────────────────────────────────
def send_smoke_proposal(project_id: int, group_id, topic_id):
    """Manda proposta smoke test nel topic con [✅ Approva][✏️ Modifica]."""
    try:
        r = supabase.table("projects").select("name,spec_md").eq("id", project_id).execute()
        if not r.data:
            return
        project = r.data[0]
        name = project.get("name", f"Progetto {project_id}")
        spec_md = (project.get("spec_md") or "")[:2000]
    except Exception as e:
        logger.warning(f"[SMOKE_PROPOSAL] load: {e}")
        return

    # Genera descrizione smoke test via Haiku
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": (
                f"Sei il COO di brAIn. Devi proporre uno smoke test per il progetto '{name}'.\n"
                f"SPEC: {spec_md}\n\n"
                f"Scrivi in 3 righe semplici (italiano, zero jargon):\n"
                f"1. Chi contatteremo (target specifico)\n"
                f"2. Come li contatteremo (email/LinkedIn/WhatsApp)\n"
                f"3. Cosa chiederemo/mostreremo\n"
                f"Solo le 3 righe, niente altro."
            )}],
        )
        desc = resp.content[0].text.strip()
    except Exception:
        desc = f"Proposta smoke test per {name}: contatto prospect, raccolta feedback, analisi conversione."

    sep = "\u2501" * 15
    msg = (
        f"\U0001f9ea Proposta Smoke Test — {name}\n"
        f"{sep}\n"
        f"{desc}\n"
        f"{sep}\n"
        f"Approvi l'avvio del smoke test?"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "\u2705 Approva Smoke Test",
             "callback_data": f"smoke_proposal_approve:{project_id}"},
            {"text": "\u270f\ufe0f Modifica SPEC",
             "callback_data": f"smoke_modify_spec:{project_id}"},
        ]]
    }
    send_topic_dedup(group_id, topic_id, msg, reply_markup)
    advance_pipeline_step(project_id, "smoke_pending")
