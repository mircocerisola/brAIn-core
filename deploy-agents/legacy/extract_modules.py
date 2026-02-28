"""
Script di estrazione moduli — legge agents_runner.py e scrive i moduli.
Eseguire da deploy-agents/: py -3 extract_modules.py
"""
import re, os

src = open("agents_runner.py", encoding="utf-8").read()
lines = src.split("\n")

def extract_lines(start_line, end_line):
    """Estrae le righe (1-indexed, end_line esclusa)."""
    return "\n".join(lines[start_line-1:end_line-1])

def find_func(name):
    """Trova la riga (1-indexed) di def <name>."""
    pattern = re.compile(rf"^(?:async )?def {re.escape(name)}\b")
    for i, line in enumerate(lines, 1):
        if pattern.match(line):
            return i
    raise ValueError(f"Function not found: {name}")

def next_func_line(after_line, skip_names=None):
    """
    Trova la prossima definizione di funzione top-level dopo after_line.
    skip_names: set of function names to skip (nested defs).
    """
    pattern = re.compile(r"^(?:async )?def \w+")
    for i in range(after_line, len(lines)):
        if pattern.match(lines[i]):
            return i + 1  # 1-indexed
    return len(lines) + 1

def extract_section(first_func, last_func):
    """Estrae dalla def del primo func fino all'inizio del func successivo all'ultimo."""
    start = find_func(first_func)
    # Trova fine: la prossima top-level def dopo last_func
    last_start = find_func(last_func)
    end = next_func_line(last_start)
    return extract_lines(start, end)

HEADER_CORE = """\
from __future__ import annotations
import os, json, time, hashlib, math, re
from datetime import datetime, timezone, timedelta
from calendar import monthrange
import anthropic
from supabase import create_client
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, COMMAND_CENTER_URL, PERPLEXITY_API_KEY, logger, _state
"""

HEADER_INTELLIGENCE = """\
from __future__ import annotations
import json, time, re, hashlib
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import (log_to_supabase, notify_telegram, extract_json, search_perplexity,
                        get_telegram_chat_id, emit_event,
                        get_mirco_preferences, get_sector_preference_modifier,
                        get_pipeline_thresholds, get_scan_strategy, get_scan_schedule_strategy,
                        get_sector_with_fewest_problems, get_last_sector_rotation,
                        get_high_bos_problem_sectors, build_strategy_queries)
"""

HEADER_FINANCE = """\
from __future__ import annotations
import json, time, re, math
from datetime import datetime, timezone, timedelta
from calendar import monthrange
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, COMMAND_CENTER_URL, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id
"""

HEADER_MEMORY = """\
from __future__ import annotations
import json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, search_perplexity
"""

HEADER_EXECUTION = """\
from __future__ import annotations
import os, json, time, re, uuid
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, SUPABASE_ACCESS_TOKEN, DB_PASSWORD, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json
"""

HEADER_MARKETING = """\
from __future__ import annotations
import json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json
"""

# === ESTRAI SEZIONI ===
modules = {}

# 1. core/utils.py (44 → prima di scanner_make_fingerprint 504)
s_utils = find_func("get_telegram_chat_id")
e_utils = find_func("scanner_make_fingerprint")
modules["core/utils.py"] = (HEADER_CORE, extract_lines(s_utils, e_utils))

# 2. intelligence/scanner.py (504 → prima di research_problem 1037)
s_scan = find_func("scanner_make_fingerprint")
e_scan = find_func("research_problem")
modules["intelligence/scanner.py"] = (HEADER_INTELLIGENCE, extract_lines(s_scan, e_scan))

# 3. intelligence/architect.py (1037 → prima di feasibility_calculate_score 1391)
s_arch = find_func("research_problem")
e_arch = find_func("feasibility_calculate_score")
modules["intelligence/architect.py"] = (HEADER_INTELLIGENCE, extract_lines(s_arch, e_arch))

# 4. intelligence/feasibility.py (1391 → prima di run_knowledge_keeper 2063)
s_feas = find_func("feasibility_calculate_score")
e_feas = find_func("run_knowledge_keeper")
modules["intelligence/feasibility.py"] = (HEADER_INTELLIGENCE + "from intelligence.architect import run_solution_architect\n", extract_lines(s_feas, e_feas))

# 5. memory/knowledge.py (2063 → prima di run_capability_scout 2153)
s_know = find_func("run_knowledge_keeper")
e_know = find_func("run_capability_scout")
modules["memory/knowledge.py"] = (HEADER_MEMORY, extract_lines(s_know, e_know))

# 6. memory/scout.py (2153 → prima di finance_get_usd_to_eur 2236)
s_scout = find_func("run_capability_scout")
e_scout = find_func("finance_get_usd_to_eur")
modules["memory/scout.py"] = (HEADER_MEMORY, extract_lines(s_scout, e_scout))

