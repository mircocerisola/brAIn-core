"""
brAIn Command Center Agent v1.4
Bot Telegram con lingua naturale italiana, memoria conversazione,
contesto database completo con dati qualitativi.
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

chat_history = []
MAX_HISTORY = 10

SYSTEM_PROMPT = """Sei l'assistente personale di Mirco, fondatore di brAIn — un'organizzazione AI-native che identifica problemi globali e costruisce soluzioni.

Come parli:
- SEMPRE in italiano, anche se i dati nel database sono in inglese — traduci tutto
- Parla in modo naturale e diretto, come un collega fidato. Niente burocratese, niente elenchi infiniti
- Quando descrivi un problema, racconta chi lo vive, fai capire perche conta, usa gli esempi concreti che hai nei dati
- Se Mirco chiede di approfondire qualcosa, vai nel dettaglio con spiegazioni chiare e concrete
- UNA sola domanda alla volta se devi chiedere qualcosa
- Mai ripetere cose gia dette

Come gestisci i problemi:
- Quando mostri problemi, traduci i titoli in italiano e spiega in modo chiaro cosa significano
- Usa i dati qualitativi (chi e' colpito, esempi reali, perche conta) per rendere tutto concreto e comprensibile
- I punteggi sono utili ma non bastano — quello che conta e' far capire il problema a Mirco
- Se Mirco dice "approva" o "vai" su un problema, cambia il suo status a "approved" nel database

Come gestisci le soluzioni:
- Le soluzioni vengono generate SOLO per i problemi che Mirco approva esplicitamente
- Non suggerire mai di lanciare il Solution Architect su tutti i problemi

Hai accesso ai dati del database di brAIn. Usali per dare risposte informate e concrete.
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
        problems = supabase.table("problems") \
            .select("id,title,description,sector,urgency,status,weighted_score,who_is_affected,real_world_example,why_it_matters,geographic_scope,top_markets") \
            .order("weighted_score", desc=True) \
            .limit(20) \
            .execute()
        if problems.data:
            context += "\n\nPROBLEMI NEL DATABASE (usa questi dati per rispondere in modo completo):\n"
            for p in problems.data:
                context += f"\n[ID:{p['id']}] {p['title']}\n"
                context += f"  Score: {p.get('weighted_score', '?')} | Settore: {p.get('sector', '?')} | Urgenza: {p.get('urgency', '?')} | Status: {p.get('status', '?')}\n"
                context += f"  Descrizione: {p.get('description', '')}\n"
                if p.get('who_is_affected'):
                    context += f"  Chi e' colpito: {p['who_is_affected']}\n"
                if p.get('real_world_example'):
                    context += f"  Esempio reale: {p['real_world_example']}\n"
                if p.get('why_it_matters'):
                    context += f"  Perche conta: {p['why_it_matters']}\n"
                if p.get('top_markets'):
                    context += f"  Mercati: {p['top_markets']} ({p.get('geographic_scope', '?')})\n"
    except:
        pass

    try:
        solutions = supabase.table("solutions").select("id,title,status,description,approach,problem_id").order("id", desc=True).limit(10).execute()
        scores = supabase.table("solution_scores").select("solution_id,overall_score,feasibility_score,impact_score,complexity,cost_estimate,time_to_market,notes").execute()
        score_map = {s["solution_id"]: s for s in (scores.data or [])}

        if solutions.data:
            context += "\n\nSOLUZIONI GENERATE:\n"
            for s in solutions.data:
                sc = score_map.get(s["id"], {})
                context += f"\n[ID:{s['id']}] {s['title']} — {s['status']}\n"
                context += f"  Score: {sc.get('overall_score', '?')} | Costo: {sc.get('cost_estimate', '?')} | TTM: {sc.get('time_to_market', '?')}\n"
                context += f"  Descrizione: {s.get('description', '')}\n"
                context += f"  Approccio: {s.get('approach', '')}\n"
                if sc.get('notes'):
                    context += f"  Revenue model: {sc['notes']}\n"
    except:
        pass

    try:
        knowledge = supabase.table("org_knowledge").select("title,content,category").limit(5).execute()
        if knowledge.data:
            context += "\n\nLEZIONI APPRESE:\n"
            for k in knowledge.data:
                context += f"- [{k['category']}] {k['title']}: {k.get('content', '')[:100]}\n"
    except:
        pass

    return context


def ask_claude(user_message, model="claude-haiku-4-5"):
    global chat_history

    start = time.time()
    try:
        db_context = get_db_context()
        full_system = SYSTEM_PROMPT + db_context

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

        # Controlla se Mirco vuole approvare un problema
        check_approval(user_message, reply)

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


def check_approval(user_msg, bot_reply):
    """Controlla se nel flusso conversazione c'e' un'approvazione di problema"""
    msg_lower = user_msg.lower()
    approval_words = ["approva", "approvalo", "vai con", "procedi con", "ok per", "si vai", "lancialo"]

    if any(word in msg_lower for word in approval_words):
        # Cerca ID problema nel contesto recente
        for h in reversed(chat_history[-3:]):
            for text in [h.get("user", ""), h.get("assistant", "")]:
                # Cerca pattern [ID:X]
                import re
                ids = re.findall(r'\[ID:(\d+)\]', text)
                if ids:
                    for pid in ids:
                        try:
                            supabase.table("problems") \
                                .update({"status": "approved"}) \
                                .eq("id", int(pid)) \
                                .execute()
                            print(f"   [APPROVED] Problema ID {pid}")
                        except:
                            pass
                    return


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
        "Ciao! Command Center brAIn pronto.\n\n"
        "Puoi chiedermi qualsiasi cosa sui problemi, le soluzioni, lo stato del sistema.\n\n"
        "Comandi rapidi:\n"
        "/problems — lista problemi\n"
        "/solutions — lista soluzioni\n"
        "/status — stato sistema\n\n"
        "Oppure scrivi liberamente."
    )
    log_to_supabase("command_center", "start", f"user_id={AUTHORIZED_USER_ID}", "Bot avviato", "none")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    try:
        problems = supabase.table("problems").select("id", count="exact").execute()
        approved = supabase.table("problems").select("id", count="exact").eq("status", "approved").execute()
        solutions = supabase.table("solutions").select("id", count="exact").execute()
        projects = supabase.table("projects").select("id", count="exact").execute()
        sources = supabase.table("scan_sources").select("id", count="exact").execute()
        knowledge = supabase.table("org_knowledge").select("id", count="exact").execute()

        text = (
            f"Problemi trovati: {problems.count or 0} ({approved.count or 0} approvati)\n"
            f"Soluzioni generate: {solutions.count or 0}\n"
            f"Progetti attivi: {projects.count or 0}\n"
            f"Fonti monitorate: {sources.count or 0}\n"
            f"Lezioni apprese: {knowledge.count or 0}"
        )
    except Exception as e:
        text = f"Errore: {e}"

    await update.message.reply_text(text)
    log_to_supabase("command_center", "status", "status", text[:200], "none")


