"""CPeO — Chief People & Evolution Officer. Dominio: team, manager, coaching Chief, knowledge base.
v5.25: create_training_plan, daily_gap_analysis, handle_training_request.
"""
from __future__ import annotations
import json
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from core.base_chief import BaseChief, CHIEF_ICONS
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome
from core.utils import search_perplexity

# Chief ID → nome completo per riferimento
_CHIEF_NAMES = {
    "cso": "CSO (Chief Strategy Officer)",
    "coo": "COO (Chief Operations Officer)",
    "cto": "CTO (Chief Technology Officer)",
    "cmo": "CMO (Chief Marketing Officer)",
    "cfo": "CFO (Chief Finance Officer)",
    "clo": "CLO (Chief Legal Officer)",
    "cpeo": "CPeO (Chief People & Evolution Officer)",
}

_ALL_CHIEF_IDS = list(_CHIEF_NAMES.keys())


class CPeO(BaseChief):
    name = "CPeO"
    domain = "people"
    chief_id = "cpeo"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il CPeO di brAIn — Chief People & Evolution Officer. "
        "Genera un briefing settimanale includendo: "
        "1) Manager di cantiere attivi e loro progetti, "
        "2) Revenue share distribuito o in accumulazione, "
        "3) Performance Chief (routing errati, prompt bloccati, errori ricorrenti), "
        "4) Nuovi collaboratori onboardati, "
        "5) Learning aggiunti a chief_knowledge questa settimana, "
        "6) Raccomandazioni coaching e azioni prioritarie."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            r = supabase.table("project_members").select(
                "telegram_username,role,project_id,active,added_at"
            ).eq("active", True).execute()
            ctx["active_managers"] = r.data or []
        except Exception:
            ctx["active_managers"] = []
        try:
            r = supabase.table("manager_revenue_share").select(
                "manager_username,share_pct,project_id,active"
            ).eq("active", True).execute()
            ctx["revenue_shares"] = r.data or []
        except Exception:
            ctx["revenue_shares"] = []
        # Chief knowledge growth ultima settimana
        try:
            week_ago = (now_rome() - timedelta(days=7)).isoformat()
            r = supabase.table("chief_knowledge").select("chief_id,knowledge_type") \
                .gte("created_at", week_ago).execute()
            growth: Dict[str, int] = {}
            for row in (r.data or []):
                cid = row.get("chief_id", "?")
                growth[cid] = growth.get(cid, 0) + 1
            ctx["knowledge_growth_7d"] = growth
        except Exception:
            ctx["knowledge_growth_7d"] = {}
        return ctx


# ============================================================
# HELPER TELEGRAM — topic #people
# ============================================================

def _get_people_topic():
    """Ritorna (group_id, topic_id) per il topic #people."""
    try:
        r = supabase.table("org_config").select("value").eq("key", "chief_topic_cpeo").execute()
        topic_id = int(r.data[0]["value"]) if r.data else None
        r2 = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
        group_id = int(r2.data[0]["value"]) if r2.data else None
        return group_id, topic_id
    except Exception as e:
        logger.warning("[CPeO] topic lookup: %s", e)
        return None, None


