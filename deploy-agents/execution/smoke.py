"""
brAIn module: execution/smoke.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import os, json, time, re, uuid
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, SUPABASE_ACCESS_TOKEN, DB_PASSWORD, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json
from execution.project import get_project_db, _send_to_topic


def run_smoke_test_setup(project_id):
    """MACRO-TASK 3: Setup smoke test — crea record, trova 50 prospect via Perplexity, salva."""
    start = time.time()
    logger.info(f"[SMOKE] Avvio setup per project_id={project_id}")

    # Pipeline lock
    try:
        lock_check = supabase.table("projects").select("pipeline_locked,status").eq("id", project_id).execute()
        if lock_check.data and lock_check.data[0].get("pipeline_locked"):
            logger.info(f"[SMOKE] project {project_id} pipeline locked, skip")
            return {"status": "skipped", "reason": "pipeline già in corso"}
        supabase.table("projects").update({"pipeline_locked": True}).eq("id", project_id).execute()
    except Exception as e:
        logger.warning(f"[SMOKE] Lock check error: {e}")

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            supabase.table("projects").update({"pipeline_locked": False}).eq("id", project_id).execute()
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        try:
            supabase.table("projects").update({"pipeline_locked": False}).eq("id", project_id).execute()
        except Exception:
            pass
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    landing_url = project.get("smoke_test_url") or project.get("landing_page_url", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()

    if not spec_md:
        return {"status": "error", "error": "spec_md mancante"}

    # Notifica avvio
    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, f"\U0001f9ea Smoke test avviato per {name}\nRicerca prospect in corso...")

    # Crea record smoke_test
    smoke_id = None
    try:
        res = supabase.table("smoke_tests").insert({
            "project_id": project_id,
            "landing_page_url": landing_url or "",
        }).execute()
        if res.data:
            smoke_id = res.data[0]["id"]
    except Exception as e:
        logger.error(f"[SMOKE] smoke_tests insert: {e}")
        return {"status": "error", "error": str(e)}

    # Estrai target dalla SPEC per trovare prospect
    spec_lines = spec_md[:3000]
    target_query = ""
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": (
                f"Da questa SPEC, estrai il target customer in 1 riga concisa per una query di ricerca Perplexity "
                f"(es: 'avvocati italiani 35-50 anni studio legale piccolo'). Solo la riga.\n\n{spec_lines}"
            )}],
        )
        target_query = resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"[SMOKE] target extraction: {e}")
        target_query = f"clienti di {name}"

    # Trova prospect via Perplexity
    prospects_raw = []
    try:
        query = (f"trova 20 {target_query} con contatto email o LinkedIn pubblico in Italia. "
                 f"Elenca nome, ruolo, email/LinkedIn in formato: Nome | Ruolo | Contatto")
        perplexity_result = search_perplexity(query)
        if perplexity_result:
            # Estrai righe con | come separatore
            for line in perplexity_result.split("\n"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3 and parts[2] and ("@" in parts[2] or "linkedin" in parts[2].lower()):
                    prospects_raw.append({
                        "name": parts[0][:100],
                        "contact": parts[2][:200],
                        "channel": "email" if "@" in parts[2] else "linkedin",
                    })
    except Exception as e:
        logger.warning(f"[SMOKE] Perplexity prospect search: {e}")

    # Inserisci prospect in DB
    inserted = 0
    for p in prospects_raw[:50]:
        try:
            supabase.table("smoke_test_prospects").insert({
                "smoke_test_id": smoke_id,
                "project_id": project_id,
                "name": p["name"],
                "contact": p["contact"],
                "channel": p["channel"],
                "status": "pending",
            }).execute()
            inserted += 1
        except Exception as e:
            logger.warning(f"[SMOKE] prospect insert: {e}")

    # Aggiorna conteggio
    try:
        supabase.table("smoke_tests").update({"prospects_count": inserted}).eq("id", smoke_id).execute()
    except Exception:
        pass

    # Aggiorna status progetto
    try:
        supabase.table("projects").update({"status": "smoke_test_running"}).eq("id", project_id).execute()
    except Exception:
        pass

    # Invia card con risultato
    sep = "\u2501" * 15
    msg = (
        f"\U0001f9ea Smoke Test \u2014 {name}\n"
        f"{sep}\n"
        f"Prospect trovati: {inserted}\n"
        f"Landing: {landing_url or 'non ancora deployata'}\n"
        f"Analisi risultati disponibile dopo 7 giorni.\n"
        f"{sep}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "\u2705 Avvia Outreach", "callback_data": f"smoke_approve:{project_id}:{smoke_id}"},
                {"text": "\u274c Annulla", "callback_data": f"smoke_cancel:{project_id}:{smoke_id}"},
            ],
        ]
    }
    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, msg, reply_markup=reply_markup)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("smoke_test_agent", "smoke_setup", 2,
                    f"project={project_id}", f"smoke_id={smoke_id} prospects={inserted}",
                    "claude-haiku-4-5-20251001", 0, 0, 0, duration_ms)

    # Sblocca pipeline
    try:
        supabase.table("projects").update({"pipeline_locked": False}).eq("id", project_id).execute()
    except Exception as e:
        logger.warning(f"[SMOKE] Unlock error: {e}")

    logger.info(f"[SMOKE] Setup completato project={project_id} smoke_id={smoke_id} prospects={inserted}")
    return {"status": "ok", "project_id": project_id, "smoke_id": smoke_id, "prospects_count": inserted}


def analyze_feedback_for_spec(project_id):
    """MACRO-TASK 3: Analizza feedback smoke test dopo 7 giorni. Genera SPEC_UPDATES.md e insights."""
    start = time.time()
    logger.info(f"[SMOKE_ANALYZE] Avvio analisi per project_id={project_id}")

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()
    github_repo = project.get("github_repo", "")

    # Recupera smoke test più recente
    try:
        st = supabase.table("smoke_tests").select("*").eq("project_id", project_id).order("started_at", desc=True).limit(1).execute()
        if not st.data:
            return {"status": "error", "error": "smoke test not found"}
        smoke = st.data[0]
        smoke_id = smoke["id"]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    # Recupera prospect con feedback
    try:
        prospects = supabase.table("smoke_test_prospects").select("*").eq("smoke_test_id", smoke_id).execute()
        prospects_data = prospects.data or []
    except Exception:
        prospects_data = []

    sent = sum(1 for p in prospects_data if p.get("sent_at"))
    rejected = [p for p in prospects_data if p.get("status") == "rejected"]
    forms = [p for p in prospects_data if p.get("status") == "form_compiled"]
    rejection_reasons = [p.get("rejection_reason", "") for p in rejected if p.get("rejection_reason")]

    conv_rate = (len(forms) / max(sent, 1)) * 100

    # Genera insights con Claude
    insights_prompt = f"""Analizza i risultati di questo smoke test per il prodotto "{name}".

