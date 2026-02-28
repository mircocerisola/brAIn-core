"""CPeO — Chief People & Evolution Officer. Dominio: team, manager, coaching Chief, knowledge base."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from core.base_chief import BaseChief
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome


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


def coach_chiefs() -> Dict[str, Any]:
    """
    Coaching automatico dei Chief ogni lunedì 06:30.
    Analizza ultimi 7gg di chief_decisions per ogni Chief:
    - Routing fuori dominio frequenti → Chief non conosce confini
    - Prompt bloccati sandbox → Chief tenta di sconfinare
    - Errori ripetuti stesso tipo → gap conoscenza
    Genera learning in chief_knowledge (knowledge_type='coaching', importance=4).
    Invia report al topic #people.
    """
    start = now_rome()
    week_ago = (start - timedelta(days=7)).isoformat()
    logger.info("[CPeO] Avvio coach_chiefs")

    # Recupera topic #people
    people_topic_id = None
    group_id = None
    try:
        r = supabase.table("org_config").select("value").eq("key", "chief_topic_cpeo").execute()
        if r.data:
            people_topic_id = int(r.data[0]["value"])
        r2 = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
        if r2.data:
            group_id = int(r2.data[0]["value"])
    except Exception as e:
        logger.warning(f"[CPeO] topic lookup: {e}")

    def _send_people(text: str):
        if not group_id or not people_topic_id or not TELEGRAM_BOT_TOKEN:
            return
        import requests as _req
        try:
            _req.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": group_id, "message_thread_id": people_topic_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"[CPeO] Telegram send: {e}")

    # Domain map per ogni Chief (chief_id → chief_domain)
    chief_domains = {
        "cso": "strategy", "coo": "ops", "cto": "tech",
        "cmo": "marketing", "cfo": "finance", "clo": "legal", "cpeo": "people",
    }

    learning_added = 0
    chief_status: Dict[str, str] = {}  # chief_id → "ok" | "warning: ..."

    for chief_id, domain in chief_domains.items():
        issues: List[str] = []

        try:
            # 1. Routing fuori dominio frequenti
            r = supabase.table("chief_decisions").select("decision_type,summary") \
                .eq("chief_domain", domain) \
                .like("decision_type", "routed_to_%") \
                .gte("created_at", week_ago).execute()
            routing_count = len(r.data or [])
            if routing_count >= 3:
                dest_chiefs = {}
                for row in (r.data or []):
                    dest = row.get("decision_type", "").replace("routed_to_", "")
                    dest_chiefs[dest] = dest_chiefs.get(dest, 0) + 1
                top_dest = sorted(dest_chiefs.items(), key=lambda x: x[1], reverse=True)[0]
                issues.append(
                    f"routing_fuori_dominio: {routing_count} richieste ridirezionate (più frequente: → {top_dest[0].upper()})"
                )
        except Exception as e:
            logger.warning(f"[CPeO] routing check {chief_id}: {e}")

        try:
            # 2. Prompt bloccati sandbox
            r = supabase.table("code_tasks").select("id,title") \
                .eq("requested_by", chief_id) \
                .eq("sandbox_passed", False) \
                .gte("created_at", week_ago).execute()
            blocked_count = len(r.data or [])
            if blocked_count >= 2:
                issues.append(
                    f"sandbox_violations: {blocked_count} prompt bloccati — stai tentando di accedere ad aree fuori perimetro"
                )
        except Exception as e:
            logger.warning(f"[CPeO] sandbox check {chief_id}: {e}")

        try:
            # 3. Errori ripetuti stesso tipo (da agent_logs)
            r = supabase.table("agent_logs").select("agent_id,error") \
                .like("agent_id", f"%{chief_id}%") \
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
                    f"errori_ripetuti: {len(error_rows)} errori nell'ultima settimana (più comune: '{top_error[0]}')"
                )
        except Exception as e:
            logger.warning(f"[CPeO] errors check {chief_id}: {e}")

        # Genera learning per ogni issue trovata
        if not issues:
            chief_status[chief_id] = "ok"
            continue

        chief_status[chief_id] = f"warning: {len(issues)} problemi"

        for issue in issues:
            issue_type = issue.split(":")[0]
            # Genera istruzione specifica con Claude Haiku
            coaching_prompt = (
                f"Sei il CPeO di brAIn. Stai creando un learning per il {chief_id.upper()}.\n"
                f"Problema rilevato: {issue}\n\n"
                f"Scrivi in 2-3 frasi un'istruzione chiara e specifica per evitare questo problema in futuro. "
                f"Tono: diretto, costruttivo. Inizia con 'In futuro:'"
            )
            try:
                resp = claude.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=200,
                    messages=[{"role": "user", "content": coaching_prompt}],
                )
                instruction = resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"[CPeO] coaching generation {chief_id}: {e}")
                instruction = f"In futuro: monitora attentamente {issue_type} per evitare problemi ricorrenti."

            content = (
                f"Hai avuto il problema: {issue}\n\n"
                f"{instruction}"
            )
            try:
                supabase.table("chief_knowledge").insert({
                    "chief_id": chief_id,
                    "knowledge_type": "coaching",
                    "title": f"Coaching {issue_type} — {start.strftime('%Y-%m-%d')}",
                    "content": content,
                    "importance": 4,
                    "created_at": start.isoformat(),
                    "updated_at": start.isoformat(),
                }).execute()
                learning_added += 1
            except Exception as e:
                logger.warning(f"[CPeO] insert coaching {chief_id}: {e}")

    # Costruisci report
    status_lines = []
    for chief_id in ["cso", "coo", "cto", "cmo", "cfo", "clo", "cpeo"]:
        st = chief_status.get(chief_id, "ok")
        icon = "\u2705" if st == "ok" else "\u26a0\ufe0f"
        detail = "" if st == "ok" else f" — {st.replace('warning: ', '')}"
        status_lines.append(f"{icon} {chief_id.upper()}{detail}")

    report = (
        f"\U0001f393 Coaching settimanale Chief\n\n"
        + "\n".join(status_lines)
        + f"\n\nLearning aggiunti: {learning_added}"
    )

    _send_people(report)
    logger.info(f"[CPeO] coaching completato: learning_added={learning_added}")
    return {
        "status": "ok",
        "learning_added": learning_added,
        "chief_status": chief_status,
    }
