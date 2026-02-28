"""Test v5.29: CSO ha identita fissa e cultura iniettata."""
import sys
import os
sys.path.insert(0, "deploy-agents")

def test_cso_importa_cultura():
    """CSO deve importare CULTURA_BRAIN."""
    with open(os.path.join("deploy-agents", "csuite", "cso.py"), "r", encoding="utf-8") as f:
        code = f.read()
    assert "CULTURA_BRAIN" in code, "CSO non importa CULTURA_BRAIN"
    print("PASS: CSO importa CULTURA_BRAIN")

def test_cso_domain_identity():
    """CSO deve avere MY_DOMAIN e MY_REFUSE_DOMAINS."""
    with open(os.path.join("deploy-agents", "csuite", "cso.py"), "r", encoding="utf-8") as f:
        code = f.read()
    assert "MY_DOMAIN" in code, "CSO manca MY_DOMAIN"
    assert "MY_REFUSE_DOMAINS" in code, "CSO manca MY_REFUSE_DOMAINS"
    print("PASS: CSO ha MY_DOMAIN e MY_REFUSE_DOMAINS")

def test_cso_web_search_inherited():
    """CSO deve ereditare web search da BaseChief (via answer_question)."""
    with open(os.path.join("deploy-agents", "core", "base_chief.py"), "r", encoding="utf-8") as f:
        code = f.read()
    assert "detect_web_search" in code, "base_chief.py non ha detect_web_search"
    assert "web_search" in code, "base_chief.py non ha web_search"
    print("PASS: CSO eredita web search da BaseChief")

def test_cultura_errori_ammessi():
    """CULTURA_BRAIN deve avere regola sugli errori."""
    from csuite.cultura import CULTURA_BRAIN
    assert "ERRORI" in CULTURA_BRAIN or "errori" in CULTURA_BRAIN.lower(), "Regola errori mancante"
    print("PASS: regola errori presente in CULTURA_BRAIN")

def test_cultura_non_sai_lo_dici():
    """CULTURA_BRAIN deve avere regola 'non sai lo dici'."""
    from csuite.cultura import CULTURA_BRAIN
    assert "NON SAI" in CULTURA_BRAIN, "Regola 'NON SAI' mancante"
    print("PASS: regola 'NON SAI? LO DICI.' presente")

if __name__ == "__main__":
    test_cso_importa_cultura()
    test_cso_domain_identity()
    test_cso_web_search_inherited()
    test_cultura_errori_ammessi()
    test_cultura_non_sai_lo_dici()
    print("\nTutti i test PASS")
