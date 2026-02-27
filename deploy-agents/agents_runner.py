"""
brAIn Agents Runner v5.0 — Pure routing (<200 lines).
Business logic → module packages. Endpoints → core/endpoints.py.
"""
import os
import asyncio
from aiohttp import web
from dotenv import load_dotenv
from core.config import logger

load_dotenv()
PORT = int(os.environ.get("PORT", 8080))

from core.endpoints import (
    health_check,
    run_scanner_endpoint, run_custom_scan_endpoint, run_targeted_scan_endpoint,
    run_architect_endpoint, run_knowledge_endpoint, run_scout_endpoint,
    run_finance_endpoint, run_finance_morning_endpoint, run_finance_weekly_endpoint,
    run_finance_monthly_endpoint,
    run_feasibility_endpoint, run_bos_endpoint,
    run_pipeline_endpoint, run_events_endpoint,
    run_daily_report_endpoint, run_cost_report_endpoint,
    run_activity_report_endpoint, run_auto_report_endpoint,
    run_kpi_update_endpoint, run_recycle_endpoint,
    run_source_refresh_endpoint, run_sources_cleanup_endpoint,
    run_weekly_threshold_endpoint, run_action_queue_cleanup_endpoint,
    run_project_init_endpoint, run_project_build_prompt_endpoint,
    run_spec_update_endpoint, run_validation_endpoint,
    run_continue_build_endpoint, run_generate_invite_endpoint,
    run_migration_endpoint,
    run_legal_review_endpoint, run_legal_docs_endpoint, run_legal_compliance_endpoint,
    run_smoke_setup_endpoint, run_smoke_analyze_endpoint, run_smoke_check_start_endpoint,
    run_marketing_run_endpoint, run_marketing_brand_endpoint, run_marketing_report_endpoint,
    run_all_endpoint,
    # C-Suite + Ethics
    run_csuite_briefing_endpoint, run_csuite_ask_endpoint, run_csuite_anomalies_endpoint,
    run_csuite_report_endpoint, run_csuite_morning_report_endpoint,
    run_ethics_check_endpoint, run_ethics_check_active_endpoint,
    # CDO + CPeO
    run_cto_data_audit_endpoint, run_cto_knowledge_monitor_endpoint,
    run_cpeo_coaching_endpoint,
    # Memory
    run_memory_create_episode_endpoint, run_memory_extract_facts_endpoint, run_memory_cleanup_endpoint,
    # Admin
    run_founder_pipeline_endpoint, run_resend_spec_endpoint, run_cleanup_old_topics_endpoint,
    run_health_check_endpoint,
    run_smoke_design_endpoint, run_restaurant_reposition_endpoint,
    run_smoke_daily_update_endpoint,
    run_flush_bos_endpoint,
)


