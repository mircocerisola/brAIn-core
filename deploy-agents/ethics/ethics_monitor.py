"""
brAIn Ethics Monitor v1.0
Valuta progetti rispetto al Codice Etico brAIn.
Endpoint: POST /ethics/check {project_id}
Chiamato da: spec_generator, build_agent, landing_page_generator.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Dict, Any, List

from core.config import supabase, claude, logger
from core.utils import notify_telegram, log_to_supabase
from core.templates import now_rome


# ============================================================
# CODICE ETICO brAIn — HARDCODED, NON MODIFICABILE VIA PROMPT
# ============================================================

ETHICAL_CODE = {
    "version": "1.0",
    "principles": [
        {
            "id": "E1",
            "name": "Nessun danno intenzionale",
            "description": "Il progetto non deve causare danni fisici, psicologici o economici intenzionali agli utenti o a terzi.",
            "severity": "critical",
        },
        {
            "id": "E2",
            "name": "Trasparenza AI",
            "description": "Il progetto deve dichiarare chiaramente l'uso di AI dove rilevante per gli utenti.",
            "severity": "high",
        },
        {
            "id": "E3",
            "name": "Privacy e GDPR",
            "description": "Il progetto deve rispettare GDPR e non raccogliere dati personali senza consenso esplicito.",
            "severity": "critical",
        },
        {
            "id": "E4",
            "name": "Nessuna manipolazione",
            "description": "Il progetto non deve usare dark patterns, manipolazione psicologica o inganni per ottenere comportamenti dagli utenti.",
            "severity": "critical",
        },
        {
            "id": "E5",
            "name": "Accessibilità",
            "description": "Il progetto deve essere ragionevolmente accessibile (no discriminazione per disabilità, lingua, reddito).",
            "severity": "medium",
        },
        {
            "id": "E6",
            "name": "Impatto ambientale",
            "description": "Il progetto deve minimizzare l'impatto ambientale (efficienza energetica, no spreco computazionale).",
            "severity": "low",
        },
        {
            "id": "E7",
            "name": "Equità algoritmica",
            "description": "Se il progetto usa algoritmi decisionali, devono essere equi e non discriminatori per genere, etnia, nazionalità.",
            "severity": "high",
        },
        {
            "id": "E8",
            "name": "Legalità",
            "description": "Il progetto deve operare nel pieno rispetto delle leggi applicabili nell'UE e nei mercati target.",
            "severity": "critical",
        },
    ],
    "auto_block_severities": ["critical"],  # Blocca automaticamente per violazioni critical
}


def check_project_ethics(project_id: int) -> Dict[str, Any]:
    """
    Esegue ethics check su un progetto.
    Ritorna: {status, violations, blocked, project_id}
    Se blocked=True, aggiorna projects.status='ethics_blocked' e notifica Mirco.
    """
    logger.info(f"[ETHICS] Checking project {project_id}...")

    # Carica progetto
    try:
        r = supabase.table("projects").select(
            "id,name,spec_md,status,solution_id"
        ).eq("id", project_id).execute()
        if not r.data:
            return {"status": "error", "error": f"Project {project_id} not found"}
        project = r.data[0]
    except Exception as e:
        return {"status": "error", "error": str(e)}

    spec_text = (project.get("spec_md") or "")[:4000]
    project_name = project.get("name", f"Project {project_id}")

    # Carica solution per più contesto
    solution_desc = ""
    try:
        sol_r = supabase.table("solutions").select("title,description").eq(
            "id", project.get("solution_id", 0)
        ).execute()
        if sol_r.data:
            solution_desc = f"Soluzione: {sol_r.data[0].get('title', '')} — {sol_r.data[0].get('description', '')[:500]}"
    except Exception:
        pass

    # Costruisci prompt per Claude
    principles_text = "\n".join(
        f"- [{p['id']}] {p['name']} (severity: {p['severity']}): {p['description']}"
        for p in ETHICAL_CODE["principles"]
    )

    prompt = f"""Sei il Ethics Monitor di brAIn. Valuta il seguente progetto rispetto al Codice Etico.

PROGETTO: {project_name}
{solution_desc}

SPEC (estratto):
{spec_text}

PRINCIPI ETICI:
{principles_text}

Per ogni violazione rilevata, rispondi con JSON array:
[
  {{
    "principle_id": "E1",
    "principle_name": "...",
    "violation": "descrizione specifica della violazione",
    "severity": "critical|high|medium|low",
    "suggestion": "come risolvere"
  }}
]

Se nessuna violazione: risposta JSON = []
Rispondi SOLO con il JSON array, nessun altro testo."""

    violations: List[Dict] = []
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Parse JSON
        if raw.startswith("["):
            violations = json.loads(raw)
        else:
            # Cerca array nel testo
            import re
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            if m:
                violations = json.loads(m.group())
    except Exception as e:
        logger.error(f"[ETHICS] Claude error: {e}")
        # Non bloccare per errori tecnici
        violations = []

    # Determina se bloccare
    auto_block_severities = set(ETHICAL_CODE["auto_block_severities"])
    critical_violations = [v for v in violations if v.get("severity") in auto_block_severities]
    blocked = len(critical_violations) > 0

    # Salva violations nel DB
    for v in violations:
        try:
            supabase.table("ethics_violations").insert({
                "project_id": project_id,
                "principle_id": v.get("principle_id", ""),
                "principle_name": v.get("principle_name", ""),
                "violation": v.get("violation", ""),
                "severity": v.get("severity", "medium"),
                "suggestion": v.get("suggestion", ""),
                "blocked": blocked,
                "ethics_version": ETHICAL_CODE["version"],
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"[ETHICS] Save violation error: {e}")

    # Se blocked: aggiorna status progetto
    if blocked:
        try:
            supabase.table("projects").update({"status": "ethics_blocked"}).eq("id", project_id).execute()
        except Exception as e:
            logger.warning(f"[ETHICS] Update project status error: {e}")

        # Notifica Mirco — IMMEDIATA
        violations_text = "\n".join(
            f"⚠️ [{v['principle_id']}] {v['principle_name']}: {v['violation'][:100]}"
            for v in critical_violations
        )
        notify_telegram(
            f"🚫 *ETHICS BLOCK — {project_name}*\n\n"
            f"Il progetto è stato bloccato per violazioni etiche critiche:\n{violations_text}\n\n"
            f"Progetto ID: {project_id} — status: ethics_blocked",
            level="critical",
            source="ethics_monitor",
        )
        logger.warning(f"[ETHICS] Project {project_id} BLOCKED — {len(critical_violations)} critical violations")
    else:
        if violations:
            # Violazioni non critiche: notifica senza bloccare
            v_text = "\n".join(f"- [{v['principle_id']}] {v['violation'][:80]}" for v in violations)
            notify_telegram(
                f"⚠️ *Ethics Warning — {project_name}*\n{v_text}",
                level="warning",
                source="ethics_monitor",
            )
        logger.info(f"[ETHICS] Project {project_id} passed — {len(violations)} warnings")

    log_to_supabase(
        "ethics_monitor", "check", 0,
        f"project_id={project_id}",
        f"violations={len(violations)} blocked={blocked}",
        "claude-haiku-4-5-20251001",
    )

    return {
        "status": "ok",
        "project_id": project_id,
        "project_name": project_name,
        "violations": violations,
        "blocked": blocked,
        "ethics_version": ETHICAL_CODE["version"],
    }
