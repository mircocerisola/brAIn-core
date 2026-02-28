"""
CDO — Chief Data Officer.
Non è un Chief autonomo: è una funzione del CTO.
Monitora qualità dati, knowledge base, e storage.
Report e notifiche vanno nel topic #technology (chief_topic_cto).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome


def _get_technology_topic() -> tuple:
    """Ritorna (group_id, topic_id) del topic #technology."""
    try:
        r = supabase.table("org_config").select("value") \
            .eq("key", "telegram_group_id").execute()
        group_id = int(r.data[0]["value"]) if r.data else None
    except Exception:
        group_id = None
    try:
        r = supabase.table("org_config").select("value") \
            .eq("key", "chief_topic_cto").execute()
        topic_id = int(r.data[0]["value"]) if r.data else None
    except Exception:
        topic_id = None
    return group_id, topic_id


def _send_to_topic(group_id, topic_id, text: str) -> None:
    """Invia testo al topic Telegram."""
    if not group_id or not topic_id or not TELEGRAM_BOT_TOKEN:
        return
    import requests as _req
    try:
        _req.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": group_id, "message_thread_id": topic_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"[CDO] Telegram send error: {e}")


def _save_cdo_report(cto_domain: str, report_type: str, title: str, content: str) -> None:
    """Salva report CDO in chief_knowledge del CTO."""
    try:
        supabase.table("chief_knowledge").insert({
            "chief_id": cto_domain,
            "knowledge_type": "cdo_report",
            "title": title,
            "content": content,
            "importance": 3,
            "created_at": now_rome().isoformat(),
            "updated_at": now_rome().isoformat(),
        }).execute()
    except Exception as e:
        logger.warning(f"[CDO] save_cdo_report error: {e}")


def audit_data_quality() -> Dict[str, Any]:
    """
    Controlla qualità dei dati nel DB:
    - Tabelle con >80% NULL su colonne chiave
    - Duplicati in problems
    - org_knowledge entries da >60gg
    - chief_knowledge non aggiornato da >14gg per un Chief
    Produce report al CTO nel topic #technology.
    """
    start = now_rome()

    issues: List[str] = []
    logger.info("[CDO] Avvio audit_data_quality")

    # 1. Duplicati in problems (stesso fingerprint o titolo quasi uguale)
    try:
        r = supabase.table("problems").select("id,title,fingerprint").execute()
        rows = r.data or []
        seen_fp = {}
        dup_count = 0
        for row in rows:
            fp = row.get("fingerprint", "")
            if fp and fp in seen_fp:
                dup_count += 1
            elif fp:
                seen_fp[fp] = row["id"]
        if dup_count > 0:
            issues.append(f"⚠️ {dup_count} problemi con fingerprint duplicato in problems")
    except Exception as e:
        logger.warning(f"[CDO] duplicates check: {e}")

    # 2. org_knowledge entries da >60gg (candidati archiviazione)
    try:
        cutoff = (start - timedelta(days=60)).isoformat()
        r = supabase.table("org_knowledge").select("id,category") \
            .lt("created_at", cutoff).execute()
        stale_count = len(r.data or [])
        if stale_count > 10:
            issues.append(f"📦 {stale_count} entries org_knowledge da >60gg — candidati archiviazione")
    except Exception as e:
        logger.warning(f"[CDO] org_knowledge check: {e}")

    # 3. chief_knowledge non aggiornato da >14gg per almeno un Chief
    try:
        cutoff14 = (start - timedelta(days=14)).isoformat()
        for chief_id in ["cso", "coo", "cto", "cmo", "cfo", "clo", "cpeo"]:
            r = supabase.table("chief_knowledge").select("id,updated_at") \
                .eq("chief_id", chief_id) \
                .neq("knowledge_type", "profile") \
                .order("updated_at", desc=True).limit(1).execute()
            if not r.data:
                issues.append(f"🔴 {chief_id.upper()}: nessuna conoscenza specialistica")
            else:
                last_updated = r.data[0].get("updated_at", "")
                if last_updated and last_updated < cutoff14:
                    issues.append(f"⚠️ {chief_id.upper()}: knowledge non aggiornata da >14gg")
    except Exception as e:
        logger.warning(f"[CDO] chief_knowledge staleness: {e}")

    # 4. projects con spec_md NULL e status non archived
    try:
        r = supabase.table("projects").select("id,name,status") \
            .is_("spec_md", "null").neq("status", "archived").execute()
        no_spec = len(r.data or [])
        if no_spec > 0:
            names = [row.get("name", "?")[:30] for row in (r.data or [])[:3]]
            issues.append(f"⚠️ {no_spec} cantieri senza spec_md: {', '.join(names)}")
    except Exception as e:
        logger.warning(f"[CDO] spec_md check: {e}")

    # Costruisci report
    duration_s = int((now_rome() - start).total_seconds())
    status_icon = "🟢" if not issues else ("🔴" if len(issues) > 3 else "🟡")

    report_lines = [
        f"🔍 CDO Data Audit — {start.strftime('%Y-%m-%d')}",
        "",
        f"Segnale: {status_icon} {'OK' if not issues else f'{len(issues)} problemi trovati'}",
    ]
    if issues:
        report_lines.extend(issues)
    else:
        report_lines.append("✅ Nessun problema rilevato")
    report_lines.append("")
    report_lines.append(f"Durata: {duration_s}s")

    report_text = "\n".join(report_lines)

    # Salva in chief_knowledge del CTO
    _save_cdo_report("cto", "data_audit", f"CDO Data Audit {start.strftime('%Y-%m-%d')}", report_text)

    # Invia nel topic #technology
    group_id, topic_id = _get_technology_topic()
    _send_to_topic(group_id, topic_id, report_text)

    logger.info(f"[CDO] audit completato: {len(issues)} issues")
    return {"status": "ok", "issues": len(issues), "problems": issues}


