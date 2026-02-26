#!/usr/bin/env python3
"""
brAIn Legal Agent — standalone
Funzioni: review_project, generate_project_docs, monitor_brain_compliance
Uso standalone: python agents/legal_agent.py --project-id 2
               python agents/legal_agent.py --compliance
               python agents/legal_agent.py --docs --project-id 2
"""
import os, sys, json, argparse, requests, time
from datetime import datetime, timezone

try:
    import anthropic
except ImportError:
    print("ERRORE: anthropic non installato. pip install anthropic")
    sys.exit(1)

# Carica .env
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


LEGAL_SYSTEM_PROMPT = """Sei il Legal Agent di brAIn, esperto di diritto digitale europeo (GDPR, AI Act, Direttiva E-Commerce, normativa italiana).
Analizza un progetto e valuta i rischi legali per operare in Europa.

RISPOSTA: JSON puro, nessun testo fuori.
{
  "green_points": ["punto OK 1"],
  "yellow_points": ["attenzione 1: cosa fare"],
  "red_points": ["blocco critico 1: perche' blocca il lancio"],
  "report_md": "# Review Legale\\n...",
  "can_proceed": true
}"""


def review_project(project_id):
    """Review legale completa del progetto."""
    if not supabase:
        return {"status": "error", "error": "Supabase non configurato"}
    proj = supabase.table("projects").select("*").eq("id", project_id).execute()
    if not proj.data:
        return {"status": "error", "error": "project not found"}
    project = proj.data[0]
    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    if not spec_md:
        return {"status": "error", "error": "spec_md mancante"}

    print(f"[LEGAL] Review per: {name}")
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=LEGAL_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": (
            f"Progetto: {name}\nSettore: {sector}\n\nSPEC:\n{spec_md[:5000]}"
        )}],
    )
    raw = response.content[0].text.strip()
    import re
    m = re.search(r'\{[\s\S]*\}', raw)
    review_data = json.loads(m.group(0)) if m else json.loads(raw)

    green = review_data.get("green_points", [])
    yellow = review_data.get("yellow_points", [])
    red = review_data.get("red_points", [])

    print(f"Risultato: 🟢 {len(green)} | 🟡 {len(yellow)} | 🔴 {len(red)}")
    if red:
        print("Blocchi critici:")
        for r in red:
            print(f"  🔴 {r}")
    if yellow:
        print("Attenzioni:")
        for y in yellow:
            print(f"  🟡 {y}")

    # Salva in DB
    if supabase:
        supabase.table("legal_reviews").insert({
            "project_id": project_id,
            "review_type": "spec_review",
            "status": "completed",
            "green_points": json.dumps(green),
            "yellow_points": json.dumps(yellow),
            "red_points": json.dumps(red),
            "report_md": review_data.get("report_md", ""),
        }).execute()
        new_status = "legal_ok" if review_data.get("can_proceed") else "legal_blocked"
        supabase.table("projects").update({"status": new_status}).eq("id", project_id).execute()
        print(f"Status progetto aggiornato: {new_status}")

    return review_data


def generate_project_docs(project_id):
    """Genera Privacy Policy, ToS, Client Contract."""
    if not supabase:
        return {"status": "error", "error": "Supabase non configurato"}
    proj = supabase.table("projects").select("name,spec_md,slug").eq("id", project_id).execute()
    if not proj.data:
        return {"status": "error", "error": "project not found"}
    project = proj.data[0]
    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    docs = {}
    for doc_type, doc_name in [
        ("privacy_policy", "Privacy Policy"),
        ("terms_of_service", "Termini di Servizio"),
        ("client_contract", "Contratto Cliente"),
    ]:
        prompt = (f"Genera {doc_name} per il prodotto '{name}' (legge italiana/europea).\n"
                  f"SPEC: {spec_md[:2000]}\nFormato: testo legale formale, sezioni numerate, italiano. Max 800 parole.")
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        docs[doc_type] = resp.content[0].text.strip()
        print(f"  ✓ {doc_name} generata ({len(docs[doc_type])} chars)")
    return docs


def monitor_brain_compliance():
    """Compliance check settimanale di brAIn."""
    prompt = """Sei il Legal Monitor di brAIn. Verifica la compliance dell'organismo.
brAIn: scansiona problemi globali, genera soluzioni con AI, lancia MVP, raccoglie feedback in Europa.
Verifica: GDPR, AI Act 2026, Direttiva E-Commerce, normativa italiana.
Risposta testo piano italiano, max 10 righe."""
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    report = resp.content[0].text.strip()
    print(report)
    return {"status": "ok", "report": report}


def main():
    parser = argparse.ArgumentParser(description="brAIn Legal Agent")
    parser.add_argument("--project-id", type=int, help="ID progetto")
    parser.add_argument("--review", action="store_true", help="Review legale")
    parser.add_argument("--docs", action="store_true", help="Genera documenti legali")
    parser.add_argument("--compliance", action="store_true", help="Compliance check brAIn")
    args = parser.parse_args()

    if args.compliance:
        monitor_brain_compliance()
    elif args.docs and args.project_id:
        generate_project_docs(args.project_id)
    elif args.project_id:
        review_project(args.project_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
