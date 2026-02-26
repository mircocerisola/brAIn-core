"""
brAIn BaseChief — classe base per i Chief Agent del C-Suite.
Eredita da BaseAgent. Aggiunge: domain context, briefing settimanale,
anomaly detection, receive_capability_update.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from core.config import supabase, logger
from core.base_agent import BaseAgent


class BaseChief(BaseAgent):
    """Classe base per tutti i Chief Agent (CSO, CFO, CMO, ecc.)."""

    domain: str = "general"          # es. "finance", "strategy", "marketing"
    default_model: str = "claude-sonnet-4-6"
    briefing_prompt_template: str = ""  # Override nelle sottoclassi

    def get_domain_context(self) -> Dict[str, Any]:
        """
        Ritorna contesto DB rilevante per il dominio.
        Override nelle sottoclassi per dati specifici.
        """
        context: Dict[str, Any] = {}
        try:
            # Ultime decisioni del Chief
            r = supabase.table("chief_decisions") \
                .select("decision_type,summary,created_at") \
                .eq("chief_domain", self.domain) \
                .order("created_at", desc=True).limit(5).execute()
            context["recent_decisions"] = r.data or []
        except Exception as e:
            logger.warning(f"[{self.name}] get_domain_context error: {e}")
            context["recent_decisions"] = []
        try:
            # Ultima memoria del Chief
            r = supabase.table("chief_memory") \
                .select("key,value,updated_at") \
                .eq("chief_domain", self.domain) \
                .order("updated_at", desc=True).limit(10).execute()
            context["memory"] = {row["key"]: row["value"] for row in (r.data or [])}
        except Exception as e:
            logger.warning(f"[{self.name}] chief_memory error: {e}")
            context["memory"] = {}
        return context

    def answer_question(self, question: str, user_context: Optional[str] = None) -> str:
        """
        Risponde a una domanda nel proprio dominio.
        Usa il contesto del DB + dominio + eventuale user_context.
        """
        if self.is_circuit_open():
            return f"[{self.name}] Sistema temporaneamente non disponibile. Riprova tra qualche minuto."

        domain_ctx = self.get_domain_context()
        ctx_str = json.dumps(domain_ctx, ensure_ascii=False, indent=2)[:2000]

        system = (
            f"Sei il {self.name} di brAIn, responsabile del dominio '{self.domain}'. "
            f"Rispondi in italiano, conciso, orientato all'azione. "
            f"Contesto dominio:\n{ctx_str}"
        )
        if user_context:
            system += f"\n\nContesto utente: {user_context}"

        try:
            return self.call_claude(question, system=system, max_tokens=1500)
        except Exception as e:
            logger.error(f"[{self.name}] answer_question error: {e}")
            return f"[{self.name}] Errore nella risposta: {e}"

    def generate_weekly_briefing(self) -> Dict[str, Any]:
        """
        Genera un briefing settimanale nel dominio del Chief.
        Salva in chief_decisions con decision_type='weekly_briefing'.
        """
        if self.is_circuit_open():
            return {"status": "circuit_open", "chief": self.name}

        domain_ctx = self.get_domain_context()
        ctx_str = json.dumps(domain_ctx, ensure_ascii=False, indent=2)[:3000]

        prompt = self.briefing_prompt_template or (
            f"Genera un briefing settimanale per il dominio '{self.domain}'. "
            f"Contesto attuale:\n{ctx_str}\n\n"
            f"Include: 1) Stato attuale, 2) Trend principali, 3) Rischi, "
            f"4) Raccomandazioni per la settimana. Formato: testo strutturato, max 500 parole."
        )

        try:
            briefing_text = self.call_claude(prompt, max_tokens=2000)
            # Salva briefing
            try:
                supabase.table("chief_decisions").insert({
                    "chief_domain": self.domain,
                    "decision_type": "weekly_briefing",
                    "summary": briefing_text[:1000],
                    "full_text": briefing_text,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"[{self.name}] Save briefing error: {e}")

            # Notifica Mirco
            self.notify_mirco(
                f"📊 *{self.name} Briefing Settimanale*\n\n{briefing_text[:800]}",
                level="info"
            )
            return {"status": "ok", "chief": self.name, "briefing": briefing_text[:500]}
        except Exception as e:
            logger.error(f"[{self.name}] generate_weekly_briefing error: {e}")
            return {"status": "error", "chief": self.name, "error": str(e)}

    def check_anomalies(self) -> List[Dict[str, Any]]:
        """
        Controlla anomalie nel dominio.
        Override nelle sottoclassi per logica specifica.
        Ritorna lista di anomalie: [{type, description, severity}].
        """
        return []

    def receive_capability_update(self, capability: Dict[str, Any]) -> None:
        """
        Riceve aggiornamento da Capability Scout.
        capability: {name, description, category, url, domain}
        Valuta rilevanza per il proprio dominio e salva in chief_memory.
        """
        if capability.get("domain") not in (self.domain, "general", None):
            return

        prompt = (
            f"Il Capability Scout ha trovato questa nuova capacità:\n"
            f"Nome: {capability.get('name', '')}\n"
            f"Descrizione: {capability.get('description', '')}\n\n"
            f"Sei il {self.name} (dominio: {self.domain}). "
            f"In 2-3 frasi: questa capacità è rilevante? Come potrebbe essere usata in brAIn?"
        )

        try:
            assessment = self.call_claude(prompt, max_tokens=300)
            # Salva in chief_memory
            key = f"capability_{capability.get('name', 'unknown').replace(' ', '_')[:50]}"
            try:
                supabase.table("chief_memory").upsert({
                    "chief_domain": self.domain,
                    "key": key,
                    "value": assessment,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"[{self.name}] Save capability error: {e}")

            logger.info(f"[{self.name}] Capability '{capability.get('name')}' assessed")
        except Exception as e:
            logger.warning(f"[{self.name}] receive_capability_update error: {e}")

    def save_decision(self, decision_type: str, summary: str, full_text: str = "") -> None:
        """Salva una decisione/raccomandazione in chief_decisions."""
        try:
            supabase.table("chief_decisions").insert({
                "chief_domain": self.domain,
                "decision_type": decision_type,
                "summary": summary[:1000],
                "full_text": full_text or summary,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"[{self.name}] save_decision error: {e}")
