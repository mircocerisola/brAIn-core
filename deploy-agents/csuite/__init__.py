"""
brAIn C-Suite AI — 8 Chief agents.
Registry e routing per dominio.
"""
from csuite.cso import CSO
from csuite.cfo import CFO
from csuite.cmo import CMO
from csuite.cto import CTO
from csuite.coo import COO
from csuite.cpo import CPO
from csuite.clo import CLO
from csuite.cpeo import CPeO

# Istanze singleton
_chiefs = {
    "strategy": CSO(),
    "finance": CFO(),
    "marketing": CMO(),
    "tech": CTO(),
    "ops": COO(),
    "product": CPO(),
    "legal": CLO(),
    "people": CPeO(),
}

# Keyword mapping per routing da testo
KEYWORD_ROUTING = {
    "cso": "strategy", "strategia": "strategy", "mercato": "strategy",
    "competizione": "strategy", "opportunità": "strategy", "pivot": "strategy",
    "cfo": "finance", "costi": "finance", "budget": "finance", "revenue": "finance",
    "finanza": "finance", "burn rate": "finance", "marginalità": "finance",
    "cmo": "marketing", "marketing": "marketing", "brand": "marketing",
    "growth": "marketing", "conversione": "marketing", "ads": "marketing",
    "cto": "tech", "infrastruttura": "tech", "deploy": "tech", "codice": "tech",
    "architettura": "tech", "sicurezza tecnica": "tech",
    "coo": "ops", "operazioni": "ops", "cantiere": "ops", "pipeline": "ops",
    "blocco": "ops", "collo di bottiglia": "ops",
    "cpo": "product", "prodotto": "product", "ux": "product", "roadmap": "product",
    "feedback utenti": "product", "mvp": "product",
    "clo": "legal", "legale": "legal", "compliance": "legal", "gdpr": "legal",
    "contratto": "legal", "rischio legale": "legal",
    "cpeo": "people", "team": "people", "manager": "people",
    "collaboratori": "people", "revenue share": "people",
}


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
