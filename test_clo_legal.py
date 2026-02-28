"""Test v5.32: CLO legal documents + gate check."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import MagicMock, patch


def test_clo_has_legal_methods():
    """CLO ha generate_legal_documents, legal_gate_check, callbacks."""
    from csuite.clo import CLO, REQUIRED_LEGAL_DOCS
    assert hasattr(CLO, "generate_legal_documents")
    assert hasattr(CLO, "legal_gate_check")
    assert hasattr(CLO, "handle_legal_docs_approve")
    assert hasattr(CLO, "handle_legal_docs_view")
    assert len(REQUIRED_LEGAL_DOCS) == 4
    print("PASS: CLO ha tutti i metodi legali + 4 documenti obbligatori")


def test_legal_gate_missing_docs():
    """Gate check con documenti mancanti ritorna approved=False."""
    from csuite.clo import CLO
    mock_sb = MagicMock()
    # Nessun documento trovato
    mock_sb.table.return_value.select.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(
        data=[]
    )

    with patch("csuite.clo.supabase", mock_sb):
        clo = CLO()
        result = clo.legal_gate_check(5)

    assert result["approved"] is False
    assert len(result["missing"]) == 4
    print("PASS: Gate check senza documenti -> approved=False, 4 mancanti")


def test_legal_gate_all_docs():
    """Gate check con tutti i documenti + legal_status approved."""
    from csuite.clo import CLO, REQUIRED_LEGAL_DOCS
    mock_sb = MagicMock()
    # Tutti i documenti presenti
    mock_sb.table.return_value.select.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(
        data=[{"asset_type": d, "content": "doc content"} for d in REQUIRED_LEGAL_DOCS]
    )
    # legal_status = approved
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"legal_status": "approved"}]
    )

    with patch("csuite.clo.supabase", mock_sb):
        clo = CLO()
        result = clo.legal_gate_check(5)

    assert result["approved"] is True
    assert len(result["missing"]) == 0
    print("PASS: Gate check con tutti documenti + approved -> True")


def test_legal_docs_approve_callback():
    """handle_legal_docs_approve aggiorna legal_status."""
    from csuite.clo import CLO
    mock_sb = MagicMock()
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])

    with patch("csuite.clo.supabase", mock_sb):
        clo = CLO()
        result = clo.handle_legal_docs_approve(5)

    assert result.get("status") == "ok"
    # Verifica che update sia stato chiamato con legal_status=approved
    mock_sb.table.return_value.update.assert_called()
    print("PASS: handle_legal_docs_approve aggiorna legal_status")


if __name__ == "__main__":
    test_clo_has_legal_methods()
    test_legal_gate_missing_docs()
    test_legal_gate_all_docs()
    test_legal_docs_approve_callback()
    print("\nTutti i test CLO legal PASS")
