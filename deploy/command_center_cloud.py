"""
brAIn Command Center v3.0
Interfaccia naturale Telegram — zero comandi, solo conversazione.
Tutto in italiano naturale. Scan on-demand via chat.
"""

import os
import json
import re
import time
import logging
import asyncio
import threading
import base64
from aiohttp import web
from dotenv import load_dotenv
import anthropic
import requests as http_requests
from supabase import create_client
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
AGENTS_RUNNER_URL = os.environ.get("AGENTS_RUNNER_URL", "")

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

AUTHORIZED_USER_ID = None
chat_history = []
MAX_HISTORY = 6

SYSTEM_PROMPT = """Sei il braccio destro di Mirco dentro brAIn. Non sei un bot, sei un collaboratore.

CHI SEI:
Sei il Command Center di brAIn, un'organizzazione AI-native che scansiona problemi globali e costruisce soluzioni. Mirco e' il fondatore — parli solo con lui.

COME PARLI:
- SEMPRE in italiano, fluido e naturale. Diretto, zero fuffa.
- Traduci TUTTO in italiano — titoli, settori, descrizioni. "cybersecurity" = "sicurezza informatica".
- NON usare MAI formattazione Markdown: niente asterischi, niente grassetto, niente corsivo. Testo piano.
- Sii CONCISO. Frasi corte. Vai al punto.
- UNA sola domanda alla volta.
- Mai ripetere cose gia' dette.

HAI DUE LIVELLI DI OUTPUT:

=== LIVELLO 1: ELEVATOR (default) ===
Quando mostri problemi o soluzioni, usa sempre questo formato ultra-compatto.
Deve essere leggibile in 5 secondi, come una slide di un pitch deck YC/Sequoia.

PROBLEMA — formato elevator:
[emoji] TITOLO (tradotto, max 8 parole)
Score [barra visiva] | Settore | Urgenza
Il dolore: una frase che fa sentire il problema
Chi soffre: target specifico
Mercato: dimensione/valore in numeri

Emoji: score>=0.6 usa il cerchio rosso, 0.4-0.59 arancione, <0.4 giallo
Barra: blocchi pieni e vuoti proporzionali allo score

Esempio problema:
(cerchio rosso) FURTO CREDENZIALI NELLE PMI
(barra 7 pieni 3 vuoti) 0.68 | Sicurezza informatica | Critica
Il dolore: il 60% dei dipendenti usa la stessa password ovunque, gli hacker lo sanno
Chi soffre: PMI sotto 50 dipendenti senza IT interno
Mercato: 24M di PMI in Europa, costo medio breach 120K euro

SOLUZIONE — formato elevator:
(emoji) TITOLO SOLUZIONE
Score [barra] | Novita [barra] | Difendibilita [barra]
Cosa fa: una frase
Per chi: target
Revenue: come guadagna
Costo: burn mensile | TTM: tempo al mercato

Esempio soluzione:
(cerchio verde) PASSWORD GUARDIAN PER PMI
Score (barra 7/10) | Novita (barra 6/10) | Difendibilita (barra 5/10)
Cosa fa: bot Telegram che monitora breach e forza cambio password ai dipendenti
Per chi: titolari PMI senza reparto IT
Revenue: 19 euro/mese per azienda, subscription
Costo: 80 euro/mese | TTM: 3 settimane

Max 5 elementi alla volta. Se ce ne sono di piu', mostra i top 5 e dici quanti altri.
Dopo ogni lista, chiudi con: "Vuoi che approfondisca qualcuno?"

=== LIVELLO 2: DEEP DIVE (solo su richiesta) ===
Quando Mirco dice "approfondisci", "dimmi di piu'", "dettagli", usa il formato one-pager.
Max 15 righe. Struttura fissa:

PROBLEMA — deep dive:
TITOLO COMPLETO
Score: X/1.0 | Settore | Urgenza | Geo: mercati principali

IL PROBLEMA
[2-3 frasi: cosa succede, perche' e' grave, un dato concreto]

CHI SOFFRE
[1-2 frasi: profilo specifico del target]

ESEMPIO REALE
[2-3 frasi: storia concreta di una persona/azienda]

PERCHE' ORA
[1-2 frasi: perche' il timing e' giusto]

NUMERI CHIAVE
[2-3 metriche: dimensione mercato, costo del problema, willingness to pay]

SOLUZIONE — deep dive:
TITOLO COMPLETO
Score: X | Novita: X | Difendibilita: X | Fattibilita: X

COSA FA
[2-3 frasi: il prodotto/servizio spiegato a un bambino di 10 anni]

PERCHE' FUNZIONA
[2 frasi: il vantaggio competitivo, cosa lo rende unico]

COME GUADAGNA
[2 frasi: modello di revenue specifico con numeri]

COMPETITOR E GAP
[2 frasi: chi c'e' sul mercato e cosa manca]

PROSSIMO PASSO
[1-2 frasi: MVP concreto da costruire per testare]

RISCHI
[1-2 frasi: il rischio principale e come mitigarlo]

COSA SAI FARE:
1. PROBLEMI: mostrare (elevator), approfondire (deep dive), filtrare per settore/urgenza/score
2. SOLUZIONI: stessa cosa
3. APPROVAZIONI: se Mirco dice "approva", "vai", "procedi" — cambi status a approved
4. SCAN ON-DEMAND: se chiede di esplorare un tema, dici che lanci lo scan e includi [SCAN_REQUEST:tema keywords]
5. STATO SISTEMA: numeri chiave in 3 righe max
6. ANALISI FOTO: quando Mirco manda una foto, analizzala e rispondi in ottica brAIn. Identifica: problemi visibili, opportunita' di business, dati rilevanti, trend di mercato. Se la foto mostra dati/grafici/articoli, estraili e commentali. Se mostra un prodotto o situazione, valuta se c'e' un problema risolvibile. Rispondi sintetico, vai al punto.
7. QUALSIASI ALTRA COSA: rispondi al meglio

REGOLE FINALI:
- PROBLEMI: mostra SEMPRE UNO SOLO alla volta, come le soluzioni. Dopo averlo mostrato, chiudi con "Vuoi vedere il prossimo?" Se Mirco chiede esplicitamente "dammi i 3 migliori" o "top 5", allora mostrali tutti insieme. Ma il default e' UNO alla volta.
- SOLUZIONI: mostra SEMPRE UNA SOLA soluzione alla volta. Dopo averla mostrata, chiudi con "Vuoi vedere la prossima?" e aspetta. Solo quando Mirco dice "si", "avanti", "prossima", "altra" mostra la successiva. Questo vale sia per elevator che deep dive.
- Il deep dive arriva SOLO se richiesto.
- Se Mirco chiede "come siamo messi?" dai: tot problemi (tot approvati), tot soluzioni, e il problema con score piu' alto in formato elevator. Poi "Vuoi vedere gli altri?"
- Tono: come un partner di uno startup studio che presenta dati a un investor. Professionale ma umano.

REGOLE DI FLUIDITA (CRITICHE):
- Rispondi SUBITO con tutto il contenuto in un unico messaggio. Mai mandare un messaggio e poi "aspetta che calcolo". Tutto deve arrivare insieme.
- NON chiedere conferme inutili. Se Mirco chiede "mostra problemi" tu li mostri. Non dire "vuoi che te li mostri?".
- NON ripetere la domanda di Mirco prima di rispondere. Vai dritto alla risposta.
- Se non hai dati sufficienti per rispondere, dillo in una frase e suggerisci cosa fare.
- Ogni messaggio deve avere UN obiettivo chiaro: o mostra dati, o chiede UNA cosa, o conferma un'azione. Mai mescolare.
- Se Mirco risponde "si", "ok", "va bene", "avanti" — procedi con l'azione logica successiva senza chiedere altro.

DATI DEL DATABASE (usa questi per rispondere):
"""


