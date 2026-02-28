"""Test v5.32: CMO landing flow v2 — design concept, NO codice."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch


def test_cmo_never_writes_html():
    """CMO.design_landing_concept non genera HTML, genera brief JSON."""
    from csuite.cmo import CMO

    mock_sb = MagicMock()
    # Project lookup
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": 5, "name": "TestBrand", "brand_name": "TestBrand",
               "brand_email": "test@test.com", "brand_domain": "test.com",
               "smoke_test_method": "cold", "topic_id": 91, "description": "test SaaS"}]
    )
    mock_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    mock_claude_resp = '{"palette": {"primary": "#000"}, "hero": {"headline": "Test"}, "sections": [], "style_notes": "modern"}'

    with patch("csuite.cmo.supabase", mock_sb), \
         patch("csuite.cmo.TELEGRAM_BOT_TOKEN", ""), \
         patch("csuite.utils.web_search", return_value="risultati ricerca"), \
         patch.object(CMO, "call_claude", return_value=mock_claude_resp):
        cmo = CMO()
        result = cmo.design_landing_concept(5)

    assert result.get("status") == "ok", f"Atteso ok, ottenuto {result}"
    assert "brief" in result, "Deve contenere brief"
    print("PASS: CMO design_landing_concept ritorna brief, non HTML")


def test_cmo_parse_brief_json():
    """_parse_brief_json gestisce vari formati."""
    from csuite.cmo import CMO
    cmo = CMO()

    # JSON puro
    result = cmo._parse_brief_json('{"palette": {"primary": "#000"}}')
    assert result is not None
    assert result["palette"]["primary"] == "#000"

    # JSON in markdown fence
    result = cmo._parse_brief_json('```json\n{"hero": {"headline": "Test"}}\n```')
    assert result is not None
    assert result["hero"]["headline"] == "Test"

    # Testo non JSON
    result = cmo._parse_brief_json("questo non e' json")
    assert result is None

    print("PASS: _parse_brief_json gestisce JSON, markdown, non-JSON")


def test_cmo_has_landing_callbacks():
    """CMO ha handle_landing_approve/modify/redo."""
    from csuite.cmo import CMO
    assert hasattr(CMO, "handle_landing_approve")
    assert hasattr(CMO, "handle_landing_modify")
    assert hasattr(CMO, "handle_landing_redo")
    print("PASS: CMO ha tutti i callback handlers")


def test_generate_landing_page_html_deprecated():
    """generate_landing_page_html() delega a design_landing_concept()."""
    from csuite.cmo import CMO

    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": 5, "name": "Test", "brand_name": "Test",
               "brand_email": "", "brand_domain": "", "smoke_test_method": "",
               "topic_id": None, "description": ""}]
    )
    mock_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    with patch("csuite.cmo.supabase", mock_sb), \
         patch("csuite.cmo.TELEGRAM_BOT_TOKEN", ""), \
         patch("csuite.utils.web_search", return_value="test"), \
         patch.object(CMO, "call_claude", return_value='{"palette": {}}'):
        cmo = CMO()
        result = cmo.generate_landing_page_html(5)

    assert result.get("status") == "ok"
    print("PASS: generate_landing_page_html delega a design_landing_concept")


if __name__ == "__main__":
    test_cmo_never_writes_html()
    test_cmo_parse_brief_json()
    test_cmo_has_landing_callbacks()
    test_generate_landing_page_html_deprecated()
    print("\nTutti i test CMO landing v2 PASS")
