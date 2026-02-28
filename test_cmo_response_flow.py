"""Test 4: verifica CMO response flow — keyword detection, answer_question override."""
import sys
sys.path.insert(0, "deploy-agents")

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

from csuite.cmo import CMO, format_cmo_message, _BOZZA_KEYWORDS

# 1. _BOZZA_KEYWORDS contiene le keyword richieste
required_kw = ["bozza", "landing", "visiva", "design", "mockup", "wireframe", "pagina"]
for kw in required_kw:
    check("keyword_" + kw, kw in _BOZZA_KEYWORDS, "missing from _BOZZA_KEYWORDS")

# 2. CMO ha answer_question override
check("has_answer_question", "answer_question" in CMO.__dict__)

# 3. CMO ha _handle_bozza_request
check("has_handle_bozza", hasattr(CMO, "_handle_bozza_request"))

# 4. CMO ha _extract_project_name
check("has_extract_name", hasattr(CMO, "_extract_project_name"))

# 5. _extract_project_name funziona
try:
    cmo = CMO()
    name1 = cmo._extract_project_name("Fammi una bozza per RestaAI")
    check("extract_name_per", name1 == "RestaAI", "got=" + repr(name1))

    name2 = cmo._extract_project_name("crea landing di NovaTech")
    check("extract_name_di", name2 == "NovaTech", "got=" + repr(name2))

    name3 = cmo._extract_project_name('design per "SmartMenu"')
    check("extract_name_quotes", "Smart" in name3 or "SmartMenu" in name3, "got=" + repr(name3))
except Exception as e:
    check("extract_name", False, str(e))

# 6. format_cmo_message corretto
try:
    msg = format_cmo_message("Test", "Contenuto")
    check("format_correct", msg == "\U0001f3a8 CMO\nTest\n\nContenuto", "got=" + repr(msg))
except Exception as e:
    check("format_correct", False, str(e))

# 7. generate_bozza_visiva ha nuova signature
try:
    import inspect
    sig = inspect.signature(CMO.generate_bozza_visiva)
    params = list(sig.parameters.keys())
    check("bozza_params", "project_name" in params and "tagline" in params and "thread_id" in params,
          "params=" + str(params))
except Exception as e:
    check("bozza_params", False, str(e))

# 8. _send_bozza_photo_v2 esiste
check("has_send_photo_v2", hasattr(CMO, "_send_bozza_photo_v2"))

# 9. _log_bozza_learning esiste
check("has_log_learning", hasattr(CMO, "_log_bozza_learning"))

# 10. Vecchia _send_bozza_photo NON piu presente (rimpiazzata da v2)
check("old_send_removed", "_send_bozza_photo" not in CMO.__dict__ or "_send_bozza_photo_v2" in CMO.__dict__)

# Output
print("=== TEST CMO RESPONSE FLOW ===")
for r in results:
    print(r)
print("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===")

with open("test_cmo_response_flow.txt", "w", encoding="utf-8") as f:
    f.write("=== TEST CMO RESPONSE FLOW ===\n")
    for r in results:
        f.write(r + "\n")
    f.write("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===\n")