def optimize_knowledge_storage() -> Dict[str, Any]:
    """
    Analizza storage conoscenza:
    - Conta righe per tabella knowledge
    - Identifica tabelle con 0 righe da >30gg
    - Suggerisce compressione learning simili
    - Propone threshold auto-archiviazione
    """
    start = now_rome()

    logger.info("[CDO] Avvio optimize_knowledge_storage")

    table_stats: Dict[str, int] = {}
    suggestions: List[str] = []

    # Conta righe per tabella
    for table in ["org_shared_knowledge", "chief_knowledge", "org_knowledge", "chief_memory", "chief_decisions"]:
        try:
            r = supabase.table(table).select("id", count="exact").execute()
            cnt = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
            table_stats[table] = cnt
        except Exception as e:
            logger.warning(f"[CDO] count {table}: {e}")
            table_stats[table] = -1

    # Controlla chief_decisions: se > 500 per un Chief suggerisci compressione
    try:
        for chief_id in ["cso", "coo", "cto", "cmo", "cfo", "clo", "cpeo"]:
            r = supabase.table("chief_decisions").select("id") \
                .eq("chief_domain", chief_id).execute()
            cnt = len(r.data or [])
            if cnt > 500:
                suggestions.append(f"📦 {chief_id.upper()}: {cnt} chief_decisions — considera archiviazione dei >90gg")
    except Exception as e:
        logger.warning(f"[CDO] chief_decisions analysis: {e}")

    # Coaching duplicati: chief_knowledge tipo 'coaching' simili
    try:
        r = supabase.table("chief_knowledge").select("chief_id,title,content") \
            .eq("knowledge_type", "coaching").execute()
        coaching_rows = r.data or []
        if len(coaching_rows) > 50:
            suggestions.append(f"💡 {len(coaching_rows)} coaching entries — valuta merge di quelli simili per Chief")
    except Exception as e:
        logger.warning(f"[CDO] coaching analysis: {e}")

    duration_s = int((now_rome() - start).total_seconds())

    stats_lines = [f"  {t}: {cnt} righe" for t, cnt in table_stats.items()]
    report_lines = [
        f"💾 CDO Storage Optimization — {start.strftime('%Y-%m-%d')}",
        "",
        "Tabelle knowledge:",
    ] + stats_lines
    if suggestions:
        report_lines.append("")
        report_lines.extend(suggestions)
    else:
        report_lines.append("✅ Storage ottimale, nessuna azione necessaria")
    report_lines.append(f"\nDurata: {duration_s}s")

    report_text = "\n".join(report_lines)
    _save_cdo_report("cto", "storage_optimization", f"CDO Storage {start.strftime('%Y-%m-%d')}", report_text)

    group_id, topic_id = _get_technology_topic()
    _send_to_topic(group_id, topic_id, report_text)

    logger.info(f"[CDO] storage analysis completata: {table_stats}")
    return {"status": "ok", "table_stats": table_stats, "suggestions": suggestions}