def _send_people(text, reply_markup=None):
    """Invia messaggio nel topic #people."""
    group_id, topic_id = _get_people_topic()
    if not group_id or not topic_id or not TELEGRAM_BOT_TOKEN:
        return
    import requests as _req
    payload = {"chat_id": group_id, "message_thread_id": topic_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        _req.post(
            "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
            json=payload, timeout=10,
        )
    except Exception as e:
        logger.warning("[CPeO] Telegram send: %s", e)


# ============================================================
# TASK 1: create_training_plan(chief_name, topic)
# ============================================================

def create_training_plan(chief_name: str, topic: str) -> Dict[str, Any]:
    """
    Crea piano di formazione per un Chief su un topic specifico.
    Phase A: ricerca Perplexity (universita, esperti, aziende).
    Phase B: genera TRAINING-PLAN-{CHIEF}-{TOPIC}-{date}.md, salva in Supabase + Drive.
    Phase C: card Telegram in #people con 4 bottoni.
    """
    start = now_rome()
    chief_id = chief_name.lower().replace(" ", "")
    date_str = start.strftime("%Y-%m-%d")
    topic_slug = re.sub(r'[^a-zA-Z0-9]+', '-', topic.lower()).strip('-')[:30]
    filename = "TRAINING-PLAN-" + chief_id.upper() + "-" + topic_slug.upper() + "-" + date_str + ".md"

    logger.info("[CPeO] create_training_plan: %s / %s", chief_id, topic)

    # ── Phase A: Perplexity research ──
    sources: List[Dict[str, str]] = []

    # 1. Universita
    uni_query = (
        topic + " best practices frameworks taught at "
        "Wharton Harvard Kellogg INSEAD Bocconi university courses curriculum"
    )
    uni_result = search_perplexity(uni_query, max_tokens=1500)
    if uni_result:
        sources.append({"type": "universities", "content": uni_result})
    time.sleep(1)

    # 2. Esperti
    experts_query = (
        topic + " expert insights methodology from "
        "Seth Godin Byron Sharp Rory Sutherland Mark Ritson Philip Kotler "
        "thought leaders best frameworks"
    )
    exp_result = search_perplexity(experts_query, max_tokens=1500)
    if exp_result:
        sources.append({"type": "experts", "content": exp_result})
    time.sleep(1)

    # 3. Aziende
    companies_query = (
        topic + " real world case studies examples from "
        "Apple Patagonia Duolingo Notion Airbnb "
        "successful strategies and results"
    )
    comp_result = search_perplexity(companies_query, max_tokens=1500)
    if comp_result:
        sources.append({"type": "companies", "content": comp_result})

    if not sources:
        logger.warning("[CPeO] Perplexity non ha restituito risultati per %s", topic)
        return {"status": "error", "error": "nessun risultato Perplexity"}

    # ── Phase B: Genera piano con Claude ──
    research_text = ""
    for s in sources:
        research_text += "\n\n--- " + s["type"].upper() + " ---\n" + s["content"]

    chief_full = _CHIEF_NAMES.get(chief_id, chief_id.upper())
    plan_prompt = (
        "Sei il CPeO di brAIn. Genera un piano di formazione dettagliato per il "
        + chief_full + " sul tema: " + topic + ".\n\n"
        "Ricerca raccolta:\n" + research_text + "\n\n"
        "Il piano deve includere:\n"
        "1. OBIETTIVO: cosa il Chief deve imparare e perche\n"
        "2. FONTI CHIAVE: universita, esperti, aziende rilevanti (con riferimenti specifici)\n"
        "3. MODULI (3-5 moduli): titolo + contenuto sintetico + esercizio pratico\n"
        "4. APPLICAZIONE: come applicare nel contesto brAIn\n"
        "5. KPI APPRENDIMENTO: come misurare se il Chief ha imparato\n\n"
        "Formato: markdown pulito, senza bold (**), senza separatori (---). "
        "Tono: diretto, pratico, zero fuffa. Italiano."
    )

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": plan_prompt}],
        )
        plan_md = resp.content[0].text.strip()
    except Exception as e:
        logger.warning("[CPeO] Claude plan generation: %s", e)
        plan_md = "# Piano " + topic + " per " + chief_id.upper() + "\n\nGenerazione fallita: " + str(e)

    # Prependi header al piano
    full_md = (
        "# " + filename.replace(".md", "").replace("-", " ") + "\n"
        "Generato: " + start.strftime("%Y-%m-%d %H:%M") + " CET\n"
        "Chief: " + chief_full + "\n"
        "Topic: " + topic + "\n\n"
        + plan_md
    )

    # Salva in Supabase training_plans
    plan_id = None
    try:
        result = supabase.table("training_plans").insert({
            "chief_name": chief_id,
            "topic": topic,
            "plan_md": full_md,
            "sources_json": json.dumps([{"type": s["type"], "length": len(s["content"])} for s in sources]),
            "status": "draft",
            "created_at": start.isoformat(),
            "updated_at": start.isoformat(),
        }).execute()
        if result.data:
            plan_id = result.data[0].get("id")
        logger.info("[CPeO] Training plan salvato in DB: id=%s", plan_id)
    except Exception as e:
        logger.warning("[CPeO] training_plans insert: %s", e)

    # Drive upload (brAIn/Training/)
    drive_url = _upload_training_to_drive(filename, full_md)
    if drive_url and plan_id:
        try:
            supabase.table("training_plans").update({
                "drive_url": drive_url,
            }).eq("id", plan_id).execute()
        except Exception:
            pass

    # ── Phase C: Card Telegram ──
    card_text = (
        "\U0001f331 CPeO\n"
        "Training Plan " + chief_id.upper() + "\n\n"
        "Topic: " + topic + "\n"
        "Fonti: " + str(len(sources)) + " ricerche Perplexity\n"
        "Moduli: vedi piano completo"
    )
    markup = None
    if plan_id:
        markup = {"inline_keyboard": [
            [
                {"text": "\u2705 Approva ed esegui", "callback_data": "training_approve:" + str(plan_id)},
                {"text": "\U0001f4c4 Vedi piano", "callback_data": "training_view:" + str(plan_id)},
            ],
            [
                {"text": "\U0001f4dd Feedback fonti", "callback_data": "training_feedback:" + str(plan_id)},
                {"text": "\u274c Annulla", "callback_data": "training_cancel:" + str(plan_id)},
            ],
        ]}
    _send_people(card_text, reply_markup=markup)

    logger.info("[CPeO] Training plan creato: %s (plan_id=%s)", filename, plan_id)
    return {
        "status": "ok",
        "plan_id": plan_id,
        "chief_name": chief_id,
        "topic": topic,
        "filename": filename,
        "drive_url": drive_url or "",
        "sources_count": len(sources),
    }


