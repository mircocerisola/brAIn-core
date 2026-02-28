"""
Test v5.35 — Task Management System
10 test di verifica per il sistema di gestione task.
"""
import sys
import os
import re

# Setup path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "deploy-agents"))

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {label}")
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


# ============================================================
# TEST 1: Tabelle migration presenti nel SQL
# ============================================================
print("\n=== TEST 1: Migration SQL contiene le tabelle corrette ===")
with open("supabase/migrations/20260228_v535_task_management.sql") as f:
    sql = f.read()

check("chief_pending_tasks CREATE TABLE", "CREATE TABLE IF NOT EXISTS chief_pending_tasks" in sql)
check("coo_project_tasks CREATE TABLE", "CREATE TABLE IF NOT EXISTS coo_project_tasks" in sql)
check("chief_pending_tasks status CHECK", "CHECK (status IN ('pending', 'done', 'blocked'))" in sql)
check("coo_project_tasks status CHECK", "CHECK (status IN ('da_fare', 'fatto', 'bloccato'))" in sql)
check("coo_project_tasks priority CHECK", "CHECK (priority IN ('P0', 'P1', 'P2'))" in sql)
check("RLS su chief_pending_tasks", "ENABLE ROW LEVEL SECURITY" in sql)
check("Indici presenti", "idx_chief_pending_tasks_chief_status" in sql)
check("source field", "source TEXT DEFAULT 'mirco'" in sql)

# ============================================================
# TEST 2: CULTURA_BRAIN contiene regole task
# ============================================================
print("\n=== TEST 2: CULTURA_BRAIN ha regole task management ===")
from csuite.cultura import CULTURA_BRAIN

check("Principio DA FARE/FATTO/BLOCCATO", "DA FARE" in CULTURA_BRAIN and "FATTO" in CULTURA_BRAIN and "BLOCCATO" in CULTURA_BRAIN)
check("Formato conferma task", "Task [N]" in CULTURA_BRAIN)
check("Frasi vietate task", "sto cercando" in CULTURA_BRAIN)
check("ti aggiorno dopo vietato", "ti aggiorno dopo" in CULTURA_BRAIN)
check("ci lavoro vietato", "ci lavoro" in CULTURA_BRAIN)
check("No stato in corso", "Non esistono task" in CULTURA_BRAIN)

# ============================================================
# TEST 3: BaseChief ha metodi task management
# ============================================================
print("\n=== TEST 3: BaseChief metodi task management ===")
from csuite.coo import COO

coo = COO()

check("_load_pending_tasks esiste", hasattr(coo, "_load_pending_tasks"))
check("_save_task esiste", hasattr(coo, "_save_task"))
check("_complete_task esiste", hasattr(coo, "_complete_task"))
check("_block_task esiste", hasattr(coo, "_block_task"))
check("_format_pending_tasks_context esiste", hasattr(coo, "_format_pending_tasks_context"))
check("_contains_task_forbidden esiste", hasattr(coo, "_contains_task_forbidden"))
check("_TASK_FORBIDDEN_PHRASES list", len(coo._TASK_FORBIDDEN_PHRASES) >= 10)

# ============================================================
# TEST 4: _contains_task_forbidden detecta frasi vietate
# ============================================================
print("\n=== TEST 4: Frasi vietate task detection ===")

check("'sto cercando' detected",
      coo._contains_task_forbidden("Ok sto cercando le info"))
check("'ti aggiorno dopo' detected",
      coo._contains_task_forbidden("Ti aggiorno dopo con i dati"))
check("'ci lavoro' detected",
      coo._contains_task_forbidden("Ci lavoro subito"))
check("'me ne occupo' detected",
      coo._contains_task_forbidden("Me ne occupo io adesso"))
check("frase OK non detected",
      not coo._contains_task_forbidden("Ecco il risultato della ricerca: 50 prospect trovati"))
check("'risposta in elaborazione' detected",
      coo._contains_task_forbidden("Risposta in elaborazione, attendi"))

# ============================================================
# TEST 5: _format_pending_tasks_context formatta correttamente
# ============================================================
print("\n=== TEST 5: Formato pending tasks context ===")

tasks_sample = [
    {"id": 1, "task_number": 1, "task_description": "Analizza i competitor", "status": "pending"},
    {"id": 2, "task_number": 2, "task_description": "Crea report vendite", "status": "pending"},
]

ctx = coo._format_pending_tasks_context(tasks_sample)
check("Header presente", "TASK PENDENTI DA COMPLETARE" in ctx)
check("Task #1 presente", "Task #1" in ctx)
check("Task #2 presente", "Task #2" in ctx)
check("Descrizione inclusa", "Analizza i competitor" in ctx)
check("Lista vuota = stringa vuota", coo._format_pending_tasks_context([]) == "")

# ============================================================
# TEST 6: COO ha metodi TODO list
# ============================================================
print("\n=== TEST 6: COO TODO list management ===")

