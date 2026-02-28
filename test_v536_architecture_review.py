"""
Test v5.36 — Architecture Review + Fix Urgenti.
15 test PARTE B + 12 test architettura = 27 test totali.
"""
import sys, os, types, inspect, re

# ---- Stub dipendenze esterne ----
class _FakeResult:
    def __init__(self, data=None, count=0):
        self.data = data or []
        self.count = count

class _FakeQuery:
    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def neq(self, *a, **kw): return self
    def in_(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def lte(self, *a, **kw): return self
    def lt(self, *a, **kw): return self
    def like(self, *a, **kw): return self
    def ilike(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def insert(self, *a, **kw): return self
    def update(self, *a, **kw): return self
    def upsert(self, *a, **kw): return self
    def delete(self): return self
    def execute(self): return _FakeResult()

class _FakeSupabase:
    def table(self, name): return _FakeQuery()
    def rpc(self, name, params=None): return _FakeQuery()

class _FakeUsage:
    input_tokens = 100
    output_tokens = 50

class _FakeContent:
    def __init__(self, text="test response"):
        self.text = text

class _FakeResponse:
    def __init__(self, text="test response"):
        self.content = [_FakeContent(text)]
        self.usage = _FakeUsage()

class _FakeClaude:
    class messages:
        @staticmethod
        def create(**kwargs):
            return _FakeResponse()

class _FakeClientOptions:
    def __init__(self, **kw):
        self.postgrest_client_timeout = kw.get("postgrest_client_timeout", 10)

# Prepara sys.modules
fake_supabase_mod = types.ModuleType("supabase")
fake_supabase_mod.create_client = lambda *a, **kw: _FakeSupabase()
fake_supabase_mod.ClientOptions = _FakeClientOptions
sys.modules["supabase"] = fake_supabase_mod

fake_anthropic = types.ModuleType("anthropic")
fake_anthropic.Anthropic = lambda **kw: _FakeClaude()
sys.modules["anthropic"] = fake_anthropic

fake_dotenv = types.ModuleType("dotenv")
fake_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"] = fake_dotenv

fake_requests = types.ModuleType("requests")
fake_requests.post = lambda *a, **kw: type("R", (), {
    "status_code": 200,
    "json": lambda s: {"choices": [{"message": {"content": "ok"}}]},
    "text": "ok"
})()
fake_requests.get = lambda *a, **kw: type("R", (), {"status_code": 200, "text": "ok"})()
sys.modules["requests"] = fake_requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "deploy-agents"))

from core.config import supabase, claude
from core.base_agent import BaseAgent

passed = 0
failed = 0

