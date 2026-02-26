"""
brAIn module: execution/legal.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import os, json, time, re, uuid
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, SUPABASE_ACCESS_TOKEN, DB_PASSWORD, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json
from execution.project import get_project_db, _send_to_topic


def run_legal_review(project_id):
    """MACRO-TASK 2: Review legale del progetto. Triggered dopo validazione SPEC."""
    start = time.time()
    logger.info(f"[LEGAL] Avvio review per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()

    if not spec_md:
        return {"status": "error", "error": "spec_md mancante"}

    # Notifica avvio nel topic
    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, f"\u2696\ufe0f Review legale in corso per {name}...")

    user_prompt = f"""Progetto: {name}
Settore: {sector or "non specificato"}

SPEC (estratto rilevante per analisi legale):
{spec_md[:5000]}

Analizza i rischi legali per operare in Europa con questo progetto."""

    tokens_in = tokens_out = 0
    review_data = {}
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=LEGAL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        # Estrai JSON
        import re as _re2
        m = _re2.search(r'\{[\s\S]*\}', raw)
        if m:
            review_data = json.loads(m.group(0))
        else:
            review_data = json.loads(raw)
    except Exception as e:
        logger.error(f"[LEGAL] Claude error: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    green = review_data.get("green_points", [])
    yellow = review_data.get("yellow_points", [])
    red = review_data.get("red_points", [])
    can_proceed = review_data.get("can_proceed", len(red) == 0)
    report_md = review_data.get("report_md", "")

    # Salva in legal_reviews
    review_id = None
    try:
        res = supabase.table("legal_reviews").insert({
            "project_id": project_id,
            "review_type": "spec_review",
            "status": "completed",
            "green_points": json.dumps(green),
            "yellow_points": json.dumps(yellow),
            "red_points": json.dumps(red),
            "report_md": report_md,
        }).execute()
        if res.data:
            review_id = res.data[0]["id"]
    except Exception as e:
        logger.error(f"[LEGAL] DB insert: {e}")

    # Aggiorna status progetto
    new_status = "legal_ok" if can_proceed else "legal_blocked"
    try:
        supabase.table("projects").update({"status": new_status}).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[LEGAL] status update: {e}")

    # Invia card nel topic
    sep = "\u2501" * 15
    msg = (
        f"\u2696\ufe0f Review Legale \u2014 {name}\n"
        f"{sep}\n"
        f"\U0001f7e2 OK: {len(green)} punti | \U0001f7e1 Attenzione: {len(yellow)} | \U0001f534 Blocchi: {len(red)}\n"
        f"{sep}"
    )
    if red:
        msg += "\n\U0001f534 " + "\n\U0001f534 ".join(red[:3])
        msg += f"\n{sep}"
    elif yellow:
        msg += "\n\U0001f7e1 " + "\n\U0001f7e1 ".join(yellow[:2])
        msg += f"\n{sep}"

    if can_proceed:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "\U0001f4c4 Dettaglio review", "callback_data": f"legal_read:{project_id}:{review_id or 0}"},
                    {"text": "\U0001f680 Procedi build", "callback_data": f"legal_proceed:{project_id}"},
                ],
            ]
        }
    else:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "\U0001f4c4 Dettaglio review", "callback_data": f"legal_read:{project_id}:{review_id or 0}"},
                    {"text": "\U0001f534 Blocca progetto", "callback_data": f"legal_block:{project_id}"},
                ],
            ]
        }

    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, msg, reply_markup=reply_markup)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("legal_agent", "legal_review", 2,
                    f"project={project_id}", f"green={len(green)} yellow={len(yellow)} red={len(red)}",
                    "claude-sonnet-4-6", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[LEGAL] Completato project={project_id} green={len(green)} yellow={len(yellow)} red={len(red)}")
    return {
        "status": "ok",
        "project_id": project_id,
        "review_id": review_id,
        "can_proceed": can_proceed,
        "green": len(green), "yellow": len(yellow), "red": len(red),
    }


def generate_project_docs(project_id):
    """MACRO-TASK 2: Genera Privacy Policy, ToS, Client Contract per il progetto."""
    start = time.time()
    logger.info(f"[LEGAL_DOCS] Avvio per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("name,spec_md,slug").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    github_repo = project.get("github_repo") or project.get("slug", "")

    docs = {}
    total_cost = 0.0
    for doc_type, doc_name in [
        ("privacy_policy", "Privacy Policy"),
        ("terms_of_service", "Termini di Servizio"),
        ("client_contract", "Contratto Cliente"),
    ]:
        prompt = f"""Genera {doc_name} per il prodotto "{name}" (legge italiana/europea).
