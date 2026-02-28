"""Test 2: compute_chief_gap_profile('cmo') — verifica top 3 gap piu alti."""
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

try:
    from csuite.cpeo import compute_chief_gap_profile
    check("import", True)
except Exception as e:
    check("import", False, str(e))

# Chiama compute_chief_gap_profile per CMO
try:
    profile = compute_chief_gap_profile("cmo")
    check("call_ok", profile.get("status") == "ok", str(profile.get("error", "")))
    check("has_gap_score", "gap_score_globale" in profile)
    check("has_top3", "top3" in profile and len(profile.get("top3", [])) == 3)
    check("has_competenze", profile.get("competenze_totali", 0) > 0,
          "competenze=" + str(profile.get("competenze_totali", 0)))

    # Verifica che top 3 siano le competenze con gap piu alto
    top3 = profile.get("top3", [])
    if top3:
        gaps = [t.get("gap", 0) for t in top3]
        check("top3_ordered", gaps == sorted(gaps, reverse=True),
              "gaps=" + str(gaps))
        check("top3_have_competenza", all("competenza" in t for t in top3))
        check("top3_have_gap", all("gap" in t for t in top3))
        check("top3_have_categoria", all("categoria" in t for t in top3))

        # Tutti i gap dovrebbero essere > 0 (score_percentuale default 50, livello_atteso >= 75)
        check("gap_positive", all(g > 0 for g in gaps), "gaps=" + str(gaps))
    else:
        check("top3_ordered", False, "top3 vuoto")

    # gap_score_globale dovrebbe essere > 0
    check("gap_score_positive", profile.get("gap_score_globale", 0) > 0,
          "gap_score=" + str(profile.get("gap_score_globale", 0)))

except Exception as e:
    check("call_ok", False, str(e))

# Output
print("=== TEST GAP PROFILE CMO ===")
for r in results:
    print(r)
print("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===")

with open("test_gap_profile_cmo.txt", "w", encoding="utf-8") as f:
    f.write("=== TEST GAP PROFILE CMO ===\n")
    for r in results:
        f.write(r + "\n")
    if 'profile' in dir():
        f.write("\n--- PROFILE DATA ---\n")
        import json
        f.write(json.dumps(profile, indent=2, default=str) + "\n")
    f.write("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===\n")
