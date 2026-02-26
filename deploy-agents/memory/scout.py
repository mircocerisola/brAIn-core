"""
brAIn module: memory/scout.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, search_perplexity, extract_json, emit_event
from memory.knowledge import SCOUT_TOPICS, SCOUT_PROMPT


def run_capability_scout():
    logger.info("Capability Scout v1.1 starting...")

    search_results = []
    for topic in SCOUT_TOPICS:
        result = search_perplexity(topic)
        if result:
            search_results.append((topic, result))
        time.sleep(1)

    if not search_results:
        return {"status": "no_results", "saved": 0}

    combined = "\n\n---\n\n".join([f"Topic: {t}\nResults: {r}" for t, r in search_results])

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=SCOUT_PROMPT,
            messages=[{"role": "user", "content": f"Analizza SOLO JSON:\n\n{combined}"}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        log_to_supabase("capability_scout", "analyze_discoveries", 5,
            f"Analizzati {len(search_results)} topic", reply[:500],
            "claude-haiku-4-5-20251001",
            response.usage.input_tokens, response.usage.output_tokens,
            (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            duration)

        data = extract_json(reply)
        saved = 0
        if data:
            for disc in data.get("discoveries", []):
                if disc.get("relevance") in ("high", "medium"):
                    try:
                        status = "evaluating" if disc.get("action") in ("adopt", "evaluate") else "discovered"
                        supabase.table("capability_log").insert({
                            "tool_name": disc.get("tool_name", ""),
                            "category": disc.get("category", "other"),
                            "description": disc.get("description", ""),
                            "potential_impact": disc.get("potential_impact", ""),
                            "cost": disc.get("cost", "unknown"),
                            "status": status,
                        }).execute()
                        saved += 1

                        if disc.get("relevance") == "high" and disc.get("action") == "adopt":
                            emit_event("capability_scout", "high_impact_tool", None,
                                {"tool": disc["tool_name"], "impact": disc.get("potential_impact", "")}, "high")
                            notify_telegram(f"Tool consigliato: {disc['tool_name']}\n{disc.get('potential_impact', '')}")

                        # Task 5: notifica i Chief rilevanti della nuova capability
                        try:
                            from csuite import _chiefs
                            cap_info = {
                                "name": disc.get("tool_name", ""),
                                "description": disc.get("description", ""),
                                "category": disc.get("category", "other"),
                                "domain": disc.get("category", None),
                            }
                            for chief in _chiefs.values():
                                chief.receive_capability_update(cap_info)
                        except Exception:
                            pass
                    except Exception:
                        pass

        return {"status": "completed", "saved": saved}

    except Exception as e:
        logger.error(f"[SCOUT ERROR] {e}")
        return {"status": "error", "error": str(e)}


# ============================================================
# FINANCE AGENT v2.0 — CFO AI ENTERPRISE
# ============================================================

# (constants moved to finance/finance.py)