def _upload_training_to_drive(filename, content):
    """Carica training plan su Google Drive in brAIn/Training/."""
    import os
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_json:
        logger.warning("[CPeO] GOOGLE_SERVICE_ACCOUNT_JSON non configurato, skip Drive")
        return ""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaInMemoryUpload

        creds_dict = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/drive"]
        )
        service = build("drive", "v3", credentials=creds)
    except Exception as e:
        logger.warning("[CPeO] Drive auth error: %s", e)
        return ""

    # Cerca/crea cartella brAIn
    brain_folder_id = _drive_find_or_create(service, "brAIn", None)
    if not brain_folder_id:
        return ""
    # Sotto-cartella Training
    training_folder_id = _drive_find_or_create(service, "Training", brain_folder_id)
    if not training_folder_id:
        return ""

    try:
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/markdown")
        uploaded = service.files().create(
            body={"name": filename, "parents": [training_folder_id], "mimeType": "text/markdown"},
            media_body=media, fields="id,webViewLink",
        ).execute()
        url = uploaded.get("webViewLink", "")
        logger.info("[CPeO] Drive upload: %s -> %s", filename, url)
        return url
    except Exception as e:
        logger.warning("[CPeO] Drive upload error: %s", e)
        return ""


def _drive_find_or_create(service, name, parent_id):
    """Cerca o crea cartella Drive."""
    try:
        q = "name='" + name + "' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            q += " and '" + parent_id + "' in parents"
        results = service.files().list(q=q, fields="files(id)").execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]
    except Exception:
        pass
    try:
        meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            meta["parents"] = [parent_id]
        folder = service.files().create(body=meta, fields="id").execute()
        return folder["id"]
    except Exception as e:
        logger.warning("[CPeO] Drive folder create error: %s", e)
        return None


# ============================================================
# CALLBACK HANDLERS per card training
# ============================================================

