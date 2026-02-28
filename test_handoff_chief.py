"""Test v5.31: handoff esplicito tra Chief."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch

def test_complete_task_and_handoff():
    """complete_task_and_handoff deve aggiornare conversation_state e creare agent_event."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])

        from csuite.coo import COO
        coo = COO()

        result = coo.complete_task_and_handoff(
            topic_id=91,
            from_chief="cmo",
            to_chief="cto",
            task_summary="Landing page completata",
        )
        assert result["from"] == "cmo", f"from errato: {result['from']}"
        assert result["to"] == "cto", f"to errato: {result['to']}"
        assert result["topic_id"] == 91, f"topic_id errato: {result['topic_id']}"

        # Verifica che upsert sia stato chiamato (set_active_chief)
        calls = [str(c) for c in mock_sb.table.call_args_list]
        assert any("conversation_state" in c for c in calls), "conversation_state non aggiornata"
        assert any("agent_events" in c for c in calls), "agent_events non creato"
        print("PASS: handoff cmo->cto completato, conversation_state + agent_event creati")

def test_handoff_method_exists():
    """COO deve avere complete_task_and_handoff nel suo __dict__."""
    with patch("core.config.supabase") as mock_sb, \
         patch("core.config.claude") as mock_cl, \
         patch("core.config.TELEGRAM_BOT_TOKEN", "fake"):

        mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        mock_sb.table.return_value.select.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

        from csuite.coo import COO
        assert hasattr(COO, "complete_task_and_handoff"), "COO manca complete_task_and_handoff"
        assert hasattr(COO, "set_active_chief"), "COO manca set_active_chief"
        assert hasattr(COO, "get_active_chief_for_topic"), "COO manca get_active_chief_for_topic"
        assert hasattr(COO, "clear_active_chief"), "COO manca clear_active_chief"
        assert hasattr(COO, "handle_interruption"), "COO manca handle_interruption"
        print("PASS: COO ha tutti i 5 metodi conversation_state")

if __name__ == "__main__":
    test_complete_task_and_handoff()
    test_handoff_method_exists()
    print("\nTutti i test PASS")
