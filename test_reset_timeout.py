"""Test v5.31: conversation_state timeout dopo 30 minuti."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

def test_timeout_clears_chief():
    """Chief attivo scaduto (>30 min) deve restituire None."""
    from csuite.coo import COO, CONVERSATION_TIMEOUT_MINUTES
    coo = COO()
    mock_sb = MagicMock()
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"active_chief": "cmo", "last_message_at": old_time}]
    )
    mock_sb.table.return_value.delete.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    with patch("csuite.coo.supabase", mock_sb):
        result = coo.get_active_chief_for_topic(91)
    assert result is None, f"Atteso None (timeout), ottenuto {result}"
    print(f"PASS: Chief scaduto dopo {CONVERSATION_TIMEOUT_MINUTES} min -> None")

def test_fresh_chief_returns():
    """Chief attivo fresco (<30 min) deve restituire il chief_id."""
    from csuite.coo import COO
    coo = COO()
    mock_sb = MagicMock()
    fresh_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"active_chief": "cto", "last_message_at": fresh_time}]
    )
    with patch("csuite.coo.supabase", mock_sb):
        result = coo.get_active_chief_for_topic(91)
    assert result == "cto", f"Atteso cto, ottenuto {result}"
    print("PASS: Chief fresco (5 min) -> cto")

def test_timeout_constant():
    """CONVERSATION_TIMEOUT_MINUTES deve essere 30."""
    from csuite.coo import CONVERSATION_TIMEOUT_MINUTES
    assert CONVERSATION_TIMEOUT_MINUTES == 30, f"Atteso 30, ottenuto {CONVERSATION_TIMEOUT_MINUTES}"
    print("PASS: CONVERSATION_TIMEOUT_MINUTES = 30")

if __name__ == "__main__":
    test_timeout_clears_chief()
    test_fresh_chief_returns()
    test_timeout_constant()
    print("\nTutti i test PASS")
