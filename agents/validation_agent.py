"""
brAIn Validation Agent
Report settimanale SCALE/PIVOT/KILL per progetti in stato 'validating'.
Invia report nel Forum Topic del progetto + DM Mirco.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic
import requests
from supabase import create_client

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1000


def _get_telegram_group_id():
    try:
        r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
        if r.data:
            return json.loads(r.data[0]["value"])
    except:
        pass
    return None


def _get_telegram_chat_id():
    try:
        r = supabase.table("org_config").select("value").eq("key", "telegram_user_id").execute()
        if r.data:
            return json.loads(r.data[0]["value"])
    except:
        pass
    return None


def _send_telegram(chat_id, text, thread_id=None):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return
    payload = {"chat_id": chat_id, "text": text}
    if thread_id:
        payload["message_thread_id"] = thread_id
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception as e:
        logger.error(f"[TG] {e}")


def _log(action, input_summary, output_summary, model, tokens_in, tokens_out, cost, duration_ms, status="success", error=None):
    try:
        supabase.table("agent_logs").insert({
            "agent_id": "validation_agent",
            "action": action,
            "layer": 3,
            "input_summary": (input_summary or "")[:500],
            "output_summary": (output_summary or "")[:500],
            "model_used": model,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "status": status,
            "error": error,
        }).execute()
    except Exception as e:
        logger.error(f"[LOG] {e}")


VALIDATION_SYSTEM_PROMPT = """Sei il Portfolio Manager di brAIn. Analizza le metriche di un progetto MVP e dai un verdetto chiaro.

VERDETTO (scegli uno solo):
- SCALE: metriche >= target, crescita positiva, aumenta investimento
- PIVOT: metriche < 50% target ma segnali positivi, cambia angolo
- KILL: metriche < 30% target, 3+ settimane consecutive, nessun segnale, ferma e archivia

FORMATO RISPOSTA (testo piano, max 8 righe):
VERDETTO: [SCALE/PIVOT/KILL]
KPI attuale: [valore] vs target [valore] ([percentuale]%)
Trend: [crescente/stabile/decrescente]
Revenue settimana corrente: EUR [valore]
Motivo principale: [1 riga]
Azione raccomandata: [1 riga concreta]"""


def _analyze_project(project, metrics):
    """Analizza metriche vs KPI target e ritorna verdetto."""
    kpis = project.get("kpis") or {}
    if isinstance(kpis, str):
        try:
            kpis = json.loads(kpis)
        except:
            kpis = {}

    name = project.get("name", "Progetto")
    primary_kpi = kpis.get("primary", "customers")
    target_w4 = kpis.get("target_week4", 0)
    target_w12 = kpis.get("target_week12", 0)
    revenue_target = kpis.get("revenue_target_month3_eur", 0)

    # Prepara sommario metriche
    metrics_summary = []
    total_revenue = 0.0
    for m in metrics:
        metrics_summary.append(
            f"Week {m['week']}: customers={m.get('customers_count', 0)}, "
            f"revenue={m.get('revenue_eur', 0):.2f} EUR, "
            f"{m.get('key_metric_name', primary_kpi)}={m.get('key_metric_value', 0)}"
        )
        total_revenue += float(m.get("revenue_eur", 0) or 0)

    current_week = max((m["week"] for m in metrics), default=0)

    user_prompt = f"""Progetto: {name}
KPI primario target: {primary_kpi} — settimana 4: {target_w4}, settimana 12: {target_w12}
Revenue target mese 3: EUR {revenue_target}
Settimana corrente: {current_week}

Ultime 4 settimane di metriche:
{chr(10).join(metrics_summary) if metrics_summary else "Nessuna metrica registrata"}

Revenue totale finora: EUR {total_revenue:.2f}

Analizza e dai il verdetto."""

    tokens_in = tokens_out = 0
    try:
        response = claude.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=VALIDATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        verdict = response.content[0].text.strip()
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[VALIDATION] Claude error: {e}")
        return None, 0, 0

    return verdict, tokens_in, tokens_out


def run():
    """Ciclo principale: valida tutti i progetti in stato 'validating'."""
    start = time.time()
    logger.info("[VALIDATION] Avvio ciclo settimanale")

    group_id = _get_telegram_group_id()
    chat_id = _get_telegram_chat_id()

    try:
        projects_result = supabase.table("projects").select("*").eq("status", "validating").execute()
        projects = projects_result.data or []
    except Exception as e:
        logger.error(f"[VALIDATION] DB error: {e}")
        return {"status": "error", "error": str(e)}

    if not projects:
        logger.info("[VALIDATION] Nessun progetto in stato validating")
        return {"status": "ok", "projects_analyzed": 0}

    logger.info(f"[VALIDATION] {len(projects)} progetti da analizzare")

    total_tokens_in = total_tokens_out = 0
    analyzed = 0
    sep = "\u2501" * 15

    for project in projects:
        project_id = project["id"]
        name = project.get("name", f"Progetto {project_id}")
        topic_id = project.get("topic_id")

        try:
            metrics_result = supabase.table("project_metrics").select("*")\
                .eq("project_id", project_id)\
                .order("week", desc=True)\
                .limit(4)\
                .execute()
            metrics = list(reversed(metrics_result.data or []))
        except Exception as e:
            logger.warning(f"[VALIDATION] Metrics load error for {project_id}: {e}")
            metrics = []

        verdict_text, tok_in, tok_out = _analyze_project(project, metrics)
        if not verdict_text:
            continue

        total_tokens_in += tok_in
        total_tokens_out += tok_out
        analyzed += 1

        # Aggiorna status se KILL
        if "KILL" in verdict_text.upper():
            try:
                supabase.table("projects").update({
                    "status": "killed",
                    "notes": f"KILL — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {verdict_text[:200]}",
                }).eq("id", project_id).execute()
            except:
                pass

        # Messaggio report
        report_msg = (
            f"\U0001f4ca REPORT SETTIMANALE\n"
            f"{sep}\n"
            f"\U0001f3d7\ufe0f {name}\n"
            f"{verdict_text}\n"
            f"{sep}"
        )

        # Invia nel topic del progetto
        if group_id and topic_id:
            _send_telegram(group_id, report_msg, thread_id=topic_id)

        # Invia anche in DM (sempre)
        if chat_id:
            _send_telegram(chat_id, report_msg)

        logger.info(f"[VALIDATION] {name}: report inviato")

    duration_ms = int((time.time() - start) * 1000)
    cost = (total_tokens_in * 0.8 + total_tokens_out * 4.0) / 1_000_000
    _log("validation_weekly", f"{len(projects)} progetti", f"{analyzed} analizzati",
         MODEL, total_tokens_in, total_tokens_out, cost, duration_ms)

    logger.info(f"[VALIDATION] Completato: {analyzed} progetti in {duration_ms}ms")
    return {
        "status": "ok",
        "projects_analyzed": analyzed,
        "cost_usd": round(cost, 6),
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