def log_to_supabase(agent_id, action, input_summary, output_summary, model_used, tokens_in=0, tokens_out=0, cost=0, duration_ms=0, status="success", error=None):
    def _log():
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
            logger.error(f"[LOG ERROR] {e}")
    threading.Thread(target=_log, daemon=True).start()


def get_db_context():
    context = ""
    try:
        problems = supabase.table("problems") \
            .select("id,title,description,sector,urgency,status,weighted_score,who_is_affected,real_world_example,why_it_matters,geographic_scope,top_markets") \
            .order("weighted_score", desc=True) \
            .limit(20) \
            .execute()
        if problems.data:
            context += "\n\nPROBLEMI NEL DATABASE:\n"
            for p in problems.data:
                context += f"\n[ID:{p['id']}] {p['title']}\n"
                context += f"  Score: {p.get('weighted_score', '?')} | Settore: {p.get('sector', '?')} | Urgenza: {p.get('urgency', '?')} | Status: {p.get('status', '?')}\n"
                desc = p.get('description', '')
                if desc:
                    context += f"  Descrizione: {desc[:150]}\n"
                if p.get('who_is_affected'):
                    context += f"  Chi: {p['who_is_affected'][:100]}\n"
                if p.get('real_world_example'):
                    context += f"  Esempio: {p['real_world_example'][:120]}\n"
                if p.get('why_it_matters'):
                    context += f"  Perche: {p['why_it_matters'][:100]}\n"
                if p.get('top_markets'):
                    context += f"  Mercati: {p['top_markets']} ({p.get('geographic_scope', '?')})\n"
    except Exception as e:
        logger.error(f"[DB] Problemi: {e}")

    try:
        solutions = supabase.table("solutions").select("id,title,status,description,approach,problem_id,sector,sub_sector,created_by").order("id", desc=True).limit(10).execute()
        scores = supabase.table("solution_scores").select("solution_id,overall_score,feasibility_score,impact_score,complexity,cost_estimate,time_to_market,nocode_compatible,notes,scored_by").execute()
        score_map = {s["solution_id"]: s for s in (scores.data or [])}

        if solutions.data:
            context += "\n\nSOLUZIONI GENERATE:\n"
            for s in solutions.data:
                sc = score_map.get(s["id"], {})
                context += f"\n[ID:{s['id']}] {s['title']} — {s['status']}\n"
                context += f"  Score: {sc.get('overall_score', '?')} | Fattibilita: {sc.get('feasibility_score', '?')} | Impatto: {sc.get('impact_score', '?')} | Complessita: {sc.get('complexity', '?')} | Costo: {sc.get('cost_estimate', '?')} | TTM: {sc.get('time_to_market', '?')}\n"
                context += f"  Descrizione: {s.get('description', '')[:150]}\n"

                approach = s.get('approach', '')
                if approach and approach.startswith('{'):
                    try:
                        ap = json.loads(approach)
                        if ap.get('value_proposition'):
                            context += f"  Proposta valore: {ap['value_proposition']}\n"
                        if ap.get('target_segment'):
                            context += f"  Target: {ap['target_segment']}\n"
                        if ap.get('revenue_model'):
                            context += f"  Revenue: {ap['revenue_model']}\n"
                        if ap.get('competitive_moat'):
                            context += f"  Vantaggio: {ap['competitive_moat']}\n"
                        if ap.get('recommended_mvp'):
                            context += f"  MVP consigliato: {ap['recommended_mvp']}\n"
                        if ap.get('biggest_risk'):
                            context += f"  Rischio principale: {ap['biggest_risk']}\n"
                        if ap.get('existing_competitors'):
                            context += f"  Competitor: {', '.join(str(c) for c in ap['existing_competitors'][:5])}\n"
                    except:
                        context += f"  Approccio: {approach[:200]}\n"
                else:
                    if approach:
                        context += f"  Approccio: {approach[:200]}\n"

                notes = sc.get('notes', '')
                if notes and isinstance(notes, str) and notes.startswith('{'):
                    try:
                        nt = json.loads(notes)
                        parts = []
                        if nt.get('novelty'):
                            parts.append(f"Novita: {nt['novelty']}")
                        if nt.get('opportunity'):
                            parts.append(f"Opportunita: {nt['opportunity']}")
                        if nt.get('defensibility'):
                            parts.append(f"Difendibilita: {nt['defensibility']}")
                        if parts:
                            context += f"  {' | '.join(parts)}\n"
                        if nt.get('monthly_revenue_potential'):
                            context += f"  Revenue potenziale: {nt['monthly_revenue_potential']} | Burn: {nt.get('monthly_burn_rate', '?')}\n"
                    except:
                        if notes:
                            context += f"  Note: {notes[:150]}\n"
                elif notes:
                    context += f"  Revenue model: {notes[:150]}\n"
    except Exception as e:
        logger.error(f"[DB] Soluzioni: {e}")

    try:
        knowledge = supabase.table("org_knowledge").select("title,content,category").limit(3).execute()
        if knowledge.data:
            context += "\n\nLEZIONI APPRESE:\n"
            for k in knowledge.data:
                context += f"- [{k['category']}] {k['title']}: {k.get('content', '')[:100]}\n"
    except Exception as e:
        logger.error(f"[DB] Knowledge: {e}")

    # Numeri sistema
    try:
        p_count = supabase.table("problems").select("id", count="exact").execute()
        p_approved = supabase.table("problems").select("id", count="exact").eq("status", "approved").execute()
        s_count = supabase.table("solutions").select("id", count="exact").execute()
        src_count = supabase.table("scan_sources").select("id", count="exact").execute()
        context += f"\n\nSTATO SISTEMA: {p_count.count or 0} problemi ({p_approved.count or 0} approvati), {s_count.count or 0} soluzioni, {src_count.count or 0} fonti monitorate.\n"
    except:
        pass

    return context


