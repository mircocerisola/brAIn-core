"""
brAIn module: execution/smoke.py
Smoke test CSO autonomo multi-metodo — identita' anonima, 5 metodi, blocker management.
"""
from __future__ import annotations
import os, json, time, re
from datetime import datetime, timezone, timedelta
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json, search_perplexity
from execution.project import _send_to_topic, _commit_to_project_repo
from execution.pipeline import (
    advance_pipeline_step, generate_smoke_results_card,
    _get_group_id, _send_topic_raw, SEP, create_build_blocker,
)


# ============================================================
# BRAND IDENTITY — anonima, zero menzione Mirco o brAIn
# ============================================================

def _generate_brand_identity(solution, slug):
    """Genera identita' anonima per il progetto."""
    brand_name = solution.get("brand_brief", "") or ""
    if not brand_name:
        try:
            resp = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=50,
                messages=[{"role": "user", "content": (
                    "Genera un nome brand breve (1-2 parole, memorabile, moderno) per un prodotto che: "
                    + solution.get("title", "") + ". Solo il nome, nient'altro."
                )}],
            )
            brand_name = resp.content[0].text.strip().strip('"').strip("'")[:40]
        except Exception:
            brand_name = solution.get("title", slug)[:40]

    brand_domain = slug + ".com"
    brand_email = "hello@" + brand_domain

    return {
        "brand_name": brand_name,
        "brand_email": brand_email,
        "brand_domain": brand_domain,
        "brand_linkedin": "",
        "brand_landing_url": "",
    }


# ============================================================
# ENTRY POINT — run_smoke_design (da BOS approvato)
# ============================================================

def run_smoke_design(solution_id):
    """LEAN PIPELINE: Crea progetto minimo, genera brand identity, seleziona metodo, avvia CSO design."""
    from execution.project import _slugify, _get_telegram_group_id, _create_forum_topic
    from execution.pipeline import design_smoke_test
    from csuite.cso import select_smoke_test_method

    logger.info("[SMOKE_DESIGN] Avvio per solution_id=%s", solution_id)

    # Carica soluzione
    try:
        sol = supabase.table("solutions").select("*").eq("id", int(solution_id)).execute()
        if not sol.data:
            return {"status": "error", "error": "solution not found"}
        solution = sol.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = solution.get("title", "Project " + str(solution_id))[:80]
    slug = _slugify(name)
    bos_score = float(solution.get("bos_score") or 0)

    # Anti-duplicazione: riusa progetto esistente
    try:
        existing = supabase.table("projects").select(
            "id,status,pipeline_step,brand_name,brand_email,brand_domain,smoke_test_method"
        ).eq("bos_id", int(solution_id)).execute()
        if existing.data:
            proj = existing.data[0]
            if proj.get("status") not in ("new", "init", "failed", "smoke_test_pending"):
                logger.info("[SMOKE_DESIGN] solution %s gia' processata, skip", solution_id)
                return {"status": "skipped", "reason": "progetto gia' in corso"}
            project_id = proj["id"]
            # Preserva brand esistente se gia' impostato, altrimenti genera
            if proj.get("brand_name") and proj.get("brand_email"):
                brand = {
                    "brand_name": proj["brand_name"],
                    "brand_email": proj["brand_email"],
                    "brand_domain": proj.get("brand_domain") or "",
                    "brand_linkedin": "",
                    "brand_landing_url": "",
                }
                method = proj.get("smoke_test_method") or select_smoke_test_method(solution)
            else:
                brand = _generate_brand_identity(solution, slug)
                method = select_smoke_test_method(solution)
            supabase.table("projects").update({
                "brand_name": brand["brand_name"],
                "brand_email": brand["brand_email"],
                "brand_domain": brand["brand_domain"],
                "brand_linkedin": brand.get("brand_linkedin", ""),
                "brand_landing_url": brand.get("brand_landing_url", ""),
                "smoke_test_method": method,
                "status": "smoke_test_pending",
                "pipeline_locked": False,
            }).eq("id", project_id).execute()
            logger.info("[SMOKE_DESIGN] Riuso progetto id=%s, brand=%s method=%s", project_id, brand["brand_name"], method)
            result = design_smoke_test(project_id)
            return {"status": "ok", "project_id": project_id, "reused": True, "design": result}
    except Exception as e:
        logger.warning("[SMOKE_DESIGN] Duplicate check: %s", e)

    # Genera brand identity anonima
    brand = _generate_brand_identity(solution, slug)

    # Seleziona metodo smoke test
    method = select_smoke_test_method(solution)

    # Crea record progetto minimo con brand identity
    project_id = None
    try:
        result = supabase.table("projects").insert({
            "name": name,
            "slug": slug,
            "bos_id": int(solution_id),
            "bos_score": bos_score,
            "status": "smoke_test_pending",
            "pipeline_step": "bos_approved",
            "pipeline_territory": "cso",
            "pipeline_locked": False,
            "brand_name": brand["brand_name"],
            "brand_email": brand["brand_email"],
            "brand_domain": brand["brand_domain"],
            "brand_linkedin": brand["brand_linkedin"],
            "brand_landing_url": brand["brand_landing_url"],
            "smoke_test_method": method,
        }).execute()
        if result.data:
            project_id = result.data[0]["id"]
    except Exception as e:
        logger.error("[SMOKE_DESIGN] DB insert: %s", e)
        return {"status": "error", "error": str(e)}

    logger.info("[SMOKE_DESIGN] Progetto creato: id=%s slug=%s brand=%s method=%s",
                project_id, slug, brand["brand_name"], method)

    # Crea Forum Topic cantiere
    group_id = _get_telegram_group_id()
    if group_id:
        topic_id = _create_forum_topic(group_id, brand["brand_name"])
        if topic_id:
            try:
                supabase.table("projects").update({"topic_id": topic_id}).eq("id", project_id).execute()
            except Exception:
                pass

    # CSO progetta smoke test
    result = design_smoke_test(project_id)
    return {"status": "ok", "project_id": project_id, "brand": brand, "method": method, "design": result}


