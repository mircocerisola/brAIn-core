"""
brAIn CTO Agent v1.1
Esegue task di codice via Claude Code CLI.
Completamento compatto — dettagli solo in allegato .txt.
"""

import os
import asyncio
import subprocess
import time
import logging
import io
from aiohttp import web
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/workspace")

AUTHORIZED_USER_ID = None
tg_app = None
current_task = None  # {"prompt", "start_time", "output_log", "chat_id"}

# Storico dettagli per bottone Dettaglio/Log parziale
completed_tasks: dict = {}  # task_id -> {prompt, output_log, commit_hash, modified_files}
partial_logs: dict = {}     # update_id -> log parziale
_task_counter = 0


# ─── FORMAT HELPERS ──────────────────────────────────────────────────────────

def strip_prompt_from_output(output_log: str) -> str:
    """Skippa righe iniziali che cominciano con 'Esegui con'."""
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
    """File modificati dall'ultimo commit."""
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


def extract_task_title(prompt: str) -> str:
    """Prima riga non vuota del prompt, max 40 char."""
    for line in prompt.split("\n"):
        if line.strip():
            title = line.strip()
            return title[:40] + ("…" if len(title) > 40 else "")
    return "task"


def build_detail_txt(
    prompt: str,
    output_log: str,
    commit_hash: str,
    modified_files: list,
) -> str:
    """Contenuto del file .txt allegato per il bottone Dettaglio."""
    files_str = "\n".join(modified_files) if modified_files else "nessuno"
    return (
        "=== PROMPT ===\n"
        f"{prompt}\n\n"
        "=== OUTPUT COMPLETO ===\n"
        f"{output_log}\n\n"
        "=== COMMIT ===\n"
        f"{commit_hash or 'nessuno'}\n\n"
        "=== FILE MODIFICATI ===\n"
        f"{files_str}\n"
    )


def format_completion_message(
    task_id: str,
    duration_sec: float,
    commit_hash: str,
    prompt: str,
) -> tuple:
    """Messaggio compatto ≤6 righe inline + tastiera con Dettaglio e Nuovo task."""
    duration_min = max(1, round(duration_sec / 60))
    commit_str = commit_hash if commit_hash else "nessuno"
    title = extract_task_title(prompt)

    text = (
        f"✅ Completato — {title}\n"
        "───────────\n"
        f"⏱ Durata: {duration_min} min\n"
        f"🔀 Commit: {commit_str}\n"
        "───────────"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 Dettaglio", callback_data=f"detail_{task_id}"),
            InlineKeyboardButton("🔁 Nuovo task", callback_data="new_task"),
        ]
    ])
    return text, keyboard


def format_update_message(update_id: str, output_log: str) -> tuple:
    """Aggiornamento intermedio: max 4 righe inline + bottone Log parziale."""
    clean = strip_prompt_from_output(output_log)
    last_2 = get_last_n_lines(clean, 2)
    if last_2:
        text = f"⏳ In esecuzione...\n{last_2}"
    else:
        text = "⏳ In esecuzione — attendere output"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Log parziale", callback_data=f"log_{update_id}")]
    ])
    return text, keyboard


# ─── CALLBACK HANDLER ────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("detail_"):
        task_id = data[len("detail_"):]
        task = completed_tasks.get(task_id)
        if not task:
            await query.message.reply_text("Dettaglio non più disponibile.")
            return
        txt = build_detail_txt(
            task["prompt"],
            task["output_log"],
            task["commit_hash"],
            task["modified_files"],
        )
        bio = io.BytesIO(txt.encode("utf-8"))
        bio.name = f"detail_{task_id}.txt"
        await query.message.reply_document(document=bio, filename=f"detail_{task_id}.txt")

    elif data.startswith("log_"):
        update_id = data[len("log_"):]
        log = partial_logs.get(update_id, "")
        if not log:
            await query.message.reply_text("Log non disponibile.")
            return
        bio = io.BytesIO(log.encode("utf-8"))
        bio.name = f"log_{update_id}.txt"
        await query.message.reply_document(document=bio, filename=f"log_{update_id}.txt")

    elif data == "new_task":
        await query.message.reply_text("Pronto. Invia il prossimo task.")


# ─── TASK EXECUTION ──────────────────────────────────────────────────────────

async def run_task(prompt: str, chat_id: int):
    global current_task, _task_counter

    _task_counter += 1
    task_id = str(_task_counter)
    start_time = time.time()
    output_log = ""
    _update_counter = 0

    current_task = {
        "prompt": prompt,
        "start_time": start_time,
        "output_log": "",
        "chat_id": chat_id,
    }

    async def send_periodic_updates():
        nonlocal _update_counter
        while current_task is not None:
            await asyncio.sleep(300)  # 5 minuti
            if current_task is not None:
                _update_counter += 1
                uid = f"{task_id}_u{_update_counter}"
                partial_logs[uid] = current_task["output_log"]
                text, keyboard = format_update_message(uid, current_task["output_log"])
                try:
                    await tg_app.bot.send_message(
                        chat_id=chat_id, text=text, reply_markup=keyboard
                    )
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

        # Salva per bottone Dettaglio
        completed_tasks[task_id] = {
            "prompt": prompt,
            "output_log": output_log,
            "commit_hash": commit_hash,
            "modified_files": modified_files,
        }

        text, keyboard = format_completion_message(task_id, duration_sec, commit_hash, prompt)
        try:
            await tg_app.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"[COMPLETION] {e}")
        current_task = None


# ─── TELEGRAM ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTHORIZED_USER_ID
    AUTHORIZED_USER_ID = update.effective_user.id
    await update.message.reply_text("brAIn CTO v1.1 attivo. Inviami un task.")


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
    return web.Response(text="brAIn CTO v1.1 OK", status=200)


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
    logger.info("brAIn CTO v1.1 — Claude Code CLI runner")

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/", telegram_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Running on :{PORT}")

    token = os.getenv("TELEGRAM_BOT_TOKEN_CTO") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if token:
        try:
            tg_app = Application.builder().token(token).build()
            tg_app.add_handler(CommandHandler("start", cmd_start))
            tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            tg_app.add_handler(CallbackQueryHandler(handle_callback))
            await tg_app.initialize()
            await tg_app.start()
            if WEBHOOK_URL:
                await tg_app.bot.set_webhook(url=WEBHOOK_URL)
                logger.info(f"Webhook: {WEBHOOK_URL}")
            logger.info("Telegram OK")
        except Exception as e:
            logger.warning(f"Telegram non disponibile: {e}")
            tg_app = None
    else:
        logger.warning("TELEGRAM_BOT_TOKEN non impostato — solo HTTP health check attivo")

    try:
        while True:
            await asyncio.sleep(3600)
    except Exception:
        pass
    finally:
        if tg_app:
            await tg_app.stop()
            await tg_app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
