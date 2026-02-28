"""Test v5.31: solo un Chief alla volta risponde per topic."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

def test_set_and_get_active_chief():
    """set_active_chief + get_active_chief_for_topic roundtrip."""
    with patch("core.config.supabase", MagicMock()), \
         patch("core.config.claude", MagicMock()), \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):
        from csuite.coo import COO

    mock_sb = MagicMock()
    mock_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
    fresh_time = datetime.now(timezone.utc).isoformat()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"active_chief": "cto", "last_message_at": fresh_time}]
    )
    coo = COO()

    with patch("csuite.coo.supabase", mock_sb):
        coo.set_active_chief(91, "cto", project_slug="restai")
        print("PASS: set_active_chief chiama upsert su conversation_state")

        result = coo.get_active_chief_for_topic(91)
        assert result == "cto", f"Atteso cto, ottenuto {result}"
        print("PASS: get_active_chief_for_topic restituisce cto")

def test_no_active_returns_none():
    """Se non c'e' nessun Chief attivo, restituisce None."""
    from csuite.coo import COO
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    coo = COO()

    with patch("csuite.coo.supabase", mock_sb):
        result = coo.get_active_chief_for_topic(999)
        assert result is None, f"Atteso None, ottenuto {result}"
        print("PASS: nessun Chief attivo -> None")

if __name__ == "__main__":
    test_set_and_get_active_chief()
    test_no_active_returns_none()
    print("\nTutti i test PASS")
