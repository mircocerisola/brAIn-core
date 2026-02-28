"""Test v5.32: CTO build_landing_from_brief."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch


def test_cto_has_build_landing():
    """CTO ha il metodo build_landing_from_brief."""
    from csuite.cto import CTO
    assert hasattr(CTO, "build_landing_from_brief")
    assert hasattr(CTO, "_send_landing_preview")
    assert hasattr(CTO, "_send_telegram_to_topic")
    print("PASS: CTO ha build_landing_from_brief + helpers")


def test_cto_build_landing_no_brief():
    """Senza brief, ritorna errore."""
    from csuite.cto import CTO
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    with patch("csuite.cto.supabase", mock_sb), \
         patch("csuite.cto.TELEGRAM_BOT_TOKEN", ""):
        cto = CTO()
        result = cto.build_landing_from_brief(999)

    assert "error" in result, f"Atteso errore, ottenuto {result}"
    print("PASS: CTO build_landing senza brief -> errore")


def test_cto_build_landing_with_brief():
    """Con brief, genera HTML via Claude."""
    from csuite.cto import CTO
    mock_sb = MagicMock()
    # project_assets lookup: landing_brief
    brief_json = '{"palette": {"primary": "#000", "accent": "#52B788", "bg": "#fff"}, "hero": {"headline": "Test", "subheadline": "Sub", "cta_text": "Demo"}, "sections": [], "fonts": {"heading": "Inter", "body": "Inter"}, "style_notes": "modern"}'
    mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"content": brief_json}]
    )
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"brand_name": "TestBrand", "name": "TestBrand",
               "brand_email": "test@test.com", "brand_domain": "test.com",
               "topic_id": None, "description": "SaaS test"}]
    )
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    html_output = "<html><body><h1>TestBrand</h1></body></html>"

    with patch("csuite.cto.supabase", mock_sb), \
         patch("csuite.cto.TELEGRAM_BOT_TOKEN", ""), \
         patch.object(CTO, "call_claude", return_value=html_output):
        cto = CTO()
        result = cto.build_landing_from_brief(5)

    assert result.get("status") == "ok", f"Atteso ok, ottenuto {result}"
    assert result.get("html_length", 0) > 0
    print("PASS: CTO build_landing con brief -> HTML generato")


if __name__ == "__main__":
    test_cto_has_build_landing()
    test_cto_build_landing_no_brief()
    test_cto_build_landing_with_brief()
    print("\nTutti i test CTO landing build PASS")
