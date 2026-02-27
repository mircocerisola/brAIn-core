"""
brAIn Runner — CLI locale per eseguire code_tasks approvati.
Polling: controlla code_tasks ogni 30s, esegue quelli approvati via Claude Code subprocess.

Usage:
    python brain_runner.py                # esegue un task approvato
    python brain_runner.py --watch        # polling continuo
    python brain_runner.py --list         # mostra task recenti
"""
from __future__ import annotations
import os, sys, time, argparse, subprocess, threading, json
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

try:
    from supabase import create_client
    supabase = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))
except Exception as e:
    print(f"[ERROR] Supabase init: {e}")
    sys.exit(1)


# ============================================================
# EXECUTOR PURO — subprocess.Popen per Claude Code
# ============================================================

_processes = {}
_lock = threading.Lock()


def run(prompt):
    """Lancia Claude Code come subprocess. Ritorna {pid, status}."""
    cmd = ["claude", "--dangerously-skip-permissions", "-p", prompt]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.getenv("BRAIN_REPO_PATH", os.getcwd()),
        )
    except FileNotFoundError:
        return {"pid": None, "status": "error", "error": "claude CLI non trovato nel PATH"}
    except Exception as e:
        return {"pid": None, "status": "error", "error": str(e)}

    entry = {
        "proc": proc,
        "stdout_lines": [],
        "stderr_lines": [],
        "returncode": None,
    }

    def _read_stream(stream, target_list):
        try:
            for line in stream:
                with _lock:
                    target_list.append(line.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=_read_stream, args=(proc.stdout, entry["stdout_lines"]), daemon=True).start()
    threading.Thread(target=_read_stream, args=(proc.stderr, entry["stderr_lines"]), daemon=True).start()

    with _lock:
        _processes[proc.pid] = entry

    print(f"[RUNNER] Processo avviato PID={proc.pid}")
    return {"pid": proc.pid, "status": "running"}


def get_output(pid):
    """Ritorna output corrente del processo."""
    with _lock:
        entry = _processes.get(pid)
        if not entry:
            return {"error": "PID non trovato", "pid": pid}
        rc = entry["proc"].poll()
        entry["returncode"] = rc
        return {
            "pid": pid,
            "running": rc is None,
            "returncode": rc,
            "stdout": list(entry["stdout_lines"]),
            "stderr": list(entry["stderr_lines"]),
        }


def interrupt(pid):
    """Manda SIGTERM al processo."""
    with _lock:
        entry = _processes.get(pid)
        if not entry:
            return {"error": "PID non trovato", "pid": pid}
    try:
        entry["proc"].terminate()
        print(f"[RUNNER] SIGTERM inviato a PID={pid}")
        return {"pid": pid, "status": "terminated"}
    except Exception as e:
        return {"pid": pid, "error": str(e)}


def is_running(pid):
    """Controlla se il processo e' ancora attivo."""
    with _lock:
        entry = _processes.get(pid)
        if not entry:
            return False
        return entry["proc"].poll() is None


# ============================================================
# CLI — polling code_tasks + esecuzione locale
# ============================================================

def format_row(row):
    ts = row.get("created_at", "")[:19].replace("T", " ")
    task_id = str(row.get("id", "?")).rjust(5)
    chief = (row.get("requested_by") or "?").upper().ljust(5)
    title = (row.get("title") or "").replace("\n", " ")[:50].ljust(50)
    status = (row.get("status") or "?").ljust(16)
    dur = "-"
    try:
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(row.get("updated_at", row["created_at"]).replace("Z", "+00:00"))
        secs = int((updated - created).total_seconds())
        dur = f"{secs}s"
    except Exception:
        pass
    return f"[{ts}] [{task_id}] [{chief}] {title} [{status}] [{dur}]"


def list_tasks(limit=20):
    print("\n" + "\u2501" * 90)
    print(f"  CODE TASKS  [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC]")
    print("\u2501" * 90)
    try:
        rows = supabase.table("code_tasks").select("*").order("created_at", desc=True).limit(limit).execute().data or []
        for row in rows:
            print(format_row(row))
        if not rows:
            print("  (nessun task trovato)")
    except Exception as e:
        print(f"  [ERROR] {e}")
    print("\u2501" * 90)


def execute_one():
    """Cerca un task approvato e lo esegue."""
    try:
        rows = supabase.table("code_tasks").select("id,prompt,title") \
            .eq("status", "approved").order("created_at", desc=False).limit(1).execute().data or []
    except Exception as e:
        print(f"[ERROR] query: {e}")
        return False

    if not rows:
        return False

    task = rows[0]
    task_id = task["id"]
    prompt = task.get("prompt", "")
    title = (task.get("title") or "")[:60]
    print(f"\n[EXEC] Task #{task_id}: {title}")

    # Aggiorna status a running
    try:
        supabase.table("code_tasks").update({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", task_id).execute()
    except Exception:
        pass

    # Esegui
    result = run(prompt)
    if result.get("status") == "error":
        print(f"[ERROR] {result.get('error')}")
        try:
            supabase.table("code_tasks").update({
                "status": "error", "output": result.get("error"),
            }).eq("id", task_id).execute()
        except Exception:
            pass
        return True

    pid = result["pid"]
    try:
        supabase.table("code_tasks").update({"pid": pid}).eq("id", task_id).execute()
    except Exception:
        pass

    # Attendi completamento
    print(f"[WAIT] PID={pid} in esecuzione...")
    while is_running(pid):
        time.sleep(10)
        out = get_output(pid)
        stdout_count = len(out.get("stdout", []))
        stderr_count = len(out.get("stderr", []))
        print(f"  ... stdout={stdout_count} lines, stderr={stderr_count} lines")

    # Completato
    out = get_output(pid)
    rc = out.get("returncode", -1)
    stdout_lines = out.get("stdout", [])
    stderr_lines = out.get("stderr", [])

    if rc == 0:
        print(f"[DONE] Task #{task_id} completato (exit 0)")
        status = "done"
    else:
        print(f"[FAIL] Task #{task_id} fallito (exit {rc})")
        status = "error"

    # Ultime righe output
    for line in stdout_lines[-10:]:
        print(f"  > {line}")
    if stderr_lines:
        print("  [STDERR]:")
        for line in stderr_lines[-5:]:
            print(f"  ! {line}")

    try:
        supabase.table("code_tasks").update({
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "output": "\n".join(stdout_lines[-50:]),
        }).eq("id", task_id).execute()
    except Exception:
        pass

    return True


def main():
    parser = argparse.ArgumentParser(description="brAIn Runner — executor locale Claude Code")
    parser.add_argument("--list", action="store_true", help="Mostra task recenti")
    parser.add_argument("--watch", action="store_true", help="Polling continuo (30s)")
    parser.add_argument("--limit", type=int, default=20, help="Numero task da mostrare")
    args = parser.parse_args()

    if args.list:
        list_tasks(args.limit)
        return

    if args.watch:
        print("brAIn Runner — watch mode (Ctrl+C per uscire)")
        while True:
            if not execute_one():
                time.sleep(30)
    else:
        if not execute_one():
            print("Nessun task approvato da eseguire.")
            list_tasks(5)


if __name__ == "__main__":
    main()
