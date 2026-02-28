"""Test 4: daily_gap_analysis() — verifica gap_topics mai vuoto (usa top 3 competenze)."""
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

# Chiama daily_gap_analysis
try:
    from csuite.cpeo import daily_gap_analysis
    result = daily_gap_analysis()
    check("call_ok", result.get("status") == "ok", str(result))
except Exception as e:
    check("call_ok", False, str(e))
    result = {}

results_list = result.get("results", [])
check("has_results", len(results_list) == 7, "count=" + str(len(results_list)))

# Per ogni Chief verifica gap_topics non vuoto
for r in results_list:
    chief_id = r.get("chief_id", "?")
    gap_topics = r.get("gap_topics", [])
    gap_score = r.get("gap_score", 0)

    check(chief_id + "_has_topics", len(gap_topics) > 0,
          "gap_topics=" + str(gap_topics))
    check(chief_id + "_topics_are_competenze", all("_" in t for t in gap_topics),
          "topics=" + str(gap_topics))
    check(chief_id + "_gap_score_positive", gap_score > 0,
          "gap_score=" + str(gap_score))

# Verifica gap_analysis_log ha entries con gap_topics non vuoto
from core.config import supabase
import json
try:
    r = supabase.table("gap_analysis_log").select("chief_name,gap_score,gap_topics") \
        .order("created_at", desc=True).limit(7).execute()
    for row in (r.data or []):
        chief = row.get("chief_name", "?")
        topics = row.get("gap_topics", "[]")
        if isinstance(topics, str):
            topics = json.loads(topics)
        check("db_" + chief + "_topics_not_empty", len(topics) > 0,
              "topics=" + str(topics))
except Exception as e:
    check("db_check", False, str(e))

# Output
print("=== TEST GAP TOPICS REALI ===")
for r in results:
    print(r)
print("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===")

with open("test_gap_topics_reali.txt", "w", encoding="utf-8") as f:
    f.write("=== TEST GAP TOPICS REALI ===\n")
    for r in results:
        f.write(r + "\n")
    f.write("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===\n")