Estrai le caratteristiche rilevanti dalla SPEC: {spec_md[:2000]}
Formato: testo legale formale, sezioni numerate, italiano.
Max 800 parole. Solo il documento, niente intro."""
        try:
            resp = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            docs[doc_type] = resp.content[0].text.strip()
            total_cost += (resp.usage.input_tokens * 0.8 + resp.usage.output_tokens * 4.0) / 1_000_000
        except Exception as e:
            logger.warning(f"[LEGAL_DOCS] {doc_type}: {e}")
            docs[doc_type] = f"[Errore generazione {doc_name}]"

    # Commit su GitHub
    if github_repo:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _commit_to_project_repo(github_repo, "docs/privacy_policy.md",
                                docs.get("privacy_policy", ""), f"docs: Privacy Policy {ts}")
        _commit_to_project_repo(github_repo, "docs/terms_of_service.md",
                                docs.get("terms_of_service", ""), f"docs: Terms of Service {ts}")
        _commit_to_project_repo(github_repo, "docs/client_contract.md",
                                docs.get("client_contract", ""), f"docs: Client Contract {ts}")

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("legal_agent", "generate_docs", 2,
                    f"project={project_id}", f"docs generati: {list(docs.keys())}",
                    "claude-haiku-4-5-20251001", 0, 0, total_cost, duration_ms)

    logger.info(f"[LEGAL_DOCS] Completato project={project_id} in {duration_ms}ms")
    return {"status": "ok", "project_id": project_id, "docs": list(docs.keys())}


def monitor_brain_compliance():
    """MACRO-TASK 2: Weekly compliance check per brAIn stessa. Ogni lunedi 07:00."""
    start = time.time()
    logger.info("[COMPLIANCE] Avvio monitoraggio settimanale brAIn")

    prompt = """Sei il Legal Monitor di brAIn. Analizza l'organismo brAIn e verifica la compliance.
brAIn e' un'organizzazione AI-native che:
- Scansiona problemi globali via Perplexity API (web scraping indiretto)
- Genera soluzioni con Claude AI
- Costruisce e lancia MVP
- Raccoglie feedback da prospect via email/Telegram
- Opera in Europa (Italia, Frankfurt)

Verifica compliance con: GDPR, AI Act 2026, Direttiva E-Commerce, normativa italiana.
Risposta in testo piano italiano, max 10 righe, formato:
COMPLIANCE CHECK brAIn — [data]
[status: OK/ATTENZIONE/CRITICO]
[elenco punti numerati]"""

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        report = resp.content[0].text.strip()
        cost = (resp.usage.input_tokens * 0.8 + resp.usage.output_tokens * 4.0) / 1_000_000
    except Exception as e:
        logger.error(f"[COMPLIANCE] {e}")
        return {"status": "error", "error": str(e)}

    # Invia a Mirco
    chat_id = get_telegram_chat_id()
    if chat_id:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if token:
            try:
                http_requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": report},
                    timeout=15,
                )
            except Exception as e:
                logger.warning(f"[COMPLIANCE] Telegram: {e}")

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("legal_agent", "compliance_check", 1,
                    "brain_compliance_weekly", report[:200],
                    "claude-haiku-4-5-20251001", 0, 0, cost, duration_ms)

    return {"status": "ok", "report": report}


# ---- SMOKE TEST AGENT (MACRO-TASK 3) ----

