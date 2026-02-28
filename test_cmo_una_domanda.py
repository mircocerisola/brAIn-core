"""Test v5.29: CMO rispetta regola 'una domanda alla volta' dalla cultura."""
import sys
sys.path.insert(0, "deploy-agents")

def test_cultura_una_domanda():
    """CULTURA_BRAIN deve contenere la regola UNA DOMANDA ALLA VOLTA."""
    from csuite.cultura import CULTURA_BRAIN
    assert "UNA DOMANDA ALLA VOLTA" in CULTURA_BRAIN, "Regola 'UNA DOMANDA ALLA VOLTA' mancante"
    print("PASS: regola 'UNA DOMANDA ALLA VOLTA' presente in CULTURA_BRAIN")

def test_cultura_no_separatori():
    """CULTURA_BRAIN deve vietare separatori."""
    from csuite.cultura import CULTURA_BRAIN
    assert "Separatori" in CULTURA_BRAIN or "separatori" in CULTURA_BRAIN, "Divieto separatori mancante"
    print("PASS: divieto separatori presente in CULTURA_BRAIN")

def test_cultura_no_markdown():
    """CULTURA_BRAIN deve vietare tabelle markdown nei messaggi Telegram."""
    from csuite.cultura import CULTURA_BRAIN
    cultura_lower = CULTURA_BRAIN.lower()
    assert "tabelle markdown" in cultura_lower or "markdown" in cultura_lower, "Divieto markdown mancante"
    print("PASS: divieto markdown/tabelle presente in CULTURA_BRAIN")

def test_cultura_formato_icona():
    """CULTURA_BRAIN deve specificare il formato icona + nome."""
    from csuite.cultura import CULTURA_BRAIN
    assert "{icona}" in CULTURA_BRAIN or "icona" in CULTURA_BRAIN.lower(), "Formato icona mancante"
    assert "{NOME}" in CULTURA_BRAIN or "NOME" in CULTURA_BRAIN, "Formato NOME mancante"
    print("PASS: formato icona + NOME presente in CULTURA_BRAIN")

def test_cmo_importa_cultura():
    """CMO deve importare CULTURA_BRAIN."""
    import os
    with open(os.path.join("deploy-agents", "csuite", "cmo.py"), "r", encoding="utf-8") as f:
        code = f.read()
    assert "CULTURA_BRAIN" in code, "CMO non importa CULTURA_BRAIN"
    print("PASS: CMO importa CULTURA_BRAIN")

if __name__ == "__main__":
    test_cultura_una_domanda()
    test_cultura_no_separatori()
    test_cultura_no_markdown()
    test_cultura_formato_icona()
    test_cmo_importa_cultura()
    print("\nTutti i test PASS")
