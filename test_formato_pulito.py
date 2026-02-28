"""Test 1: verifica formato pulito CTO e CMO — zero separatori, zero vecchi pattern."""
import sys
sys.path.insert(0, "deploy-agents")
import inspect

results = []
pass_count = 0
fail_count = 0

def check(label, condition, detail=""):
    global pass_count, fail_count
    if condition:
        pass_count += 1
        results.append("  " + label + ": PASS")
    else:
        fail_count += 1
        results.append("  " + label + ": FAIL" + (": " + detail if detail else ""))

# --- CTO ---
try:
    from csuite.cto import CTO, format_cto_message
    check("cto_import", True)
except Exception as e:
    check("cto_import", False, str(e))

# format_cto_message esiste e funziona
try:
    msg = format_cto_message("Titolo test", "Contenuto test")
    check("cto_format_exists", True)
    check("cto_format_icon", msg.startswith("\U0001f527 CMO") is False and msg.startswith("\U0001f527 CTO"))
    check("cto_format_structure", "\U0001f527 CTO\nTitolo test\n\nContenuto test" == msg,
          "got=" + repr(msg))
except Exception as e:
    check("cto_format_exists", False, str(e))

# format_cto_message senza contenuto
try:
    msg2 = format_cto_message("Solo titolo")
    check("cto_format_no_content", msg2 == "\U0001f527 CTO\nSolo titolo",
          "got=" + repr(msg2))
except Exception as e:
    check("cto_format_no_content", False, str(e))

# Verifica zero separatori nel source code CTO (nei messaggi, non nei commenti)
try:
    src = inspect.getsource(CTO)
    # Rimuovi commenti Python (linee che iniziano con #)
    lines = src.split("\n")
    code_lines = [l for l in lines if not l.strip().startswith("#") and not l.strip().startswith('"""')]
    code_text = "\n".join(code_lines)

    bad_patterns = ["───", "------", "______", "CTO:", "CTO risponde", "risponde:"]
    found_bad = []
    for pat in bad_patterns:
        if pat in code_text:
            found_bad.append(pat)
    check("cto_zero_separators", len(found_bad) == 0, "found=" + str(found_bad))
except Exception as e:
    check("cto_zero_separators", False, str(e))

# Verifica build_update_message usa format_cto_message
try:
    msg = CTO.build_update_message(5, "line1\nline2\nline3")
    check("cto_update_msg_format", msg.startswith("\U0001f527 CTO\n"), "got=" + repr(msg[:50]))
    check("cto_update_msg_no_sep", "───" not in msg and "---" not in msg and "===" not in msg)
except Exception as e:
    check("cto_update_msg", False, str(e))

# --- CMO ---
try:
    from csuite.cmo import CMO, format_cmo_message
    check("cmo_import", True)
except Exception as e:
    check("cmo_import", False, str(e))

# format_cmo_message esiste e funziona
try:
    msg = format_cmo_message("Titolo test", "Contenuto test")
    check("cmo_format_exists", True)
    check("cmo_format_structure", "\U0001f3a8 CMO\nTitolo test\n\nContenuto test" == msg,
          "got=" + repr(msg))
except Exception as e:
    check("cmo_format_exists", False, str(e))

# format_cmo_message senza contenuto
try:
    msg2 = format_cmo_message("Solo titolo")
    check("cmo_format_no_content", msg2 == "\U0001f3a8 CMO\nSolo titolo",
          "got=" + repr(msg2))
except Exception as e:
    check("cmo_format_no_content", False, str(e))

# Verifica zero separatori nel source CMO
try:
    src = inspect.getsource(CMO)
    lines = src.split("\n")
    code_lines = [l for l in lines if not l.strip().startswith("#") and not l.strip().startswith('"""')]
    code_text = "\n".join(code_lines)

    bad_patterns = ["───", "------", "______", "CMO:", "CMO risponde", "risponde:"]
    found_bad = []
    for pat in bad_patterns:
        if pat in code_text:
            found_bad.append(pat)
    check("cmo_zero_separators", len(found_bad) == 0, "found=" + str(found_bad))
except Exception as e:
    check("cmo_zero_separators", False, str(e))

# Verifica CMO ha answer_question override
try:
    has_override = "answer_question" in CMO.__dict__
    check("cmo_answer_question_override", has_override)
except Exception as e:
    check("cmo_answer_question_override", False, str(e))

# Output
print("=== TEST FORMATO PULITO ===")
for r in results:
    print(r)
print("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===")

with open("test_formato_pulito.txt", "w", encoding="utf-8") as f:
    f.write("=== TEST FORMATO PULITO ===\n")
    for r in results:
        f.write(r + "\n")
    f.write("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===\n")
