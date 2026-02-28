"""Test 5: verifica agent_capabilities ha le colonne richieste."""
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

import psycopg2

try:
    conn = psycopg2.connect(
        host="db.rcwawecswjzpnycuirpx.supabase.co",
        port=5432, dbname="postgres", user="postgres",
        password="Dm2C4ILZ4oNXsq"
    )
    cur = conn.cursor()

    # Check columns
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name='agent_capabilities'
        ORDER BY ordinal_position
    """)
    columns = {row[0]: row[1] for row in cur.fetchall()}

    required_columns = {
        "livello_atteso": "integer",
        "score_percentuale": "integer",
        "gap": "integer",
        "descrizione": "text",
        "comportamenti_attesi": "text",
        "competenza": "text",
        "categoria": "text",
        "fonte": "text",
    }

    for col, expected_type in required_columns.items():
        exists = col in columns
        correct_type = columns.get(col) == expected_type if exists else False
        check("col_" + col, exists, "mancante")
        if exists:
            check("type_" + col, correct_type,
                  "expected=" + expected_type + " got=" + str(columns.get(col)))

    # Verify gap is GENERATED
    cur.execute("""
        SELECT generation_expression
        FROM information_schema.columns
        WHERE table_name='agent_capabilities' AND column_name='gap'
    """)
    gen = cur.fetchone()
    check("gap_is_generated", gen is not None and gen[0] is not None,
          "generation=" + str(gen))

    # Check unique index
    cur.execute("""
        SELECT indexname FROM pg_indexes
        WHERE tablename='agent_capabilities'
        AND indexname='agent_capabilities_name_competenza_idx'
    """)
    idx = cur.fetchone()
    check("unique_index_exists", idx is not None)

    # Check capability_log has created_at
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='capability_log' AND column_name='created_at'
    """)
    cap_created = cur.fetchone()
    check("capability_log_created_at", cap_created is not None)

    # Check agent_performance table exists
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_name='agent_performance'
    """)
    perf_table = cur.fetchone()
    check("agent_performance_table", perf_table is not None)

    conn.close()

except Exception as e:
    check("db_connection", False, str(e))

# Output
print("=== TEST SCHEMA CAPABILITIES ===")
for r in results:
    print(r)
print("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===")

with open("test_schema_capabilities.txt", "w", encoding="utf-8") as f:
    f.write("=== TEST SCHEMA CAPABILITIES ===\n")
    for r in results:
        f.write(r + "\n")
    f.write("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===\n")
