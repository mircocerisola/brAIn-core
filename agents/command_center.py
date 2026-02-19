"""
brAIn Command Center Agent v1.0
Il primo agente: riceve comandi da Telegram, elabora con Claude, logga in Supabase.
"""

import os
import json
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic
from supabase import create_client
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Carica le chiavi dal file .env
load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Inizializza i client
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ID Telegram di Mirco (si imposta al primo /start)
AUTHORIZED_USER_ID = None

# System prompt del Command Center
SYSTEM_PROMPT = """Sei il Command Center di brAIn, un'organizzazione AI-native.
Il tuo ruolo è assistere Mirco (il fondatore) nella gestione dell'organizzazione.

Rispondi SEMPRE in italiano.
Sii diretto e conciso — zero filler.
Una sola domanda per volta se devi chiedere qualcosa.

Puoi aiutare con:
- Status dell'organizzazione e dei progetti
- Decisioni strategiche
- Analisi e ricerche
- Qualsiasi richiesta operativa

Sei connesso a Supabase (database) e puoi consultare dati quando serve.
"""


def log_to_supabase(agent_id, action, input_summary, output_summary, model_used, tokens_in=0, tokens_out=0, cost=0, duration_ms=0, status="success", error=None):
    """Logga ogni azione in agent_logs"""
    try:
        supabase.table("agent_logs").insert({
            "agent_id": agent_id,
            "action": action,
            "layer": 0,
            "input_summary": input_summary[:500] if input_summary else None,
            "output_summary": output_summary[:500] if output_summary else None,
            "model_used": model_used,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "status": status,
            "error": error,
        }).execute()
    except Exception as e:
        print(f"[LOG ERROR] {e}")


def ask_claude(user_message, model="claude-haiku-4-5-20251001"):
    """Chiama Claude API e ritorna la risposta"""
    start = time.time()
    try:
        response = claude.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        # Stima costo (Haiku: $1/$5 per M token)
        cost = (tokens_in * 1.0 + tokens_out * 5.0) / 1_000_000

        log_to_supabase(
            agent_id="command_center",
            action="chat",
            input_summary=user_message,
            output_summary=reply,
            model_used=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            duration_ms=duration,
        )
        return reply

    except Exception as e:
        duration = int((time.time() - start) * 1000)
        log_to_supabase(
            agent_id="command_center",
            action="chat",
            input_summary=user_message,
            output_summary=None,
            model_used=model,
            duration_ms=duration,
            status="error",
            error=str(e),
        )
        return f"Errore: {e}"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start — registra l'utente autorizzato"""
    global AUTHORIZED_USER_ID
    AUTHORIZED_USER_ID = update.effective_user.id

    # Salva in org_config
    try:
        supabase.table("org_config").upsert({
            "key": "telegram_user_id",
            "value": json.dumps(AUTHORIZED_USER_ID),
            "description": "ID Telegram di Mirco"
        }, on_conflict="key").execute()
    except Exception as e:
        print(f"[CONFIG ERROR] {e}")

    await update.message.reply_text(
        "brAIn Command Center attivo.\n"
        f"Il tuo ID ({AUTHORIZED_USER_ID}) è registrato.\n\n"
        "Comandi:\n"
        "/status — stato dell'organizzazione\n"
        "/help — lista comandi\n\n"
        "Oppure scrivi qualsiasi cosa e rispondo."
    )

    log_to_supabase("command_center", "start", f"user_id={AUTHORIZED_USER_ID}", "Bot avviato", "none")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /status — mostra stato organizzazione"""
    if not is_authorized(update):
        return

    try:
        # Conta records nelle tabelle principali
        problems = supabase.table("problems").select("id", count="exact").execute()
        solutions = supabase.table("solutions").select("id", count="exact").execute()
        projects = supabase.table("projects").select("id", count="exact").execute()
        logs = supabase.table("agent_logs").select("id", count="exact").execute()

        status_text = (
            "📊 brAIn Status\n\n"
            f"🔍 Problemi identificati: {problems.count or 0}\n"
            f"💡 Soluzioni generate: {solutions.count or 0}\n"
            f"🚀 Progetti attivi: {projects.count or 0}\n"
            f"📝 Azioni loggate: {logs.count or 0}\n"
        )
    except Exception as e:
        status_text = f"Errore nel recupero status: {e}"

    await update.message.reply_text(status_text)
    log_to_supabase("command_center", "status", "status request", status_text, "none")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /help"""
    if not is_authorized(update):
        return

    help_text = (
        "🧠 brAIn Command Center\n\n"
        "/start — Registra e attiva il bot\n"
        "/status — Stato dell'organizzazione\n"
        "/help — Questo messaggio\n\n"
        "Scrivi qualsiasi messaggio per parlare con Claude."
    )
    await update.message.reply_text(help_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce messaggi di testo normali"""
    if not is_authorized(update):
        return

    user_message = update.message.text
    await update.message.reply_text("⏳ Elaboro...")

    reply = ask_claude(user_message)
    await update.message.reply_text(reply)


def is_authorized(update: Update) -> bool:
    """Verifica che l'utente sia Mirco"""
    global AUTHORIZED_USER_ID

    # Se non abbiamo ancora un ID, caricalo da Supabase
    if AUTHORIZED_USER_ID is None:
        try:
            result = supabase.table("org_config").select("value").eq("key", "telegram_user_id").execute()
            if result.data:
                AUTHORIZED_USER_ID = json.loads(result.data[0]["value"])
        except:
            pass

    if AUTHORIZED_USER_ID is None:
        return True  # Primo avvio, accetta chiunque faccia /start

    if update.effective_user.id != AUTHORIZED_USER_ID:
        return False

    return True


def main():
    """Avvia il bot"""
    print("🧠 brAIn Command Center avviato...")
    print(f"   Connesso a Supabase: {SUPABASE_URL}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("   Bot Telegram in ascolto...")
    app.run_polling()


if __name__ == "__main__":
    main()