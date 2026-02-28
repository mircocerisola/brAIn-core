"""Test v5.29: CULTURA_BRAIN iniettata in tutti i Chief via base_chief.py."""
import sys
import os
sys.path.insert(0, "deploy-agents")

def test_cultura_in_base_chief():
    """CULTURA_BRAIN deve essere importata in base_chief.py e preposta nel system prompt."""
    with open(os.path.join("deploy-agents", "core", "base_chief.py"), "r", encoding="utf-8") as f:
        code = f.read()
    assert "from csuite.cultura import CULTURA_BRAIN" in code, "base_chief.py deve importare CULTURA_BRAIN"
    assert "CULTURA_BRAIN" in code, "base_chief.py deve usare CULTURA_BRAIN"
    print("PASS: CULTURA_BRAIN importata e usata in base_chief.py")

def test_cultura_in_all_chiefs():
    """Ogni Chief deve importare CULTURA_BRAIN."""
    chiefs = ["cmo.py", "cto.py", "cso.py", "cfo.py", "coo.py", "clo.py", "cpeo.py"]
    found = 0
    for chief_file in chiefs:
        path = os.path.join("deploy-agents", "csuite", chief_file)
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
        if "CULTURA_BRAIN" in code:
            found += 1
            print(f"PASS: {chief_file} importa CULTURA_BRAIN")
        else:
            print(f"FAIL: {chief_file} NON importa CULTURA_BRAIN")
    assert found >= 7, f"Solo {found}/7 Chief importano CULTURA_BRAIN"
    print(f"PASS: tutti {found}/7 Chief importano CULTURA_BRAIN")

def test_cultura_content():
    """CULTURA_BRAIN deve contenere le regole fondamentali."""
    from csuite.cultura import CULTURA_BRAIN
    assert "NON SAI" in CULTURA_BRAIN, "Regola 1 mancante"
    assert "INTERNET" in CULTURA_BRAIN or "BISOGNO" in CULTURA_BRAIN, "Regola 2 mancante"
    assert "DOMINIO" in CULTURA_BRAIN, "Regola 3 mancante"
    assert "UNA DOMANDA" in CULTURA_BRAIN, "Regola 6 mancante"
    assert "VIETATO" in CULTURA_BRAIN, "Sezione VIETATO mancante"
    print("PASS: CULTURA_BRAIN contiene tutte le regole fondamentali")

def test_get_chief_system_prompt():
    """get_chief_system_prompt deve costruire prompt completo."""
    from csuite.cultura import get_chief_system_prompt
    prompt = get_chief_system_prompt("cfo", "finanza e costi", "codice e marketing")
    assert "CFO" in prompt, "Ruolo CFO mancante"
    assert "finanza e costi" in prompt, "Dominio mancante"
    assert "codice e marketing" in prompt, "Refuse domains mancante"
    assert "NON SAI" in prompt, "Cultura non iniettata nel prompt"
    print("PASS: get_chief_system_prompt costruisce prompt completo con cultura")

if __name__ == "__main__":
    test_cultura_in_base_chief()
    test_cultura_in_all_chiefs()
    test_cultura_content()
    test_get_chief_system_prompt()
    print("\nTutti i test PASS")