async def main():
    logger.info("brAIn Agents Runner v5.0 starting (modular)...")
    app = web.Application()

    # Health
    app.router.add_get("/", health_check)

    # Intelligence
    app.router.add_post("/scanner", run_scanner_endpoint)
    app.router.add_post("/scanner/custom", run_custom_scan_endpoint)
    app.router.add_post("/scanner/targeted", run_targeted_scan_endpoint)
    app.router.add_post("/architect", run_architect_endpoint)
    app.router.add_post("/feasibility", run_feasibility_endpoint)
    app.router.add_post("/bos", run_bos_endpoint)
    app.router.add_post("/pipeline", run_pipeline_endpoint)
    app.router.add_post("/events/process", run_events_endpoint)

    # Memory
    app.router.add_post("/knowledge", run_knowledge_endpoint)
    app.router.add_post("/scout", run_scout_endpoint)
    app.router.add_post("/kpi/update", run_kpi_update_endpoint)
    app.router.add_post("/thresholds/weekly", run_weekly_threshold_endpoint)
    app.router.add_post("/cycle/queue-cleanup", run_action_queue_cleanup_endpoint)

    # Cycle aliases
    app.router.add_post("/cycle/scan", run_scanner_endpoint)
    app.router.add_post("/cycle/knowledge", run_knowledge_endpoint)
    app.router.add_post("/cycle/capability", run_scout_endpoint)
    app.router.add_post("/cycle/sources", run_source_refresh_endpoint)
    app.router.add_post("/cycle/sources-cleanup", run_sources_cleanup_endpoint)
    app.router.add_post("/cycle/recycle", run_recycle_endpoint)

    # Finance
    app.router.add_post("/finance", run_finance_endpoint)
    app.router.add_post("/finance/morning", run_finance_morning_endpoint)
    app.router.add_post("/finance/weekly", run_finance_weekly_endpoint)
    app.router.add_post("/finance/monthly", run_finance_monthly_endpoint)

    # Reports
    app.router.add_post("/report/daily", run_daily_report_endpoint)
    app.router.add_post("/report/cost", run_cost_report_endpoint)
    app.router.add_post("/report/activity", run_activity_report_endpoint)
    app.router.add_post("/report/auto", run_auto_report_endpoint)

    # Execution
    app.router.add_post("/project/init", run_project_init_endpoint)
    app.router.add_post("/project/build_prompt", run_project_build_prompt_endpoint)
    app.router.add_post("/project/continue_build", run_continue_build_endpoint)
    app.router.add_post("/project/generate_invite", run_generate_invite_endpoint)
    app.router.add_post("/spec/update", run_spec_update_endpoint)
    app.router.add_post("/validation", run_validation_endpoint)
    app.router.add_post("/migration/apply", run_migration_endpoint)

    # Legal
    app.router.add_post("/legal/review", run_legal_review_endpoint)
    app.router.add_post("/legal/docs", run_legal_docs_endpoint)
    app.router.add_post("/legal/compliance", run_legal_compliance_endpoint)

    # Smoke test
    app.router.add_post("/smoke/design", run_smoke_design_endpoint)
    app.router.add_post("/smoke/setup", run_smoke_setup_endpoint)
    app.router.add_post("/smoke/analyze", run_smoke_analyze_endpoint)
    app.router.add_post("/smoke/daily-update", run_smoke_daily_update_endpoint)
    app.router.add_post("/smoke/check-start", run_smoke_check_start_endpoint)

    # Marketing
    app.router.add_post("/marketing/run", run_marketing_run_endpoint)
    app.router.add_post("/marketing/brand", run_marketing_brand_endpoint)
    app.router.add_post("/marketing/report", run_marketing_report_endpoint)

    # C-Suite
    app.router.add_post("/csuite/briefing", run_csuite_briefing_endpoint)
    app.router.add_post("/csuite/ask", run_csuite_ask_endpoint)
    app.router.add_post("/csuite/anomalies", run_csuite_anomalies_endpoint)
    app.router.add_post("/csuite/report", run_csuite_report_endpoint)
    app.router.add_post("/csuite/morning-report", run_csuite_morning_report_endpoint)

    # Ethics
    app.router.add_post("/ethics/check", run_ethics_check_endpoint)
    app.router.add_post("/ethics/check-active", run_ethics_check_active_endpoint)

    # CDO (sotto CTO)
    app.router.add_post("/cto/data-audit", run_cto_data_audit_endpoint)
    app.router.add_post("/cto/knowledge-monitor", run_cto_knowledge_monitor_endpoint)

    # CPeO
    app.router.add_post("/cpeo/coaching", run_cpeo_coaching_endpoint)

    # Memory (L2 + L3)
    app.router.add_post("/memory/create-episode", run_memory_create_episode_endpoint)
    app.router.add_post("/memory/extract-facts", run_memory_extract_facts_endpoint)
    app.router.add_post("/memory/cleanup", run_memory_cleanup_endpoint)

    # Admin / one-time triggers
    app.router.add_post("/admin/founder-pipeline", run_founder_pipeline_endpoint)
    app.router.add_post("/admin/resend-spec", run_resend_spec_endpoint)
    app.router.add_post("/admin/cleanup-old-topics", run_cleanup_old_topics_endpoint)
    app.router.add_post("/admin/restaurant-reposition", run_restaurant_reposition_endpoint)
    app.router.add_post("/admin/health-check", run_health_check_endpoint)
    app.router.add_post("/admin/flush-bos", run_flush_bos_endpoint)

    # All
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
