"""
brAIn Runner — executor puro per Claude Code subprocess.
Zero messaggi Telegram, zero logica, zero timer.
Metodi: run(prompt), get_output(pid), interrupt(pid), is_running(pid).
"""
import subprocess
import threading
import os
from typing import Dict
from core.config import logger

_processes: Dict[int, dict] = {}
_lock = threading.Lock()


def run(prompt: str) -> dict:
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

    threading.Thread(
        target=_read_stream, args=(proc.stdout, entry["stdout_lines"]), daemon=True,
    ).start()
    threading.Thread(
        target=_read_stream, args=(proc.stderr, entry["stderr_lines"]), daemon=True,
    ).start()

    with _lock:
        _processes[proc.pid] = entry

    logger.info("[BRAIN_RUNNER] Processo avviato PID=%d", proc.pid)
    return {"pid": proc.pid, "status": "running"}


def get_output(pid: int) -> dict:
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


def interrupt(pid: int) -> dict:
    """Manda SIGTERM al processo."""
    with _lock:
        entry = _processes.get(pid)
        if not entry:
            return {"error": "PID non trovato", "pid": pid}

    try:
        entry["proc"].terminate()
        logger.info("[BRAIN_RUNNER] SIGTERM inviato a PID=%d", pid)
        return {"pid": pid, "status": "terminated"}
    except Exception as e:
        return {"pid": pid, "error": str(e)}


def is_running(pid: int) -> bool:
    """Controlla se il processo e' ancora attivo."""
    with _lock:
        entry = _processes.get(pid)
        if not entry:
            return False
        return entry["proc"].poll() is None
