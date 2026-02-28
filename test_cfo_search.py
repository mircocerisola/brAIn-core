"""Test v5.30: CFO web search per domande pricing."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch
import inspect

def test_cfo_search_triggers():
    """CFO_SEARCH_TRIGGERS deve contenere trigger pricing chiave."""
    from csuite.cfo import CFO_SEARCH_TRIGGERS
    assert "quanto costa" in CFO_SEARCH_TRIGGERS
    assert "pricing" in CFO_SEARCH_TRIGGERS
    assert "piano anthropic" in CFO_SEARCH_TRIGGERS
    assert "confronta" in CFO_SEARCH_TRIGGERS
    assert "quale conviene" in CFO_SEARCH_TRIGGERS
    print(f"PASS: {len(CFO_SEARCH_TRIGGERS)} trigger CFO presenti")

def test_cfo_answer_question_has_search():
    """CFO.answer_question deve usare web_search per trigger pricing."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])

        from csuite.cfo import CFO
        # Verifica override
        assert "answer_question" in CFO.__dict__, "CFO deve avere override answer_question"
        source = inspect.getsource(CFO.answer_question)
        assert "web_search" in source, "answer_question deve usare web_search"
        assert "CFO_SEARCH_TRIGGERS" in source, "answer_question deve usare CFO_SEARCH_TRIGGERS"
        print("PASS: CFO.answer_question usa web_search con trigger pricing")

def test_cfo_trigger_match():
    """Trigger CFO devono matchare domande pricing reali."""
    from csuite.cfo import CFO_SEARCH_TRIGGERS
    questions = [
        ("quanto costa il piano Max di Anthropic?", True),
        ("qual e il pricing di Supabase Pro?", True),
        ("confronta Cloud Run con Vercel", True),
        ("fammi un report dei costi interni", False),
        ("buongiorno CFO come stai", False),
    ]
    for q, expected in questions:
        found = any(t in q.lower() for t in CFO_SEARCH_TRIGGERS)
        status = "trigger" if found else "no trigger"
        assert found == expected, f"'{q[:50]}': atteso {expected}, ottenuto {found}"
        print(f"PASS: '{q[:50]}...' -> {status}")

if __name__ == "__main__":
    test_cfo_search_triggers()
    test_cfo_answer_question_has_search()
    test_cfo_trigger_match()
    print("\nTutti i test PASS")
