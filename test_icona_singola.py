"""Test v5.30: ogni messaggio Chief ha UNA sola icona, niente doppie."""
import sys
sys.path.insert(0, "deploy-agents")

def test_fmt_single_icon():
    """fmt() deve produrre esattamente UNA icona all'inizio."""
    from csuite.utils import fmt, CHIEF_ICONS
    for chief_id, icon in CHIEF_ICONS.items():
        msg = fmt(chief_id, "Test titolo", "Test contenuto")
        # La prima riga deve iniziare con l'icona
        first_line = msg.split("\n")[0]
        # Conta le icone nella prima riga (emoji unicode)
        icon_count = first_line.count(icon)
        assert icon_count == 1, f"{chief_id}: {icon_count} icone trovate, attesa 1. Riga: {first_line}"
        # Nessuna icona utente
        assert "\U0001f464" not in msg, f"{chief_id}: icona utente trovata nel messaggio"
        print("PASS: " + chief_id + " -> una sola icona")

def test_fmt_no_user_icon():
    """Nessun messaggio deve contenere icona utente 👤."""
    from csuite.utils import fmt
    chiefs = ["cmo", "cso", "cto", "cfo", "coo", "clo", "cpeo"]
    for c in chiefs:
        msg = fmt(c, "Titolo", "Contenuto test")
        assert "\U0001f464" not in msg, f"Icona utente trovata in {c}"
    print("PASS: nessuna icona utente in nessun Chief")

def test_fmt_structure():
    """Formato messaggio: icona NOME (riga 1), titolo (riga 2), vuota (riga 3), contenuto (riga 4+)."""
    from csuite.utils import fmt
    msg = fmt("cmo", "Report settimanale", "Ecco i dati.")
    lines = msg.split("\n")
    assert len(lines) >= 4, f"Messaggio troppo corto: {len(lines)} righe"
    assert "\U0001f3a8" in lines[0], f"Prima riga senza icona CMO: {lines[0]}"
    assert "CMO" in lines[0], f"Prima riga senza nome CMO: {lines[0]}"
    assert lines[1] == "Report settimanale", f"Seconda riga non e' il titolo: {lines[1]}"
    assert lines[2] == "", f"Terza riga non e' vuota: '{lines[2]}'"
    assert "Ecco i dati." in lines[3], f"Quarta riga non ha contenuto: {lines[3]}"
    print("PASS: struttura messaggio corretta (icona+nome / titolo / vuota / contenuto)")

def test_no_double_icon_in_source():
    """Nessun file Chief deve aggiungere icone extra prima di fmt()."""
    import os
    csuite_dir = os.path.join("deploy-agents", "csuite")
    for fname in os.listdir(csuite_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(csuite_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            code = f.read()
        # Non deve esserci "👤" da nessuna parte
        assert "\U0001f464" not in code, f"Icona utente trovata in {fname}"
    print("PASS: nessun file csuite/ contiene icona utente")

if __name__ == "__main__":
    test_fmt_single_icon()
    test_fmt_no_user_icon()
    test_fmt_structure()
    test_no_double_icon_in_source()
    print("\nTutti i test PASS")