# ============================================================
# START SMOKE TEST — dispatcher per metodo
# ============================================================

def start_smoke_test(project_id):
    """Avvia smoke test in base al metodo selezionato. Entry point dopo approvazione Mirco."""
    start_t = time.time()
    logger.info("[SMOKE_START] Avvio per project_id=%s", project_id)

    # Pipeline lock
    try:
        lock_check = supabase.table("projects").select("pipeline_locked,status").eq("id", project_id).execute()
        if lock_check.data and lock_check.data[0].get("pipeline_locked"):
            return {"status": "skipped", "reason": "pipeline gia' in corso"}
        supabase.table("projects").update({"pipeline_locked": True}).eq("id", project_id).execute()
    except Exception as e:
        logger.warning("[SMOKE_START] Lock check: %s", e)

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            _unlock(project_id)
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        _unlock(project_id)
        return {"status": "error", "error": str(e)}

    # Carica soluzione per contesto
    solution = {}
    bos_id = project.get("bos_id")
    if bos_id:
        try:
            sol = supabase.table("solutions").select("*").eq("id", bos_id).execute()
            if sol.data:
                solution = sol.data[0]
        except Exception:
            pass

    method = project.get("smoke_test_method", "cold_outreach")
    brand_name = project.get("brand_name", project.get("name", ""))
    brand_email = project.get("brand_email", "")
    topic_id = project.get("topic_id")
    group_id = _get_group_id()

    # Verifica blocker email — se brand_email non confermato e serve outreach
    if method in ("cold_outreach", "cold_outreach_landing") and not brand_email:
        if group_id and topic_id:
            _create_email_blocker(project_id, project.get("brand_domain", ""), group_id, topic_id)
        _unlock(project_id)
        return {"status": "blocked", "reason": "brand_email non configurato"}

    # Crea record smoke_test
    smoke_id = None
    try:
        plan = json.loads(project.get("smoke_test_plan") or "{}")
        res = supabase.table("smoke_tests").insert({
            "project_id": project_id,
            "method": method,
            "kpi_success": plan.get("kpi_success", ""),
            "kpi_failure": plan.get("kpi_failure", ""),
            "duration_days": plan.get("duration_days", 7),
            "landing_page_url": project.get("brand_landing_url", ""),
        }).execute()
        if res.data:
            smoke_id = res.data[0]["id"]
    except Exception as e:
        logger.error("[SMOKE_START] smoke_tests insert: %s", e)
        _unlock(project_id)
        return {"status": "error", "error": str(e)}

    # Notifica avvio
    if group_id and topic_id:
        dur = plan.get("duration_days", 7)
        _send_topic_raw(group_id, topic_id,
                        "Smoke test avviato — " + brand_name + "\n" + SEP +
                        "\nMetodo: " + method + "\nDurata: " + str(dur) + " giorni")

    # Dispatch per metodo
    result = {}
    if method == "cold_outreach":
        result = _run_cold_outreach(project_id, project, solution, smoke_id)
    elif method == "landing_page_ads":
        result = _run_landing_page_ads(project_id, project, solution, smoke_id)
    elif method == "concierge":
        result = _run_concierge(project_id, project, solution, smoke_id)
    elif method == "pre_order":
        result = _run_pre_order(project_id, project, solution, smoke_id)
    elif method == "paid_ads":
        result = _run_paid_ads(project_id, project, solution, smoke_id)
    elif method == "cold_outreach_landing":
        result = _run_cold_outreach(project_id, project, solution, smoke_id)
        _run_landing_page_ads(project_id, project, solution, smoke_id)
    else:
        result = _run_cold_outreach(project_id, project, solution, smoke_id)

    # Avanza pipeline
    advance_pipeline_step(project_id, "smoke_test_running")
    try:
        supabase.table("projects").update({"status": "smoke_test_running"}).eq("id", project_id).execute()
    except Exception:
        pass

    duration_ms = int((time.time() - start_t) * 1000)
    log_to_supabase("smoke_test_agent", "smoke_start", 2,
                    "project=" + str(project_id) + " method=" + method,
                    "smoke_id=" + str(smoke_id) + " prospects=" + str(result.get("prospects_count", 0)),
                    "claude-sonnet-4-6", 0, 0, 0, duration_ms)

    _unlock(project_id)
    logger.info("[SMOKE_START] Completato project=%s method=%s", project_id, method)
    return {"status": "ok", "project_id": project_id, "smoke_id": smoke_id, "method": method, **result}


# ============================================================
# METODO A — COLD OUTREACH (B2B)
# ============================================================