def monitor_knowledge_growth() -> Dict[str, Any]:
    """
    Traccia crescita settimanale org_shared_knowledge e chief_knowledge per ogni Chief.
    Alert CPeO se un Chief non riceve nuova conoscenza da >7gg.
    """
    start = now_rome()

    week_ago = (start - timedelta(days=7)).isoformat()
    logger.info("[CDO] Avvio monitor_knowledge_growth")

    growth: Dict[str, int] = {}
    alerts: List[str] = []

    # Crescita org_shared_knowledge
    try:
        r = supabase.table("org_shared_knowledge").select("id") \
            .gte("created_at", week_ago).execute()
        growth["org_shared"] = len(r.data or [])
    except Exception:
        growth["org_shared"] = 0

    # Crescita per ogni Chief
    for chief_id in ["cso", "coo", "cto", "cmo", "cfo", "clo", "cpeo"]:
        try:
            r = supabase.table("chief_knowledge").select("id") \
                .eq("chief_id", chief_id) \
                .gte("created_at", week_ago).execute()
            cnt = len(r.data or [])
            growth[chief_id] = cnt
            if cnt == 0:
                alerts.append(chief_id.upper())
        except Exception:
            growth[chief_id] = 0
            alerts.append(chief_id.upper())

    duration_s = int((now_rome() - start).total_seconds())

    chief_lines = []
    for cid in ["cso", "coo", "cto", "cmo", "cfo", "clo", "cpeo"]:
        cnt = growth.get(cid, 0)
        icon = "✅" if cnt > 0 else "⚠️"
        chief_lines.append(f"  {icon} {cid.upper()}: +{cnt} nuove conoscenze")

    report_lines = [
        f"📈 CDO Knowledge Growth — {start.strftime('%Y-%m-%d')} (ultimi 7gg)",
        "",
        f"org_shared_knowledge: +{growth.get('org_shared', 0)} entries",
    ] + chief_lines

    if alerts:
        report_lines.append("")
        report_lines.append(f"⚠️ Nessuna nuova conoscenza: {', '.join(alerts)}")
        report_lines.append("→ CPeO dovrebbe intervenire con coaching")

        # Alert anche al topic #people (CPeO)
        try:
            r_peo = supabase.table("org_config").select("value").eq("key", "chief_topic_cpeo").execute()
            peo_topic = int(r_peo.data[0]["value"]) if r_peo.data else None
            r_grp = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            grp_id = int(r_grp.data[0]["value"]) if r_grp.data else None
            if peo_topic and grp_id:
                cpeo_alert = (
                    f"\U0001f4e2 CDO Alert — Knowledge Stale\n\n"
                    f"Chief senza nuova conoscenza nell'ultima settimana: {', '.join(alerts)}\n"
                    f"Pianifica coaching immediato."
                )
                _send_to_topic(grp_id, peo_topic, cpeo_alert)
        except Exception as e:
            logger.warning(f"[CDO] CPeO alert: {e}")
    else:
        report_lines.append("✅ Tutti i Chief hanno ricevuto nuova conoscenza")

    report_lines.append(f"\nDurata: {duration_s}s")
    report_text = "\n".join(report_lines)

    _save_cdo_report("cto", "knowledge_growth", f"CDO Knowledge Growth {start.strftime('%Y-%m-%d')}", report_text)
    group_id, topic_id = _get_technology_topic()
    _send_to_topic(group_id, topic_id, report_text)

    logger.info(f"[CDO] growth monitor completato: alerts={alerts}")
    return {"status": "ok", "growth": growth, "alerts": alerts}
