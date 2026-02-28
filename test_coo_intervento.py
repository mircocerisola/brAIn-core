"""Test v5.31: COO interviene su trigger frustrazione."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch

def test_intervention_triggers_exist():
    """COO_INTERVENTION_TRIGGERS deve contenere trigger chiave."""
    from csuite.coo import COO_INTERVENTION_TRIGGERS
    assert "confuso" in COO_INTERVENTION_TRIGGERS
    assert "basta" in COO_INTERVENTION_TRIGGERS
    assert "non era per te" in COO_INTERVENTION_TRIGGERS
    assert "rispondete tutti" in COO_INTERVENTION_TRIGGERS
    print(f"PASS: {len(COO_INTERVENTION_TRIGGERS)} trigger COO presenti")

def test_handle_interruption_detects():
    """handle_interruption deve intercettare messaggi di frustrazione."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])

        from csuite.coo import COO
        coo = COO()

        # Messaggio frustrato -> True
        result = coo.handle_interruption(91, "basta, rispondete tutti quanti!")
        assert result is True, f"Atteso True, ottenuto {result}"
        print("PASS: handle_interruption rileva frustrazione")

        # Messaggio normale -> False
        result2 = coo.handle_interruption(91, "fammi il report settimanale")
        assert result2 is False, f"Atteso False, ottenuto {result2}"
        print("PASS: handle_interruption ignora messaggi normali")

def test_handle_interruption_no_false_positive():
    """Messaggi normali NON devono triggerare intervento COO."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])

        from csuite.coo import COO
        coo = COO()
        normals = [
            "aggiorna il report",
            "qual e lo status del progetto",
            "dammi i costi del mese",
            "come procede il cantiere",
        ]
        for m in normals:
            assert coo.handle_interruption(91, m) is False, f"False positive su: {m}"
        print("PASS: zero false positive su 4 messaggi normali")

if __name__ == "__main__":
    test_intervention_triggers_exist()
    test_handle_interruption_detects()
    test_handle_interruption_no_false_positive()
    print("\nTutti i test PASS")