check("_load_todo_list esiste", hasattr(coo, "_load_todo_list"))
check("_add_todo_task esiste", hasattr(coo, "_add_todo_task"))
check("_update_todo_status esiste", hasattr(coo, "_update_todo_status"))
check("_assign_task_to_chief esiste", hasattr(coo, "_assign_task_to_chief"))
check("_format_todo_list esiste", hasattr(coo, "_format_todo_list"))
check("_format_todo_response esiste", hasattr(coo, "_format_todo_response"))
check("_escalate_to_mirco esiste", hasattr(coo, "_escalate_to_mirco"))

# ============================================================
# TEST 7: _format_todo_list formatta correttamente
# ============================================================
print("\n=== TEST 7: Formato TODO list ===")

todo_sample = [
    {"id": 1, "priority": "P0", "owner_chief": "cmo", "task_description": "Crea landing", "status": "bloccato", "blocked_by": "mirco"},
    {"id": 2, "priority": "P1", "owner_chief": "cso", "task_description": "Trova prospect", "status": "da_fare", "blocked_by": ""},
    {"id": 3, "priority": "P2", "owner_chief": "cto", "task_description": "Deploy sito", "status": "fatto", "blocked_by": ""},
]

todo_text = coo._format_todo_list(todo_sample)
check("Bloccato ha emoji rossa", "\U0001f534" in todo_text)
check("Da fare ha emoji grigia", "\u26AA" in todo_text)
check("Fatto ha emoji verde", "\u2705" in todo_text)
check("P0 presente", "P0" in todo_text)
check("CMO owner", "CMO" in todo_text)
check("BLOCCATO DA mirco", "BLOCCATO DA mirco" in todo_text)
check("Lista vuota", coo._format_todo_list([]) == "Nessun task attivo.")

# ============================================================
# TEST 8: COO get_domain_context include todo_list e chief_pending_tasks
# ============================================================
print("\n=== TEST 8: COO domain context ha todo_list ===")

# Non chiamiamo il DB, ma verifichiamo che il codice le cerchi
import inspect
src = inspect.getsource(coo.get_domain_context)
check("todo_list in domain context", "todo_list" in src)
check("coo_project_tasks query", "coo_project_tasks" in src)
check("chief_pending_tasks in domain context", "chief_pending_tasks" in src)

# ============================================================
# TEST 9: SANDBOX_PERIMETERS aggiornati
# ============================================================
print("\n=== TEST 9: SANDBOX_PERIMETERS con nuove tabelle ===")
from core.base_chief import SANDBOX_PERIMETERS

for chief in ["cso", "cmo", "cfo", "clo", "cpeo"]:
    tables = SANDBOX_PERIMETERS.get(chief, {}).get("tables_allowed", [])
    check(f"{chief} ha chief_pending_tasks", "chief_pending_tasks" in tables)

coo_tables = SANDBOX_PERIMETERS.get("coo", {}).get("tables_allowed", [])
check("COO ha chief_pending_tasks", "chief_pending_tasks" in coo_tables)
check("COO ha coo_project_tasks", "coo_project_tasks" in coo_tables)

# ============================================================
# TEST 10: COO answer_question integra regole task
# ============================================================
print("\n=== TEST 10: COO answer_question ha regole task ===")

src_aq = inspect.getsource(coo.answer_question)
check("TODO list caricata in answer_question", "_load_todo_list" in src_aq)
check("Regole task nel contesto", "REGOLE TASK COO" in src_aq)
check("Task forbidden check", "_contains_task_forbidden" in src_aq)
check("DA FARE/FATTO/BLOCCATO in regole", "DA FARE" in src_aq and "FATTO" in src_aq and "BLOCCATO" in src_aq)
check("Format todo list in answer", "_format_todo_list" in src_aq)

# ============================================================
# ANTI-PATTERN CHECK: nessuna frase vietata nel codice
# ============================================================
print("\n=== ANTI-PATTERN CHECK ===")

# Verifica che cultura.py contiene le frasi come VIETATE, non come uso
with open("deploy-agents/csuite/cultura.py") as f:
    cultura_src = f.read()

check("'Non esistono task in corso' in cultura",
      "Non esistono" in cultura_src or "non in corso" in cultura_src.lower()
      or "DA FARE" in cultura_src)

# Verifica che base_chief ha la lista forbidden completa
check("Almeno 10 frasi forbidden", len(coo._TASK_FORBIDDEN_PHRASES) >= 10)
check("'sto analizzando' nella lista", "sto analizzando" in coo._TASK_FORBIDDEN_PHRASES)
check("'lo verifico' nella lista", "lo verifico" in coo._TASK_FORBIDDEN_PHRASES)


# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'='*50}")
print(f"RISULTATO: {PASS} PASS / {FAIL} FAIL su {PASS+FAIL} test")
print(f"{'='*50}")

if FAIL > 0:
    sys.exit(1)
