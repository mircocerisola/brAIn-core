"""Test v5.32: nuovi endpoint in endpoints.py."""
import sys
sys.path.insert(0, "deploy-agents")


def test_endpoints_importable():
    """Tutti i nuovi endpoint sono importabili da endpoints.py."""
    from core.endpoints import (
        run_clo_generate_legal_docs_endpoint,
        run_clo_legal_gate_endpoint,
        run_cto_build_landing_endpoint,
        run_cmo_design_landing_endpoint,
        run_cpeo_legal_updates_endpoint,
    )
    assert callable(run_clo_generate_legal_docs_endpoint)
    assert callable(run_clo_legal_gate_endpoint)
    assert callable(run_cto_build_landing_endpoint)
    assert callable(run_cmo_design_landing_endpoint)
    assert callable(run_cpeo_legal_updates_endpoint)
    print("PASS: Tutti i 5 nuovi endpoint importabili")


def test_agents_runner_routes():
    """agents_runner.py importa tutti i nuovi endpoint."""
    with open("deploy-agents/agents_runner.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "run_clo_generate_legal_docs_endpoint" in content
    assert "run_clo_legal_gate_endpoint" in content
    assert "run_cto_build_landing_endpoint" in content
    assert "run_cmo_design_landing_endpoint" in content
    assert "run_cpeo_legal_updates_endpoint" in content
    assert "/clo/generate-legal-docs" in content
    assert "/clo/legal-gate" in content
    assert "/cto/build-landing" in content
    assert "/cmo/design-landing" in content
    assert "/cpeo/legal-updates" in content
    print("PASS: agents_runner ha tutte le route")


if __name__ == "__main__":
    test_endpoints_importable()
    test_agents_runner_routes()
    print("\nTutti i test endpoints v5.32 PASS")
