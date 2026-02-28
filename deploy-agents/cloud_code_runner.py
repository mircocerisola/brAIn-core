"""
brain-code-executor — Cloud Run Job per esecuzione Claude Code headless.
Legge task da code_tasks (status=ready), clona repo, esegue Claude Code,
salva output progressivamente in output_log, push automatico.
"""
import os
import sys
import subprocess
import time
import shutil
import threading
import re
from datetime import datetime, timezone

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
REPO = "mircocerisola/brAIn-core"
WORKSPACE = "/tmp/brain-workspace"
FLUSH_INTERVAL = 15  # secondi tra flush output_log
INTERRUPT_CHECK = 10  # secondi tra check interrupt

try:
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"[FATAL] Supabase init: {e}")
    sys.exit(1)


# ============================================================
# HELPERS
# ============================================================

def _update_task(task_id, data):
    try:
        supabase.table("code_tasks").update(data).eq("id", task_id).execute()
    except Exception as e:
        print(f"[ERROR] update task #{task_id}: {e}")


def _flush_log(task_id, lines):
    try:
        supabase.table("code_tasks").update({
            "output_log": "\n".join(lines[-500:]),
        }).eq("id", task_id).execute()
    except Exception as e:
        print(f"[ERROR] flush log: {e}")


def _is_interrupt_requested(task_id):
    try:
        r = supabase.table("code_tasks").select("status").eq("id", task_id).execute()
        return r.data and r.data[0].get("status") == "interrupt_requested"
    except Exception:
        return False


def _build_enriched_prompt(prompt, task_id):
    """Arricchisce il prompt con CLAUDE.md + architettura dal DB.
    Il Code Agent deve conoscere il DNA di brAIn per lavorare bene.
    """
    context_parts = []

    # 1. Leggi CLAUDE.md dal repo clonato
    claude_md_path = os.path.join(WORKSPACE, "CLAUDE.md")
    if os.path.exists(claude_md_path):
        try:
            with open(claude_md_path, "r", encoding="utf-8") as f:
                claude_md = f.read()
            # Limita a 4000 chars per non esplodere il contesto
            if len(claude_md) > 4000:
                claude_md = claude_md[:4000] + "\n[...troncato...]"
            context_parts.append(
                "CONTESTO ORGANIZZATIVO (da CLAUDE.md):\n" + claude_md
            )
        except Exception as e:
            print(f"[WARN] Read CLAUDE.md: {e}")

    # 2. Leggi architettura dal DB (cto_architecture_summary)
    try:
        r = supabase.table("cto_architecture_summary").select("summary") \
            .order("created_at", desc=True).limit(1).execute()
        if r.data and r.data[0].get("summary"):
            arch = r.data[0]["summary"]
            if len(arch) > 2000:
                arch = arch[:2000] + "\n[...troncato...]"
            context_parts.append(
                "ARCHITETTURA CODEBASE (snapshot CTO):\n" + arch
            )
    except Exception:
        pass

    # 3. Regole operative
    context_parts.append(
        "REGOLE OPERATIVE:\n"
        "- Codebase 100% Python sincrono. Cloud Run + Claude API + Supabase + Telegram.\n"
        "- Directory principali: deploy-agents/ (agents-runner), deploy/ (command-center).\n"
        "- NON usare formattazione Markdown nelle risposte a Mirco (no asterischi, grassetto).\n"
        "- NON fare deploy (build/push) — il deploy viene gestito separatamente.\n"
        "- Testa le modifiche se possibile (pytest). Se i test falliscono, non committare.\n"
        "- Committa su main con messaggio chiaro. Push automatico."
    )

    if not context_parts:
        return prompt

    enriched = "\n\n".join(context_parts) + "\n\n---\n\nTASK DA ESEGUIRE:\n" + prompt
    print(f"[JOB] Prompt arricchito: {len(enriched)} chars (original: {len(prompt)})")
    return enriched


# ============================================================
# MAIN
# ============================================================

