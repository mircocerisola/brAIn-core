"""
brAIn Runner — CLI per monitorare task C-Suite e pipeline.
Legge da Supabase e stampa log in formato strutturato.

Formato riga: [TIMESTAMP] [TASK_ID] [CHIEF_ID] [TITLE] [STATUS] [DURATION_SEC]

Usage:
    python brain_runner.py [--limit N] [--chief cso|cfo|...] [--status pending|completed|blocked]
    python brain_runner.py --watch   # refresh ogni 10s
"""
from __future__ import annotations
import os, sys, time, argparse
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

try:
    from supabase import create_client
    supabase = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))
except Exception as e:
    print(f"[ERROR] Supabase init: {e}")
    sys.exit(1)


def format_row(row: dict) -> str:
    """Formatta una riga da code_tasks in formato log standard."""
    ts = row.get("created_at", "")[:19].replace("T", " ")
    task_id = str(row.get("id", "?")).rjust(5)
    chief = (row.get("requested_by") or "?").upper().ljust(5)
    title = (row.get("title") or "").replace("\n", " ")[:50].ljust(50)
    status = (row.get("status") or "?").ljust(16)
    # duration non sempre disponibile — usa updated_at - created_at se possibile
    dur = "-"
    try:
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(row.get("updated_at", row["created_at"]).replace("Z", "+00:00"))
        secs = int((updated - created).total_seconds())
        dur = f"{secs}s"
    except Exception:
        pass
    sandboxed = " [SANDBOX_FAIL]" if row.get("sandbox_passed") is False else ""
    return f"[{ts}] [{task_id}] [{chief}] {title} [{status}] [{dur}]{sandboxed}"


def format_decision_row(row: dict) -> str:
    """Formatta una riga da chief_decisions."""
    ts = row.get("created_at", "")[:19].replace("T", " ")
    chief = (row.get("chief_domain") or "?").upper().ljust(8)
    dtype = (row.get("decision_type") or "?").ljust(20)
    summary = (row.get("summary") or "").replace("\n", " ")[:60]
    return f"[{ts}] [{chief}] [{dtype}] {summary}"


def run_once(args) -> None:
    limit = args.limit

    # code_tasks
    print("\n" + "━" * 90)
    print(f"  CODE TASKS  [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC]")
    print("━" * 90)
    try:
        q = supabase.table("code_tasks").select("*").order("created_at", desc=True).limit(limit)
        if args.chief:
            q = q.eq("requested_by", args.chief.lower())
        if args.status:
            q = q.eq("status", args.status)
        rows = q.execute().data or []
        if rows:
            for row in rows:
                print(format_row(row))
        else:
            print("  (nessun task trovato)")
    except Exception as e:
        print(f"  [ERROR] code_tasks: {e}")

    # chief_decisions (ultime N)
    print("\n" + "━" * 90)
    print("  CHIEF DECISIONS (ultime)")
    print("━" * 90)
    try:
        q2 = supabase.table("chief_decisions").select("*").order("created_at", desc=True).limit(limit)
        if args.chief:
            q2 = q2.eq("chief_domain", args.chief.lower())
        rows2 = q2.execute().data or []
        if rows2:
            for row in rows2:
                print(format_decision_row(row))
        else:
            print("  (nessuna decisione trovata)")
    except Exception as e:
        print(f"  [ERROR] chief_decisions: {e}")

    # projects pipeline_locked
    print("\n" + "━" * 90)
    print("  PROJECTS — pipeline attive")
    print("━" * 90)
    try:
        locked = supabase.table("projects").select("id,name,status,pipeline_locked").eq("pipeline_locked", True).execute().data or []
        if locked:
            for p in locked:
                print(f"  project_id={p['id']} name={p.get('name','?')[:30]} status={p.get('status','?')} LOCKED=True")
        else:
            print("  (nessun progetto con pipeline attiva)")
    except Exception as e:
        print(f"  [ERROR] projects: {e}")

    print("━" * 90)


def main():
    parser = argparse.ArgumentParser(description="brAIn Runner — monitor C-Suite tasks")
    parser.add_argument("--limit", type=int, default=20, help="Numero di righe da mostrare")
    parser.add_argument("--chief", type=str, default="", help="Filtra per chief (cso, cfo, cmo, cto, ...)")
    parser.add_argument("--status", type=str, default="", help="Filtra per status (pending_approval, completed, blocked)")
    parser.add_argument("--watch", action="store_true", help="Refresh ogni 10 secondi")
    args = parser.parse_args()

    if args.watch:
        print("brAIn Runner — watch mode (Ctrl+C per uscire)")
        while True:
            run_once(args)
            time.sleep(10)
    else:
        run_once(args)


if __name__ == "__main__":
    main()
