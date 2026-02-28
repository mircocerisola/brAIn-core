"""Test v5.30: CMO cerca online autonomamente PRIMA di generare landing."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch, call
import inspect

def test_cmo_landing_has_web_search():
    """generate_landing_page_html deve chiamare web_search PRIMA di call_claude."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])

        from csuite.cmo import CMO
        # Verifica che il source code di generate_landing_page_html contiene web_search
        source = inspect.getsource(CMO.generate_landing_page_html)
        assert "web_search" in source, "generate_landing_page_html deve usare web_search"
        print("PASS: generate_landing_page_html contiene web_search")

        # Verifica che web_search viene chiamata PRIMA di call_claude
        ws_pos = source.index("web_search")
        cc_pos = source.index("call_claude")
        assert ws_pos < cc_pos, "web_search deve essere chiamata PRIMA di call_claude"
        print("PASS: web_search chiamata PRIMA di call_claude")

def test_cmo_landing_no_questions():
    """CMO non deve chiedere a Mirco stile o riferimenti."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

        from csuite.cmo import CMO
        source = inspect.getsource(CMO.generate_landing_page_html)
        # Non deve chiedere a Mirco
        assert "scegli" not in source.lower(), "CMO non deve chiedere 'scegli' a Mirco"
        assert "quale prefer" not in source.lower(), "CMO non deve chiedere preferenze"
        assert "dammi" not in source.lower(), "CMO non deve chiedere 'dammi' a Mirco"
        print("PASS: CMO non chiede nulla a Mirco nella landing generation")

def test_cmo_landing_uses_perplexity_results():
    """Il prompt Claude deve includere i risultati della ricerca web."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

        from csuite.cmo import CMO
        source = inspect.getsource(CMO.generate_landing_page_html)
        assert "ref_search" in source, "Risultati ricerca devono essere usati nel prompt"
        assert "style_search" in source, "Risultati stile devono essere usati nel prompt"
        assert "Riferimenti visivi" in source, "Prompt deve citare riferimenti visivi"
        print("PASS: prompt Claude include risultati ricerca web")

if __name__ == "__main__":
    test_cmo_landing_has_web_search()
    test_cmo_landing_no_questions()
    test_cmo_landing_uses_perplexity_results()
    print("\nTutti i test PASS")