def _run_cold_outreach(project_id, project, solution, smoke_id):
    """Trova 50 prospect via Perplexity, genera sequenza 3 email."""
    brand_name = project.get("brand_name", "")
    brand_email = project.get("brand_email", "")
    name = project.get("name", "")
    topic_id = project.get("topic_id")
    group_id = _get_group_id()

    # Estrai target dalla soluzione/piano
    plan = json.loads(project.get("smoke_test_plan") or "{}")
    target_desc = plan.get("target_description", "")
    if not target_desc:
        target_desc = solution.get("customer_segment") or solution.get("title") or name

    # Trova prospect via Perplexity
    prospects = _find_prospects_perplexity(target_desc, 50)

    # Inserisci prospect in DB
    inserted = 0
    for p in prospects[:50]:
        try:
            contact = p.get("contact", "")
            channel = "email" if "@" in contact else "linkedin"
            supabase.table("smoke_test_prospects").insert({
                "smoke_test_id": smoke_id,
                "project_id": project_id,
                "name": p.get("name", "")[:100],
                "company": p.get("company", "")[:200],
                "role": p.get("role", "")[:200],
                "contact": contact[:200],
                "linkedin_url": p.get("linkedin", "")[:300],
                "channel": channel,
                "status": "pending",
            }).execute()
            inserted += 1
        except Exception as e:
            logger.warning("[COLD_OUTREACH] prospect insert: %s", e)

    # Genera sequenza 3 email
    email_sequence = _generate_cold_email_sequence(brand_name, solution.get("title", name), brand_email)

    # Salva sequenza e count
    try:
        supabase.table("smoke_tests").update({
            "prospects_count": inserted,
            "cold_email_sequence": json.dumps(email_sequence),
        }).eq("id", smoke_id).execute()
    except Exception:
        pass

    # Notifica nel topic cantiere
    if group_id and topic_id:
        email_preview = email_sequence[0].get("subject", "N/A") if email_sequence else "N/A"
        msg = (
            "Cold outreach pronto — " + brand_name + "\n" +
            SEP + "\n" +
            "Prospect trovati: " + str(inserted) + "\n" +
            "Email da: " + brand_email + "\n" +
            "Sequenza: 3 touchpoint (giorno 1, 3, 6)\n" +
            "Prima email subject: " + email_preview + "\n" +
            SEP + "\n" +
            "Azione richiesta: invia la prima email ai prospect."
        )
        reply_markup = {
            "inline_keyboard": [[
                {"text": "Vedi lista prospect", "callback_data": "smoke_prospects:" + str(project_id) + ":" + str(smoke_id)},
                {"text": "Vedi email template", "callback_data": "smoke_emails:" + str(project_id) + ":" + str(smoke_id)},
            ]]
        }
        _send_topic_raw(group_id, topic_id, msg, reply_markup)

    return {"prospects_count": inserted, "email_sequence_count": len(email_sequence)}