def handle_training_approve(plan_id: int) -> Dict[str, Any]:
    """Approva piano: salva knowledge chunks in chief_knowledge."""
    try:
        r = supabase.table("training_plans").select("chief_name,topic,plan_md") \
            .eq("id", plan_id).execute()
        if not r.data:
            return {"error": "piano non trovato"}
        plan = r.data[0]
    except Exception as e:
        return {"error": str(e)}

    chief_id = plan.get("chief_name", "")
    topic = plan.get("topic", "")
    plan_md = plan.get("plan_md", "")

    # Genera knowledge chunks con Haiku
    chunk_prompt = (
        "Sei il CPeO di brAIn. Hai un piano di formazione per il " + chief_id.upper() + ".\n"
        "Estrai da questo piano 3-5 learning concreti e azionabili.\n"
        "Ogni learning deve essere una frase diretta che il Chief puo applicare subito.\n"
        "Formato: una lista numerata 1. 2. 3. ecc.\n\n"
        "Piano:\n" + plan_md[:3000]
    )
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": chunk_prompt}],
        )
        chunks_text = resp.content[0].text.strip()
    except Exception as e:
        logger.warning("[CPeO] chunk generation: %s", e)
        chunks_text = "1. Studia " + topic + " per migliorare le competenze."

    # Salva ogni chunk in chief_knowledge
    now = now_rome()
    added = 0
    for line in chunks_text.split("\n"):
        line = re.sub(r'^\d+[\.\)]\s*', '', line).strip()
        if not line or len(line) < 10:
            continue
        try:
            supabase.table("chief_knowledge").insert({
                "chief_id": chief_id,
                "knowledge_type": "training",
                "title": "Training " + topic + " #" + str(added + 1),
                "content": line,
                "importance": 5,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).execute()
            added += 1
        except Exception as e:
            logger.warning("[CPeO] insert knowledge chunk: %s", e)

    # Aggiorna status piano
    try:
        supabase.table("training_plans").update({
            "status": "approved",
            "updated_at": now.isoformat(),
        }).eq("id", plan_id).execute()
    except Exception:
        pass

    _send_people(
        "\U0001f331 CPeO\n"
        "Training approvato\n\n"
        "Chief: " + chief_id.upper() + "\n"
        "Topic: " + topic + "\n"
        "Knowledge aggiunti: " + str(added)
    )
    logger.info("[CPeO] Training approved plan_id=%d, chunks=%d", plan_id, added)
    return {"status": "ok", "plan_id": plan_id, "knowledge_added": added}


def handle_training_view(plan_id: int) -> str:
    """Ritorna testo completo del piano per visualizzazione."""
    try:
        r = supabase.table("training_plans").select("plan_md,chief_name,topic") \
            .eq("id", plan_id).execute()
        if not r.data:
            return "Piano non trovato."
        plan = r.data[0]
        text = plan.get("plan_md", "")
        if len(text) > 4000:
            text = text[:4000] + "\n\n[...troncato, vedi Drive per versione completa]"
        return text
    except Exception as e:
        return "Errore: " + str(e)


def handle_training_cancel(plan_id: int) -> Dict[str, Any]:
    """Annulla piano: status=cancelled."""
    try:
        supabase.table("training_plans").update({
            "status": "cancelled",
            "updated_at": now_rome().isoformat(),
        }).eq("id", plan_id).execute()
        logger.info("[CPeO] Training cancelled plan_id=%d", plan_id)
        return {"status": "cancelled", "plan_id": plan_id}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# TASK 2: daily_gap_analysis()
# ============================================================

def daily_gap_analysis() -> Dict[str, Any]:
    """
    Gap analysis giornaliera su tutti i 7 Chief (incluso CPeO).
    Analizza: org_knowledge, chief_knowledge, agent_logs, training_plans, capability_log.
    gap_score 0-1. Se >= 0.3, chiama create_training_plan().
    """
    start = now_rome()
    week_ago = (start - timedelta(days=7)).isoformat()
    logger.info("[CPeO] Avvio daily_gap_analysis")

    results_list: List[Dict[str, Any]] = []
    training_proposed = 0

    for chief_id in _ALL_CHIEF_IDS:
        gap_score = 0.0
        gap_topics: List[str] = []
        sources_checked: Dict[str, Any] = {}

        # 1. Errori ultimi 7 giorni (peso 0.3)
        error_count = 0
        try:
            r = supabase.table("agent_logs").select("id") \
                .like("agent_id", "%" + chief_id + "%") \
                .eq("status", "error") \
                .gte("created_at", week_ago).execute()
            error_count = len(r.data or [])
            sources_checked["agent_logs_errors"] = error_count
        except Exception:
            pass
        error_score = min(error_count / 10.0, 1.0) * 0.3

        # 2. Routing fuori dominio (peso 0.25)
        routing_count = 0
        try:
            chief_domain_map = {
                "cso": "strategy", "coo": "ops", "cto": "tech",
                "cmo": "marketing", "cfo": "finance", "clo": "legal", "cpeo": "people",
            }
            domain = chief_domain_map.get(chief_id, "")
            r = supabase.table("chief_decisions").select("id") \
                .eq("chief_domain", domain) \
                .like("decision_type", "routed_to_%") \
                .gte("created_at", week_ago).execute()
            routing_count = len(r.data or [])
            sources_checked["routing_misses"] = routing_count
        except Exception:
            pass
        routing_score = min(routing_count / 5.0, 1.0) * 0.25

        # 3. Knowledge growth (peso 0.2) — inverso: meno knowledge = piu gap
        knowledge_count = 0
        try:
            r = supabase.table("chief_knowledge").select("id") \
                .eq("chief_id", chief_id) \
                .gte("created_at", week_ago).execute()
            knowledge_count = len(r.data or [])
            sources_checked["knowledge_added_7d"] = knowledge_count
        except Exception:
            pass
        knowledge_score = max(0, (1.0 - knowledge_count / 5.0)) * 0.2

        # 4. Training plans completati (peso 0.15) — inverso
        training_count = 0
        try:
            r = supabase.table("training_plans").select("id") \
                .eq("chief_name", chief_id) \
                .eq("status", "approved").execute()
            training_count = len(r.data or [])
            sources_checked["training_approved"] = training_count
        except Exception:
            pass
        training_score = max(0, (1.0 - training_count / 3.0)) * 0.15

        # 5. Capability gap (peso 0.1) — nuove capability non integrate
        cap_count = 0
        try:
            r = supabase.table("capability_log").select("id") \
                .gte("discovered_at", week_ago).execute()
            cap_count = len(r.data or [])
            sources_checked["new_capabilities"] = cap_count
        except Exception:
            pass
        cap_score = min(cap_count / 10.0, 1.0) * 0.1

        gap_score = round(error_score + routing_score + knowledge_score + training_score + cap_score, 2)

        # Identifica topic del gap
        if error_count >= 3:
            gap_topics.append("error_handling")
        if routing_count >= 2:
            gap_topics.append("domain_boundaries")
        if knowledge_count == 0:
            gap_topics.append("knowledge_stagnation")

        # Salva in gap_analysis_log
        gap_entry = {
            "chief_name": chief_id,
            "gap_score": gap_score,
            "gap_topics": json.dumps(gap_topics),
            "sources_checked": json.dumps(sources_checked),
            "training_proposed": gap_score >= 0.3,
            "created_at": start.isoformat(),
        }

        plan_id = None
        if gap_score >= 0.3 and gap_topics:
            # Proponi training
            topic_for_training = gap_topics[0].replace("_", " ")
            try:
                plan_result = create_training_plan(chief_id, topic_for_training)
                plan_id = plan_result.get("plan_id")
                training_proposed += 1
            except Exception as e:
                logger.warning("[CPeO] auto training %s: %s", chief_id, e)

        if plan_id:
            gap_entry["training_plan_id"] = plan_id

        try:
            supabase.table("gap_analysis_log").insert(gap_entry).execute()
        except Exception as e:
            logger.warning("[CPeO] gap_analysis_log insert: %s", e)

        results_list.append({
            "chief_id": chief_id,
            "gap_score": gap_score,
            "gap_topics": gap_topics,
            "training_proposed": gap_score >= 0.3,
        })

    # Report sintetico in #people
    report_lines = []
    for r in results_list:
        icon = "\u2705" if r["gap_score"] < 0.3 else "\u26a0\ufe0f"
        score_str = str(r["gap_score"])
        topics_str = ", ".join(r["gap_topics"]) if r["gap_topics"] else "ok"
        report_lines.append(icon + " " + r["chief_id"].upper() + " " + score_str + " " + topics_str)

    report = (
        "\U0001f331 CPeO\n"
        "Gap Analysis Giornaliera\n\n"
        + "\n".join(report_lines)
        + "\n\nTraining proposti: " + str(training_proposed)
    )
    _send_people(report)
    logger.info("[CPeO] gap_analysis completata: %d chief, %d training proposti", len(results_list), training_proposed)
    return {"status": "ok", "results": results_list, "training_proposed": training_proposed}


# ============================================================
# TASK 3: handle_training_request(message, chief_names)
# ============================================================

def handle_training_request(message: str, chief_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Training on-demand su richiesta diretta di Mirco.
    Interpreta linguaggio naturale, estrae skill + chief list.
    Se 'tutti', applica a tutti e 7.
    """
    logger.info("[CPeO] handle_training_request: %s", message[:100])

    # Estrai topic dal messaggio
    topic = _extract_topic_from_message(message)
    if not topic:
        return {"status": "error", "error": "non riesco a estrarre il topic dal messaggio"}

    # Determina chief target
    if chief_names:
        targets = [c.lower().replace(" ", "") for c in chief_names]
    else:
        targets = _extract_chiefs_from_message(message)

    if not targets:
        return {"status": "error", "error": "nessun Chief identificato nel messaggio"}

    # Espandi "tutti"
    if "tutti" in targets or "all" in targets:
        targets = list(_ALL_CHIEF_IDS)

    # Valida chief names
    valid_targets = [t for t in targets if t in _ALL_CHIEF_IDS]
    if not valid_targets:
        return {"status": "error", "error": "Chief non riconosciuti: " + ", ".join(targets)}

    # Crea training plan per ogni Chief
    results = []
    for chief_id in valid_targets:
        try:
            result = create_training_plan(chief_id, topic)
            results.append(result)
        except Exception as e:
            logger.warning("[CPeO] training request %s: %s", chief_id, e)
            results.append({"status": "error", "chief_name": chief_id, "error": str(e)})

    logger.info("[CPeO] training_request completato: %d plan creati", len(results))
    return {
        "status": "ok",
        "topic": topic,
        "targets": valid_targets,
        "plans": results,
    }


def _extract_topic_from_message(message: str) -> str:
    """Estrae topic di training dal messaggio naturale."""
    # Pattern: "training su/di/in TOPIC"
    m = re.search(r'training\s+(?:su|di|in|per|about)\s+(.+?)(?:\s+per\s+|\s+a\s+|\s+ai\s+|$)', message, re.IGNORECASE)
    if m:
        topic = m.group(1).strip().rstrip('.,;:')
        # Rimuovi eventuale nome Chief dal topic
        for cid in _ALL_CHIEF_IDS:
            topic = re.sub(r'\b' + cid + r'\b', '', topic, flags=re.IGNORECASE).strip()
        if topic:
            return topic

    # Pattern: "fai un training TOPIC"
    m = re.search(r'(?:fai|crea|genera|prepara)\s+(?:un\s+)?training\s+(.+?)(?:\s+per\s+|\s+a\s+|$)', message, re.IGNORECASE)
    if m:
        topic = m.group(1).strip().rstrip('.,;:')
        for cid in _ALL_CHIEF_IDS:
            topic = re.sub(r'\b' + cid + r'\b', '', topic, flags=re.IGNORECASE).strip()
        if topic:
            return topic

    # Fallback: usa Haiku per estrarre
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{"role": "user", "content": (
                "Estrai il topic di training da questo messaggio. "
                "Rispondi SOLO con il topic, nient'altro.\n\n"
                "Messaggio: " + message
            )}],
        )
        return resp.content[0].text.strip()
    except Exception:
        return ""


def _extract_chiefs_from_message(message: str) -> List[str]:
    """Estrae lista Chief dal messaggio naturale."""
    found = []
    msg_lower = message.lower()

    if "tutti" in msg_lower or "all" in msg_lower:
        return ["tutti"]

    for cid in _ALL_CHIEF_IDS:
        if cid in msg_lower:
            found.append(cid)

    return found


# ============================================================
# COACHING SETTIMANALE (esistente)
# ============================================================

def coach_chiefs() -> Dict[str, Any]:
    """
    Coaching automatico dei Chief ogni lunedi 06:30.
    Analizza ultimi 7gg di chief_decisions per ogni Chief:
    - Routing fuori dominio frequenti
    - Prompt bloccati sandbox
    - Errori ripetuti stesso tipo
    Genera learning in chief_knowledge (knowledge_type='coaching', importance=4).
    Invia report al topic #people.
    """
    start = now_rome()
    week_ago = (start - timedelta(days=7)).isoformat()
    logger.info("[CPeO] Avvio coach_chiefs")

    chief_domains = {
        "cso": "strategy", "coo": "ops", "cto": "tech",
        "cmo": "marketing", "cfo": "finance", "clo": "legal", "cpeo": "people",
    }

    learning_added = 0
    chief_status: Dict[str, str] = {}

    for chief_id, domain in chief_domains.items():
        issues: List[str] = []

        try:
            r = supabase.table("chief_decisions").select("decision_type,summary") \
                .eq("chief_domain", domain) \
                .like("decision_type", "routed_to_%") \
                .gte("created_at", week_ago).execute()
            routing_count = len(r.data or [])
            if routing_count >= 3:
                dest_chiefs: Dict[str, int] = {}
                for row in (r.data or []):
                    dest = row.get("decision_type", "").replace("routed_to_", "")
                    dest_chiefs[dest] = dest_chiefs.get(dest, 0) + 1
                top_dest = sorted(dest_chiefs.items(), key=lambda x: x[1], reverse=True)[0]
                issues.append(
                    "routing_fuori_dominio: " + str(routing_count)
                    + " richieste ridirezionate (piu frequente: -> " + top_dest[0].upper() + ")"
                )
        except Exception as e:
            logger.warning("[CPeO] routing check %s: %s", chief_id, e)

        try:
            r = supabase.table("code_tasks").select("id,title") \
                .eq("requested_by", chief_id) \
                .eq("sandbox_passed", False) \
                .gte("created_at", week_ago).execute()
            blocked_count = len(r.data or [])
            if blocked_count >= 2:
                issues.append(
                    "sandbox_violations: " + str(blocked_count)
                    + " prompt bloccati"
                )
        except Exception as e:
            logger.warning("[CPeO] sandbox check %s: %s", chief_id, e)

        try:
            r = supabase.table("agent_logs").select("agent_id,error") \
                .like("agent_id", "%" + chief_id + "%") \
                .eq("status", "error") \
                .gte("created_at", week_ago).execute()
            error_rows = r.data or []
            if len(error_rows) >= 3:
                error_types: Dict[str, int] = {}
                for row in error_rows:
                    err_short = (row.get("error") or "")[:50]
                    error_types[err_short] = error_types.get(err_short, 0) + 1
                top_error = sorted(error_types.items(), key=lambda x: x[1], reverse=True)[0]
                issues.append(
                    "errori_ripetuti: " + str(len(error_rows))
                    + " errori (piu comune: '" + top_error[0] + "')"
                )
        except Exception as e:
            logger.warning("[CPeO] errors check %s: %s", chief_id, e)

        if not issues:
            chief_status[chief_id] = "ok"
            continue

        chief_status[chief_id] = "warning: " + str(len(issues)) + " problemi"

        for issue in issues:
            issue_type = issue.split(":")[0]
            coaching_prompt = (
                "Sei il CPeO di brAIn. Stai creando un learning per il " + chief_id.upper() + ".\n"
                "Problema rilevato: " + issue + "\n\n"
                "Scrivi in 2-3 frasi un'istruzione chiara e specifica per evitare questo problema in futuro. "
                "Tono: diretto, costruttivo. Inizia con 'In futuro:'"
            )
            try:
                resp = claude.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=200,
                    messages=[{"role": "user", "content": coaching_prompt}],
                )
                instruction = resp.content[0].text.strip()
            except Exception as e:
                logger.warning("[CPeO] coaching generation %s: %s", chief_id, e)
                instruction = "In futuro: monitora attentamente " + issue_type + " per evitare problemi ricorrenti."

            content = "Hai avuto il problema: " + issue + "\n\n" + instruction
            try:
                supabase.table("chief_knowledge").insert({
                    "chief_id": chief_id,
                    "knowledge_type": "coaching",
                    "title": "Coaching " + issue_type + " - " + start.strftime("%Y-%m-%d"),
                    "content": content,
                    "importance": 4,
                    "created_at": start.isoformat(),
                    "updated_at": start.isoformat(),
                }).execute()
                learning_added += 1
            except Exception as e:
                logger.warning("[CPeO] insert coaching %s: %s", chief_id, e)

    # Costruisci report
    status_lines = []
    for chief_id in ["cso", "coo", "cto", "cmo", "cfo", "clo", "cpeo"]:
        st = chief_status.get(chief_id, "ok")
        icon = "\u2705" if st == "ok" else "\u26a0\ufe0f"
        detail = "" if st == "ok" else " - " + st.replace("warning: ", "")
        status_lines.append(icon + " " + chief_id.upper() + detail)

    report = (
        "\U0001f331 CPeO\n"
        "Coaching settimanale Chief\n\n"
        + "\n".join(status_lines)
        + "\n\nLearning aggiunti: " + str(learning_added)
    )

    _send_people(report)
    logger.info("[CPeO] coaching completato: learning_added=%d", learning_added)
    return {
        "status": "ok",
        "learning_added": learning_added,
        "chief_status": chief_status,
    }
