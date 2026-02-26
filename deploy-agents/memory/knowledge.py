"""
brAIn module: memory/knowledge.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, search_perplexity, extract_json, emit_event


KNOWLEDGE_PROMPT = """Sei il Knowledge Keeper di brAIn.
Analizza i log degli agenti e estrai lezioni apprese.

LINGUA: Rispondi SEMPRE in italiano. Titoli e contenuti delle lezioni in italiano.

Rispondi SOLO con JSON:
{"lessons":[{"title":"titolo","content":"descrizione","category":"process","actionable":"azione"}],"patterns":[{"pattern":"descrizione","frequency":"quanto"}],"summary":"riassunto breve"}

Categorie: process, technical, strategic, cost, performance.
SOLO JSON."""


def run_knowledge_keeper():
    logger.info("Knowledge Keeper v1.1 starting...")

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        logs = supabase.table("agent_logs").select("*").gte("created_at", since).order("created_at", desc=True).limit(50).execute()
        logs = logs.data or []
    except:
        logs = []

    if not logs:
        return {"status": "no_logs", "saved": 0}

    simple_logs = [{
        "agent": l.get("agent_id"), "action": l.get("action"),
        "status": l.get("status"), "model": l.get("model_used"),
        "tokens_in": l.get("tokens_input"), "tokens_out": l.get("tokens_output"),
        "cost": l.get("cost_usd"), "duration_ms": l.get("duration_ms"),
        "error": l.get("error"), "time": l.get("created_at"),
    } for l in logs]

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=KNOWLEDGE_PROMPT,
            messages=[{"role": "user", "content": f"Analizza SOLO JSON:\n\n{json.dumps(simple_logs, default=str)}"}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("knowledge_keeper", "analyze_logs", 5,
            f"Analizzati {len(logs)} log", reply[:500],
            "claude-haiku-4-5-20251001",
            response.usage.input_tokens, response.usage.output_tokens,
            (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            duration)

        data = extract_json(reply)
        saved = 0
        if data:
            for lesson in data.get("lessons", []):
                try:
                    supabase.table("org_knowledge").insert({
                        "title": lesson.get("title", ""),
                        "content": lesson.get("content", ""),
                        "category": lesson.get("category", "general"),
                        "source": "knowledge_keeper_v1",
                    }).execute()
                    saved += 1
                except:
                    pass

            for p in data.get("patterns", []):
                if "error" in p.get("pattern", "").lower() or "fail" in p.get("pattern", "").lower():
                    emit_event("knowledge_keeper", "error_pattern_detected", None,
                        {"pattern": p["pattern"]}, "high")
                    notify_telegram(f"Pattern di errore rilevato: {p['pattern']}")

        return {"status": "completed", "saved": saved}

    except Exception as e:
        logger.error(f"[KK ERROR] {e}")
        return {"status": "error", "error": str(e)}


# ============================================================
# CAPABILITY SCOUT v1.1
# ============================================================

SCOUT_TOPICS = [
    "new AI agent frameworks tools 2025 2026",
    "Claude API new features updates 2026",
    "best no-code AI automation tools 2026",
    "Supabase new features updates 2026",
    "open source AI tools for startups 2026",
]

SCOUT_PROMPT = """Sei il Capability Scout di brAIn.
brAIn usa: Claude API (Haiku/Sonnet), Supabase, Python, Google Cloud Run, Telegram Bot.
Budget: 1000 euro/mese. Preferenza: no-code o low-code.

Seleziona SOLO le 3-5 scoperte piu rilevanti.

Rispondi SOLO con JSON:
{"discoveries":[{"tool_name":"nome","category":"ai_model","description":"cosa fa","potential_impact":"come aiuta brAIn","cost":"stima","relevance":"high","action":"evaluate"}],"summary":"riassunto"}
SOLO JSON."""


