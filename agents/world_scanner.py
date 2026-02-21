"""
brAIn World Scanner Agent v1.0
Layer 1 — Scansiona il web per identificare problemi globali risolvibili.
Usa Perplexity Sonar per ricerca, Claude per analisi e scoring.
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

SCAN_QUERIES = [
    "biggest unsolved problems people face daily 2026",
    "growing market gaps underserved needs 2026",
    "most common complaints frustrations online 2026",
    "emerging problems from AI automation 2026",
    "small business pain points no good solution 2026",
]

ANALYSIS_PROMPT = """Sei il World Scanner di brAIn, un'organizzazione AI-native con 1 umano + agenti AI.
Budget: 1000 euro/mese. Competenza tecnica: no-code/low-code. Stack: Claude API, Supabase, Python, Telegram.

Analizza i risultati di ricerca e identifica 3-5 PROBLEMI CONCRETI che:
1. Colpiscono molte persone (mercato grande)
2. Non hanno una soluzione soddisfacente
3. Potrebbero essere risolti con AI + automazione
4. Possono partire con budget minimo (sotto 200 euro/mese)
5. Hanno potenziale di revenue

Per ogni problema, assegna un punteggio 0-1 basato su: dimensione mercato, urgenza, fattibilità con il nostro stack, potenziale revenue.

Rispondi SOLO con JSON valido:
{"problems":[{"title":"titolo breve","description":"descrizione del problema","domain":"settore","market_size_estimate":"stima","urgency":"low|medium|high|critical","score":0.8,"reasoning":"perché questo problema è interessante"}],"scan_summary":"riassunto della scansione"}

SOLO JSON, niente altro."""


def search_perplexity(query):
    try:
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": query}],
                "max_tokens": 500,
            },
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            print(f"[ERROR] Perplexity {response.status_code}")
            return None
    except Exception as e:
        print(f"[ERROR] Search failed: {e}")
        return None


def extract_json(text):
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


def analyze_scan(search_results):
    combined = "\n\n---\n\n".join([
        f"Query: {topic}\nResults: {result}"
        for topic, result in search_results if result
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
                "content": f"Analizza questi risultati e identifica problemi risolvibili. SOLO JSON:\n\n{combined}"
            }]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text

        supabase.table("agent_logs").insert({
            "agent_id": "world_scanner",
            "action": "scan_and_analyze",
            "layer": 1,
            "input_summary": f"Scansionati {len(search_results)} topic",
            "output_summary": reply[:500],
            "model_used": "claude-haiku-4-5-20251001",
            "tokens_input": response.usage.input_tokens,
            "tokens_output": response.usage.output_tokens,
            "cost_usd": (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
            "duration_ms": duration,
            "status": "success",
        }).execute()

        # Log scansione
        supabase.table("scan_logs").insert({
            "agent_id": "world_scanner",
            "query": json.dumps(SCAN_QUERIES),
            "sources_scanned": len(search_results),
            "results_found": len(search_results),
            "duration_ms": duration,
            "status": "completed",
        }).execute()

        return reply

    except Exception as e:
        print(f"[ERROR] Analisi fallita: {e}")
        return None


def save_problems(analysis_text):
    data = extract_json(analysis_text)
    if data is None:
        print("[ERROR] Impossibile estrarre JSON")
        return 0

    saved = 0
    for prob in data.get("problems", []):
        try:
            supabase.table("problems").insert({
                "title": prob.get("title", "Senza titolo"),
                "description": prob.get("description", ""),
                "domain": prob.get("domain", "general"),
                "market_size_estimate": prob.get("market_size_estimate", ""),
                "urgency": prob.get("urgency", "medium"),
                "score": prob.get("score", 0.5),
                "status": "new",
                "created_by": "world_scanner",
            }).execute()
            saved += 1
            print(f"   [{prob.get('score', '?')}] {prob.get('title')}")
        except Exception as e:
            print(f"[ERROR] Salvataggio fallito: {e}")

    summary = data.get("scan_summary", "")
    if summary:
        print(f"\n   Summary: {summary}")

    return saved


def run():
    print("World Scanner avviato...")

    search_results = []
    for query in SCAN_QUERIES:
        print(f"   Scansiono: {query}")
        result = search_perplexity(query)
        if result:
            search_results.append((query, result))
            print(f"   -> Trovato")
        else:
            print(f"   -> Nessun risultato")
        time.sleep(1)

    print(f"\n   {len(search_results)}/{len(SCAN_QUERIES)} scansioni completate")

    if not search_results:
        print("   Nessun risultato. Esco.")
        return

    print("   Analisi problemi con Claude...")
    analysis = analyze_scan(search_results)

    if analysis:
        saved = save_problems(analysis)
        print(f"\n   {saved} problemi salvati in database")
    else:
        print("   Analisi fallita.")

    print("World Scanner completato.")


if __name__ == "__main__":
    run()