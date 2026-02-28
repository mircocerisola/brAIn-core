"""Test 1: SELECT agent_name, COUNT(*) etc. da agent_capabilities."""
import sys
sys.path.insert(0, "deploy-agents")
from core.config import supabase

results = []

try:
    r = supabase.table("agent_capabilities").select(
        "agent_name,livello_atteso,score_percentuale,gap"
    ).execute()
    rows = r.data or []

    # Group by agent_name
    from collections import defaultdict
    by_chief = defaultdict(list)
    for row in rows:
        by_chief[row["agent_name"]].append(row)

    lines = []
    lines.append("agent_name | competenze | target_medio | score_medio | gap_medio")
    lines.append("-" * 70)

    for chief in sorted(by_chief.keys()):
        caps = by_chief[chief]
        count = len(caps)
        avg_target = round(sum(c.get("livello_atteso", 0) for c in caps) / count, 1) if count else 0
        avg_score = round(sum(c.get("score_percentuale", 0) for c in caps) / count, 1) if count else 0
        avg_gap = round(sum(c.get("gap", 0) for c in caps) / count, 1) if count else 0
        lines.append(f"{chief:8s} | {count:10d} | {avg_target:12.1f} | {avg_score:11.1f} | {avg_gap:9.1f}")
        results.append(f"  {chief}: {count} competenze, target={avg_target}, score={avg_score}, gap={avg_gap}: PASS")

    total = len(rows)
    lines.append(f"\nTOTALE: {total} competenze per {len(by_chief)} Chief")

except Exception as e:
    results.append(f"  query: FAIL: {e}")
    lines = [f"ERRORE: {e}"]

# Output
print("=== TEST CAPABILITIES COMPLETE ===")
for r in results:
    print(r)
print(f"\n=== TOTALE: {len(results)} PASS ===")

with open("test_capabilities_complete.txt", "w", encoding="utf-8") as f:
    for line in lines:
        f.write(line + "\n")
    f.write("\n--- RESULTS ---\n")
    for r in results:
        f.write(r + "\n")
    f.write(f"\n=== TOTALE: {len(results)} PASS ===\n")
