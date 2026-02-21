"""
brAIn Capability Scout Agent v1.1
Layer 5 — Scopre nuovi tool, modelli AI e tecnologie utili per l'organizzazione.
Usa Perplexity Sonar per cercare sul web.
"""

import os
import json
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic
from supabase import create_client
import requests

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

SEARCH_TOPICS = [
    "new AI agent frameworks tools 2025 2026",
    "Claude API new features updates 2026",
    "best no-code AI automation tools 2026",
    "Supabase new features updates 2026",
    "open source AI tools for startups 2026",
]

ANALYSIS_PROMPT = """Sei il Capability Scout di brAIn, un'organizzazione AI-native.
Analizzi le scoperte dal web e valuti quali tool/modelli/tecnologie potrebbero essere utili.

brAIn usa: Claude API (Haiku/Sonnet), Supabase (PostgreSQL + pgvector), Python, Google Cloud Run, Telegram Bot.
Budget: 1000 euro/mese totali. Preferenza: no-code o low-code.

Seleziona SOLO le 3-5 scoperte piu rilevanti. Non elencare tutto.

Rispondi SOLO con JSON valido, nessun testo prima o dopo:
{"discoveries":[{"tool_name":"nome","category":"ai_model","description":"cosa fa","potential_impact":"come aiuta brAIn","cost":"stima","relevance":"high","action":"evaluate"}],"summary":"riassunto breve"}

Categorie: ai_model, framework, database, automation, monitoring, other
Relevance: high, medium, low
Action: adopt, evaluate, monitor, ignore
SOLO JSON."""


def search_perplexity(query):
    """Cerca sul web usando Perplexity Sonar"""
    try:
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [
                    {"role": "user", "content": query}
                ],
                "max_tokens": 500,
            },
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            print(f"[ERROR] Perplexity {response.status_code}: {response.text[:200]}")
            return None
    except Exception as e:
        print(f"[ERROR] Perplexity search failed: {e}")
        return None


def extract_json(text):
    """Estrae JSON da una stringa"""
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


def analyze_discoveries(search_results):
    """Analizza i risultati con Claude"""
    combined = "\n\n---\n\n".join([
        f"Topic: {topic}\nResults: {result}"
        for topic, result in search_results
        if result
    ])

    if not combined:
        return None

    start = time.time()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=ANALYSIS_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Analizza queste scoperte e rispondi SOLO con JSON:\n\n{combined}"
            }]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        print(f"[DEBUG] Risposta Claude ({len(reply)} chars): {reply[:200]}...")

        supabase.table("agent_logs").insert({
            "agent_id": "capability_scout",
            "action": "analyze_discoveries",
            "layer": 5,
            "input_summary": f"Analizzati {len(search_results)} topic",
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


def save_discoveries(analysis_text):
    """Salva le scoperte in capability_log"""
    data = extract_json(analysis_text)
    if data is None:
        print("[ERROR] Impossibile estrarre JSON")
        return 0

    saved = 0
    for disc in data.get("discoveries", []):
        if disc.get("relevance") in ("high", "medium"):
            try:
                status = "evaluating" if disc.get("action") in ("adopt", "evaluate") else "discovered"
                supabase.table("capability_log").insert({
                    "tool_name": disc.get("tool_name", "Unknown"),
                    "category": disc.get("category", "other"),
                    "description": disc.get("description", ""),
                    "potential_impact": disc.get("potential_impact", ""),
                    "cost": disc.get("cost", "unknown"),
                    "status": status,
                }).execute()
                saved += 1
            except Exception as e:
                print(f"[ERROR] Salvataggio fallito: {e}")

    summary = data.get("summary", "")
    if summary:
        print(f"\n   Summary: {summary}")

    return saved


def run():
    print("Capability Scout avviato...")

    search_results = []
    for topic in SEARCH_TOPICS:
        print(f"   Cerco: {topic}")
        result = search_perplexity(topic)
        if result:
            search_results.append((topic, result))
            print(f"   -> Trovato")
        else:
            print(f"   -> Nessun risultato")
        time.sleep(1)

    print(f"\n   {len(search_results)}/{len(SEARCH_TOPICS)} ricerche completate")

    if not search_results:
        print("   Nessun risultato. Esco.")
        return

    print("   Analisi con Claude...")
    analysis = analyze_discoveries(search_results)

    if analysis:
        saved = save_discoveries(analysis)
        print(f"\n   {saved} scoperte salvate in capability_log")
    else:
        print("   Analisi fallita.")

    print("Capability Scout completato.")


if __name__ == "__main__":
    run()