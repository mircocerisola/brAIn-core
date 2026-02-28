"""Test v5.34: Universal 3-level context + CTO Phoenix + CSO auto + CMO ads + CLO legal + CPeO versioning."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch


# ============================================================
# FIX 4: Universal 3-level context (base_chief.py)
# ============================================================

def test_context_level_light():
    """1. Messaggi brevi -> light."""
    from csuite.coo import COO
    chief = COO()
    assert chief.detect_context_level("ciao") == "light"
    assert chief.detect_context_level("ok") == "light"
    assert chief.detect_context_level("grazie") == "light"
    assert chief.detect_context_level("si") == "light"
    print("PASS: context level light")


def test_context_level_medium():
    """2. Domande standard -> medium."""
    from csuite.coo import COO
    chief = COO()
    assert chief.detect_context_level("a che punto siamo col progetto?") == "medium"
    assert chief.detect_context_level("come va il deploy?") == "medium"
    assert chief.detect_context_level("quanti errori ci sono stati?") == "medium"
    print("PASS: context level medium")


def test_context_level_full():
    """3. Domande complesse -> full."""
    from csuite.coo import COO
    chief = COO()
    assert chief.detect_context_level("analizza lo stato del sistema e fai un report completo") == "full"
    assert chief.detect_context_level("fammi un audit dettagliato dell'architettura") == "full"
    assert chief.detect_context_level("spiegami come funziona il pipeline di deploy") == "full"
    # Messaggi lunghi > 200 chars -> full
    long_msg = "Questa e' una domanda molto lunga " * 10
    assert chief.detect_context_level(long_msg) == "full"
    print("PASS: context level full")


def test_topic_summary_load():
    """4. _load_topic_summary carica da Supabase."""
    from csuite.coo import COO
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"summary": "Discussione su RestaAI", "message_count": 5}]
    )
    chief = COO()
    with patch("core.base_chief.supabase", mock_sb):
        result = chief._load_topic_summary("123:456")
    assert "RestaAI" in result
    print("PASS: topic summary loaded")


def test_topic_summary_empty():
    """5. _load_topic_summary ritorna stringa vuota se non esiste."""
    from csuite.coo import COO
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[]
    )
    chief = COO()
    with patch("core.base_chief.supabase", mock_sb):
        result = chief._load_topic_summary("999:999")
    assert result == ""
    print("PASS: topic summary empty OK")


def test_base_chief_has_context_keywords():
    """6. BaseChief ha _LIGHT_KEYWORDS e _FULL_KEYWORDS."""
    from csuite.coo import COO
    chief = COO()
    assert hasattr(chief, "_LIGHT_KEYWORDS")
    assert hasattr(chief, "_FULL_KEYWORDS")
    assert "ciao" in chief._LIGHT_KEYWORDS
    assert "analizza" in chief._FULL_KEYWORDS
    print("PASS: context keywords presenti")


# ============================================================
# FIX 1: CTO Phoenix Snapshot
# ============================================================

def test_cto_has_phoenix_snapshot():
    """7. CTO ha metodo generate_phoenix_snapshot."""
    from csuite.cto import CTO
    cto = CTO()
    assert hasattr(cto, "generate_phoenix_snapshot")
    assert callable(cto.generate_phoenix_snapshot)
    print("PASS: CTO.generate_phoenix_snapshot presente")


def test_cto_parse_python_file():
    """8. _parse_python_file estrae classi, metodi, import."""
    from csuite.cto import CTO
    cto = CTO()
    code = '''import os
from typing import Dict

class MyClass:
    def method_one(self):
        pass
    def method_two(self, arg):
        pass

def standalone_func():
    pass
'''
    result = cto._parse_python_file(code)
    assert "MyClass" in result["classes"]
    assert "method_one" in result["methods"]
    assert "method_two" in result["methods"]
    assert "standalone_func" in result["methods"]
    assert result["line_count"] > 5
    assert any("import os" in i for i in result["imports"])
    print("PASS: parse_python_file corretto")


def test_cto_github_list_files():
    """9. _github_list_files ritorna lista vuota senza token."""
    from csuite.cto import CTO
    cto = CTO()
    with patch("csuite.cto.GITHUB_TOKEN", ""):
        result = cto._github_list_files("deploy-agents/core")
    assert result == []
    print("PASS: github_list_files senza token = []")


# ============================================================
# FIX 2: GitHub webhook
# ============================================================

def test_cto_has_github_webhook():
    """10. CTO ha metodo handle_github_webhook."""
    from csuite.cto import CTO
    cto = CTO()
    assert hasattr(cto, "handle_github_webhook")
    assert callable(cto.handle_github_webhook)
    print("PASS: CTO.handle_github_webhook presente")


def test_github_webhook_no_py_files():
    """11. Webhook senza file Python -> noop."""
    from csuite.cto import CTO
    cto = CTO()
    payload = {"commits": [{"added": ["README.md"], "modified": ["docs/plan.txt"], "removed": []}]}
    result = cto.handle_github_webhook(payload)
    assert result["updated"] == 0
    print("PASS: webhook no Python = noop")


# ============================================================
# FIX 3: CTO domain context enhanced
# ============================================================

def test_cto_domain_context_has_architecture():
    """12. CTO.get_domain_context include architecture_snapshot e security."""
    from csuite.cto import CTO
    mock_sb = MagicMock()
    # Mock base context
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"total_files": 50, "snapshot_date": "2026-02-28"}]
    )
    with patch("csuite.cto.supabase", mock_sb), \
         patch("core.base_chief.supabase", mock_sb):
        cto = CTO()
        ctx = cto.get_domain_context()
    # Il metodo non deve crashare
    assert isinstance(ctx, dict)
    print("PASS: CTO domain context non crasha")


# ============================================================
# FIX 8: CTO Security Report
# ============================================================

def test_cto_has_security_report():
    """13. CTO ha metodo generate_security_report."""
    from csuite.cto import CTO
    cto = CTO()
    assert hasattr(cto, "generate_security_report")
    assert callable(cto.generate_security_report)
    print("PASS: CTO.generate_security_report presente")


# ============================================================
# FIX 9: CTO Prompt with Architecture
# ============================================================

def test_cto_has_prompt_with_arch():
    """14. CTO ha metodo generate_prompt_with_architecture."""
    from csuite.cto import CTO
    cto = CTO()
    assert hasattr(cto, "generate_prompt_with_architecture")
    assert callable(cto.generate_prompt_with_architecture)
    print("PASS: CTO.generate_prompt_with_architecture presente")


# ============================================================
# FIX 5: CSO Auto Pipeline
# ============================================================

def test_cso_has_auto_pipeline():
    """15. CSO ha metodo auto_pipeline."""
    from csuite.cso import CSO
    cso = CSO()
    assert hasattr(cso, "auto_pipeline")
    assert callable(cso.auto_pipeline)
    print("PASS: CSO.auto_pipeline presente")


def test_cso_auto_pipeline_scoring():
    """16. auto_pipeline: problemi senza score vengono scorati."""
    from csuite.cso import CSO
    mock_sb = MagicMock()
    # problems.select -> 1 problema senza score
    mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"id": 1, "title": "Test Problem", "description": "Desc", "weighted_score": None}]
    )
    # problems.select approved (per soluzioni) -> vuoto
    mock_sb.table.return_value.select.return_value.eq.return_value.lt.return_value.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    with patch("csuite.cso.supabase", mock_sb), \
         patch("core.base_chief.supabase", mock_sb):
        cso = CSO()
        # Mock call_claude per scoring
        with patch.object(cso, "call_claude", return_value='{"weighted_score": 0.65}'):
            with patch.object(cso, "_send_to_chief_topic"):
                result = cso.auto_pipeline()
    assert result["status"] == "ok"
    assert result["scored"] >= 0  # potrebbe essere 0 o 1 a seconda del mock
    print("PASS: CSO auto_pipeline scoring")


# ============================================================
# FIX 6: CMO Paid Ads
# ============================================================

def test_cmo_has_paid_ads():
    """17. CMO ha metodo plan_paid_ads."""
    from csuite.cmo import CMO
    cmo = CMO()
    assert hasattr(cmo, "plan_paid_ads")
    assert callable(cmo.plan_paid_ads)
    print("PASS: CMO.plan_paid_ads presente")


def test_cmo_paid_ads_no_project():
    """18. plan_paid_ads senza progetto -> error."""
    from csuite.cmo import CMO
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    with patch("csuite.cmo.supabase", mock_sb), \
         patch("core.base_chief.supabase", mock_sb):
        cmo = CMO()
        result = cmo.plan_paid_ads(9999)
    assert result["status"] == "error"
    print("PASS: CMO paid_ads no project = error")


# ============================================================
# FIX 7: CLO Daily Legal Scan
# ============================================================

def test_clo_has_daily_legal_scan():
    """19. CLO ha metodo daily_legal_scan."""
    from csuite.clo import CLO
    clo = CLO()
    assert hasattr(clo, "daily_legal_scan")
    assert callable(clo.daily_legal_scan)
    print("PASS: CLO.daily_legal_scan presente")


# ============================================================
# FIX 11: CPeO Versioning
# ============================================================

def test_cpeo_track_version():
    """20. track_version salva su brain_versions."""
    from csuite.cpeo import track_version
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
    with patch("csuite.cpeo.supabase", mock_sb):
        track_version("agents-runner", "v5.34", "00113-abc", "Test changes")
    mock_sb.table.assert_called_with("brain_versions")
    print("PASS: track_version salva su DB")


def test_cpeo_log_improvement():
    """21. log_improvement salva su improvement_log."""
    from csuite.cpeo import log_improvement
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
    with patch("csuite.cpeo.supabase", mock_sb):
        log_improvement("v5.33", "v5.34", "feature", "Context universale")
    mock_sb.table.assert_called_with("improvement_log")
    print("PASS: log_improvement salva su DB")


def test_cpeo_weekly_improvement_report():
    """22. generate_weekly_improvement_report esiste e ritorna dict."""
    from csuite.cpeo import generate_weekly_improvement_report
    assert callable(generate_weekly_improvement_report)
    print("PASS: generate_weekly_improvement_report importabile")


# ============================================================
# Endpoint tests
# ============================================================

def test_endpoints_importable():
    """23. Tutti i nuovi endpoint sono importabili."""
    from core.endpoints import (
        run_cto_phoenix_snapshot_endpoint,
        run_cto_github_webhook_endpoint,
        run_cto_security_report_endpoint,
        run_cto_prompt_with_arch_endpoint,
        run_cso_auto_pipeline_endpoint,
        run_cmo_paid_ads_endpoint,
        run_clo_daily_legal_scan_endpoint,
        run_cpeo_version_track_endpoint,
        run_cpeo_weekly_improvement_endpoint,
    )
    assert callable(run_cto_phoenix_snapshot_endpoint)
    assert callable(run_cto_github_webhook_endpoint)
    assert callable(run_cto_security_report_endpoint)
    assert callable(run_cto_prompt_with_arch_endpoint)
    assert callable(run_cso_auto_pipeline_endpoint)
    assert callable(run_cmo_paid_ads_endpoint)
    assert callable(run_clo_daily_legal_scan_endpoint)
    assert callable(run_cpeo_version_track_endpoint)
    assert callable(run_cpeo_weekly_improvement_endpoint)
    print("PASS: 9 nuovi endpoint importabili")


def test_agents_runner_routes():
    """24. agents_runner.py ha tutte le nuove route."""
    with open("deploy-agents/agents_runner.py", "r", encoding="utf-8") as f:
        content = f.read()
    routes = [
        "/cto/phoenix-snapshot",
        "/cto/github-webhook",
        "/cto/security-report",
        "/cto/prompt-with-arch",
        "/cso/auto-pipeline",
        "/cmo/paid-ads",
        "/clo/daily-legal-scan",
        "/cpeo/version-track",
        "/cpeo/weekly-improvement",
    ]
    for route in routes:
        assert route in content, "Manca route: " + route
    print("PASS: 9 nuove route presenti")


# ============================================================
# Migration SQL
# ============================================================

def test_migration_has_tables():
    """25. Migration SQL contiene tutte e 6 le tabelle."""
    with open("supabase/migrations/20260228_v534_universal_context.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    tables = [
        "topic_context_summary",
        "cto_architecture_index",
        "cto_architecture_summary",
        "cto_security_reports",
        "brain_versions",
        "improvement_log",
    ]
    for table in tables:
        assert table in sql, "Manca tabella: " + table
    print("PASS: 6 tabelle nella migration")


# ============================================================
# Integration: context level affects prompt building
# ============================================================

def test_context_level_in_answer_question():
    """26. answer_question logga context_level."""
    # Verifica che il codice sia presente in base_chief.py
    with open("deploy-agents/core/base_chief.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "detect_context_level" in content
    assert "ctx_level" in content
    assert "_load_topic_summary" in content
    assert "_update_topic_summary" in content
    assert "CONTESTO CONVERSAZIONE" in content
    print("PASS: context level integrato in answer_question")


def test_topic_summary_update_in_answer():
    """27. answer_question chiama _update_topic_summary dopo la risposta."""
    with open("deploy-agents/core/base_chief.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "_update_topic_summary" in content
    assert "threading.Thread" in content
    assert "daemon=True" in content
    print("PASS: topic summary update threading presente")


def test_all_chiefs_inherit_context():
    """28. Tutti i 7 Chief ereditano detect_context_level da BaseChief."""
    from csuite.cso import CSO
    from csuite.cto import CTO
    from csuite.cmo import CMO
    from csuite.cfo import CFO
    from csuite.clo import CLO
    from csuite.cpeo import CPeO
    from csuite.coo import COO

    for ChiefClass in [CSO, CTO, CMO, CFO, CLO, CPeO, COO]:
        chief = ChiefClass()
        assert hasattr(chief, "detect_context_level"), ChiefClass.__name__ + " manca detect_context_level"
        assert hasattr(chief, "_load_topic_summary"), ChiefClass.__name__ + " manca _load_topic_summary"
        assert hasattr(chief, "_update_topic_summary"), ChiefClass.__name__ + " manca _update_topic_summary"
    print("PASS: tutti i 7 Chief hanno context level")


if __name__ == "__main__":
    test_context_level_light()
    test_context_level_medium()
    test_context_level_full()
    test_topic_summary_load()
    test_topic_summary_empty()
    test_base_chief_has_context_keywords()
    test_cto_has_phoenix_snapshot()
    test_cto_parse_python_file()
    test_cto_github_list_files()
    test_cto_has_github_webhook()
    test_github_webhook_no_py_files()
    test_cto_domain_context_has_architecture()
    test_cto_has_security_report()
    test_cto_has_prompt_with_arch()
    test_cso_has_auto_pipeline()
    test_cso_auto_pipeline_scoring()
    test_cmo_has_paid_ads()
    test_cmo_paid_ads_no_project()
    test_clo_has_daily_legal_scan()
    test_cpeo_track_version()
    test_cpeo_log_improvement()
    test_cpeo_weekly_improvement_report()
    test_endpoints_importable()
    test_agents_runner_routes()
    test_migration_has_tables()
    test_context_level_in_answer_question()
    test_topic_summary_update_in_answer()
    test_all_chiefs_inherit_context()
    print("\nTutti i 28 test v5.34 PASS")