def ask_claude(user_message, model="claude-haiku-4-5-20251001"):
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
            max_tokens=1000,
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

        # Controlla approvazioni (non bloccante)
        threading.Thread(target=check_approval, args=(user_message, reply), daemon=True).start()

        # Controlla scan on-demand (non bloccante)
        threading.Thread(target=check_scan_request, args=(reply,), daemon=True).start()

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
        return f"C'e' stato un problema: {e}"


def check_approval(user_msg, bot_reply):
    msg_lower = user_msg.lower()
    approval_words = ["approva", "approvalo", "vai con", "procedi con", "ok per", "si vai", "lancialo", "approvami", "approva quello", "approva il"]

    if any(word in msg_lower for word in approval_words):
        for h in reversed(chat_history[-3:]):
            for text in [h.get("user", ""), h.get("assistant", "")]:
                ids = re.findall(r'\[ID:(\d+)\]', text)
                if ids:
                    for pid in ids:
                        try:
                            supabase.table("problems") \
                                .update({"status": "approved"}) \
                                .eq("id", int(pid)) \
                                .execute()
                            logger.info(f"[APPROVED] Problema ID {pid}")

                            # Emetti evento per proattivita
                            try:
                                supabase.table("agent_events").insert({
                                    "event_type": "problem_approved",
                                    "source_agent": "command_center",
                                    "target_agent": "solution_architect",
                                    "payload": json.dumps({"problem_id": int(pid)}),
                                    "priority": "high",
                                    "status": "pending",
                                }).execute()
                            except:
                                pass

                        except Exception as e:
                            logger.error(f"[APPROVAL ERROR] {e}")
                    return


