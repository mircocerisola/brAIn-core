"""
brAIn Knowledge Keeper Agent v1.1
Layer 5 — Analizza i log degli agenti ed estrae lezioni apprese.
"""

import os
import json
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import anthropic
from supabase import create_client

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

SYSTEM_PROMPT = """Sei il Knowledge Keeper di brAIn, un'organizzazione AI-native.
Analizza i log degli agenti e estrai lezioni apprese.

IMPORTANTE: rispondi SOLO con JSON valido, nessun testo prima o dopo. Struttura:

{"lessons":[{"title":"titolo","content":"descrizione","category":"process","actionable":"azione"}],"patterns":[{"pattern":"descrizione","frequency":"quanto"}],"summary":"riassunto breve"}

Categorie valide: process, technical, strategic, cost, performance.
Se non ci sono dati sufficienti, ritorna lessons vuoto.
SOLO JSON, niente altro."""


def get_recent_logs(hours=24):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        result = supabase.table("agent_logs") \
            .select("*") \
            .gte("created_at", since) \
            .order("created_at", desc=True) \
            .limit(50) \
            .execute()
        return result.data
    except Exception as e:
        print(f"[ERROR] Recupero log fallito: {e}")
        return []


def extract_json(text):
    """Estrae JSON da una stringa, anche se contiene testo extra"""
    text = text.replace("```json", "").replace("```", "").strip()
    
    try:
        return json.loads(text)
    except:
        pass
    
    start = text.find("{")
    if start < 0:
        return None
    
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    
    try:
        return json.loads(text[start:end])
    except:
        return None


def analyze_logs(logs):
    if not logs:
        print("Nessun log da analizzare.")
        return None

    simple_logs = []
    for log in logs:
        simple_logs.append({
            "agent": log.get("agent_id"),
            "action": log.get("action"),
            "status": log.get("status"),
            "model": log.get("model_used"),
            "tokens_in": log.get("tokens_input"),
            "tokens_out": log.get("tokens_output"),
            "cost": log.get("cost_usd"),
            "duration_ms": log.get("duration_ms"),
            "error": log.get("error"),
            "time": log.get("created_at"),
        })

    logs_text = json.dumps(simple_logs, indent=2, default=str)
    
    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Analizza questi log e rispondi SOLO con JSON:\n\n{logs_text}"
            }]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        supabase.table("agent_logs").insert({
            "agent_id": "knowledge_keeper",
            "action": "analyze_logs",
            "layer": 5,
            "input_summary": f"Analizzati {len(logs)} log",
            "output_summary": reply[:500],
            "model_used": "claude-haiku-4-5-20251001",
            "tokens_input": response.usage.input_tokens,
            "tokens_output": response.usage.output_tokens,
            "cost_usd": (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            "duration_ms": duration,
            "status": "success",
        }).execute()

        return reply

    except Exception as e:
        print(f"[ERROR] Analisi fallita: {e}")
        return None


def save_lessons(analysis_text):
    data = extract_json(analysis_text)
    
    if data is None:
        print("[ERROR] Impossibile estrarre JSON dalla risposta")
        return 0

    saved = 0
    for lesson in data.get("lessons", []):
        try:
            supabase.table("org_knowledge").insert({
                "title": lesson.get("title", "Senza titolo"),
                "content": lesson.get("content", ""),
                "category": lesson.get("category", "general"),
                "source": "knowledge_keeper_v1",
            }).execute()
            saved += 1
        except Exception as e:
            print(f"[ERROR] Salvataggio lezione fallito: {e}")

    summary = data.get("summary", "")
    if summary:
        print(f"\n   Summary: {summary}")

    return saved


def run():
    print("Knowledge Keeper avviato...")
    print(f"   Recupero log ultime 24 ore...")

    logs = get_recent_logs(hours=24)
    print(f"   Trovati {len(logs)} log")

    if not logs:
        print("   Nessun log da analizzare. Esco.")
        return

    print("   Analisi in corso con Claude...")
    analysis = analyze_logs(logs)

    if analysis:
        saved = save_lessons(analysis)
        print(f"\n   {saved} lezioni salvate in org_knowledge")
    else:
        print("   Analisi fallita.")

    print("Knowledge Keeper completato.")


if __name__ == "__main__":
    run()