async def cmd_problems(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    try:
        result = supabase.table("problems") \
            .select("id,title,sector,urgency,status,weighted_score") \
            .order("weighted_score", desc=True) \
            .limit(20) \
            .execute()

        if not result.data:
            await update.message.reply_text("Nessun problema trovato. Lancia il World Scanner.")
            return

        text = "Problemi trovati:\n\n"
        for i, p in enumerate(result.data, 1):
            emoji = "🔴" if p["urgency"] == "critical" else "🟠" if p["urgency"] == "high" else "🟡" if p["urgency"] == "medium" else "🟢"
            status_icon = "✅" if p["status"] == "approved" else "⏳"
            score = p.get("weighted_score") or p.get("score", 0)
            text += f"{i}. {emoji} [{score:.2f}] {p['title']}\n   {p['sector']} | {status_icon} {p['status']}\n\n"

        text += "Chiedimi di approfondire qualsiasi problema."

    except Exception as e:
        text = f"Errore: {e}"

    await update.message.reply_text(text)
    log_to_supabase("command_center", "problems", "lista", text[:200], "none")


async def cmd_solutions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    try:
        solutions = supabase.table("solutions").select("id,title,status,problem_id").order("id").execute()
        scores = supabase.table("solution_scores").select("solution_id,overall_score,complexity,cost_estimate,time_to_market").execute()
        problems = supabase.table("problems").select("id,title").execute()

        if not solutions.data:
            await update.message.reply_text("Nessuna soluzione ancora. Approva dei problemi e poi lanceremo il Solution Architect.")
            return

        score_map = {s["solution_id"]: s for s in (scores.data or [])}
        problem_map = {p["id"]: p["title"] for p in (problems.data or [])}

        sorted_sols = sorted(solutions.data, key=lambda s: score_map.get(s["id"], {}).get("overall_score", 0), reverse=True)

        text = "Soluzioni generate:\n\n"
        for i, sol in enumerate(sorted_sols, 1):
            sc = score_map.get(sol["id"], {})
            prob_name = problem_map.get(sol["problem_id"], "?")[:35]
            overall = sc.get("overall_score", 0)
            cost = sc.get("cost_estimate", "?")
            ttm = sc.get("time_to_market", "?")

            text += f"{i}. [{overall:.2f}] {sol['title']}\n   Per: {prob_name}\n   {cost} | {ttm}\n\n"

        text += "Chiedimi dettagli su qualsiasi soluzione."

    except Exception as e:
        text = f"Errore: {e}"

    await update.message.reply_text(text)
    log_to_supabase("command_center", "solutions", "lista", text[:200], "none")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    await update.message.reply_text(
        "Cosa posso fare:\n\n"
        "/problems — tutti i problemi trovati\n"
        "/solutions — le soluzioni generate\n"
        "/status — numeri del sistema\n\n"
        "Oppure scrivi quello che vuoi:\n"
        "- 'parlami del problema 3'\n"
        "- 'quali problemi ci sono nel food?'\n"
        "- 'approva il problema X'\n"
        "- 'qual e' la soluzione migliore?'"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_message = update.message.text
    await update.message.reply_text("Ci penso...")

    reply = ask_claude(user_message)

    # Telegram ha un limite di 4096 caratteri
    if len(reply) > 4000:
        parts = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
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
    print("brAIn Command Center v1.4 avviato...")

    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("problems", cmd_problems))
    app.add_handler(CommandHandler("solutions", cmd_solutions))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("   Bot in ascolto...")
    app.run_polling()


if __name__ == "__main__":
    main()