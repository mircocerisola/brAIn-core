"""Test v5.33: COO context awareness — delegation, history, pending, pipeline."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch, PropertyMock


def test_delegation_trigger_detection():
    """1. Mirco dice 'chiedi al CLO se ha fatto la privacy policy' -> delegation detected."""
    from csuite.coo import COO, COO_DELEGATION_TRIGGERS
    coo = COO()

    # Caso: trigger + Chief nel messaggio
    result = coo._detect_delegation_intent(
        "chiedi al CLO se ha fatto la privacy policy",
        history=[],
    )
    assert result is not None, "Delegation non rilevata!"
    assert result["target"] == "clo", "Target errato: " + str(result.get("target"))
    assert len(result["question"]) > 5, "Domanda vuota"
    print("PASS: delegation trigger + CLO detected")


def test_delegation_from_context():
    """2. Mirco parla del CLO poi dice 'chiediglielo te' -> COO identifica CLO dal contesto."""
    from csuite.coo import COO
    coo = COO()

    history = [
        {"role": "user", "text": "il CLO ha preparato la privacy policy?"},
        {"role": "bot", "text": "Non ho verificato ancora con il CLO."},
    ]
    result = coo._detect_delegation_intent("chiediglielo te", history=history)
    assert result is not None, "Delegation non rilevata dal contesto!"
    assert result["target"] == "clo", "Target errato: " + str(result.get("target"))
    print("PASS: 'chiediglielo te' -> CLO dal contesto")


def test_no_delegation_without_trigger():
    """Messaggio normale senza trigger -> nessuna delegation."""
    from csuite.coo import COO
    coo = COO()

    result = coo._detect_delegation_intent("a che punto siamo col progetto?", history=[])
    assert result is None, "False positive delegation!"
    print("PASS: nessuna delegation senza trigger")


def test_pending_actions_lifecycle():
    """4. coo_pending_actions: save -> load -> complete."""
    from csuite.coo import COO
    mock_sb = MagicMock()
    # Save returns id
    mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": 42}]
    )
    # Load returns pending action
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": 42, "action_description": "Chiesto al CLO", "target_chief": "clo",
               "status": "pending", "created_at": "2026-02-28T10:00:00"}]
    )
    # Complete updates
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    with patch("csuite.coo.supabase", mock_sb):
        coo = COO()
        # Save
        action_id = coo._save_pending_action(
            topic_id=91,
            action_description="Chiesto al CLO la privacy policy",
            target_chief="clo",
        )
        assert action_id == 42, "Save fallito"

        # Complete
        coo._complete_pending_action(42)
        mock_sb.table.return_value.update.assert_called()

    print("PASS: pending actions lifecycle (save + complete)")


def test_project_state_update():
    """5. coo_project_state viene aggiornata."""
    from csuite.coo import COO
    mock_sb = MagicMock()
    mock_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    with patch("csuite.coo.supabase", mock_sb):
        coo = COO()
        coo._update_project_state("restai", project_id=5, current_step="smoke_test_designing")
        mock_sb.table.return_value.upsert.assert_called()

    print("PASS: coo_project_state aggiornata")


def test_topic_history_loading():
    """6. COO legge topic_conversation_history prima di ogni risposta."""
    from csuite.coo import COO
    mock_sb = MagicMock()
    # Mock data in DESC order (come arriva dal DB), il codice fa reversed()
    mock_sb.table.return_value.select.return_value.like.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[
            {"role": "bot", "text": "Tutto procede bene.", "created_at": "2026-02-28T09:01:00"},
            {"role": "user", "text": "come va il progetto?", "created_at": "2026-02-28T09:00:00"},
        ]
    )

    with patch("csuite.coo.supabase", mock_sb):
        coo = COO()
        history = coo._load_topic_history(91, limit=20)
        assert len(history) == 2, "History non caricata: " + str(len(history))
        assert history[0]["role"] == "user"

    print("PASS: topic_conversation_history caricata")


def test_forbidden_phrases_detected():
    """3. COO non risponde mai 'non ho dati' — frasi vietate rilevate."""
    from csuite.coo import COO
    coo = COO()

    assert coo._contains_forbidden("Non ho dati disponibili al momento")
    assert coo._contains_forbidden("Purtroppo non ho informazioni su questo")
    assert coo._contains_forbidden("Non lo so con certezza, verifico")
    assert not coo._contains_forbidden("Ecco lo stato del cantiere RestaAI")
    assert not coo._contains_forbidden("Ho verificato: il CLO ha completato la privacy policy")

    print("PASS: frasi vietate rilevate correttamente")


def test_enriched_context_build():
    """Verifica che il contesto arricchito contenga history, pending, pipeline, regole."""
    from csuite.coo import COO
    coo = COO()

    history = [
        {"role": "user", "text": "il CLO ha finito?", "created_at": "2026-02-28T09:00:00"},
    ]
    pending = [
        {"action_description": "Chiesto al CLO", "target_chief": "clo",
         "created_at": "2026-02-28T09:00:00"},
    ]
    pipeline = "Progetto: RestaAI\nStep: smoke_test_designing"

    ctx = coo._build_enriched_context(history, pending, pipeline, "extra context")

    assert "CONVERSAZIONE RECENTE" in ctx, "Manca conversazione"
    assert "il CLO ha finito?" in ctx, "Manca messaggio history"
    assert "AZIONI PENDENTI" in ctx, "Manca pending"
    assert "Chiesto al CLO" in ctx, "Manca action description"
    assert "STATO CANTIERE" in ctx, "Manca pipeline"
    assert "RestaAI" in ctx, "Manca progetto"
    assert "REGOLE OPERATIVE" in ctx, "Manca regole"
    assert "non ho dati" in ctx.lower(), "Manca divieto"
    assert "extra context" in ctx, "Manca user context"

    print("PASS: enriched context contiene tutto")


def test_find_chief_in_text():
    """Verifica keyword matching per tutti i Chief."""
    from csuite.coo import COO
    coo = COO()

    assert coo._find_chief_in_text("il clo ha finito?") == "clo"
    assert coo._find_chief_in_text("privacy policy pronta?") == "clo"
    assert coo._find_chief_in_text("la landing page") == "cmo"
    assert coo._find_chief_in_text("quanto costa?") == "cfo"
    assert coo._find_chief_in_text("il deploy") == "cto"
    assert coo._find_chief_in_text("strategia di mercato") == "cso"
    assert coo._find_chief_in_text("formazione del team") == "cpeo"
    assert coo._find_chief_in_text("buongiorno") is None

    print("PASS: find_chief_in_text corretto per tutti i Chief")


if __name__ == "__main__":
    test_delegation_trigger_detection()
    test_delegation_from_context()
    test_no_delegation_without_trigger()
    test_pending_actions_lifecycle()
    test_project_state_update()
    test_topic_history_loading()
    test_forbidden_phrases_detected()
    test_enriched_context_build()
    test_find_chief_in_text()
    print("\nTutti i 9 test COO context v5.33 PASS")
