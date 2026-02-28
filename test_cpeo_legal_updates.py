"""Test v5.32: CPeO daily_legal_updates."""
import sys
sys.path.insert(0, "deploy-agents")


def test_daily_legal_updates_importable():
    """daily_legal_updates e' importabile."""
    from csuite.cpeo import daily_legal_updates
    assert callable(daily_legal_updates)
    print("PASS: daily_legal_updates importabile")


def test_send_to_clo_topic_importable():
    """_send_to_clo_topic e' importabile."""
    from csuite.cpeo import _send_to_clo_topic
    assert callable(_send_to_clo_topic)
    print("PASS: _send_to_clo_topic importabile")


def test_daily_legal_updates_no_perplexity():
    """Senza Perplexity, ritorna errore."""
    from unittest.mock import patch, MagicMock

    # search_perplexity ritorna stringa vuota
    with patch("csuite.cpeo.search_perplexity", return_value=""), \
         patch("csuite.cpeo.TELEGRAM_BOT_TOKEN", ""):
        from csuite.cpeo import daily_legal_updates
        result = daily_legal_updates()

    assert result.get("status") == "error"
    print("PASS: daily_legal_updates senza Perplexity -> errore")


if __name__ == "__main__":
    test_daily_legal_updates_importable()
    test_send_to_clo_topic_importable()
    test_daily_legal_updates_no_perplexity()
    print("\nTutti i test CPeO legal updates PASS")
