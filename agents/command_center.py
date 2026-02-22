"""
brAIn Command Center Agent v1.3
Con memoria conversazione e /problems, /solutions.
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

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

AUTHORIZED_USER_ID = None

# Memoria conversazione (ultime 10 coppie messaggio/risposta)
chat_history = []
MAX_HISTORY = 10

SYSTEM_PROMPT = """Sei il Command Center di brAIn, un'organizzazione AI-native.
Il tuo ruolo è assistere Mirco (il fondatore) nella gestione dell'organizzazione.

Rispondi SEMPRE in italiano.
Sii diretto e conciso — zero filler.
Una sola domanda per volta se devi chiedere qualcosa.

Ti vengono forniti dati dal database di brAIn come contesto. Usali per dare risposte informate.
Hai memoria della conversazione corrente — puoi fare riferimento a messaggi precedenti.
"""


def log_to_supabase(agent_id, action, input_summary, output_summary, model_used, tokens_in=0, tokens_out=0, cost=0, duration_ms=0, status="success", error=None):
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


def get_db_context():
    context = ""
    try:
        problems = supabase.table("problems").select("title,score,urgency,status,domain").order("score", desc=True).limit(10).execute()
        if problems.data:
            context += "\n\nPROBLEMI IDENTIFICATI:\n"
            for p in problems.data:
                context += f"- [{p['score']}] {p['title']} ({p['domain']}) - {p['urgency']} - {p['status']}\n"
    except:
        pass

    try:
        solutions = supabase.table("solutions").select("id,title,status,description,approach").order("id", desc=True).limit(10).execute()
        scores = supabase.table("solution_scores").select("solution_id,overall_score,feasibility_score,impact_score,complexity,cost_estimate,time_to_market,notes").execute()
        score_map = {s["solution_id"]: s for s in (scores.data or [])}

        if solutions.data:
            context += "\n\nSOLUZIONI GENERATE:\n"
            for s in solutions.data:
                sc = score_map.get(s["id"], {})
                context += (
                    f"- [{sc.get('overall_score', '?')}] {s['title']} - {s['status']}\n"
                    f"  Descrizione: {s.get('description', '')[:150]}\n"
                    f"  Approach: {s.get('approach', '')[:150]}\n"
                    f"  Costo: {sc.get('cost_estimate', '?')} | TTM: {sc.get('time_to_market', '?')} | Revenue: {sc.get('notes', '?')}\n"
                )
    except:
        pass

    try:
        knowledge = supabase.table("org_knowledge").select("title,category").limit(10).execute()
        if knowledge.data:
            context += "\n\nLEZIONI APPRESE:\n"
            for k in knowledge.data:
                context += f"- [{k['category']}] {k['title']}\n"
    except:
        pass

    return context


def ask_claude(user_message, model="claude-haiku-4-5-20251001"):
    global chat_history

    start = time.time()
    try:
        db_context = get_db_context()
        full_system = SYSTEM_PROMPT + db_context

        # Costruisci messaggi con storia
        messages = []
        for h in chat_history:
            messages.append({"role": "user", "content": h["user"]})
            messages.append({"role": "assistant", "content": h["assistant"]})
        messages.append({"role": "user", "content": user_message})

        response = claude.messages.create(
            model=model,
            max_tokens=1024,
            system=full_system,
            messages=messages,
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = (tokens_in * 1.0 + tokens_out * 5.0) / 1_000_000

        # Salva in storia
        chat_history.append({"user": user_message, "assistant": reply})
        if len(chat_history) > MAX_HISTORY:
            chat_history = chat_history[-MAX_HISTORY:]

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
    global AUTHORIZED_USER_ID
    AUTHORIZED_USER_ID = update.effective_user.id

    try:
        supabase.table("org_config").upsert({
            "key": "telegram_user_id",
            "value": json.dumps(AUTHORIZED_USER_ID),
            "description": "ID Telegram di Mirco"
        }, on_conflict="key").execute()
    except Exception as e:
        print(f"[CONFIG ERROR] {e}")

    await update.message.reply_text(
        "brAIn Command Center v1.3 attivo.\n\n"
        "Comandi:\n"
        "/status — stato organizzazione\n"
        "/problems — problemi identificati\n"
        "/solutions — soluzioni generate\n"
        "/help — lista comandi\n\n"
        "Oppure scrivi qualsiasi cosa."
    )
    log_to_supabase("command_center", "start", f"user_id={AUTHORIZED_USER_ID}", "Bot avviato", "none")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    try:
        problems = supabase.table("problems").select("id", count="exact").execute()
        solutions = supabase.table("solutions").select("id", count="exact").execute()
        projects = supabase.table("projects").select("id", count="exact").execute()
        logs = supabase.table("agent_logs").select("id", count="exact").execute()
        knowledge = supabase.table("org_knowledge").select("id", count="exact").execute()
        capabilities = supabase.table("capability_log").select("id", count="exact").execute()

        status_text = (
            "brAIn Status\n\n"
            f"Problemi identificati: {problems.count or 0}\n"
            f"Soluzioni generate: {solutions.count or 0}\n"
            f"Progetti attivi: {projects.count or 0}\n"
            f"Lezioni apprese: {knowledge.count or 0}\n"
            f"Tool scoperti: {capabilities.count or 0}\n"
            f"Azioni loggate: {logs.count or 0}\n"
        )
    except Exception as e:
        status_text = f"Errore nel recupero status: {e}"

    await update.message.reply_text(status_text)
    log_to_supabase("command_center", "status", "status request", status_text, "none")


async def cmd_problems(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    try:
        result = supabase.table("problems").select("title,score,urgency,domain,status").order("score", desc=True).limit(10).execute()

        if not result.data:
            await update.message.reply_text("Nessun problema identificato.")
            return

        text = "Problemi identificati:\n\n"
        for i, p in enumerate(result.data, 1):
            emoji = "🔴" if p["urgency"] == "critical" else "🟠" if p["urgency"] == "high" else "🟡" if p["urgency"] == "medium" else "🟢"
            text += f"{i}. {emoji} [{p['score']}] {p['title']}\n   {p['domain']} | {p['status']}\n\n"

    except Exception as e:
        text = f"Errore: {e}"

    await update.message.reply_text(text)
    log_to_supabase("command_center", "problems", "lista problemi", text[:200], "none")


async def cmd_solutions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    try:
        solutions = supabase.table("solutions").select("id,title,status,problem_id").order("id").execute()
        scores = supabase.table("solution_scores").select("solution_id,overall_score,feasibility_score,impact_score,complexity,cost_estimate,time_to_market").execute()
        problems = supabase.table("problems").select("id,title").execute()

        if not solutions.data:
            await update.message.reply_text("Nessuna soluzione generata.")
            return

        score_map = {s["solution_id"]: s for s in (scores.data or [])}
        problem_map = {p["id"]: p["title"] for p in (problems.data or [])}

        sorted_sols = sorted(solutions.data, key=lambda s: score_map.get(s["id"], {}).get("overall_score", 0), reverse=True)

        text = "Soluzioni (per score):\n\n"
        for i, sol in enumerate(sorted_sols, 1):
            sc = score_map.get(sol["id"], {})
            prob_name = problem_map.get(sol["problem_id"], "?")[:30]
            overall = sc.get("overall_score", 0)
            complexity = sc.get("complexity", "?")
            cost = sc.get("cost_estimate", "?")
            ttm = sc.get("time_to_market", "?")

            text += (
                f"{i}. [{overall:.2f}] {sol['title']}\n"
                f"   {prob_name}\n"
                f"   {complexity} | {cost} | {ttm}\n\n"
            )

    except Exception as e:
        text = f"Errore: {e}"

    await update.message.reply_text(text)
    log_to_supabase("command_center", "solutions", "lista soluzioni", text[:200], "none")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    help_text = (
        "brAIn Command Center v1.3\n\n"
        "/start — Registra e attiva\n"
        "/status — Stato organizzazione\n"
        "/problems — Problemi identificati\n"
        "/solutions — Soluzioni generate\n"
        "/help — Questo messaggio\n\n"
        "Chat libera con memoria della conversazione."
    )
    await update.message.reply_text(help_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_message = update.message.text
    await update.message.reply_text("Elaboro...")

    reply = ask_claude(user_message)
    await update.message.reply_text(reply)


def is_authorized(update: Update) -> bool:
    global AUTHORIZED_USER_ID

    if AUTHORIZED_USER_ID is None:
        try:
            result = supabase.table("org_config").select("value").eq("key", "telegram_user_id").execute()
            if result.data:
                AUTHORIZED_USER_ID = json.loads(result.data[0]["value"])
        except:
            pass

    if AUTHORIZED_USER_ID is None:
        return True

    if update.effective_user.id != AUTHORIZED_USER_ID:
        return False

    return True


def main():
    print("brAIn Command Center v1.3 avviato...")
    print(f"   Connesso a Supabase: {os.getenv('SUPABASE_URL')}")

    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("problems", cmd_problems))
    app.add_handler(CommandHandler("solutions", cmd_solutions))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("   Bot Telegram in ascolto...")
    app.run_polling()


if __name__ == "__main__":
    main()