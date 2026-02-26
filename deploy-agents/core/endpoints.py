"""
brAIn Endpoints — async HTTP handlers per agents_runner.
"""
from __future__ import annotations
import os, asyncio, threading
from datetime import datetime
from aiohttp import web

from core.config import supabase, logger
from core.utils import get_telegram_chat_id, get_standard_queries
from intelligence.scanner import run_world_scanner, run_custom_scan, run_scan
from intelligence.architect import run_solution_architect
from intelligence.feasibility import run_feasibility_engine, run_bos_endpoint_logic, run_auto_pipeline
from intelligence.pipeline import process_events
from memory.knowledge import run_knowledge_keeper
from memory.scout import run_capability_scout
from memory.kpi import update_kpi_daily
from memory.recycler import run_idea_recycler
from memory.sources import run_source_refresh, run_sources_cleanup_weekly, run_targeted_scan
from memory.thresholds import run_weekly_threshold_update, run_action_queue_cleanup
from finance.finance import (
    run_finance_agent, finance_morning_report, finance_weekly_report, finance_monthly_report
)
from finance.reports import (
    generate_cost_report_v2, generate_activity_report_v2, _get_rome_tz
)
from execution.builder import generate_build_prompt, init_project
from execution.validator import run_validation_agent, continue_build_agent, run_spec_update, _generate_team_invite_link_sync
from execution.legal import run_legal_review, generate_project_docs, monitor_brain_compliance
from execution.smoke import run_smoke_test_setup, analyze_feedback_for_spec
from marketing.agents import run_marketing, generate_marketing_report



async def health_check(request):
    return web.Response(text="OK", status=200)

async def run_scanner_endpoint(request):
    result = run_world_scanner()
    return web.json_response(result)

async def run_custom_scan_endpoint(request):
    try:
        data = await request.json()
        topic = data.get("topic", "")
        if not topic:
            return web.json_response({"error": "missing topic"}, status=400)
        result = run_custom_scan(topic)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_targeted_scan_endpoint(request):
    try:
        data = await request.json()
        source_name = data.get("source_name")
        use_top = data.get("use_top", False)
        sector = data.get("sector")
        result = run_targeted_scan(source_name=source_name, use_top=use_top, sector=sector)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_architect_endpoint(request):
    result = run_solution_architect()
    return web.json_response(result)

async def run_knowledge_endpoint(request):
    result = run_knowledge_keeper()
    return web.json_response(result)

async def run_scout_endpoint(request):
    result = run_capability_scout()
    return web.json_response(result)

async def run_finance_endpoint(request):
    try:
        data = await request.json()
        target_date = data.get("date")
    except Exception:
        target_date = None
    result = run_finance_agent(target_date=target_date)
    return web.json_response(result)

async def run_finance_morning_endpoint(request):
    result = finance_morning_report()
    return web.json_response(result)

async def run_finance_weekly_endpoint(request):
    result = finance_weekly_report()
    return web.json_response(result)

async def run_finance_monthly_endpoint(request):
    result = finance_monthly_report()
    return web.json_response(result)

async def run_feasibility_endpoint(request):
    try:
        data = await request.json()
        solution_id = data.get("solution_id")
    except Exception:
        solution_id = None
    result = run_feasibility_engine(solution_id=solution_id)
    return web.json_response(result)

async def run_bos_endpoint(request):
    try:
        data = await request.json()
        solution_id = data.get("solution_id")
    except Exception:
        solution_id = None
    result = run_bos_endpoint_logic(solution_id=solution_id)
    return web.json_response(result)

async def run_events_endpoint(request):
    result = process_events()
    return web.json_response(result)

async def run_pipeline_endpoint(request):
    try:
        sources = supabase.table("scan_sources").select("*").eq("status", "active") \
            .order("relevance_score", desc=True).limit(10).execute()
        sources = sources.data or []
    except Exception:
        sources = []
    queries = get_standard_queries(sources)
    scan_result = run_scan(queries)
    saved_ids = scan_result.get("saved_ids", [])
    if saved_ids:
        run_auto_pipeline(saved_ids)
    return web.json_response({"scan": scan_result, "pipeline": f"{len(saved_ids)} problemi processati"})

