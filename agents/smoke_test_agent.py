#!/usr/bin/env python3
"""
brAIn Smoke Test Agent — standalone
Funzioni: setup (trova prospect), analyze (analizza feedback)
Uso: python agents/smoke_test_agent.py --setup --project-id 2
     python agents/smoke_test_agent.py --analyze --project-id 2
"""
import os, sys, json, argparse, requests, time, re
from datetime import datetime, timezone

try:
    import anthropic
except ImportError:
    print("ERRORE: anthropic non installato. pip install anthropic")
    sys.exit(1)

def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    try:
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass

_load_env()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

try:
    from supabase import create_client
    supabase = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))
except Exception as e:
    print(f"WARN: Supabase non configurato: {e}")
    supabase = None

PERPLEXITY_KEY = os.getenv("PERPLEXITY_API_KEY", "")


def _search_perplexity(query):
    if not PERPLEXITY_KEY:
        return ""
    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
            json={"model": "sonar", "messages": [{"role": "user", "content": query}]},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"WARN: Perplexity: {e}")
    return ""


def setup(project_id):
    """Crea smoke test, trova prospect via Perplexity, salva in DB."""
    if not supabase:
        print("ERRORE: Supabase non configurato")
        return
    proj = supabase.table("projects").select("*").eq("id", project_id).execute()
    if not proj.data:
        print(f"ERRORE: project {project_id} non trovato")
        return
    project = proj.data[0]
    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    print(f"[SMOKE] Setup per: {name}")

    # Crea smoke_test record
    res = supabase.table("smoke_tests").insert({
        "project_id": project_id,
        "landing_page_url": project.get("smoke_test_url", ""),
    }).execute()
    smoke_id = res.data[0]["id"] if res.data else None
    print(f"  smoke_id: {smoke_id}")

    # Estrai target
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": (
            f"Estrai target customer in 1 riga concisa per ricerca. Solo la riga.\n{spec_md[:2000]}"
        )}],
    )
    target_query = resp.content[0].text.strip()
    print(f"  Target: {target_query}")

    # Cerca prospect
    query = (f"trova 20 {target_query} con contatto email o LinkedIn pubblico in Italia. "
             f"Elenca: Nome | Ruolo | Email-o-LinkedIn")
    perp_result = _search_perplexity(query)

    prospects_raw = []
    if perp_result:
        for line in perp_result.split("\n"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and ("@" in parts[2] or "linkedin" in parts[2].lower()):
                prospects_raw.append({
                    "name": parts[0][:100],
                    "contact": parts[2][:200],
                    "channel": "email" if "@" in parts[2] else "linkedin",
                })

    inserted = 0
    for p in prospects_raw[:50]:
        try:
            supabase.table("smoke_test_prospects").insert({
                "smoke_test_id": smoke_id,
                "project_id": project_id,
                "name": p["name"],
                "contact": p["contact"],
                "channel": p["channel"],
                "status": "pending",
            }).execute()
            inserted += 1
        except Exception as e:
            print(f"WARN: {e}")

    supabase.table("smoke_tests").update({"prospects_count": inserted}).eq("id", smoke_id).execute()
    print(f"  Prospect inseriti: {inserted}")
    return smoke_id


def analyze(project_id):
    """Analizza feedback e genera SPEC_UPDATES.md."""
    if not supabase:
        print("ERRORE: Supabase non configurato")
        return
    proj = supabase.table("projects").select("*").eq("id", project_id).execute()
    if not proj.data:
        print(f"ERRORE: project {project_id} non trovato")
        return
    project = proj.data[0]
    name = project.get("name")
    spec_md = project.get("spec_md", "")

    st = supabase.table("smoke_tests").select("*").eq("project_id", project_id).order("started_at", desc=True).limit(1).execute()
    if not st.data:
        print("ERRORE: smoke test non trovato")
        return
    smoke = st.data[0]
    smoke_id = smoke["id"]

    prospects = supabase.table("smoke_test_prospects").select("*").eq("smoke_test_id", smoke_id).execute()
    data = prospects.data or []
    sent = sum(1 for p in data if p.get("sent_at"))
    forms = [p for p in data if p.get("status") == "form_compiled"]
    rejected = [p for p in data if p.get("status") == "rejected"]
    reasons = [p.get("rejection_reason", "") for p in rejected if p.get("rejection_reason")]
    conv_rate = (len(forms) / max(sent, 1)) * 100

    print(f"[SMOKE] Analisi {name}: {sent} inviati, {len(forms)} form, {len(rejected)} rifiuti")
    print(f"  Conversione: {conv_rate:.1f}%")

    resp = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": (
            f'Analizza smoke test "{name}". '
            f'Dati: {sent} contattati, {len(forms)} form, {conv_rate:.1f}% conv. '
            f'Rifiuti: {"; ".join(reasons[:5]) or "N/A"}. '
            f'SPEC: {spec_md[:1500]}\n'
            f'JSON: {{"overall_signal":"green/yellow/red","key_insights":[],"spec_updates":[],"recommendation":"PROCEDI/PIVOTA/FERMA","reasoning":""}}'
        )}],
    )
    m = re.search(r'\{[\s\S]*\}', resp.content[0].text)
    insights = json.loads(m.group(0)) if m else {}
    print(f"  Segnale: {insights.get('overall_signal', 'N/A').upper()}")
    print(f"  Raccomandazione: {insights.get('recommendation', 'N/A')}")

    # Genera SPEC_UPDATES.md
    output = f"# SPEC Updates — {name}\n{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
    output += f"## Segnale: {insights.get('overall_signal', 'N/A').upper()}\n\n"
    output += f"## Raccomandazione: {insights.get('recommendation', 'N/A')}\n\n"
    output += "## Insights\n" + "\n".join(f"{i+1}. {x}" for i, x in enumerate(insights.get("key_insights", [])))
    output += "\n\n## Modifiche SPEC\n" + "\n".join(f"{i+1}. {x}" for i, x in enumerate(insights.get("spec_updates", [])))

    out_path = os.path.join(os.path.dirname(__file__), '..', f"SPEC_UPDATES_{project_id}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"  SPEC_UPDATES salvato: {out_path}")
    return insights


def main():
    parser = argparse.ArgumentParser(description="brAIn Smoke Test Agent")
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    if args.setup:
        setup(args.project_id)
    elif args.analyze:
        analyze(args.project_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
