"""
brAIn Agents Runner v5.0 — Pure routing layer.
All business logic is in module packages (core/, intelligence/, execution/, etc.)
"""
import os
import asyncio
from datetime import datetime
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", 8080))

# === IMPORT FROM MODULES ===
from core.config import supabase, logger
from core.utils import (
    log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json,
    get_pipeline_thresholds, get_standard_queries,
)
from intelligence.scanner import run_world_scanner, run_custom_scan, run_targeted_scan, run_scan
from intelligence.architect import run_solution_architect
from intelligence.feasibility import run_feasibility_engine, run_bos_endpoint_logic
from intelligence.pipeline import process_events, run_auto_pipeline
from memory.knowledge import run_knowledge_keeper
from memory.scout import run_capability_scout
from memory.kpi import update_kpi_daily
from memory.recycler import run_idea_recycler
from memory.sources import run_source_refresh, run_sources_cleanup_weekly
from memory.thresholds import run_weekly_threshold_update, run_action_queue_cleanup
from finance.finance import (
    run_finance_agent, finance_morning_report, finance_weekly_report, finance_monthly_report
)
from finance.reports import (
    generate_cost_report_v2, generate_activity_report_v2, _get_rome_tz
)
from core.templates import now_rome
from execution.project import init_project, get_project_db, _generate_team_invite_link_sync
from execution.builder import (
    run_spec_generator, generate_build_prompt, run_build_agent,
    enqueue_spec_review_action,
)
from execution.validator import (
    run_validation_agent, continue_build_agent, run_spec_update
)
from execution.legal import run_legal_review, generate_project_docs, monitor_brain_compliance
from execution.smoke import run_smoke_test_setup, analyze_feedback_for_spec
from marketing.agents import run_marketing, generate_marketing_report


# === ENDPOINT HANDLERS ===

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
    hour = now_rome().hour
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


# === MAIN ===

async def main():
    logger.info("brAIn Agents Runner v5.0 starting (modular)...")

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/scanner", run_scanner_endpoint)
    app.router.add_post("/scanner/custom", run_custom_scan_endpoint)
    app.router.add_post("/scanner/targeted", run_targeted_scan_endpoint)
    app.router.add_post("/architect", run_architect_endpoint)
    app.router.add_post("/knowledge", run_knowledge_endpoint)
    app.router.add_post("/scout", run_scout_endpoint)
    app.router.add_post("/finance", run_finance_endpoint)
    app.router.add_post("/finance/morning", run_finance_morning_endpoint)
    app.router.add_post("/finance/weekly", run_finance_weekly_endpoint)
    app.router.add_post("/finance/monthly", run_finance_monthly_endpoint)
    app.router.add_post("/feasibility", run_feasibility_endpoint)
    app.router.add_post("/bos", run_bos_endpoint)
    app.router.add_post("/pipeline", run_pipeline_endpoint)
    app.router.add_post("/events/process", run_events_endpoint)
    app.router.add_post("/report/daily", run_daily_report_endpoint)
    app.router.add_post("/report/cost", run_cost_report_endpoint)
    app.router.add_post("/report/activity", run_activity_report_endpoint)
    app.router.add_post("/report/auto", run_auto_report_endpoint)
    app.router.add_post("/kpi/update", run_kpi_update_endpoint)
    app.router.add_post("/cycle/scan", run_scanner_endpoint)
    app.router.add_post("/cycle/knowledge", run_knowledge_endpoint)
    app.router.add_post("/cycle/capability", run_scout_endpoint)
    app.router.add_post("/cycle/sources", run_source_refresh_endpoint)
    app.router.add_post("/cycle/sources-cleanup", run_sources_cleanup_endpoint)
    app.router.add_post("/cycle/recycle", run_recycle_endpoint)
    app.router.add_post("/cycle/queue-cleanup", run_action_queue_cleanup_endpoint)
    app.router.add_post("/thresholds/weekly", run_weekly_threshold_endpoint)
    app.router.add_post("/project/init", run_project_init_endpoint)
    app.router.add_post("/project/build_prompt", run_project_build_prompt_endpoint)
    app.router.add_post("/spec/update", run_spec_update_endpoint)
    app.router.add_post("/validation", run_validation_endpoint)
    app.router.add_post("/project/continue_build", run_continue_build_endpoint)
    app.router.add_post("/project/generate_invite", run_generate_invite_endpoint)
    app.router.add_post("/migration/apply", run_migration_endpoint)
    app.router.add_post("/legal/review", run_legal_review_endpoint)
    app.router.add_post("/legal/docs", run_legal_docs_endpoint)
    app.router.add_post("/legal/compliance", run_legal_compliance_endpoint)
    app.router.add_post("/smoke/setup", run_smoke_setup_endpoint)
    app.router.add_post("/smoke/analyze", run_smoke_analyze_endpoint)
    app.router.add_post("/marketing/run", run_marketing_run_endpoint)
    app.router.add_post("/marketing/brand", run_marketing_brand_endpoint)
    app.router.add_post("/marketing/report", run_marketing_report_endpoint)
    app.router.add_post("/all", run_all_endpoint)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Agents Runner v5.0 on port {PORT}")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