Dati:
- Prospect contattati: {sent}
- Form compilati: {len(forms)}
- Rifiuti: {len(rejected)}
- Tasso conversione: {conv_rate:.1f}%
- Motivi rifiuto principali: {'; '.join(rejection_reasons[:5]) or 'non disponibili'}

SPEC originale (estratto): {spec_md[:2000]}

Rispondi in JSON:
{{
  "overall_signal": "green/yellow/red",
  "key_insights": ["insight 1", "insight 2", "insight 3"],
  "spec_updates": ["modifica 1 alla SPEC", "modifica 2"],
  "recommendation": "PROCEDI/PIVOTA/FERMA",
  "reasoning": "1 paragrafo max"
}}"""

    insights = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": insights_prompt}],
        )
        raw = resp.content[0].text.strip()
        import re as _re3
        m = _re3.search(r'\{[\s\S]*\}', raw)
        if m:
            insights = json.loads(m.group(0))
        cost = (resp.usage.input_tokens * 3.0 + resp.usage.output_tokens * 15.0) / 1_000_000
    except Exception as e:
        logger.error(f"[SMOKE_ANALYZE] Claude: {e}")
        cost = 0.0
        insights = {"overall_signal": "yellow", "key_insights": [], "spec_updates": [],
                    "recommendation": "ANALISI MANUALE RICHIESTA"}

    # Genera SPEC_UPDATES.md
    spec_updates_content = f"# SPEC Updates — {name}\nData: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
    spec_updates_content += f"## Segnale smoke test: {insights.get('overall_signal', 'N/A').upper()}\n\n"
    spec_updates_content += f"## Raccomandazione: {insights.get('recommendation', 'N/A')}\n\n"
    spec_updates_content += f"## Key Insights\n"
    for i, ins in enumerate(insights.get("key_insights", []), 1):
        spec_updates_content += f"{i}. {ins}\n"
    spec_updates_content += f"\n## Modifiche SPEC suggerite\n"
    for i, upd in enumerate(insights.get("spec_updates", []), 1):
        spec_updates_content += f"{i}. {upd}\n"
    spec_updates_content += f"\n## Reasoning\n{insights.get('reasoning', '')}\n"
    spec_updates_content += f"\n## Metriche\n- Contattati: {sent}\n- Form: {len(forms)}\n- Rifiuti: {len(rejected)}\n- Conversione: {conv_rate:.1f}%\n"

    if github_repo:
        _commit_to_project_repo(github_repo, "SPEC_UPDATES.md", spec_updates_content,
                                f"data: SPEC_UPDATES smoke test {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")

    # Salva insights in smoke_tests
    try:
        supabase.table("smoke_tests").update({
            "spec_insights": json.dumps(insights),
            "messages_sent": sent,
            "forms_compiled": len(forms),
            "rejections_with_reason": len(rejection_reasons),
            "conversion_rate": conv_rate,
            "recommendation": insights.get("recommendation", ""),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", smoke_id).execute()
    except Exception as e:
        logger.error(f"[SMOKE_ANALYZE] smoke_tests update: {e}")

    # Aggiorna spec_insights in projects
    try:
        supabase.table("projects").update({
            "spec_insights": json.dumps(insights),
            "status": "smoke_completed",
        }).eq("id", project_id).execute()
    except Exception:
        pass

    # Invia card risultato nel topic
    sep = "\u2501" * 15
    signal = insights.get("overall_signal", "yellow")
    signal_emoji = "\U0001f7e2" if signal == "green" else ("\U0001f534" if signal == "red" else "\U0001f7e1")
    msg = (
        f"\U0001f9ea Smoke Test completato \u2014 {name}\n"
        f"{sep}\n"
        f"Segnale: {signal_emoji} {signal.upper()}\n"
        f"Conversione: {conv_rate:.1f}% ({len(forms)}/{sent})\n"
        f"Raccomandazione: {insights.get('recommendation', 'N/A')}\n"
        f"{sep}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "\U0001f680 Avvia build", "callback_data": f"smoke_proceed:{project_id}"},
                {"text": "\U0001f4ca Insight SPEC", "callback_data": f"smoke_spec_insights:{project_id}:{smoke_id}"},
            ],
            [
                {"text": "\u270f\ufe0f Modifica SPEC", "callback_data": f"smoke_modify_spec:{project_id}"},
            ],
        ]
    }
    if group_id and topic_id:
        _send_to_topic(group_id, topic_id, msg, reply_markup=reply_markup)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("smoke_test_agent", "smoke_analyze", 2,
                    f"project={project_id}", f"conv={conv_rate:.1f}% rec={insights.get('recommendation','')}",
                    "claude-sonnet-4-6", 0, 0, cost, duration_ms)

    logger.info(f"[SMOKE_ANALYZE] Completato project={project_id} conv={conv_rate:.1f}%")
    return {
        "status": "ok",
        "project_id": project_id,
        "smoke_id": smoke_id,
        "conversion_rate": conv_rate,
        "recommendation": insights.get("recommendation", ""),
        "signal": signal,
    }


# ============================================================
# MARKETING SYSTEM (inlined) — 8 agenti + coordinator
# ============================================================

_MKT_SEP = "\u2501" * 15

