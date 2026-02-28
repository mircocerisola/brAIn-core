"""Test 3: post_task_learning — verifica score_percentuale update +5."""
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

from core.config import supabase

# Leggi score attuale di cicd_deployment_automation per CTO
old_score = None
try:
    r = supabase.table("agent_capabilities").select("score_percentuale") \
        .eq("agent_name", "cto").eq("competenza", "cicd_deployment_automation").execute()
    if r.data:
        old_score = r.data[0]["score_percentuale"]
        check("read_old_score", True, "score=" + str(old_score))
    else:
        check("read_old_score", False, "competenza non trovata")
except Exception as e:
    check("read_old_score", False, str(e))

# Chiama post_task_learning
try:
    from csuite.cpeo import post_task_learning
    result = post_task_learning("cto", "deploy cloud run", "success", "cicd_deployment_automation", True)
    check("call_ok", result.get("status") == "ok", str(result.get("error", "")))
    check("has_delta", result.get("delta") == 5, "delta=" + str(result.get("delta")))
    check("has_old_score", result.get("old_score") == old_score,
          "expected=" + str(old_score) + " got=" + str(result.get("old_score")))
    expected_new = min(100, (old_score or 50) + 5)
    check("has_new_score", result.get("new_score") == expected_new,
          "expected=" + str(expected_new) + " got=" + str(result.get("new_score")))
    check("has_lesson", len(result.get("lesson", "")) > 5,
          "lesson_len=" + str(len(result.get("lesson", ""))))
except Exception as e:
    check("call_ok", False, str(e))

# Verifica che score_percentuale sia effettivamente cambiato in DB
try:
    r = supabase.table("agent_capabilities").select("score_percentuale,gap") \
        .eq("agent_name", "cto").eq("competenza", "cicd_deployment_automation").execute()
    if r.data:
        new_score_db = r.data[0]["score_percentuale"]
        new_gap_db = r.data[0]["gap"]
        check("db_score_updated", new_score_db == expected_new,
              "db=" + str(new_score_db) + " expected=" + str(expected_new))
        check("db_gap_recalculated", new_gap_db is not None,
              "gap=" + str(new_gap_db))
    else:
        check("db_score_updated", False, "row not found")
except Exception as e:
    check("db_score_updated", False, str(e))

# Verifica org_knowledge entry
try:
    r = supabase.table("org_knowledge").select("id,title") \
        .eq("source", "post_task_learning") \
        .like("title", "%CTO%cicd_deployment_automation%") \
        .order("created_at", desc=True).limit(1).execute()
    check("org_knowledge_entry", len(r.data or []) > 0)
except Exception as e:
    check("org_knowledge_entry", False, str(e))

# Ripristina score originale
if old_score is not None:
    try:
        supabase.table("agent_capabilities").update({
            "score_percentuale": old_score,
        }).eq("agent_name", "cto").eq("competenza", "cicd_deployment_automation").execute()
        check("restore_score", True)
    except Exception as e:
        check("restore_score", False, str(e))

# Output
print("=== TEST POST TASK LEARNING ===")
for r in results:
    print(r)
print("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===")

with open("test_post_task_learning.txt", "w", encoding="utf-8") as f:
    f.write("=== TEST POST TASK LEARNING ===\n")
    for r in results:
        f.write(r + "\n")
    f.write("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===\n")
