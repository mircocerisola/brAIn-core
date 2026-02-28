"""Test v5.29: CFO auto pricing search triggers."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch

def test_cfo_search_triggers_exist():
    """CFO_SEARCH_TRIGGERS deve esistere con almeno 10 trigger."""
    from csuite.cfo import CFO_SEARCH_TRIGGERS
    assert len(CFO_SEARCH_TRIGGERS) >= 10, f"Solo {len(CFO_SEARCH_TRIGGERS)} trigger, servono >= 10"
    # Verifica trigger chiave
    triggers_str = " ".join(CFO_SEARCH_TRIGGERS)
    assert "quanto costa" in triggers_str, "Trigger 'quanto costa' mancante"
    assert "pricing" in triggers_str, "Trigger 'pricing' mancante"
    assert "piano anthropic" in triggers_str, "Trigger 'piano anthropic' mancante"
    assert "confronta" in triggers_str, "Trigger 'confronta' mancante"
    print(f"PASS: {len(CFO_SEARCH_TRIGGERS)} trigger CFO presenti")

def test_cfo_answer_question_override():
    """CFO.answer_question deve fare override con auto-search."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        # Mock supabase
        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])

        from csuite.cfo import CFO
        cfo = CFO()

        # Verifica che answer_question esiste come override
        import inspect
        assert "answer_question" in CFO.__dict__, "CFO deve avere override di answer_question"
        sig = inspect.signature(CFO.answer_question)
        assert "question" in sig.parameters, "answer_question deve accettare question"
        print("PASS: CFO ha override di answer_question")

def test_cfo_trigger_detection():
    """CFO deve rilevare i trigger pricing nel messaggio."""
    from csuite.cfo import CFO_SEARCH_TRIGGERS
    test_messages = [
        ("quanto costa il piano Max di Anthropic?", True),
        ("fammi un report sui costi", False),
        ("confronta Supabase e Firebase", True),
        ("qual è il pricing di GCP Cloud Run?", True),
        ("buongiorno CFO", False),
    ]
    for msg, expected in test_messages:
        msg_lower = msg.lower()
        found = any(t in msg_lower for t in CFO_SEARCH_TRIGGERS)
        assert found == expected, f"Trigger detection errata per '{msg}': atteso {expected}, ottenuto {found}"
        status = "trigger" if found else "no trigger"
        print(f"PASS: '{msg[:40]}...' -> {status}")

if __name__ == "__main__":
    test_cfo_search_triggers_exist()
    test_cfo_answer_question_override()
    test_cfo_trigger_detection()
    print("\nTutti i test PASS")