def _find_prospects_perplexity(target_description, count=50):
    """Trova prospect qualificati via Perplexity."""
    prospects = []
    batch_queries = [
        "Trova " + str(count // 2) + " aziende/persone che sono " + target_description +
        ". Per ogni prospect elenca: Nome Azienda | Nome Decisore | Ruolo | Email o LinkedIn. "
        "Formato: una riga per prospect, campi separati da |. Focus Nord Italia.",

        "Trova " + str(count // 2) + " aziende/persone che sono " + target_description +
        ". Per ogni prospect elenca: Nome Azienda | Nome Decisore | Ruolo | Email o LinkedIn. "
        "Formato: una riga per prospect, campi separati da |. Focus Centro-Sud Italia.",
    ]
    for query in batch_queries:
        result = search_perplexity(query)
        if result:
            for line in result.split("\n"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3:
                    contact = parts[3].strip() if len(parts) > 3 else ""
                    has_contact = "@" in contact or "linkedin" in contact.lower()
                    if has_contact or len(parts) >= 3:
                        prospects.append({
                            "company": parts[0][:200],
                            "name": parts[1][:100] if len(parts) > 1 else "",
                            "role": parts[2][:200] if len(parts) > 2 else "",
                            "contact": contact[:200],
                            "linkedin": contact if "linkedin" in contact.lower() else "",
                        })
    return prospects[:count]


def _generate_cold_email_sequence(brand_name, solution_title, brand_email):
    """Genera sequenza di 3 touchpoint email per cold outreach."""
    prompt = (
        "Genera una sequenza di 3 email per cold outreach B2B. "
        "Brand: " + brand_name + ". Prodotto: " + solution_title + ". Mittente: " + brand_email + ".\n\n"
        "Regole:\n"
        "- Max 80 parole per email\n"
        "- Tono diretto, no corporate speak, no jargon\n"
        "- Mittente e' '" + brand_name + " team'\n"
        "- Zero menzione di AI o automazione\n"
        "- Email 1 (giorno 1): introduzione + value proposition\n"
        "- Email 2 (giorno 3): follow-up con domanda specifica\n"
        "- Email 3 (giorno 6): ultimo touchpoint con CTA chiara\n\n"
        'Rispondi SOLO con JSON array:\n'
        '[{"day": 1, "subject": "...", "body": "..."}, '
        '{"day": 3, "subject": "...", "body": "..."}, '
        '{"day": 6, "subject": "...", "body": "..."}]'
    )
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\[[\s\S]*\]', raw)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        logger.warning("[COLD_EMAIL] Sonnet error: %s", e)

    # Fallback
    return [
        {"day": 1, "subject": brand_name + " — una proposta per te",
         "body": "Ciao, siamo il team di " + brand_name + ". Stiamo sviluppando " + solution_title +
                 ". Ci piacerebbe capire se e' un problema che vivi anche tu. Hai 5 minuti per una chiamata?"},
        {"day": 3, "subject": "Re: " + brand_name,
         "body": "Volevo chiederti: come gestisci oggi questo problema? Stiamo raccogliendo feedback reali."},
        {"day": 6, "subject": "Ultima nota da " + brand_name,
         "body": "Non voglio disturbarti. Se il tema ti interessa, qui trovi piu' info. Altrimenti, zero follow-up. Grazie."},
    ]


# ============================================================
# METODO B — LANDING PAGE + ADS
# ============================================================

def _run_landing_page_ads(project_id, project, solution, smoke_id):
    """Genera HTML landing page + piano ads."""
    brand_name = project.get("brand_name", "")
    topic_id = project.get("topic_id")
    group_id = _get_group_id()

    # Genera HTML landing
    landing_html = _generate_landing_html(brand_name, solution)

    # Salva HTML in DB
    try:
        supabase.table("projects").update({
            "landing_page_html": landing_html,
        }).eq("id", project_id).execute()
    except Exception:
        pass

    # Genera piano ads
    ads_plan = _generate_ads_plan(brand_name, solution)
    try:
        supabase.table("smoke_tests").update({
            "ads_plan": json.dumps(ads_plan),
        }).eq("id", smoke_id).execute()
    except Exception:
        pass

    # Crea blocker per Mirco: pubblica landing page
    if group_id and topic_id:
        create_build_blocker(
            project_id,
            "Landing page " + brand_name + " da pubblicare",
            "1. Copia l'HTML dalla sezione landing_page_html del progetto\n"
            "2. Pubblica su GitHub Pages o Carrd.co\n"
            "3. Torna qui e conferma l'URL",
            "15 minuti",
            group_id, topic_id,
        )

    # Notifica con piano ads
    if group_id and topic_id:
        budget = ads_plan.get("budget_eur", 50)
        duration = ads_plan.get("duration_days", 5)
        channels_list = ads_plan.get("channels", ["Google Ads"])
        channels_str = ", ".join(channels_list)
        targeting = ads_plan.get("targeting", "da definire")
        msg = (
            "Landing + Ads pronto — " + brand_name + "\n" +
            SEP + "\n" +
            "Landing HTML generato e salvato.\n" +
            "Piano ads: " + channels_str + "\n" +
            "Budget suggerito: EUR" + str(budget) + " per " + str(duration) + " giorni\n" +
            "Target: " + targeting + "\n" +
            SEP + "\n" +
            "Pubblica la landing page e conferma l'URL per procedere."
        )
        _send_topic_raw(group_id, topic_id, msg)

    return {"landing_html_generated": True, "ads_plan": ads_plan}


def _generate_landing_html(brand_name, solution):
    """Genera HTML completo per landing page — anonima, zero menzione brAIn/Mirco."""
    title = solution.get("title", brand_name)
    description = (solution.get("description") or "")[:500]

    prompt = (
        "Genera una landing page HTML completa per '" + brand_name + "' — " + title + ".\n"
        "Descrizione: " + description + "\n\n"
        "Requisiti:\n"
        "- HTML singolo file, CSS inline, responsive\n"
        "- Headline con value proposition chiara\n"
        "- 3 bullet benefit concreti\n"
        "- CTA 'Entra in lista d'attesa' con form email\n"
        "- Form che invia a formspree.io o salva in localStorage\n"
        "- Brand: " + brand_name + ". ZERO menzione di AI, automazione, brAIn, Mirco\n"
        "- Design moderno, colori professionali, font Google Fonts\n"
        "- Footer con copyright " + brand_name + " 2026\n\n"
        "Rispondi SOLO con l'HTML completo, nient'altro."
    )
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        html = resp.content[0].text.strip()
        m = re.search(r'<!DOCTYPE.*</html>', html, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(0)
        return html
    except Exception as e:
        logger.warning("[LANDING_HTML] Sonnet error: %s", e)
        return (
            "<!DOCTYPE html><html><head><title>" + brand_name + "</title>"
            "<style>body{font-family:sans-serif;max-width:600px;margin:0 auto;padding:40px}"
            "h1{color:#333}form{margin-top:20px}"
            "input{padding:10px;width:200px}button{padding:10px 20px;background:#007bff;color:#fff;border:none}"
            "</style></head><body>"
            "<h1>" + brand_name + "</h1><p>" + title + "</p>"
            "<form><input type='email' placeholder='La tua email'>"
            "<button>Entra in lista d'attesa</button></form>"
            "<footer><small>" + brand_name + " 2026</small></footer>"
            "</body></html>"
        )


def _generate_ads_plan(brand_name, solution):
    """Genera piano Google/Meta Ads."""
    title = solution.get("title", brand_name)
    sector = solution.get("sector", "")

    prompt = (
        "Genera un piano ads per validare interesse per '" + brand_name + "' (" + title + "), settore " + sector + ".\n"
        "Budget: minimo EUR50, max EUR100 per 5 giorni.\n\n"
        "Rispondi SOLO con JSON:\n"
        '{"channels": ["Google Ads", "Meta Ads"], '
        '"budget_eur": 50, "duration_days": 5, '
        '"targeting": "descrizione target", '
        '"keywords": ["keyword1", "keyword2", "keyword3"], '
        '"ad_copy": [{"headline": "...", "description": "..."}, {"headline": "...", "description": "..."}]}'
    )
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        logger.warning("[ADS_PLAN] Haiku error: %s", e)

    return {
        "channels": ["Google Ads"], "budget_eur": 50, "duration_days": 5,
        "targeting": "da definire", "keywords": [title.lower()], "ad_copy": [],
    }


# ============================================================
# METODO C — CONCIERGE MVP
# ============================================================

def _run_concierge(project_id, project, solution, smoke_id):
    """Genera piano concierge: servizio manuale per 10-20 clienti."""
    brand_name = project.get("brand_name", "")
    name = project.get("name", "")
    topic_id = project.get("topic_id")
    group_id = _get_group_id()
    title = solution.get("title", name)

    prompt = (
        "Progetta un Concierge MVP per validare '" + brand_name + "' — " + title + ".\n"
        "Il fondatore eseguira' il servizio MANUALMENTE per 10-20 clienti "
        "per validare la domanda prima di automatizzare.\n\n"
        "Includi:\n"
        "1. Quanti clienti target (10-20)\n"
        "2. Come trovarli (canale specifico)\n"
        "3. Script di contatto (max 100 parole)\n"
        "4. Cosa offrire gratis per 7 giorni\n"
        "5. Metriche da osservare\n"
        "6. Segnali di successo/fallimento\n\n"
        "Rispondi SOLO con JSON:\n"
        '{"target_count": 15, "how_to_find": "...", "contact_script": "...", '
        '"free_offer": "...", "metrics": ["...", "..."], '
        '"success_signal": "...", "failure_signal": "..."}'
    )
    concierge_plan = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            concierge_plan = json.loads(m.group(0))
    except Exception as e:
        logger.warning("[CONCIERGE] Sonnet error: %s", e)
        concierge_plan = {
            "target_count": 15,
            "how_to_find": "Contatto diretto",
            "contact_script": "Ciao, siamo " + brand_name + ". Ti offriamo 7 giorni gratis del nostro servizio. Interesse?",
            "free_offer": "Servizio completo gratis per 7 giorni",
            "metrics": ["Utilizzo quotidiano", "Richieste aggiuntive"],
            "success_signal": ">50% usa il servizio ogni giorno",
            "failure_signal": "<20% usa il servizio dopo 3 giorni",
        }

    # Salva piano
    try:
        supabase.table("smoke_tests").update({
            "concierge_plan": json.dumps(concierge_plan),
        }).eq("id", smoke_id).execute()
    except Exception:
        pass

    # Manda piano dettagliato a Mirco nel topic cantiere
    if group_id and topic_id:
        script = concierge_plan.get("contact_script", "N/A")
        target_count = str(concierge_plan.get("target_count", 15))
        how_to_find = concierge_plan.get("how_to_find", "N/A")
        free_offer = concierge_plan.get("free_offer", "N/A")
        success_sig = concierge_plan.get("success_signal", "N/A")
        failure_sig = concierge_plan.get("failure_signal", "N/A")
        msg = (
            "Piano Concierge MVP — " + brand_name + "\n" +
            SEP + "\n" +
            "Clienti target: " + target_count + "\n" +
            "Come trovarli: " + how_to_find + "\n" +
            "Offerta gratuita: " + free_offer + "\n" +
            "Durata: 7 giorni\n" +
            SEP + "\n" +
            "Script di contatto:\n" + script + "\n" +
            SEP + "\n" +
            "Successo: " + success_sig + "\n" +
            "Fallimento: " + failure_sig
        )
        _send_topic_raw(group_id, topic_id, msg)

    return {"concierge_plan": concierge_plan}


# ============================================================
# METODO D — PRE-ORDER / DEPOSITO
# ============================================================

def _run_pre_order(project_id, project, solution, smoke_id):
    """Genera landing con prezzo visibile e CTA pre-order."""
    brand_name = project.get("brand_name", "")
    topic_id = project.get("topic_id")
    group_id = _get_group_id()
    title = solution.get("title", brand_name)

    prompt = (
        "Genera una landing page HTML per pre-order di '" + brand_name + "' — " + title + ".\n"
        "Requisiti:\n"
        "- Prezzo visibile: 'Design partner da EUR299/mese'\n"
        "- CTA: 'Prenota il tuo posto'\n"
        "- Form con nome, email, azienda\n"
        "- Copy che filtra curiosi dai seri\n"
        "- HTML completo, CSS inline, responsive\n"
        "- ZERO menzione AI, brAIn, Mirco\n"
        "- Footer: " + brand_name + " 2026\n\n"
        "Rispondi SOLO con l'HTML completo."
    )
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        html = resp.content[0].text.strip()
        m = re.search(r'<!DOCTYPE.*</html>', html, re.DOTALL | re.IGNORECASE)
        landing_html = m.group(0) if m else html
    except Exception:
        landing_html = ("<html><body><h1>" + brand_name + "</h1>"
                        "<p>Pre-order: EUR299/mese</p></body></html>")

    # Salva HTML
    try:
        supabase.table("projects").update({
            "landing_page_html": landing_html,
        }).eq("id", project_id).execute()
    except Exception:
        pass

    # Crea blocker per pubblicazione
    if group_id and topic_id:
        create_build_blocker(
            project_id,
            "Landing pre-order " + brand_name + " da pubblicare",
            "1. Copia l'HTML dalla sezione landing_page_html del progetto\n"
            "2. Pubblica su GitHub Pages o Carrd.co\n"
            "3. Torna qui e conferma l'URL",
            "15 minuti",
            group_id, topic_id,
        )

    return {"landing_html_generated": True, "type": "pre_order"}


# ============================================================
# METODO E — PAID ADS PURI
# ============================================================

def _run_paid_ads(project_id, project, solution, smoke_id):
    """Google Ads su keyword intent -> landing page."""
    brand_name = project.get("brand_name", "")
    topic_id = project.get("topic_id")
    group_id = _get_group_id()
    title = solution.get("title", brand_name)
    sector = solution.get("sector", "")

    # Genera landing
    landing_html = _generate_landing_html(brand_name, solution)
    try:
        supabase.table("projects").update({"landing_page_html": landing_html}).eq("id", project_id).execute()
    except Exception:
        pass

    # Genera keyword plan specifico per intent
    prompt = (
        "Genera un piano Google Ads per keyword intent per '" + brand_name + "' — " + title + ", settore " + sector + ".\n"
        "Budget: EUR50-100 per 5 giorni.\n\n"
        "Rispondi SOLO con JSON:\n"
        '{"keywords_intent": ["keyword con intent di acquisto 1", "keyword 2", "keyword 3"], '
        '"negative_keywords": ["keyword da escludere 1", "keyword 2"], '
        '"budget_eur": 75, "duration_days": 5, '
        '"daily_budget_eur": 15, '
        '"expected_ctr_pct": 3.0, '
        '"ad_copy": [{"headline": "...", "description": "..."}]}'
    )
    ads_plan = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            ads_plan = json.loads(m.group(0))
    except Exception:
        ads_plan = {"keywords_intent": [title.lower()], "budget_eur": 75, "duration_days": 5}

    try:
        supabase.table("smoke_tests").update({"ads_plan": json.dumps(ads_plan)}).eq("id", smoke_id).execute()
    except Exception:
        pass

    # Blocker e notifica
    if group_id and topic_id:
        kw_list = ads_plan.get("keywords_intent", [])[:5]
        kw_str = ", ".join(kw_list)
        daily_budget = str(ads_plan.get("daily_budget_eur", 15))
        dur = str(ads_plan.get("duration_days", 5))
        create_build_blocker(
            project_id,
            "Pubblica landing + configura Google Ads",
            "1. Pubblica landing HTML su GitHub Pages o Carrd\n"
            "2. Crea campagna Google Ads con keyword:\n"
            "   " + kw_str + "\n"
            "3. Budget giornaliero: EUR" + daily_budget + "\n"
            "4. Durata: " + dur + " giorni",
            "30 minuti",
            group_id, topic_id,
        )

    return {"landing_html_generated": True, "ads_plan": ads_plan}


# ============================================================
# BLOCKER MANAGEMENT
# ============================================================

def _create_email_blocker(project_id, brand_domain, group_id, topic_id):
    """Crea blocker per registrazione dominio e email."""
    if not brand_domain:
        brand_domain = "progetto.com"

    steps = (
        "1. Vai su cloudflare.com/domains\n"
        "2. Cerca '" + brand_domain + "' — costo circa EUR10/anno\n"
        "3. Acquista e crea email hello@" + brand_domain + "\n"
        "4. Torna qui e clicca 'Completato'"
    )
    create_build_blocker(project_id, "Email brand: hello@" + brand_domain, steps, "10 minuti", group_id, topic_id)


def _unlock(project_id):
    """Sblocca pipeline."""
    try:
        supabase.table("projects").update({"pipeline_locked": False}).eq("id", project_id).execute()
    except Exception:
        pass


# ============================================================
# ANALISI RISULTATI SMOKE TEST
# ============================================================

def analyze_smoke_results(project_id):
    """Analizza risultati smoke test e genera report completo per Mirco."""
    start_t = time.time()
    logger.info("[SMOKE_RESULTS] Avvio analisi per project_id=%s", project_id)

    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    name = project.get("name", "Progetto " + str(project_id))
    brand_name = project.get("brand_name", name)
    method = project.get("smoke_test_method", "cold_outreach")
    github_repo = project.get("github_repo", "")

    # Recupera smoke test piu' recente
    try:
        st = supabase.table("smoke_tests").select("*").eq("project_id", project_id).order("started_at", desc=True).limit(1).execute()
        if not st.data:
            return {"status": "error", "error": "smoke test not found"}
        smoke = st.data[0]
        smoke_id = smoke["id"]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    # Recupera prospect
    try:
        prospects = supabase.table("smoke_test_prospects").select("*").eq("smoke_test_id", smoke_id).execute()
        prospects_data = prospects.data or []
    except Exception:
        prospects_data = []

    total = len(prospects_data)
    sent = sum(1 for p in prospects_data if p.get("sent_at"))
    positive = [p for p in prospects_data if p.get("status") in ("positive", "interested", "form_compiled")]
    negative = [p for p in prospects_data if p.get("status") in ("rejected", "not_interested")]
    no_response = [p for p in prospects_data if p.get("status") in ("pending", "sent", "no_response")]
    demo_requests = [p for p in prospects_data if p.get("status") == "demo_requested"]

    responses_with_text = [p.get("response_text", "") for p in prospects_data if p.get("response_text")]
    rejection_reasons = [p.get("rejection_reason", "") for p in negative if p.get("rejection_reason")]

    # Calcola metriche
    total_reached = max(sent, total)
    pct_positive = round(len(positive) / max(total_reached, 1) * 100)
    pct_negative = round(len(negative) / max(total_reached, 1) * 100)
    pct_no_response = round(len(no_response) / max(total_reached, 1) * 100)

    # Landing page metrics
    landing_views = smoke.get("landing_visits", 0)
    landing_signups = smoke.get("forms_compiled", 0)
    total_cost = smoke.get("total_cost_eur", 0)
    cost_per_lead = round(total_cost / max(len(positive) + landing_signups, 1), 2) if total_cost else 0

    # Genera insights con Sonnet
    kpi = json.loads(project.get("smoke_test_kpi") or "{}")
    kpi_success = kpi.get("success", "15% risposta positiva")

    feedback_list = responses_with_text[:5] + rejection_reasons[:5]
    feedback_joined = "; ".join(f[:100] for f in feedback_list if f)

    insights_prompt = (
        "Analizza i risultati di questo smoke test per '" + brand_name + "'.\n"
        "Metodo: " + method + "\n"
        "Prospect raggiunti: " + str(total_reached) + "\n"
        "Risposte positive: " + str(len(positive)) + " (" + str(pct_positive) + "%)\n"
        "Risposte negative: " + str(len(negative)) + " (" + str(pct_negative) + "%)\n"
        "No risposta: " + str(len(no_response)) + " (" + str(pct_no_response) + "%)\n"
        "Richieste demo: " + str(len(demo_requests)) + "\n"
        "Landing views: " + str(landing_views) + ", Signups: " + str(landing_signups) + "\n"
        "Costo totale: EUR" + str(total_cost) + "\n"
        "KPI successo definito: " + kpi_success + "\n"
        "Feedback ricevuti: " + (feedback_joined or "nessuno") + "\n\n"
        "Rispondi in JSON:\n"
        '{"overall_signal": "green/yellow/red", '
        '"kpi_met": true, '
        '"key_insights": ["insight 1", "insight 2", "insight 3"], '
        '"top_feedback": ["feedback 1", "feedback 2", "feedback 3"], '
        '"objections": ["obiezione 1", "obiezione 2"], '
        '"recommendation": "GO/PIVOT/NO-GO", '
        '"reasoning": "motivazione in 2 righe", '
        '"risk_if_go": "rischio se si procede", '
        '"opportunity": "opportunita identificata"}'
    )

    insights = {}
    cost_usd = 0.0
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": insights_prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            insights = json.loads(m.group(0))
        cost_usd = (resp.usage.input_tokens * 3.0 + resp.usage.output_tokens * 15.0) / 1_000_000
    except Exception as e:
        logger.error("[SMOKE_RESULTS] Claude: %s", e)
        insights = {
            "overall_signal": "yellow", "kpi_met": False,
            "recommendation": "ANALISI MANUALE RICHIESTA",
            "key_insights": [], "top_feedback": [], "objections": [],
        }

    # Salva risultati in smoke_tests
    try:
        supabase.table("smoke_tests").update({
            "positive_responses": len(positive),
            "negative_responses": len(negative),
            "no_response": len(no_response),
            "demo_requests": len(demo_requests),
            "qualitative_feedback": json.dumps(feedback_list[:10]),
            "spec_insights": json.dumps(insights),
            "recommendation": insights.get("recommendation", ""),
            "cso_recommendation": insights.get("reasoning", ""),
            "conversion_rate": pct_positive,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", smoke_id).execute()
    except Exception as e:
        logger.error("[SMOKE_RESULTS] DB update: %s", e)

    # Salva risultati in projects
    try:
        supabase.table("projects").update({
            "smoke_test_results": json.dumps({
                "total_reached": total_reached,
                "positive": len(positive),
                "negative": len(negative),
                "no_response": len(no_response),
                "demo_requests": len(demo_requests),
                "landing_views": landing_views,
                "landing_signups": landing_signups,
                "total_cost_eur": total_cost,
                "cost_per_lead": cost_per_lead,
                "pct_positive": pct_positive,
            }),
            "spec_insights": json.dumps(insights),
            "status": "smoke_completed",
        }).eq("id", project_id).execute()
    except Exception:
        pass

    # Commit SPEC_UPDATES.md su GitHub se repo esiste
    if github_repo:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        spec_updates = "# Risultati Smoke Test — " + brand_name + "\n"
        spec_updates += "Data: " + date_str + "\n\n"
        spec_updates += "## Segnale: " + (insights.get("overall_signal") or "N/A").upper() + "\n"
        spec_updates += "## Raccomandazione: " + (insights.get("recommendation") or "N/A") + "\n\n"
        spec_updates += "## Metriche\n"
        spec_updates += "- Raggiunti: " + str(total_reached) + "\n"
        spec_updates += "- Positivi: " + str(len(positive)) + " (" + str(pct_positive) + "%)\n"
        spec_updates += "- Negativi: " + str(len(negative)) + " (" + str(pct_negative) + "%)\n"
        if landing_views:
            spec_updates += "- Landing views: " + str(landing_views) + ", Signups: " + str(landing_signups) + "\n"
        if total_cost:
            spec_updates += "- Costo: EUR" + str(total_cost) + ", Per lead: EUR" + str(cost_per_lead) + "\n"
        spec_updates += "\n## Insights\n"
        for ins in insights.get("key_insights", []):
            spec_updates += "- " + ins + "\n"
        spec_updates += "\n## Reasoning\n" + (insights.get("reasoning") or "") + "\n"
        _commit_to_project_repo(github_repo, "SPEC_UPDATES.md", spec_updates,
                                "data: smoke test results " + date_str)

    # Genera e invia card risultati completa
    generate_smoke_results_card(project_id, smoke_id)

    duration_ms = int((time.time() - start_t) * 1000)
    rec = insights.get("recommendation", "")
    log_to_supabase("smoke_test_agent", "smoke_results", 2,
                    "project=" + str(project_id),
                    "pos=" + str(pct_positive) + "% rec=" + rec,
                    "claude-sonnet-4-6", 0, 0, cost_usd, duration_ms)

    signal = insights.get("overall_signal", "yellow")
    logger.info("[SMOKE_RESULTS] Completato project=%s pos=%s%%", project_id, pct_positive)
    return {
        "status": "ok",
        "project_id": project_id,
        "smoke_id": smoke_id,
        "pct_positive": pct_positive,
        "recommendation": rec,
        "signal": signal,
    }


# ============================================================
# DAILY UPDATE — aggiornamento giornaliero smoke test attivi
# ============================================================

def run_smoke_daily_update():
    """Aggiornamento giornaliero per smoke test attivi + reminder per blocker pendenti."""
    updated = 0
    reminded = 0

    # 1. Aggiorna progetti con smoke test in corso
    try:
        active = supabase.table("projects").select(
            "id,name,brand_name,topic_id,smoke_test_method,smoke_test_plan"
        ).eq("pipeline_step", "smoke_test_running").execute()

        for project in (active.data or []):
            try:
                _send_daily_update_for_project(project)
                updated += 1
            except Exception as e:
                logger.warning("[SMOKE_DAILY] project %s: %s", project.get("id"), e)
    except Exception as e:
        logger.warning("[SMOKE_DAILY] active projects: %s", e)

    # 2. Reminder per progetti in designing con blocker pendenti
    try:
        designing = supabase.table("projects").select(
            "id,name,brand_name,topic_id,smoke_test_plan"
        ).eq("pipeline_step", "smoke_test_designing").execute()

        for project in (designing.data or []):
            try:
                r = _send_blocker_reminder(project)
                if r:
                    reminded += 1
            except Exception as e:
                logger.warning("[SMOKE_REMINDER] project %s: %s", project.get("id"), e)
    except Exception as e:
        logger.warning("[SMOKE_DAILY] designing projects: %s", e)

    return {"status": "ok", "updated": updated, "reminded": reminded}


def _send_blocker_reminder(project):
    """Invia reminder ogni 24h per blocker pendenti. Dopo 7 giorni → alert in #strategy."""
    from execution.pipeline import get_pending_blockers

    project_id = project["id"]
    brand_name = project.get("brand_name", project.get("name", ""))
    topic_id = project.get("topic_id")
    group_id = _get_group_id()

    if not group_id or not topic_id:
        return False

    # Controlla blockers pendenti
    pending = get_pending_blockers(project_id)
    if not pending:
        return False

    # Controlla ultimo reminder dal piano
    plan = json.loads(project.get("smoke_test_plan") or "{}")
    last_reminder = plan.get("last_reminder_at", "")
    reminder_count = plan.get("reminder_count", 0)
    doc_sent = plan.get("doc_sent_at", "")

    now = datetime.now(timezone.utc)

    # Se non c'e' un doc_sent_at o last_reminder, usa created_at del primo blocker
    if not last_reminder and not doc_sent:
        oldest_blocker = min(pending, key=lambda x: x.get("created_at", ""))
        last_reminder = oldest_blocker.get("created_at", now.isoformat())

    # Controlla se sono passate 24h dall'ultimo reminder
    try:
        last_dt = datetime.fromisoformat(last_reminder.replace("Z", "+00:00"))
        hours_since = (now - last_dt).total_seconds() / 3600
    except Exception:
        hours_since = 25  # Forza invio se errore parsing

    if hours_since < 23:
        return False  # Non ancora tempo per il reminder

    # Costruisci reminder
    pending_titles = [p.get("payload", {}).get("problem", "azione pendente") for p in pending]
    pending_list = ""
    for t in pending_titles:
        pending_list += "- " + t + "\n"

    if reminder_count >= 7:
        # Dopo 7 giorni → alert in #strategy
        alert_msg = (
            "ALERT — Smoke test " + brand_name + " bloccato da " + str(reminder_count) + " giorni\n"
            + SEP + "\n"
            "Mirco non ha completato le azioni richieste:\n"
            + pending_list
            + SEP + "\n"
            "Valutare se archiviare il progetto o ripianificare."
        )
        from execution.pipeline import _send_to_chief_topic
        _send_to_chief_topic("cso", alert_msg)
        logger.warning("[SMOKE_REMINDER] project %s bloccato da %d giorni, alert in #strategy",
                       project_id, reminder_count)
    else:
        # Reminder nel topic cantiere
        msg = (
            "PROMEMORIA — Smoke test " + brand_name + " in attesa\n"
            + SEP + "\n"
            "Azioni ancora da completare:\n"
            + pending_list + "\n"
            "Quando hai completato un'azione, premi il pulsante 'Completato' nel messaggio sopra.\n"
            "Quando tutte le azioni sono completate → lo smoke test parte in automatico."
        )
        _send_topic_raw(group_id, topic_id, msg)

    # Aggiorna contatore reminder nel piano
    try:
        plan["last_reminder_at"] = now.isoformat()
        plan["reminder_count"] = reminder_count + 1
        supabase.table("projects").update({
            "smoke_test_plan": json.dumps(plan),
        }).eq("id", project_id).execute()
    except Exception:
        pass

    return True


def _send_daily_update_for_project(project):
    """Invia aggiornamento giornaliero per un singolo progetto."""
    project_id = project["id"]
    brand_name = project.get("brand_name", project.get("name", ""))
    topic_id = project.get("topic_id")
    group_id = _get_group_id()

    if not group_id or not topic_id:
        return

    # Carica smoke test attivo
    try:
        st = supabase.table("smoke_tests").select("*").eq("project_id", project_id).order("started_at", desc=True).limit(1).execute()
        if not st.data:
            return
        smoke = st.data[0]
        smoke_id = smoke["id"]
    except Exception:
        return

    # Calcola giorno
    started = smoke.get("started_at", "")
    duration = smoke.get("duration_days", 7)
    day = 1
    if started:
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            day = (datetime.now(timezone.utc) - start_dt).days + 1
        except Exception:
            day = 1

    # Conta prospect aggiornati
    try:
        prospects = supabase.table("smoke_test_prospects").select("status").eq("smoke_test_id", smoke_id).execute()
        all_p = prospects.data or []
    except Exception:
        all_p = []

    total = len(all_p)
    contacted = sum(1 for p in all_p if p.get("status") not in ("pending",))
    positive = sum(1 for p in all_p if p.get("status") in ("positive", "interested", "form_compiled", "demo_requested"))
    pct = round(positive / max(contacted, 1) * 100)

    msg = (
        "Giorno " + str(day) + "/" + str(duration) + " — " + brand_name + "\n" +
        str(contacted) + "/" + str(total) + " contatti raggiunti, " +
        str(positive) + " risposte positive (" + str(pct) + "%)"
    )

    # Se ultimo giorno, auto-trigger analisi
    if day >= duration:
        msg += "\n\nSmoke test completato! Analisi risultati in corso..."
        _send_topic_raw(group_id, topic_id, msg)
        try:
            analyze_smoke_results(project_id)
        except Exception as e:
            logger.warning("[SMOKE_DAILY] auto-analyze: %s", e)
        return

    _send_topic_raw(group_id, topic_id, msg)

    # Salva daily update in smoke_tests
    try:
        updates = json.loads(smoke.get("daily_updates") or "[]")
        updates.append({
            "day": day,
            "contacted": contacted,
            "positive": positive,
            "pct": pct,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        supabase.table("smoke_tests").update({"daily_updates": json.dumps(updates)}).eq("id", smoke_id).execute()
    except Exception:
        pass


# ============================================================
# LEGACY WRAPPERS (backwards compatibility)
# ============================================================

def run_smoke_test_setup(project_id):
    """Legacy wrapper — chiama start_smoke_test."""
    return start_smoke_test(project_id)


def analyze_feedback_for_spec(project_id):
    """Legacy wrapper — chiama analyze_smoke_results."""
    return analyze_smoke_results(project_id)