def main():
    print("[JOB] brain-code-executor avviato")

    # 1. Trova task — da env var CODE_TASK_ID oppure primo 'ready'
    task_id_env = os.getenv("CODE_TASK_ID", "")
    if task_id_env:
        try:
            r = supabase.table("code_tasks").select("id,prompt,title") \
                .eq("id", int(task_id_env)).execute()
            rows = r.data or []
        except Exception as e:
            print(f"[ERROR] Fetch task #{task_id_env}: {e}")
            sys.exit(1)
    else:
        try:
            r = supabase.table("code_tasks").select("id,prompt,title") \
                .eq("status", "ready") \
                .order("created_at").limit(1).execute()
            rows = r.data or []
        except Exception as e:
            print(f"[ERROR] Query code_tasks: {e}")
            sys.exit(1)

    if not rows:
        print("[JOB] Nessun task da eseguire. Exit.")
        return

    task = rows[0]
    task_id = task["id"]
    prompt = task.get("prompt", "")
    title = (task.get("title") or "")[:60]
    print(f"[JOB] Task #{task_id}: {title}")

    # 2. Status -> running
    _update_task(task_id, {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "output_log": "[JOB] Avviato\n",
    })

    # 3. Git config
    subprocess.run(["git", "config", "--global", "user.name", "brAIn Code Agent"],
                    capture_output=True)
    subprocess.run(["git", "config", "--global", "user.email", "code@brain-ai.dev"],
                    capture_output=True)

    # 4. Clone repo
    if os.path.exists(WORKSPACE):
        shutil.rmtree(WORKSPACE, ignore_errors=True)

    clone_url = "https://" + GITHUB_TOKEN + "@github.com/" + REPO + ".git"
    print("[JOB] Cloning repo...")
    clone_r = subprocess.run(
        ["git", "clone", clone_url, WORKSPACE],
        capture_output=True, text=True, timeout=120,
    )
    if clone_r.returncode != 0:
        err = clone_r.stderr[:500]
        _update_task(task_id, {
            "status": "error",
            "output": "Git clone fallito: " + err,
            "output_log": "[GIT] Clone fallito:\n" + err,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        print("[ERROR] Clone failed: " + err)
        return

    _flush_log(task_id, ["[JOB] Repo clonato", "[JOB] Preparazione contesto..."])

    # 5. Verifica claude CLI
    claude_check = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    if claude_check.returncode != 0:
        _update_task(task_id, {
            "status": "error",
            "output": "Claude CLI non disponibile",
            "output_log": "[ERROR] claude --version fallito: " + claude_check.stderr[:300],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        return

    # 5b. Inietta contesto architettura nel prompt
    prompt = _build_enriched_prompt(prompt, task_id)

    _flush_log(task_id, ["[JOB] Repo clonato", "[JOB] Avvio Claude Code..."])

    # 6. Lancia Claude Code
    try:
        proc = subprocess.Popen(
            ["claude", "--dangerously-skip-permissions", "-p", prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=WORKSPACE,
            env={**os.environ, "CI": "1"},
        )
    except Exception as e:
        _update_task(task_id, {
            "status": "error",
            "output": "Errore avvio Claude Code: " + str(e),
            "output_log": "[ERROR] subprocess.Popen: " + str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        return

    print(f"[JOB] Claude Code PID={proc.pid}")
    _update_task(task_id, {"pid": proc.pid})

    # 7. Leggi stdout/stderr in tempo reale con thread
    output_lines = []
    lock = threading.Lock()

    def _reader(stream, prefix=""):
        for raw_line in stream:
            line = raw_line.rstrip("\n")
            with lock:
                output_lines.append(prefix + line)

    t_out = threading.Thread(target=_reader, args=(proc.stdout,), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, "[STDERR] "), daemon=True)
    t_out.start()
    t_err.start()

    # 8. Attendi completamento con flush periodico e check interrupt
    last_flush_time = time.time()
    while proc.poll() is None:
        time.sleep(INTERRUPT_CHECK)
        now = time.time()

        # Flush output_log
        if now - last_flush_time >= FLUSH_INTERVAL:
            with lock:
                _flush_log(task_id, list(output_lines))
            last_flush_time = now

        # Check interrupt
        if _is_interrupt_requested(task_id):
            print("[JOB] Interrupt richiesto — terminating")
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            with lock:
                output_lines.append("[JOB] Processo interrotto su richiesta")
                _flush_log(task_id, list(output_lines))
            _update_task(task_id, {
                "status": "interrupted",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "output": "\n".join(output_lines[-50:]),
            })
            shutil.rmtree(WORKSPACE, ignore_errors=True)
            return

    # Attendi fine stream reader
    t_out.join(timeout=5)
    t_err.join(timeout=5)

    rc = proc.returncode
    print(f"[JOB] Claude Code terminato rc={rc}")

    # Flush finale
    with lock:
        output_lines.append("[JOB] Exit code: " + str(rc))
        _flush_log(task_id, list(output_lines))

    # 9. Git: verifica se ci sono modifiche non pushate
    if rc == 0:
        _ensure_pushed(task_id, output_lines, lock)

    # 10. Status finale
    with lock:
        final = "\n".join(output_lines[-100:])
    _update_task(task_id, {
        "status": "done" if rc == 0 else "error",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "output": final,
    })

    # 11. Cleanup
    shutil.rmtree(WORKSPACE, ignore_errors=True)
    status_str = "done" if rc == 0 else "error"
    print(f"[JOB] Task #{task_id} completato status={status_str}")


def _ensure_pushed(task_id, output_lines, lock):
    """Se Claude Code non ha pushato, prova commit + push residuo."""
    try:
        status_r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=WORKSPACE,
        )
        if not status_r.stdout.strip():
            return  # Nessuna modifica uncommitted

        subprocess.run(["git", "add", "-A"], cwd=WORKSPACE, check=True)

        # Cerca messaggio commit dall'output
        commit_msg = "chore: auto-commit brain-code-executor"
        with lock:
            for line in output_lines:
                m = re.search(r"commit -m ['\"]([^'\"]{10,})['\"]", line)
                if m:
                    commit_msg = m.group(1)
                    break

        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=WORKSPACE, capture_output=True,
        )
        push_r = subprocess.run(
            ["git", "push"], cwd=WORKSPACE,
            capture_output=True, text=True,
        )
        if push_r.returncode == 0:
            with lock:
                output_lines.append("[GIT] Push completato")
        else:
            with lock:
                output_lines.append("[GIT] Push fallito: " + push_r.stderr[:200])
    except Exception as e:
        with lock:
            output_lines.append("[GIT] Errore: " + str(e))


if __name__ == "__main__":
    main()