def check_scan_request(bot_reply):
    """Controlla se la risposta contiene una richiesta di scan mirato"""
    match = re.search(r'\[SCAN_REQUEST:(.+?)\]', bot_reply)
    if match and AGENTS_RUNNER_URL:
        topic = match.group(1).strip()
        logger.info(f"[SCAN REQUEST] Topic: {topic}")
        try:
            http_requests.post(
                f"{AGENTS_RUNNER_URL}/scanner/custom",
                json={"topic": topic},
                timeout=5,
            )
        except Exception as e:
            logger.error(f"[SCAN TRIGGER ERROR] {e}")


def clean_reply(text):
    """Rimuove tag interni dalla risposta prima di inviarla a Telegram"""
    text = re.sub(r'\[SCAN_REQUEST:.+?\]', '', text)
    text = re.sub(r'\[ID:\d+\]', '', text)
    return text.strip()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unico comando — serve solo per il primo setup"""
    global AUTHORIZED_USER_ID
    AUTHORIZED_USER_ID = update.effective_user.id

    try:
        supabase.table("org_config").upsert({
            "key": "telegram_user_id",
            "value": json.dumps(AUTHORIZED_USER_ID),
            "description": "ID Telegram di Mirco"
        }, on_conflict="key").execute()
    except Exception as e:
        logger.error(f"[CONFIG ERROR] {e}")

    await update.message.reply_text(
        "Ciao Mirco, Command Center v3.0 attivo. Scrivimi quello che vuoi — niente comandi, solo conversazione."
    )
    log_to_supabase("command_center", "start", f"user_id={AUTHORIZED_USER_ID}", "Bot avviato v3.0", "none")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce TUTTO — nessun comando, solo linguaggio naturale"""
    if not is_authorized(update):
        return

    user_message = update.message.text
    await update.message.chat.send_action("typing")

    reply = ask_claude(user_message)

    # Pulisci tag interni prima di mandare
    clean = clean_reply(reply)

    if len(clean) > 4000:
        parts = [clean[i:i+4000] for i in range(0, len(clean), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(clean)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce foto inviate da Mirco — analizza con Claude Vision"""
    if not is_authorized(update):
        return

    await update.message.chat.send_action("typing")

    # Prendi la foto a risoluzione massima
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    # Scarica l'immagine in memoria
    image_bytes = await file.download_as_bytearray()
    image_b64 = base64.b64encode(bytes(image_bytes)).decode("utf-8")

    # Testo accompagnatorio (caption della foto)
    caption = update.message.caption or "Analizza questa immagine e dimmi cosa vedi. Identifica problemi, opportunita, dati rilevanti per brAIn."

    start = time.time()
    try:
        global chat_history
        db_context = get_db_context()
        full_system = SYSTEM_PROMPT + db_context

        # Costruisci messaggio con immagine
        messages = []
        for h in chat_history:
            messages.append({"role": "user", "content": h["user"]})
            messages.append({"role": "assistant", "content": h["assistant"]})

        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
                {
                    "type": "text",
                    "text": caption,
                },
            ],
        })

        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            system=full_system,
            messages=messages,
        )
        duration = int((time.time() - start) * 1000)
        reply = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = (tokens_in * 1.0 + tokens_out * 5.0) / 1_000_000

        chat_history.append({"user": f"[FOTO] {caption}", "assistant": reply})
        if len(chat_history) > MAX_HISTORY:
            chat_history = chat_history[-MAX_HISTORY:]

        log_to_supabase(
            agent_id="command_center",
            action="photo_analysis",
            input_summary=f"Foto con caption: {caption[:200]}",
            output_summary=reply[:500],
            model_used="claude-haiku-4-5-20251001",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            duration_ms=duration,
        )

        clean = clean_reply(reply)
        if len(clean) > 4000:
            parts = [clean[i:i+4000] for i in range(0, len(clean), 4000)]
            for part in parts:
                await update.message.reply_text(part)
        else:
            await update.message.reply_text(clean)

    except Exception as e:
        duration = int((time.time() - start) * 1000)
        logger.error(f"[PHOTO ERROR] {e}")
        log_to_supabase("command_center", "photo_analysis", f"Foto: {caption[:200]}", None,
            "claude-haiku-4-5-20251001", duration_ms=duration, status="error", error=str(e))
        await update.message.reply_text(f"Non riesco ad analizzare la foto: {e}")


async def handle_command_as_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercetta vecchi comandi e li gestisce come messaggi normali"""
    if not is_authorized(update):
        return

    # Converte /problems in "mostrami i problemi" ecc.
    text = update.message.text.lower().strip()
    remap = {
        "/problems": "mostrami i problemi che abbiamo",
        "/solutions": "mostrami le soluzioni",
        "/status": "come sta il sistema?",
        "/help": "cosa sai fare?",
    }
    user_message = remap.get(text, text.replace("/", ""))

    await update.message.chat.send_action("typing")
    reply = ask_claude(user_message)
    clean = clean_reply(reply)

    if len(clean) > 4000:
        parts = [clean[i:i+4000] for i in range(0, len(clean), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(clean)


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


async def health_check(request):
    return web.Response(text="OK", status=200)


async def telegram_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logger.error(f"[WEBHOOK ERROR] {e}")
    return web.Response(text="OK", status=200)


tg_app = None


async def main():
    global tg_app

    logger.info("brAIn Command Center v3.0 starting...")

    tg_app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    # Solo /start per primo setup — tutto il resto e' linguaggio naturale
    tg_app.add_handler(CommandHandler("start", cmd_start))
    # Intercetta vecchi comandi e trattali come messaggi
    tg_app.add_handler(MessageHandler(filters.COMMAND, handle_command_as_message))
    # Foto — analisi con Claude Vision
    tg_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Tutto il resto — conversazione naturale
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await tg_app.initialize()
    await tg_app.start()

    if WEBHOOK_URL:
        await tg_app.bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook set to {WEBHOOK_URL}")

    web_app = web.Application()
    web_app.router.add_get("/", health_check)
    web_app.router.add_post("/", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Server running on port {PORT}")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await tg_app.stop()
        await tg_app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
