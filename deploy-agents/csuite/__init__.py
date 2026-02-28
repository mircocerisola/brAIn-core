"""
brAIn C-Suite AI — 7 Chief agents.
Registry e routing per dominio.
"""
from csuite.cso import CSO
from csuite.cfo import CFO
from csuite.cmo import CMO
from csuite.cto import CTO
from csuite.coo import COO
from csuite.clo import CLO
from csuite.cpeo import CPeO

# Istanze singleton
_chiefs = {
    "strategy": CSO(),
    "finance": CFO(),
    "marketing": CMO(),
    "tech": CTO(),
    "ops": COO(),
    "legal": CLO(),
    "people": CPeO(),
}

# v5.36: ROUTING_MAP unificato — keyword → chief_id (sostituisce KEYWORD_ROUTING e ROUTING_KEYWORDS)
ROUTING_MAP = {
    # CSO
    "cso": "cso", "strategia": "cso", "mercato": "cso",
    "competizione": "cso", "opportunità": "cso", "pivot": "cso",
    "pipeline": "cso", "bos": "cso", "problemi globali": "cso",
    "smoke test": "cso", "validazione mercato": "cso", "prospect": "cso",
    # CFO
    "cfo": "cfo", "costi": "cfo", "budget": "cfo", "revenue": "cfo",
    "finanza": "cfo", "burn rate": "cfo", "marginalità": "cfo",
    "spese": "cfo", "fatturato": "cfo", "revenue share": "cfo",
    # CMO
    "cmo": "cmo", "marketing": "cmo", "brand": "cmo",
    "growth": "cmo", "conversione": "cmo", "ads": "cmo",
    "logo": "cmo", "immagine": "cmo", "design": "cmo", "avatar": "cmo",
    "brand identity": "cmo", "grafica": "cmo", "visual": "cmo",
    # CTO
    "cto": "cto", "infrastruttura": "cto", "deploy": "cto", "codice": "cto",
    "architettura": "cto", "sicurezza tecnica": "cto", "sicurezza": "cto",
    "vulnerabilità": "cto", "bug": "cto",
    # COO
    "coo": "coo", "operazioni": "coo", "cantiere": "coo", "cantieri": "coo",
    "blocco": "coo", "collo di bottiglia": "coo",
    "cpo": "coo", "prodotto": "coo", "ux": "coo", "roadmap": "coo",
    "feedback utenti": "coo", "mvp": "coo", "build": "coo", "spec": "coo",
    "lancio": "coo", "feature": "coo",
    "processi": "coo", "efficienza": "coo", "coda": "coo",
    "bottleneck": "coo", "flusso": "coo",
    # CLO
    "clo": "clo", "legale": "clo", "compliance": "clo", "gdpr": "clo",
    "contratto": "clo", "rischio legale": "clo", "termini": "clo",
    "privacy policy": "clo",
    # CPeO
    "cpeo": "cpeo", "team": "cpeo", "manager": "cpeo",
    "collaboratori": "cpeo", "onboarding": "cpeo", "formazione": "cpeo",
    "persone": "cpeo", "team building": "cpeo",
}

# Mappa chief_id → domain per compatibilita con _chiefs
_CHIEF_ID_TO_DOMAIN = {
    "cso": "strategy", "cfo": "finance", "cmo": "marketing",
    "cto": "tech", "coo": "ops", "clo": "legal", "cpeo": "people",
}

# Legacy alias per compatibilita
KEYWORD_ROUTING = {k: _CHIEF_ID_TO_DOMAIN.get(v, v) for k, v in ROUTING_MAP.items()}


def get_chief(domain: str):
    """Ritorna l'istanza del Chief per il dominio dato."""
    return _chiefs.get(domain)


def route_to_chief(text: str):
    """
    Tenta di identificare il Chief appropriato dal testo.
    Ritorna (chief_instance, domain) o (None, None).
    """
    lower = text.lower()
    for keyword, domain in KEYWORD_ROUTING.items():
        if keyword in lower:
            return _chiefs.get(domain), domain
    return None, None


def run_all_briefings():
    """Genera briefing settimanale per tutti i Chief."""
    results = {}
    for domain, chief in _chiefs.items():
        try:
            results[domain] = chief.generate_weekly_briefing()
        except Exception as e:
            results[domain] = {"status": "error", "error": str(e)}
    return results


async def run_morning_reports():
    """Report mattutino sequenziale: 7 Chief con 2 min di intervallo."""
    import asyncio
    order = ["strategy", "ops", "tech", "marketing", "finance", "legal", "people"]
    results = {}
    for i, domain in enumerate(order):
        chief = _chiefs.get(domain)
        if not chief:
            continue
        try:
            text = chief.generate_brief_report()
            results[domain] = {"chief": chief.name, "status": "ok" if text else "skipped"}
        except Exception as e:
            results[domain] = {"chief": domain, "status": "error", "error": str(e)}
        if i < len(order) - 1:
            await asyncio.sleep(120)
    return results


def run_all_anomaly_checks():
    """Controlla anomalie per tutti i Chief e notifica se trovate."""
    from core.utils import notify_telegram
    all_anomalies = {}
    for domain, chief in _chiefs.items():
        try:
            anomalies = chief.check_anomalies()
            if anomalies:
                all_anomalies[domain] = anomalies
                for a in anomalies:
                    if a.get("severity") in ("critical", "high"):
                        notify_telegram(
                            f"⚠️ *{chief.name} Anomalia* [{a['severity'].upper()}]\n{a['description']}",
                            level="warning",
                            source=chief.name,
                        )
        except Exception as e:
            all_anomalies[domain] = [{"type": "check_error", "description": str(e), "severity": "low"}]
    return all_anomalies
