"""
brAIn CTO Agent v1.0
Esegue task di codice via Claude Code CLI.
Riporta aggiornamenti ogni 5 min + messaggio completamento strutturato.
"""

import os
import asyncio
import subprocess
import time
import logging
from aiohttp import web
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/workspace")

AUTHORIZED_USER_ID = None
tg_app = None
current_task = None  # {"prompt", "start_time", "output_log", "chat_id"}


# ─── FORMAT HELPERS ──────────────────────────────────────────────────────────

def strip_prompt_from_output(output_log: str) -> str:
    """Skippa tutte le righe iniziali che cominciano con 'Esegui con'."""
    lines = output_log.split("\n")
    for i, line in enumerate(lines):
        if line.strip() and not line.startswith("Esegui con"):
            return "\n".join(lines[i:])
    return ""


def get_last_n_lines(text: str, n: int) -> str:
    """Ultime N righe non vuote."""
    lines = [l for l in text.split("\n") if l.strip()]
    return "\n".join(lines[-n:]) if lines else ""


def get_modified_files(workspace: str) -> list:
    """File modificati dall'ultimo commit (git diff HEAD~1 HEAD, poi staged)."""
    for cmd in [
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "diff", "--name-only"],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               cwd=workspace, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return [f.strip() for f in r.stdout.strip().split("\n") if f.strip()]
        except Exception:
            pass
    return []


def get_commit_hash(workspace: str) -> str:
    """Hash breve dell'ultimo commit."""
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%h"],
                           capture_output=True, text=True,
                           cwd=workspace, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def format_completion_message(
    duration_sec: float,
    modified_files: list,
    commit_hash: str,
    output_log: str,
) -> str:
    """Formato esatto del messaggio di completamento."""
    duration_min = max(1, round(duration_sec / 60))
    clean = strip_prompt_from_output(output_log)
    last_5 = get_last_n_lines(clean, 5)
    files_str = "\n".join(modified_files) if modified_files else "nessuno"
    commit_str = commit_hash if commit_hash else "nessuno"

    return (
        "✅ Completato\n"
        "───────────\n"
        f"⏱ Durata: {duration_min} min\n"
        f"📁 File modificati: {files_str}\n"
        f"🔀 Commit: {commit_str}\n"
        f"📋 Riepilogo: {last_5}\n"
        "───────────"
    )


def format_update_message(output_log: str) -> str:
    """Aggiornamento intermedio ogni 5 min: ultime 3 righe reali."""
    clean = strip_prompt_from_output(output_log)
    last_3 = get_last_n_lines(clean, 3)
    if not last_3:
        return "In esecuzione — attendere output"
    return f"⏳ In esecuzione...\n{last_3}"


# ─── TASK EXECUTION ──────────────────────────────────────────────────────────

async def run_task(prompt: str, chat_id: int):
    global current_task

    start_time = time.time()
    output_log = ""
    current_task = {
        "prompt": prompt,
        "start_time": start_time,
        "output_log": "",
        "chat_id": chat_id,
    }

    async def send_periodic_updates():
        while current_task is not None:
            await asyncio.sleep(300)  # 5 minuti
            if current_task is not None:
                msg = format_update_message(current_task["output_log"])
                try:
                    await tg_app.bot.send_message(chat_id=chat_id, text=msg)
                except Exception as e:
                    logger.error(f"[UPDATE] {e}")

    update_task = asyncio.create_task(send_periodic_updates())

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--dangerously-skip-permissions", "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=WORKSPACE_DIR,
        )
        async for line in proc.stdout:
            decoded = line.decode("utf-8", errors="replace")
            output_log += decoded
            current_task["output_log"] = output_log
        await proc.wait()

    except FileNotFoundError:
        output_log += "\nERRORE: claude CLI non trovato nel PATH."
        current_task["output_log"] = output_log
    except Exception as e:
        output_log += f"\nERRORE: {e}"
        current_task["output_log"] = output_log

    finally:
        update_task.cancel()
        duration_sec = time.time() - start_time
        modified_files = get_modified_files(WORKSPACE_DIR)
        commit_hash = get_commit_hash(WORKSPACE_DIR)
        msg = format_completion_message(
            duration_sec, modified_files, commit_hash, output_log
        )
        try:
            await tg_app.bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            logger.error(f"[COMPLETION] {e}")
        current_task = None


# ─── TELEGRAM ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTHORIZED_USER_ID
    AUTHORIZED_USER_ID = update.effective_user.id
    await update.message.reply_text("brAIn CTO v1.0 attivo. Inviami un task.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if current_task is not None:
        await update.message.reply_text("Task in corso. Attendere completamento.")
        return
    prompt = update.message.text.strip()
    await update.message.reply_text("Task ricevuto. Inizio esecuzione...")
    asyncio.create_task(run_task(prompt, update.effective_chat.id))


def is_authorized(update: Update) -> bool:
    if AUTHORIZED_USER_ID is None:
        return True
    return update.effective_user.id == AUTHORIZED_USER_ID


async def health_check(request):
    return web.Response(text="brAIn CTO v1.0 OK", status=200)


async def telegram_webhook(request):
    try:
        data = await request.json()
        upd = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(upd)
    except Exception as e:
        logger.error(f"[WEBHOOK] {e}")
    return web.Response(text="OK", status=200)


async def main():
    global tg_app
    logger.info("brAIn CTO v1.0 — Claude Code CLI runner")
    token = os.getenv("TELEGRAM_BOT_TOKEN_CTO") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_app = Application.builder().token(token).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await tg_app.initialize()
    await tg_app.start()
    if WEBHOOK_URL:
        await tg_app.bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook: {WEBHOOK_URL}")
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/", telegram_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Running on :{PORT}")
    try:
        while True:
            await asyncio.sleep(3600)
    except Exception:
        pass
    finally:
        await tg_app.stop()
        await tg_app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