async def run_daily_report_endpoint(request):
    result = generate_activity_report_v2()
    return web.json_response(result)

async def run_cost_report_endpoint(request):
    result = generate_cost_report_v2()
    return web.json_response(result)

async def run_activity_report_endpoint(request):
    result = generate_activity_report_v2()
    return web.json_response(result)

async def run_auto_report_endpoint(request):
    hour = datetime.now(_get_rome_tz()).hour
    if hour % 4 == 0:
        result = generate_cost_report_v2()
    else:
        result = generate_activity_report_v2()
    return web.json_response(result)

async def run_kpi_update_endpoint(request):
    result = update_kpi_daily()
    return web.json_response(result)

async def run_recycle_endpoint(request):
    result = run_idea_recycler()
    return web.json_response(result)

async def run_source_refresh_endpoint(request):
    result = run_source_refresh()
    return web.json_response(result)

async def run_sources_cleanup_endpoint(request):
    result = run_sources_cleanup_weekly()
    return web.json_response(result)

async def run_weekly_threshold_endpoint(request):
    result = run_weekly_threshold_update()
    return web.json_response(result)

async def run_action_queue_cleanup_endpoint(request):
    result = run_action_queue_cleanup()
    return web.json_response(result)

async def run_project_init_endpoint(request):
    try:
        data = await request.json()
        solution_id = data.get("solution_id")
        if not solution_id:
            return web.json_response({"error": "missing solution_id"}, status=400)
        result = init_project(solution_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_project_build_prompt_endpoint(request):
    try:
        data = await request.json()
        project_id = data.get("project_id")
        if not project_id:
            return web.json_response({"error": "missing project_id"}, status=400)
        result = generate_build_prompt(int(project_id))
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_spec_update_endpoint(request):
    try:
        data = await request.json()
        project_id = data.get("project_id")
        modification = data.get("modification")
        if not project_id or not modification:
            return web.json_response({"error": "missing project_id or modification"}, status=400)
        result = run_spec_update(int(project_id), modification)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_validation_endpoint(request):
    result = run_validation_agent()
    return web.json_response(result)

async def run_continue_build_endpoint(request):
    try:
        import threading as _threading
        data = await request.json()
        project_id = data.get("project_id")
        feedback = data.get("feedback", "ok")
        phase = data.get("phase")
        if not project_id or phase is None:
            return web.json_response({"error": "missing project_id or phase"}, status=400)
        _threading.Thread(
            target=continue_build_agent,
            args=(int(project_id), str(feedback), int(phase)),
            daemon=True,
        ).start()
        return web.json_response({"status": "started", "project_id": project_id, "next_phase": int(phase) + 1})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_generate_invite_endpoint(request):
    try:
        data = await request.json()
        project_id = data.get("project_id")
        phone = data.get("phone")
        if not project_id or not phone:
            return web.json_response({"error": "missing project_id or phone"}, status=400)
        pid = int(project_id)
        mirco_chat_id = get_telegram_chat_id()
        try:
            supabase.table("project_members").insert({
                "project_id": pid,
                "telegram_phone": phone,
                "role": "manager",
                "added_by": int(mirco_chat_id) if mirco_chat_id else None,
                "active": True,
            }).execute()
        except Exception as e:
            logger.warning(f"[INVITE] project_members insert: {e}")
        invite_link = _generate_team_invite_link_sync(pid)
        if invite_link:
            return web.json_response({"status": "ok", "invite_link": invite_link})
        return web.json_response({"status": "error", "invite_link": None}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_migration_endpoint(request):
    """POST /migration/apply — esegue SQL via psycopg2."""
    try:
        import psycopg2
        data = await request.json()
        sql_content = data.get("sql", "").strip()
        filename = data.get("filename", "manual")
        if not sql_content:
            return web.json_response({"error": "campo 'sql' obbligatorio"}, status=400)
        db_pass = os.getenv("DB_PASSWORD", "")
        if not db_pass:
            return web.json_response({"error": "DB_PASSWORD non configurata"}, status=500)
        supabase_url = os.getenv("SUPABASE_URL", "")
        host = supabase_url.replace("https://", "").replace("http://", "").rstrip("/")
        db_host = f"db.{host}"
        conn = psycopg2.connect(host=db_host, port=5432, dbname="postgres",
                                user="postgres", password=db_pass, sslmode="require")
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS migration_history (
                id serial PRIMARY KEY, filename text UNIQUE NOT NULL,
                applied_at timestamptz DEFAULT now());""")
            cur.execute("SELECT filename FROM migration_history WHERE filename=%s;", (filename,))
            already = cur.fetchone()
        conn.commit()
        if already:
            conn.close()
            return web.json_response({"status": "skipped", "filename": filename})
        try:
            with conn.cursor() as cur:
                cur.execute(sql_content)
                cur.execute("INSERT INTO migration_history (filename) VALUES (%s) ON CONFLICT DO NOTHING;", (filename,))
            conn.commit()
            conn.close()
            return web.json_response({"status": "ok", "filename": filename})
        except Exception as e:
            conn.rollback()
            conn.close()
            return web.json_response({"status": "error", "filename": filename, "error": str(e)}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_legal_review_endpoint(request):
    try:
        data = await request.json()
        project_id = int(data.get("project_id", 0))
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = run_legal_review(project_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_legal_docs_endpoint(request):
    try:
        data = await request.json()
        project_id = int(data.get("project_id", 0))
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = generate_project_docs(project_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_legal_compliance_endpoint(request):
    try:
        result = monitor_brain_compliance()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_smoke_setup_endpoint(request):
    try:
        data = await request.json()
        project_id = int(data.get("project_id", 0))
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = run_smoke_test_setup(project_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_smoke_analyze_endpoint(request):
    try:
        data = await request.json()
        project_id = int(data.get("project_id", 0))
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = analyze_feedback_for_spec(project_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_marketing_run_endpoint(request):
    try:
        import threading as _t
        data = await request.json()
        project_id = data.get("project_id")
        if project_id:
            project_id = int(project_id)
        target = data.get("target", "project")
        phase = data.get("phase", "full")
        _t.Thread(target=run_marketing, args=(project_id, target, phase), daemon=True).start()
        return web.json_response({"status": "started", "project_id": project_id, "phase": phase})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_marketing_brand_endpoint(request):
    try:
        import threading as _t
        data = await request.json()
        project_id = data.get("project_id")
        if project_id:
            project_id = int(project_id)
        target = data.get("target", "project")
        _t.Thread(target=run_marketing, args=(project_id, target, "brand"), daemon=True).start()
        return web.json_response({"status": "started", "project_id": project_id, "phase": "brand"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_marketing_report_endpoint(request):
    try:
        import threading as _t
        data = await request.json()
        project_id = data.get("project_id")
        if project_id:
            project_id = int(project_id)
        _t.Thread(target=generate_marketing_report, args=(project_id,), daemon=True).start()
        return web.json_response({"status": "started", "project_id": project_id})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def run_all_endpoint(request):
    results = {}
    results["scanner"] = run_world_scanner()
    results["knowledge"] = run_knowledge_keeper()
    results["scout"] = run_capability_scout()
    results["finance"] = run_finance_agent()
    results["events"] = process_events()
    return web.json_response(results)


# === C-SUITE ENDPOINTS ===

async def run_csuite_briefing_endpoint(request):
    """POST /csuite/briefing — {domain?} — genera briefing del Chief"""
    try:
        from csuite import get_chief, run_all_briefings
        data = await request.json()
        domain = data.get("domain")
        if domain:
            chief = get_chief(domain)
            if not chief:
                return web.json_response({"error": f"Chief per dominio '{domain}' non trovato"}, status=400)
            result = chief.generate_weekly_briefing()
        else:
            result = run_all_briefings()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_csuite_ask_endpoint(request):
    """POST /csuite/ask — {domain?, question, context?, project_context?, topic_scope_id?, project_scope_id?, recent_messages?} — chiede al Chief"""
    try:
        from csuite import get_chief, route_to_chief
        data = await request.json()
        domain = data.get("domain")
        question = data.get("question", "")
        context = data.get("context")
        project_context = data.get("project_context")
        topic_scope_id = data.get("topic_scope_id")
        project_scope_id = data.get("project_scope_id")
        recent_messages = data.get("recent_messages")
        if not question:
            return web.json_response({"error": "question obbligatoria"}, status=400)
        if domain:
            chief = get_chief(domain)
        else:
            chief, domain = route_to_chief(question)
        if not chief:
            return web.json_response({"error": "Impossibile identificare il Chief appropriato"}, status=400)
        answer = chief.answer_question(
            question,
            user_context=context,
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )
        return web.json_response({"status": "ok", "domain": domain, "chief": chief.name, "answer": answer})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_csuite_anomalies_endpoint(request):
    """POST /csuite/anomalies — controlla anomalie tutti i Chief"""
    try:
        from csuite import run_all_anomaly_checks
        result = run_all_anomaly_checks()
        return web.json_response({"status": "ok", "anomalies": result})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_csuite_report_endpoint(request):
    """POST /csuite/report — {domain?} — genera report breve 4h per un Chief o tutti"""
    try:
        from csuite import get_chief, _chiefs
        data = await request.json() if request.content_length else {}
        domain = data.get("domain") if data else None

        if domain:
            chief = get_chief(domain)
            if not chief:
                return web.json_response({"error": f"Chief '{domain}' non trovato"}, status=400)
            text = chief.generate_brief_report()
            return web.json_response({
                "status": "ok" if text else "skipped",
                "chief": chief.name,
                "domain": domain,
                "report": text,
            })
        else:
            # Tutti i Chief
            results = {}
            for d, chief in _chiefs.items():
                try:
                    text = chief.generate_brief_report()
                    results[d] = {"chief": chief.name, "status": "ok" if text else "skipped"}
                except Exception as e:
                    results[d] = {"chief": d, "status": "error", "error": str(e)}
            return web.json_response({"status": "ok", "results": results})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_ethics_check_endpoint(request):
    """POST /ethics/check — {project_id} — valuta etica progetto"""
    try:
        from ethics.ethics_monitor import check_project_ethics
        data = await request.json()
        project_id = data.get("project_id")
        if not project_id:
            return web.json_response({"error": "project_id obbligatorio"}, status=400)
        result = check_project_ethics(int(project_id))
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_ethics_check_active_endpoint(request):
    """POST /ethics/check-active — controlla tutti i progetti attivi."""
    try:
        from ethics.ethics_monitor import check_project_ethics
        from core.config import supabase
        r = supabase.table("projects").select("id") \
            .not_.in_("status", ["archived", "ethics_blocked", "build_complete", "launch_approved"]) \
            .execute()
        project_ids = [row["id"] for row in (r.data or [])]
        results = []
        for pid in project_ids:
            try:
                res = check_project_ethics(pid)
                results.append(res)
            except Exception as e:
                results.append({"project_id": pid, "status": "error", "error": str(e)})
        return web.json_response({"status": "ok", "checked": len(results), "results": results})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── CDO endpoints ────────────────────────────────────────────


async def run_cto_data_audit_endpoint(request):
    """POST /cto/data-audit — CDO audit qualità dati"""
    try:
        from csuite.cdo import audit_data_quality
        result = audit_data_quality()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_cto_knowledge_monitor_endpoint(request):
    """POST /cto/knowledge-monitor — CDO monitor crescita knowledge"""
    try:
        from csuite.cdo import monitor_knowledge_growth
        result = monitor_knowledge_growth()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── CPeO coaching endpoint ───────────────────────────────────


async def run_cpeo_coaching_endpoint(request):
    """POST /cpeo/coaching — coaching automatico dei Chief"""
    try:
        from csuite.cpeo import coach_chiefs
        result = coach_chiefs()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── Memory endpoints ──────────────────────────────────────────


async def run_memory_create_episode_endpoint(request):
    """POST /memory/create-episode — {scope_type, scope_id, messages} — crea episodio riassuntivo"""
    try:
        from intelligence.memory import create_episode
        data = await request.json()
        scope_type = data.get("scope_type", "topic")
        scope_id = data.get("scope_id", "")
        messages = data.get("messages", [])
        if not scope_id or not messages:
            return web.json_response({"error": "scope_id e messages obbligatori"}, status=400)
        result = create_episode(scope_type, scope_id, messages)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_memory_extract_facts_endpoint(request):
    """POST /memory/extract-facts — {message, chief_id} — estrae fatti semantici"""
    try:
        from intelligence.memory import extract_semantic_facts
        data = await request.json()
        message = data.get("message", "")
        chief_id = data.get("chief_id", "coo")
        if not message:
            return web.json_response({"facts_saved": 0})
        result = extract_semantic_facts(message, chief_id)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_memory_cleanup_endpoint(request):
    """POST /memory/cleanup — pulizia periodica tre livelli memoria"""
    try:
        from intelligence.memory import cleanup_memory
        result = cleanup_memory()
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_resend_spec_endpoint(request):
    """POST /admin/resend-spec — {solution_id} — rimanda la SPEC nel topic #strategy."""
    import os as _os
    import requests as _requests
    try:
        data = await request.json()
        solution_id = int(data.get("solution_id", 0))
        if not solution_id:
            return web.json_response({"error": "solution_id obbligatorio"}, status=400)
        # Trova il progetto per questa soluzione
        r = supabase.table("projects").select("id,name,status,spec_human_md,spec_md,bos_score") \
            .eq("bos_id", solution_id).execute()
        if not r.data:
            return web.json_response({"error": f"nessun progetto per solution_id={solution_id}"}, status=404)
        project = r.data[0]
        project_id = project["id"]

        # Leggi group_id e strategy_topic_id da org_config
        def _get_cfg(key):
            rr = supabase.table("org_config").select("value").eq("key", key).execute()
            if rr.data:
                v = rr.data[0]["value"]
                if isinstance(v, (int, float)):
                    return int(v)
                sv = str(v).strip()
                return int(sv) if sv.lstrip("-").isdigit() else None
            return None

        group_id = _get_cfg("telegram_group_id")
        strategy_topic_id = _get_cfg("chief_topic_cso")
        mirco_chat_id = _get_cfg("telegram_user_id") or 8307106544

        # Prepara messaggio
        spec_human = project.get("spec_human_md") or ""
        name = project.get("name", f"Progetto {project_id}")
        bos_score = float(project.get("bos_score") or 0)
        sep = "\u2501" * 15
        if spec_human:
            msg = f"{spec_human}\n{sep}"
        else:
            spec_excerpt = (project.get("spec_md") or "SPEC non disponibile")[:500]
            msg = f"\U0001f4cb SPEC pronta \u2014 {name}\nBOS score: {bos_score:.2f}\n\n{spec_excerpt}\n{sep}"
        reply_markup = {
            "inline_keyboard": [[
                {"text": "\u2705 Valida", "callback_data": f"spec_validate:{project_id}"},
                {"text": "\u270f\ufe0f Modifica", "callback_data": f"spec_edit:{project_id}"},
                {"text": "\U0001f4c4 Versione completa", "callback_data": f"spec_full:{project_id}"},
            ]]
        }
        token = _os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return web.json_response({"error": "TELEGRAM_BOT_TOKEN non configurato"}, status=500)

        # Invia al topic #strategy (o fallback DM Mirco)
        if group_id and strategy_topic_id:
            tg_payload = {
                "chat_id": group_id,
                "message_thread_id": strategy_topic_id,
                "text": msg[:4000],
                "reply_markup": reply_markup,
            }
        else:
            tg_payload = {"chat_id": mirco_chat_id, "text": msg[:4000], "reply_markup": reply_markup}

        resp = _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=tg_payload,
            timeout=15,
        )

        # Salva active_session su Supabase per contesto persistente
        from datetime import timezone as _tz
        try:
            supabase.table("active_session").upsert({
                "telegram_user_id": int(mirco_chat_id),
                "context_type": "spec_review",
                "project_id": project_id,
                "solution_id": None,
                "updated_at": datetime.now(_tz.utc).isoformat(),
            }, on_conflict="telegram_user_id").execute()
        except Exception as _ae:
            logger.warning(f"[ACTIVE_SESSION] save resend-spec: {_ae}")

        return web.json_response({
            "status": "ok",
            "project_id": project_id,
            "project_name": name,
            "project_status": project.get("status"),
            "telegram_ok": resp.status_code == 200,
            "sent_to": "strategy_topic" if (group_id and strategy_topic_id) else "direct_dm",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_founder_pipeline_endpoint(request):
    """POST /admin/founder-pipeline — avvia init_project per soluzioni founder bos_approved senza progetto."""
    try:
        import threading as _t
        # Trova soluzioni founder non archiviate senza progetto associato
        r = supabase.table("solutions").select("id,title,bos_score,status") \
            .eq("source", "founder").neq("status", "archived").execute()
        founder_sols = r.data or []
        triggered = []
        skipped = []
        for sol in founder_sols:
            sol_id = sol["id"]
            # Anti-dup: controlla se esiste già un progetto per questa soluzione
            existing = supabase.table("projects").select("id,status") \
                .eq("bos_id", sol_id).execute()
            if existing.data:
                proj = existing.data[0]
                if proj.get("status") not in ("new", "init", "failed"):
                    skipped.append({"solution_id": sol_id, "title": sol.get("title"), "reason": "progetto già in corso"})
                    continue
            # Avvia in background
            _t.Thread(target=init_project, args=(sol_id,), daemon=True).start()
            triggered.append({"solution_id": sol_id, "title": sol.get("title")})
        return web.json_response({
            "status": "ok",
            "triggered": triggered,
            "skipped": skipped,
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def run_cleanup_old_topics_endpoint(request):
    """POST /admin/cleanup-old-topics — elimina topic Forum e dati DB di cantieri obsoleti."""
    import os as _os
    import requests as _requests
    try:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass

        token = _os.getenv("TELEGRAM_BOT_TOKEN", "")
        deleted_topics = []
        deleted_projects = []
        errors = []

        def _get_group():
            rr = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if rr.data:
                v = rr.data[0]["value"]
                if isinstance(v, (int, float)):
                    return int(v)
                sv = str(v).strip()
                return int(sv) if sv.lstrip("-").isdigit() else None
            return None

        group_id = _get_group()

        # Trova progetti da eliminare
        slugs_filter = body.get("slugs", [])
        if slugs_filter:
            projects_r = supabase.table("projects").select("id,name,slug,topic_id,bos_id").in_("slug", slugs_filter).execute()
            projects_list = projects_r.data or []
        else:
            all_p = supabase.table("projects").select("id,name,slug,topic_id,bos_id").execute()
            OBSOLETE_KEYWORDS = ("ristorante", "prenotazioni", "test", "demo", "sandbox")
            projects_list = [
                p for p in (all_p.data or [])
                if any(kw in (p.get("slug") or "").lower() or kw in (p.get("name") or "").lower()
                       for kw in OBSOLETE_KEYWORDS)
            ]

        for proj in projects_list:
            proj_id = proj["id"]
            topic_id = proj.get("topic_id")

            # Elimina Forum Topic Telegram
            if token and group_id and topic_id:
                try:
                    r = _requests.post(
                        f"https://api.telegram.org/bot{token}/deleteForumTopic",
                        json={"chat_id": group_id, "message_thread_id": topic_id},
                        timeout=10,
                    )
                    if r.status_code == 200:
                        deleted_topics.append({"project_id": proj_id, "topic_id": topic_id})
                    else:
                        errors.append({"project_id": proj_id, "topic_id": topic_id, "error": r.text[:200]})
                except Exception as _te:
                    errors.append({"project_id": proj_id, "error": str(_te)})

            # Pulisci action_queue per questo progetto
            try:
                supabase.table("action_queue").delete().eq("project_id", proj_id).execute()
            except Exception:
                pass

            # Reset soluzione a 'proposed' per permettere retry
            if proj.get("bos_id"):
                try:
                    supabase.table("solutions").update({"status": "proposed", "bos_approved": False}) \
                        .eq("id", proj["bos_id"]).execute()
                except Exception:
                    pass

            # Elimina progetto dal DB
            try:
                supabase.table("projects").delete().eq("id", proj_id).execute()
                deleted_projects.append({"id": proj_id, "name": proj.get("name"), "slug": proj.get("slug")})
            except Exception as _de:
                errors.append({"project_id": proj_id, "error": f"DB delete: {_de}"})

        return web.json_response({
            "status": "ok",
            "deleted_topics": deleted_topics,
            "deleted_projects": deleted_projects,
            "errors": errors,
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