def check(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")

def read_file(relpath):
    path = os.path.join(os.path.dirname(__file__), "deploy-agents", relpath)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ================================================================
# PARTE B — TEST FIX URGENTI
# ================================================================

print("=" * 60)
print("PARTE B — FIX URGENTI")
print("=" * 60)

# TEST 11: Identita' — nessun messaggio orfano senza Chief
print("\n11. Identita messaggi")
cultura_text = read_file("csuite/cultura.py")
# Verifica che ogni messaggio segue schema {icona} {NOME}
check("cultura.py ha schema formato messaggi", "{icona} {NOME}" in cultura_text)
# Nessun "brAIn:" come sender
all_code = ""
for root, dirs, files in os.walk(os.path.join(os.path.dirname(__file__), "deploy-agents")):
    for f in files:
        if f.endswith(".py") and "monolith" not in f:
            try:
                all_code += open(os.path.join(root, f), "r", encoding="utf-8").read()
            except Exception:
                pass
check("no 'brAIn:' come sender nei messaggi", "\"brAIn:\"" not in all_code and "'brAIn:'" not in all_code)

# TEST 12: Prima persona
print("\n12. Prima persona")
check("regola prima persona in cultura.py",
      "prima persona" in cultura_text.lower() or "PRIMA PERSONA" in cultura_text)
check("vietato terza persona in cultura.py",
      "Parlare di se in terza persona" in cultura_text or "terza persona" in cultura_text.lower())

# TEST 13: Errori raw — nessun traceback a Mirco
print("\n13. Errori raw")
bc_text = read_file("core/base_chief.py")
# answer_question non deve ritornare str(e) diretto
has_raw_error_return = bool(re.search(r'return f".*Errore nella risposta.*\{e\}"', bc_text))
check("answer_question non ritorna errore raw", not has_raw_error_return)
# Deve avere "Problema tecnico" come fallback
check("answer_question usa fallback leggibile", "Problema tecnico" in bc_text)

# TEST 14: "Riprova tra poco"
print("\n14. Riprova tra poco")
check("no 'Riprova tra qualche minuto' in base_chief",
      "Riprova tra qualche minuto" not in bc_text)
check("no 'riprova tra poco' nei forbidden in cultura",
      "riprova tra poco" in cultura_text)  # deve essere ELENCATO come vietato

# TEST 15: Schema coerente — projects.description
print("\n15. Schema coerente")
migration_path = os.path.join(os.path.dirname(__file__),
    "supabase", "migrations", "20260228_v536_fix_projects_description.sql")
check("migration per projects.description esiste", os.path.exists(migration_path))
cmo_text = read_file("csuite/cmo.py")
# CMO non ritorna str(e) raw
has_raw_cmo_error = "return {\"error\": str(e)}" in cmo_text
check("CMO non ritorna str(e) raw", not has_raw_cmo_error)

# TEST 1: Task — verifica che cultura ha regole conteggio task
print("\n1. Task count enforcement")
check("regola CONTA i task in cultura.py",
      "CONTA i task" in cultura_text or "ricevi N task" in cultura_text.lower())

# TEST 2: Solo DA FARE / FATTO / BLOCCATO
print("\n2. Solo DA FARE / FATTO / BLOCCATO")
check("stati task definiti in cultura",
      "DA FARE" in cultura_text and "FATTO" in cultura_text and "BLOCCATO" in cultura_text)
check("'in corso' NON e stato task valido",
      "Non esistono task" in cultura_text and "in corso" in cultura_text)

# TEST 3: Frasi vaghe vietate
print("\n3. Frasi vaghe vietate")
check("'sto cercando' vietato", "sto cercando" in cultura_text)
check("'ti aggiorno dopo' vietato", "ti aggiorno dopo" in cultura_text)
check("'risposta in elaborazione' vietato", "risposta in elaborazione" in cultura_text)

# TEST 4: TODO list presente nel COO context
print("\n4. TODO list")
coo_text = read_file("csuite/coo.py")
check("COO carica todo_list in answer_question", "todo_list" in coo_text or "TODO" in coo_text)

# TEST 5: Priorita' immutabili
print("\n5. Priorita'")
check("solo Mirco cambia priorita in cultura", "priorita" in cultura_text.lower() and "Mirco" in cultura_text)

# TEST 7: Task non inventati
print("\n7. Task non inventati")
check("vietato inventare task", "Inventare task non richiesti" in cultura_text)

# TEST 8: Interazione diretta
print("\n8. Interazione diretta")
check("regola interazione diretta Mirco",
      "Mirco ti parla direttamente" in cultura_text or "interazione diretta" in cultura_text.lower())

# TEST 9-10: Persistenza task
print("\n9-10. Persistenza task")
check("task salvati in chief_pending_tasks",
      "chief_pending_tasks" in cultura_text)


# ================================================================
# PARTE A — TEST ARCHITETTURA
# ================================================================

print("\n" + "=" * 60)
print("PARTE A — ARCHITETTURA")
print("=" * 60)

# TEST A1: Prompt caching
print("\nA1. Prompt caching")
agent = BaseAgent()
agent.name = "test_agent"
_captured = {}
_orig = claude.messages.create
def _capture(**kwargs):
    _captured.update(kwargs)
    return _FakeResponse()
claude.messages.create = _capture
agent.call_claude("test", system="System prompt")
sys_val = _captured.get("system")
check("system e lista con cache_control",
      isinstance(sys_val, list) and len(sys_val) == 1
      and sys_val[0].get("cache_control", {}).get("type") == "ephemeral")

# TEST A2: Temperature
print("\nA2. Temperature")
_captured.clear()
agent.call_claude("test", system="sys", temperature=0.7)
check("temperature propagata", _captured.get("temperature") == 0.7)
claude.messages.create = _orig

# TEST A3: COO parallel
print("\nA3. COO parallel")
try:
    from csuite.coo import COO
    src = inspect.getsource(COO.get_domain_context)
    check("ThreadPoolExecutor in COO", "ThreadPoolExecutor" in src)
except Exception as e:
    check(f"COO: {e}", False)

# TEST A4: Memory batch
print("\nA4. Memory batch")
mem_text = read_file("intelligence/memory.py")
check("no SELECT access_count loop", 'select("access_count")' not in mem_text)
check("usa RPC increment_episode_access", "increment_episode_access" in mem_text)

# TEST A5: Routing unificato
print("\nA5. Routing unificato")
check("ROUTING_KEYWORDS dict rimosso",
      not re.search(r'^ROUTING_KEYWORDS\s*[:=]\s*\{', bc_text, re.MULTILINE))

# TEST A6: Model selection keyword
print("\nA6. Model selection")
try:
    from core.base_chief import BaseChief
    src = inspect.getsource(BaseChief._select_model)
    check("_select_model non chiama call_claude", "call_claude" not in src)
    check("usa _FULL_KEYWORDS", "_FULL_KEYWORDS" in src)
except Exception as e:
    check(f"model: {e}", False)
    check(f"model: {e}", False)

# TEST A7: Circuit breaker jitter
print("\nA7. Circuit breaker")
ba_src = inspect.getsource(BaseAgent.call_claude)
check("jitter nel retry", "random.uniform" in ba_src)

# TEST A8: Health check DB
print("\nA8. Health check")
ep_text = read_file("core/endpoints.py")
check("health verifica DB", "org_config" in ep_text and "503" in ep_text)

# TEST A9: Perplexity logging
print("\nA9. Perplexity logging")
utils_text = read_file("core/utils.py")
check("search_perplexity logga agent_logs", "perplexity_search" in utils_text)

# TEST A10: COO forbidden unificato
print("\nA10. COO forbidden check")
aq_src = inspect.getsource(COO.answer_question)
forbidden_section = aq_src[aq_src.find("forbidden"):]
super_calls = forbidden_section.count("super().answer_question")
check("max 1 rigenerazione forbidden", super_calls <= 1)

# TEST A11: Chief personality
print("\nA11. Personality")
try:
    from csuite.cultura import CHIEF_PERSONALITY
    check("CHIEF_PERSONALITY con 7 Chief", len(CHIEF_PERSONALITY) == 7)
except Exception as e:
    check(f"personality: {e}", False)

# TEST A12: Temperature per Chief
print("\nA12. Temperature per Chief")
try:
    from csuite.cfo import CFO
    from csuite.clo import CLO
    from csuite.cso import CSO
    from csuite.cmo import CMO
    from csuite.cto import CTO
    from csuite.cpeo import CPeO
    all_set = all(getattr(c, "default_temperature", None) is not None
                  for c in [COO, CSO, CFO, CMO, CTO, CLO, CPeO])
    check("tutti i 7 Chief hanno temperature", all_set)
except Exception as e:
    check(f"temp: {e}", False)


# ================================================================
# RIEPILOGO
# ================================================================
print(f"\n{'=' * 60}")
print(f"RISULTATO: {passed}/{passed + failed} PASS")
if failed:
    print(f"FALLITI: {failed}")
    sys.exit(1)
else:
    print("TUTTI I TEST PASSATI")
    sys.exit(0)