# 7. finance/finance.py (2236 → prima di _get_rome_tz 3144)
s_fin = find_func("finance_get_usd_to_eur")
e_fin = find_func("_get_rome_tz")
modules["finance/finance.py"] = (HEADER_FINANCE, extract_lines(s_fin, e_fin))

# 8. finance/reports.py (3144 → prima di update_kpi_daily 3384)
s_rep = find_func("_get_rome_tz")
e_rep = find_func("update_kpi_daily")
modules["finance/reports.py"] = (HEADER_FINANCE, extract_lines(s_rep, e_rep))

# 9. memory/kpi.py (3384 → prima di process_events 3437)
s_kpi = find_func("update_kpi_daily")
e_kpi = find_func("process_events")
modules["memory/kpi.py"] = (HEADER_MEMORY, extract_lines(s_kpi, e_kpi))

# 10. intelligence/pipeline.py (3437 → prima di run_action_queue_cleanup 3560)
s_pipe = find_func("process_events")
e_pipe = find_func("run_action_queue_cleanup")
modules["intelligence/pipeline.py"] = (
    HEADER_INTELLIGENCE + "from intelligence.architect import run_solution_architect\nfrom intelligence.feasibility import run_feasibility_engine, run_bos_endpoint_logic, enqueue_bos_action\n",
    extract_lines(s_pipe, e_pipe)
)

# 11. memory/thresholds.py (3560 → prima di run_idea_recycler 3695)
s_thr = find_func("run_action_queue_cleanup")
e_thr = find_func("run_idea_recycler")
modules["memory/thresholds.py"] = (HEADER_MEMORY, extract_lines(s_thr, e_thr))

# 12. memory/recycler.py (3695 → prima di run_targeted_scan 3746)
s_rec = find_func("run_idea_recycler")
e_rec = find_func("run_targeted_scan")
modules["memory/recycler.py"] = (HEADER_MEMORY, extract_lines(s_rec, e_rec))

# 13. memory/sources.py (3746 → prima di _github_project_api 3977)
s_src = find_func("run_targeted_scan")
e_src = find_func("_github_project_api")
modules["memory/sources.py"] = (HEADER_MEMORY, extract_lines(s_src, e_src))

# 14. execution/project.py (3977 → prima di run_spec_generator 4337)
s_proj = find_func("_github_project_api")
e_proj = find_func("run_spec_generator")
modules["execution/project.py"] = (HEADER_EXECUTION, extract_lines(s_proj, e_proj))

# 15. execution/builder.py (4337 → prima di run_legal_review 5153)
s_bld = find_func("run_spec_generator")
e_bld = find_func("run_legal_review")
modules["execution/builder.py"] = (
    HEADER_EXECUTION + "from execution.project import (_github_project_api, _commit_to_project_repo,\n    _get_telegram_group_id, _create_forum_topic, _send_to_topic, _slugify,\n    _create_supabase_project, get_project_db)\n",
    extract_lines(s_bld, e_bld)
)

# 16. execution/legal.py (5153 → prima di run_smoke_test_setup 5408)
s_leg = find_func("run_legal_review")
e_leg = find_func("run_smoke_test_setup")
modules["execution/legal.py"] = (
    HEADER_EXECUTION + "from execution.project import get_project_db, _send_to_topic\n",
    extract_lines(s_leg, e_leg)
)

# 17. execution/smoke.py (5408 → prima di _mkt_card 5712)
s_smk = find_func("run_smoke_test_setup")
e_smk = find_func("_mkt_card")
modules["execution/smoke.py"] = (
    HEADER_EXECUTION + "from execution.project import get_project_db, _send_to_topic\n",
    extract_lines(s_smk, e_smk)
)

# 18. marketing/* — find actual start of run_validation_agent to split marketing from execution tail
s_mkt = find_func("_mkt_card")
e_mkt = find_func("run_validation_agent")
modules["marketing/agents.py"] = (HEADER_MARKETING, extract_lines(s_mkt, e_mkt))

# 19. execution/validator.py — validation + continue build + invite link + spec update
s_val = find_func("run_validation_agent")
# Find end: first async def (endpoint section)
e_val = find_func("health_check")
modules["execution/validator.py"] = (
    HEADER_EXECUTION + "from execution.project import get_project_db, _send_to_topic, _commit_to_project_repo\n",
    extract_lines(s_val, e_val)
)

# Write module files
for path, (header, body) in modules.items():
    full_path = path
    os.makedirs(os.path.dirname(full_path) if os.path.dirname(full_path) else ".", exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(f'"""\nbrAIn module: {path}\nAuto-extracted from agents_runner.py\n"""\n')
        f.write(header)
        f.write("\n\n")
        f.write(body)
        f.write("\n")
    print(f"Written: {full_path} ({body.count(chr(10))} lines)")

print("\nDone! Check syntax with: py -3 -m py_compile <file>")
