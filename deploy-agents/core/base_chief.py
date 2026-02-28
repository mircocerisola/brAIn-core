"""
brAIn BaseChief — classe base per i Chief Agent del C-Suite.
Eredita da BaseAgent. Aggiunge: domain context, briefing settimanale,
anomaly detection, receive_capability_update, sandbox sicurezza, routing automatico.
"""
from __future__ import annotations
import json
import time as _time
import requests as _requests
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.base_agent import BaseAgent
from core.templates import now_rome
from csuite.cultura import CULTURA_BRAIN


# ============================================================
# SANDBOX PERIMETERS — hardcoded, non modificabili da prompt
# ============================================================

# ============================================================
# ICONE CHIEF — fisse, usate in ogni messaggio Telegram
# ============================================================

CHIEF_ICONS: Dict[str, str] = {
    "cto": "\U0001f527",   # 🔧
    "cfo": "\U0001f4ca",   # 📊
    "cso": "\U0001f3af",   # 🎯
    "cmo": "\U0001f3a8",   # 🎨
    "coo": "\u2699\ufe0f", # ⚙️
    "clo": "\u2696\ufe0f", # ⚖️
    "cpeo": "\U0001f331",  # 🌱
}


SANDBOX_PERIMETERS: Dict[str, Dict[str, Any]] = {
    "cso": {
        "file_allowed": [],
        "tables_allowed": ["problems", "solutions", "solution_scores", "bos_archive",
                           "pipeline_thresholds", "chief_memory", "chief_decisions",
                           "smoke_tests", "smoke_test_prospects", "smoke_test_events",
                           "chief_pending_tasks"],
        "tables_forbidden": [],
    },
    "coo": {
        "file_allowed": ["core/", "execution/", "deploy-agents/execution/"],
        "tables_allowed": ["agent_logs", "agent_events", "action_queue", "scan_schedule",
                           "projects", "project_metrics", "kpi_daily", "smoke_tests",
                           "smoke_test_prospects", "smoke_test_events",
                           "chief_memory", "chief_decisions", "project_assets",
                           "chief_pending_tasks", "coo_project_tasks"],
        "tables_forbidden": ["solutions", "brand_assets", "org_config", "code_tasks"],
    },
    "cto": {
        "file_allowed": ["*"],
        "tables_allowed": ["*"],
        "tables_forbidden": [],
    },
    "cmo": {
        "file_allowed": ["marketing/", "deploy-agents/marketing/"],
        "tables_allowed": ["brand_assets", "marketing_reports", "smoke_test_prospects",
                           "chief_memory", "chief_decisions", "project_assets",
                           "chief_pending_tasks"],
        "tables_forbidden": ["agent_logs", "org_config", "scan_sources", "solutions", "projects", "code_tasks"],
    },
    "cfo": {
        "file_allowed": ["finance/", "deploy-agents/finance/"],
        "tables_allowed": ["finance_metrics", "kpi_daily", "exchange_rates",
                           "chief_memory", "chief_decisions", "manager_revenue_share",
                           "chief_pending_tasks"],
        "tables_forbidden": ["agent_logs", "org_config", "scan_sources", "projects", "code_tasks"],
    },
    "clo": {
        "file_allowed": ["execution/legal_agent.py", "ethics/"],
        "tables_allowed": ["legal_reviews", "ethics_violations", "authorization_matrix",
                           "chief_memory", "chief_decisions",
                           "chief_pending_tasks"],
        "tables_forbidden": ["agent_logs", "org_config", "scan_sources", "solutions", "brand_assets", "code_tasks"],
    },
    "cpeo": {
        "file_allowed": ["memory/"],
        "tables_allowed": ["project_members", "org_knowledge", "capability_log",
                           "training_materials", "training_plans", "chief_memory", "chief_decisions",
                           "chief_pending_tasks"],
        "tables_forbidden": ["agent_logs", "org_config", "scan_sources", "solutions", "brand_assets", "code_tasks"],
    },
}

# v5.36: ROUTING_KEYWORDS rimosso — unificato in csuite/__init__.py come ROUTING_MAP


def _get_telegram_chat_id_sync() -> Optional[str]:
    """Ottieni chat_id di Mirco da org_config."""
    try:
        from core.utils import get_telegram_chat_id
        return get_telegram_chat_id()
    except Exception:
        return None


def _send_telegram_message(chat_id: str, text: str, reply_markup: Optional[Dict] = None) -> None:
    """Invia messaggio Telegram direttamente via HTTP."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        _requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload, timeout=10,
        )
    except Exception as e:
        logger.warning(f"[BASE_CHIEF] Telegram send error: {e}")


class BaseChief(BaseAgent):
    """Classe base per tutti i Chief Agent (CSO, CFO, CMO, ecc.)."""

    domain: str = "general"          # es. "finance", "strategy", "marketing"
    chief_id: str = ""               # es. "cso", "cfo", "cmo"
    default_model: str = "claude-sonnet-4-6"
    default_temperature: Optional[float] = None  # v5.36: override per Chief
    briefing_prompt_template: str = ""  # Override nelle sottoclassi

    # FIX 5 — model routing
    _ALWAYS_SONNET = {"cso", "cmo", "cpeo", "cto"}
    _ADAPTIVE_MODEL = {"cfo", "clo", "coo"}

    # v5.34 — 3-level context system
    _LIGHT_KEYWORDS = {
        "ciao", "ok", "grazie", "si", "no", "va bene", "perfetto",
        "buongiorno", "buonasera", "capito", "ricevuto",
    }
    _FULL_KEYWORDS = {
        "analizza", "analisi", "strategia", "piano", "pianifica",
        "confronta", "valuta", "dettaglio", "approfond", "spiega",
        "perche", "come funziona", "architettura", "report completo",
        "audit", "revisione", "ottimizza", "migliora", "problema",
    }

    # System prompt cache — TTL 5 minuti, evita 6+ query DB per messaggio
    _base_prompt_cache: Dict[str, Tuple[str, float]] = {}  # chief_id -> (prompt, timestamp)
    _BASE_PROMPT_TTL = 300  # secondi

    # ------------------------------------------------------------------
    # v5.34 — Universal 3-level context
    # ------------------------------------------------------------------

    def detect_context_level(self, message: str) -> str:
        """Classifica il messaggio in light/medium/full per calibrare il contesto.
        light (~200 tok): saluti, conferme, risposte brevi.
        medium (~500 tok): domande standard di dominio.
        full (~1500 tok): analisi complesse, pianificazione, debug.
        """
        msg_lower = message.lower().strip()

        # Light: messaggi brevissimi o saluti
        if len(msg_lower) < 15 or msg_lower in self._LIGHT_KEYWORDS:
            return "light"

        # Full: keyword di analisi/complessita'
        for kw in self._FULL_KEYWORDS:
            if kw in msg_lower:
                return "full"

        # Full: messaggi lunghi (>200 chars) sono probabilmente complessi
        if len(msg_lower) > 200:
            return "full"

        return "medium"

    def _load_topic_summary(self, scope_id: str) -> str:
        """Carica il riassunto incrementale del topic da topic_context_summary."""
        if not scope_id:
            return ""
        try:
            r = supabase.table("topic_context_summary") \
                .select("summary,message_count") \
                .eq("scope_id", scope_id).execute()
            if r.data and r.data[0].get("summary"):
                return r.data[0]["summary"]
        except Exception as e:
            logger.warning("[%s] _load_topic_summary error: %s", self.name, e)
        return ""

    def _update_topic_summary(self, scope_id: str, message: str,
                               response: str) -> None:
        """Aggiorna il riassunto incrementale del topic via Haiku (fire-and-forget)."""
        if not scope_id:
            return
        try:
            existing = self._load_topic_summary(scope_id)
            prompt = (
                "Aggiorna questo riassunto di conversazione con il nuovo scambio.\n"
                "Mantieni SOLO informazioni chiave: decisioni, fatti, numeri, azioni.\n"
                "Max 300 parole. Scrivi in italiano.\n\n"
                f"RIASSUNTO ATTUALE:\n{existing or '(vuoto)'}\n\n"
                f"NUOVO MESSAGGIO UTENTE:\n{message[:500]}\n\n"
                f"RISPOSTA CHIEF:\n{response[:500]}\n\n"
                "RIASSUNTO AGGIORNATO:"
            )
            updated = self.call_claude(
                prompt, model="claude-haiku-4-5-20251001", max_tokens=400
            )
            # Conta messaggi
            count = 1
            try:
                r = supabase.table("topic_context_summary") \
                    .select("message_count").eq("scope_id", scope_id).execute()
                if r.data:
                    count = (r.data[0].get("message_count") or 0) + 1
            except Exception:
                pass

            supabase.table("topic_context_summary").upsert({
                "scope_id": scope_id,
                "summary": updated.strip(),
                "message_count": count,
                "last_updated": now_rome().isoformat(),
                "chief_id": self.chief_id,
            }).execute()
        except Exception as e:
            logger.warning("[%s] _update_topic_summary error: %s", self.name, e)

    def _select_model(self, question: str) -> str:
        """v5.36: Seleziona modello ottimale con keyword heuristic (zero chiamate Haiku).
        CSO/CMO/CPeO/CTO → sempre Sonnet (dominio creativo/strategico).
        CFO/CLO/COO → Haiku se query breve e semplice, Sonnet se keyword complesse.
        """
        chief_id = self.chief_id or ""
        if chief_id in self._ALWAYS_SONNET:
            return "claude-sonnet-4-6"
        if chief_id in self._ADAPTIVE_MODEL:
            q_lower = question.lower()
            # Se contiene keyword complesse → Sonnet
            for kw in self._FULL_KEYWORDS:
                if kw in q_lower:
                    return "claude-sonnet-4-6"
            # Se corto e semplice → Haiku
            if len(q_lower) < 80:
                return "claude-haiku-4-5-20251001"
        return "claude-sonnet-4-6"

    def _send_to_chief_topic(self, text: str) -> None:
        """FIX 2: Invia testo al Forum Topic del Chief (ricava topic_id da org_config)."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            topic_key = f"chief_topic_{self.chief_id}"
            r = supabase.table("org_config").select("value").eq("key", topic_key).execute()
            if not r.data:
                return
            topic_id = int(r.data[0]["value"])
            group_r = supabase.table("org_config").select("value") \
                .eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return
            group_id = int(group_r.data[0]["value"])
            _requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": group_id,
                    "message_thread_id": topic_id,
                    "text": text[:4000],
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"[{self.name}] _send_to_chief_topic error: {e}")

    def get_domain_context(self) -> Dict[str, Any]:
        """
        Ritorna contesto DB rilevante per il dominio.
        Override nelle sottoclassi per dati specifici.
        v5.11: errori restituiti come stringa 'errore lettura DB: {motivo}'
        """
        context: Dict[str, Any] = {}
        try:
            r = supabase.table("chief_decisions") \
                .select("decision_type,summary,created_at") \
                .eq("chief_domain", self.domain) \
                .order("created_at", desc=True).limit(5).execute()
            context["recent_decisions"] = r.data or []
        except Exception as e:
            logger.warning(f"[{self.name}] get_domain_context error: {e}")
            context["recent_decisions"] = f"errore lettura DB: {e}"
        try:
            r = supabase.table("chief_memory") \
                .select("key,value,updated_at") \
                .eq("chief_domain", self.domain) \
                .order("updated_at", desc=True).limit(10).execute()
            context["memory"] = {row["key"]: row["value"] for row in (r.data or [])}
        except Exception as e:
            logger.warning(f"[{self.name}] chief_memory error: {e}")
            context["memory"] = f"errore lettura DB: {e}"
        return context

    def _build_base_prompt(self) -> str:
        """Costruisce la parte STATICA del system prompt (profilo + org + chief knowledge).
        Cachata per _BASE_PROMPT_TTL secondi per ridurre query DB da ~6 a ~0 per messaggio.
        """
        cached = BaseChief._base_prompt_cache.get(self.chief_id)
        if cached:
            prompt_text, ts = cached
            if _time.time() - ts < self._BASE_PROMPT_TTL:
                return prompt_text

        parts: List[str] = [CULTURA_BRAIN]

        # 1. Profilo Chief
        profile_text = ""
        try:
            r = supabase.table("chief_knowledge") \
                .select("content") \
                .eq("chief_id", self.chief_id) \
                .eq("knowledge_type", "profile") \
                .limit(1).execute()
            if r.data:
                profile_text = r.data[0]["content"]
        except Exception as e:
            logger.warning("[%s] build base prompt profile: %s", self.name, e)

        if profile_text:
            parts.append("=== PROFILO E RUOLO ===\n" + profile_text)
        else:
            parts.append(
                "Sei il " + self.name + " di brAIn, responsabile del dominio '"
                + self.domain + "'."
            )

        # 2. Conoscenza condivisa brAIn (top 10 per importanza)
        try:
            r = supabase.table("org_shared_knowledge") \
                .select("category,title,content") \
                .order("importance", desc=True).limit(10).execute()
            if r.data:
                osk_lines = []
                for row in r.data:
                    osk_lines.append(
                        "[" + row["category"].upper() + "] "
                        + row["title"] + ": " + (row["content"] or "")[:300]
                    )
                parts.append("=== CONOSCENZA brAIn ===\n" + "\n\n".join(osk_lines))
        except Exception as e:
            logger.warning("[%s] build base prompt org_knowledge: %s", self.name, e)

        # 3. Conoscenza specialistica Chief (top 20, escluso profile)
        try:
            r = supabase.table("chief_knowledge") \
                .select("knowledge_type,title,content") \
                .eq("chief_id", self.chief_id) \
                .neq("knowledge_type", "profile") \
                .order("importance", desc=True).limit(20).execute()
            if r.data:
                ck_lines = []
                for row in r.data:
                    ck_lines.append(
                        "[" + row["knowledge_type"].upper() + "] "
                        + row["title"] + ": " + (row["content"] or "")[:400]
                    )
                parts.append(
                    "=== CONOSCENZA SPECIALISTICA " + self.name + " ===\n"
                    + "\n\n".join(ck_lines)
                )
        except Exception as e:
            logger.warning("[%s] build base prompt chief_knowledge: %s", self.name, e)

        # 4. Regola fondamentale
        parts.append(
            "=== REGOLA FONDAMENTALE ===\n"
            "Rispondi SOLO su argomenti del tuo dominio come descritto nel PROFILO E RUOLO. "
            "Se la richiesta non e' di tua competenza, attiva check_domain_routing(). "
            "Rispondi sempre in italiano, conciso, formato card con separatori. "
            "Zero fuffa, vai al punto. UNA sola domanda alla volta se devi chiedere.\n\n"
            "INFRASTRUTTURA INVISIBILE: Non menzionare mai Command Center, bot, /comandi, "
            "endpoint, API interne, o altri agenti interni nelle tue risposte. "
            "Sei tu che agisci direttamente. I dati li hai nella sezione DATI LIVE. "
            "Se hai bisogno di eseguire codice, descrivi l'azione necessaria. "
            "Mai delegare visibilmente ad altri sistemi. Mai dire 'chiedo a', 'attivo', "
            "'digita /comando'. Tu agisci, tu decidi, tu rispondi.\n\n"
            "DATI AUTONOMI: Hai accesso diretto ai dati del tuo dominio nella sezione DATI LIVE. "
            "Usa SEMPRE quei dati per rispondere alle domande. "
            "Se un dato mostra un errore di lettura, riportalo esplicitamente. "
            "Mai dire 'non ho accesso' o 'non posso leggere il database'.\n\n"
            "AZIONE CODICE: Se la tua risposta richiede modifiche al codice "
            "(nuovo agente, fix bug, nuova feature, modifica file), "
            "NON eseguire direttamente. Aggiungi in fondo alla risposta:\n"
            "<<CODE_ACTION>>\n"
            "{\"title\": \"...\", \"description\": \"2-3 righe\", "
            "\"files\": [\"file1.py\"], \"time_estimate\": \"15 min\", "
            "\"prompt\": \"prompt dettagliato per Claude Code\"}\n"
            "<<END_CODE_ACTION>>\n"
            "Il sistema inviera' automaticamente una card di approvazione a Mirco."
        )

        result = "\n\n".join(parts)
        BaseChief._base_prompt_cache[self.chief_id] = (result, _time.time())
        logger.debug("[%s] Base prompt cached (%d chars)", self.name, len(result))
        return result

    def build_system_prompt(self, project_context: Optional[str] = None,
                            topic_scope_id: Optional[str] = None,
                            project_scope_id: Optional[str] = None,
                            recent_messages: Optional[List[Dict]] = None,
                            query: Optional[str] = None) -> str:
        """
        Assembla il system prompt dinamico per il Chief:
        CACHED (5 min): profilo + org knowledge + chief knowledge + regola
        LIVE: episodic memory + relevant memories + contesto progetto
        """
        # Parte statica (cachata)
        parts: List[str] = [self._build_base_prompt()]

        # Parte dinamica: episodic memory topic
        if topic_scope_id:
            try:
                from intelligence.memory import get_episodes
                episodes = get_episodes("topic", topic_scope_id, limit=5)
                if episodes:
                    parts.append("=== SESSIONI PRECEDENTI ===\n" + "\n---\n".join(episodes))
            except Exception as e:
                logger.warning("[%s] episodic topic memory error: %s", self.name, e)

        # Parte dinamica: episodic memory progetto
        if project_scope_id:
            try:
                from intelligence.memory import get_episodes
                proj_ep = get_episodes("project", str(project_scope_id), limit=5)
                if proj_ep:
                    parts.append("=== STORIA CANTIERE ===\n" + "\n---\n".join(proj_ep))
            except Exception as e:
                logger.warning("[%s] episodic project memory error: %s", self.name, e)

        # Parte dinamica: memorie rilevanti per la query corrente
        if query:
            try:
                from intelligence.memory import search_relevant_memories
                relevant = search_relevant_memories(self.chief_id, query, limit=5)
                if relevant:
                    parts.append(
                        "=== MEMORIE RILEVANTI ===\n" + "\n---\n".join(relevant)
                    )
            except Exception as e:
                logger.warning("[%s] relevant memory search error: %s", self.name, e)

        # Contesto progetto
        if project_context:
            parts.append("=== CONTESTO CANTIERE ===\n" + project_context)

        return "\n\n".join(parts)

    def answer_question(self, question: str, user_context: Optional[str] = None,
                        project_context: Optional[str] = None,
                        topic_scope_id: Optional[str] = None,
                        project_scope_id: Optional[str] = None,
                        recent_messages: Optional[List[Dict]] = None) -> str:
        """Risponde a una domanda nel proprio dominio usando system prompt dinamico.
        FIX 4: inietta get_domain_context() live prima di rispondere.
        FIX 5: usa modello ottimale via _select_model().
        v5.29: web search via Perplexity se Mirco chiede di cercare online.
        v5.34: 3-level context (light/medium/full) + topic summary incrementale.
        """
        if self.is_circuit_open():
            from csuite.utils import fmt
            return fmt(self.chief_id, "Problema tecnico temporaneo",
                       "Ho un sovraccarico di richieste. Mi riprendo tra un minuto.")

        # v5.34: detect context level
        ctx_level = self.detect_context_level(question)
        logger.info("[%s] context_level=%s for: %s", self.name, ctx_level, question[:60])

        # v5.29: web search trigger — cerca online se Mirco lo chiede
        from csuite.utils import detect_web_search, web_search, fmt
        search_query = detect_web_search(question)
        if search_query:
            logger.info("[%s] Web search trigger: %s", self.name, search_query[:80])
            self._send_to_chief_topic(
                fmt(self.chief_id, "Ricerca online", "Sto cercando: " + search_query[:80] + "...")
            )
            search_result = web_search(search_query, self.chief_id)
            web_context = (
                "RISULTATO RICERCA WEB:\n" + search_result
                + "\n\nUsa queste informazioni per rispondere a Mirco in modo diretto."
            )
            if user_context:
                user_context = user_context + "\n\n" + web_context
            else:
                user_context = web_context

        # v5.34: build system prompt based on context level
        if ctx_level == "light":
            # Solo base prompt + domain context, niente memory/episodes
            system = self._build_base_prompt()
        elif ctx_level == "medium":
            # Base + topic summary + solo 3 episodes (no relevant memories search)
            system = self._build_base_prompt()
            topic_summary = self._load_topic_summary(topic_scope_id or "")
            if topic_summary:
                system += "\n\n=== CONTESTO CONVERSAZIONE ===\n" + topic_summary
            if topic_scope_id:
                try:
                    from intelligence.memory import get_episodes
                    episodes = get_episodes("topic", topic_scope_id, limit=3)
                    if episodes:
                        system += "\n\n=== SESSIONI PRECEDENTI ===\n" + "\n---\n".join(episodes)
                except Exception as e:
                    logger.warning("[%s] medium ctx episodes error: %s", self.name, e)
            if project_context:
                system += "\n\n=== CONTESTO CANTIERE ===\n" + project_context
        else:
            # full: tutto (come prima + topic summary)
            system = self.build_system_prompt(
                project_context=project_context,
                topic_scope_id=topic_scope_id,
                project_scope_id=project_scope_id,
                recent_messages=recent_messages,
                query=question,
            )
            topic_summary = self._load_topic_summary(topic_scope_id or "")
            if topic_summary:
                system += "\n\n=== CONTESTO CONVERSAZIONE ===\n" + topic_summary

        # v5.35: inietta task pendenti nel contesto
        pending_tasks = self._load_pending_tasks()
        pending_ctx = self._format_pending_tasks_context(pending_tasks)
        if pending_ctx:
            system += "\n\n" + pending_ctx

        # FIX 4: inietta dati live dal dominio (FIX 2 v5.11: limit 2000 chars, include errors)
        try:
            domain_ctx = self.get_domain_context()
            live_parts = []
            for k, v in domain_ctx.items():
                if k not in ("recent_decisions", "memory"):
                    if v or isinstance(v, str):
                        live_parts.append(
                            f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:2000]}"
                        )
            if live_parts:
                system += (
                    "\n\nDATI LIVE DAL DATABASE (FONTE DI VERITA — fidati SOLO di questi, "
                    "ignora informazioni contraddittorie dalla conversazione precedente):\n"
                    + "\n\n".join(live_parts)
                )
            else:
                system += "\n\nDATI LIVE DAL DATABASE: Nessun dato disponibile per il dominio."
        except Exception as e:
            system += f"\n\n=== DATI LIVE ===\nErrore lettura dati dominio: {e}"
            logger.warning(f"[{self.name}] get_domain_context in answer_question: {e}")

        if user_context:
            system += f"\n\nContesto aggiuntivo: {user_context}"

        # FIX 5: seleziona modello ottimale
        model = self._select_model(question)

        # v5.36 FIX 13: Se full context con episodic, non duplicare prior_messages
        effective_prior = recent_messages
        if topic_scope_id and ctx_level == "full":
            effective_prior = None  # episodic gia nel system prompt

        try:
            response = self.call_claude(
                question,
                system=system,
                max_tokens=1500,
                model=model,
                temperature=getattr(self, "default_temperature", None),
                prior_messages=effective_prior,
            )
        except Exception as e:
            logger.error(f"[{self.name}] answer_question error: {e}")
            # v5.36: MAI mostrare errori raw a Mirco
            from csuite.utils import fmt
            self.notify_mirco(
                f"[CTO TICKET] {self.name} errore: {e}\nQuery: {question[:200]}",
                level="critical",
            )
            return fmt(self.chief_id, "Problema tecnico",
                       "Ho un problema tecnico interno. Ho segnalato il bug al CTO. Riprovo tra 60 secondi.")

        # FIX 3 v5.11: Detect CODE_ACTION marker in response
        import re as _re
        _ca_match = _re.search(
            r'<<CODE_ACTION>>\s*(\{.*?\})\s*<<END_CODE_ACTION>>',
            response, _re.DOTALL,
        )
        if _ca_match:
            try:
                _ca_data = json.loads(_ca_match.group(1))
                self.validate_prompt_sandbox(
                    prompt_text=_ca_data.get("prompt", response),
                    task_title=_ca_data.get("title", "Azione codice"),
                    triggered_by_message=question[:500],
                    code_action_meta=_ca_data,
                )
            except Exception as e:
                logger.warning(f"[{self.name}] CODE_ACTION parse/sandbox error: {e}")
            # Remove marker from visible response
            response = _re.sub(
                r'<<CODE_ACTION>>.*?<<END_CODE_ACTION>>', '', response, flags=_re.DOTALL
            ).strip()

        # v5.34: aggiorna topic summary (fire-and-forget, non blocca)
        if topic_scope_id and ctx_level != "light":
            try:
                import threading
                t = threading.Thread(
                    target=self._update_topic_summary,
                    args=(topic_scope_id, question, response),
                    daemon=True,
                )
                t.start()
            except Exception:
                pass

        return response

    def answer_question_with_routing(self, question: str, user_context: Optional[str] = None,
                                     no_redirect: bool = False,
                                     project_context: Optional[str] = None,
                                     topic_scope_id: Optional[str] = None,
                                     project_scope_id: Optional[str] = None,
                                     recent_messages: Optional[List[Dict]] = None) -> str:
        """
        Risponde con routing automatico: se la domanda non è di competenza,
        la passa al Chief corretto. Previene loop con no_redirect=True.
        """
        if not no_redirect:
            routed = self.check_domain_routing(
                question,
                project_context=project_context,
                topic_scope_id=topic_scope_id,
                project_scope_id=project_scope_id,
                recent_messages=recent_messages,
            )
            if routed:
                return routed  # risposta già inviata via Telegram
        return self.answer_question(
            question, user_context,
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )

    # ============================================================
    # DOMAIN BOUNDARY CHECK — fast keyword refuse
    # ============================================================

    def _check_domain_boundary(self, question: str) -> Optional[str]:
        """Fast keyword check su MY_REFUSE_DOMAINS.
        Se match, logga e inserisce agent_event per COO redirect.
        Ritorna messaggio di redirect se fuori dominio, None se OK.
        """
        refuse_domains = getattr(self, "MY_REFUSE_DOMAINS", [])
        if not refuse_domains:
            return None  # COO non rifiuta nulla

        q_lower = question.lower()
        matched = [kw for kw in refuse_domains if kw in q_lower]
        if not matched:
            return None

        logger.info("[%s] Domain boundary: rifiuto keyword=%s", self.name, matched)

        # Inserisci agent_event per COO redirect
        try:
            supabase.table("agent_events").insert({
                "event_type": "domain_boundary_redirect",
                "source_agent": self.chief_id,
                "target_agent": "coo",
                "payload": json.dumps({
                    "question": question[:500],
                    "matched_keywords": matched,
                    "reason": "fuori dominio " + self.chief_id,
                }),
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[%s] domain_boundary event: %s", self.name, e)

        icon = CHIEF_ICONS.get(self.chief_id, "")
        return (
            icon + " " + self.name + "\n"
            "Fuori dal mio dominio\n\n"
            "Questa richiesta riguarda: " + ", ".join(matched) + "\n"
            "La passo al COO per il coordinamento."
        )

    # ============================================================
    # TASK 4 — ROUTING AUTOMATICO TRA CHIEF
    # ============================================================

    def check_domain_routing(self, question: str, project_context: Optional[str] = None,
                             topic_scope_id: Optional[str] = None,
                             project_scope_id: Optional[str] = None,
                             recent_messages: Optional[List[Dict]] = None) -> Optional[str]:
        """
        Verifica se la domanda è di competenza del Chief.
        Se no, la passa al Chief corretto e notifica Mirco con card routing.
        Ritorna la risposta del Chief destinazione, o None se la domanda è propria.
        """
        chief_id = self.chief_id or self.name.lower()

        # v5.36: import ROUTING_MAP unificato da csuite
        from csuite import ROUTING_MAP
        # Fast keyword pre-check: se match univoco a un altro chief → skip Claude
        question_lower = question.lower()
        keyword_target = None
        for kw, target_id in ROUTING_MAP.items():
            if kw in question_lower and target_id != chief_id:
                keyword_target = target_id
                break

        # Verifica con Claude Haiku per routing preciso
        routing_prompt = (
            f"Sei un sistema di routing per il C-Suite di un'organizzazione AI.\n"
            f"Chief corrente: {chief_id} (dominio: {self.domain})\n"
            f"Domanda ricevuta: \"{question}\"\n\n"
            f"Chief disponibili e loro domini:\n"
            f"cso=strategy/pipeline/smoke_test/market_validation, cfo=finance/budget, cmo=marketing/brand,\n"
            f"cto=tech/code/infra, coo=operations/build/projects/spec/launch,\n"
            f"clo=legal/compliance, cpeo=people/team\n\n"
            f"Rispondi SOLO JSON: {{\"is_own_domain\": true/false, \"correct_chief\": \"cso\", \"reason\": \"...\"}}"
        )
        try:
            raw = self.call_claude(routing_prompt, model="claude-haiku-4-5-20251001", max_tokens=200)
            import re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if m:
                routing_data = json.loads(m.group(0))
            else:
                routing_data = {"is_own_domain": True}
        except Exception as e:
            logger.warning(f"[{self.name}] routing check error: {e}")
            return None

        if routing_data.get("is_own_domain", True):
            return None  # risponde il Chief corrente

        correct_chief_id = routing_data.get("correct_chief", keyword_target)
        reason = routing_data.get("reason", "")

        if not correct_chief_id or correct_chief_id == chief_id:
            return None

        # Ottieni il Chief destinazione
        try:
            from csuite import _chiefs
            chief_map = {
                "cso": "strategy", "cfo": "finance", "cmo": "marketing",
                "cto": "tech", "coo": "ops", "cpo": "ops",
                "clo": "legal", "cpeo": "people",
            }
            dest_domain = chief_map.get(correct_chief_id)
            dest_chief = _chiefs.get(dest_domain) if dest_domain else None
        except Exception:
            dest_chief = None

        if not dest_chief:
            return None  # non trovato, risponde il Chief corrente

        # Ottieni risposta dal Chief destinazione (no_redirect=True per evitare loop)
        try:
            dest_answer = dest_chief.answer_question(
                question,
                project_context=project_context,
                topic_scope_id=topic_scope_id,
                project_scope_id=project_scope_id,
                recent_messages=recent_messages,
            )
        except Exception as e:
            logger.warning(f"[{self.name}] Routing to {correct_chief_id} error: {e}")
            return None

        # Formatta card routing e invia a Mirco
        card = (
            f"\U0001f4e8 {self.name} \u2192 {dest_chief.name}\n\n"
            f"Hai chiesto: \"{question[:100]}\"\n"
            f"Competenza: {dest_chief.name}\n\n"
            f"\u2193 Risposta:\n{dest_answer[:600]}"
        )
        chat_id = _get_telegram_chat_id_sync()
        if chat_id:
            _send_telegram_message(str(chat_id), card)

        # Log routing in chief_decisions
        try:
            self.save_decision(
                decision_type=f"routed_to_{correct_chief_id}",
                summary=f"Domanda routed: '{question[:80]}' → {correct_chief_id}. Motivo: {reason}",
                full_text=f"Domanda: {question}\nRisposta {correct_chief_id}: {dest_answer[:500]}",
            )
        except Exception as e:
            logger.warning(f"[{self.name}] save routing decision error: {e}")

        logger.info(f"[{self.name}] Routing: '{question[:60]}' → {correct_chief_id}")
        return dest_answer

    # ============================================================
    # TASK 3 — SANDBOX SICUREZZA PROMPT
    # ============================================================

    def validate_prompt_sandbox(self, prompt_text: str,
                                task_title: str = "",
                                triggered_by_message: str = "",
                                code_action_meta: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Valida un prompt prima di salvarlo in code_tasks.
        1. Chiama Claude Haiku per analizzare files e tabelle toccate
        2. Confronta col perimetro hardcoded del Chief
        3. Se OK → salva in code_tasks con sandbox_passed=True, invia card approvazione a Mirco
        4. Se violazione → alerta Mirco con card + inline keyboard, salva come blocked
        Solo CTO può autorizzare override (callback sandbox_override:task_id).
        code_action_meta: opzionale, contiene {title, description, files, time_estimate, prompt}
        """
        chief_id = self.chief_id or self.name.lower()
        perimeter = SANDBOX_PERIMETERS.get(chief_id, {})

        # Analisi sicurezza con Claude Haiku
        analysis_prompt = (
            f"Sei un sistema di sicurezza. Analizza questo prompt e identifica cosa tocca.\n"
            f"Prompt da analizzare:\n{prompt_text[:3000]}\n\n"
            f"Rispondi SOLO con JSON valido:\n"
            f'{{\"safe\": true, \"reason\": \"...\", \"files_touched\": [\"file1.py\"], \"tables_touched\": [\"tablename\"]}}'
        )
        try:
            raw = self.call_claude(analysis_prompt, model="claude-haiku-4-5-20251001", max_tokens=500)
            import re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            analysis = json.loads(m.group(0)) if m else {"safe": True, "files_touched": [], "tables_touched": []}
        except Exception as e:
            logger.warning(f"[{self.name}] sandbox analysis error: {e}")
            analysis = {"safe": True, "files_touched": [], "tables_touched": [], "reason": f"analysis error: {e}"}

        files_touched: List[str] = analysis.get("files_touched", [])
        tables_touched: List[str] = analysis.get("tables_touched", [])

        # Verifica perimetro
        file_allowed = perimeter.get("file_allowed", [])
        tables_allowed = perimeter.get("tables_allowed", [])
        tables_forbidden = perimeter.get("tables_forbidden", [])

        unauthorized_files: List[str] = []
        unauthorized_tables: List[str] = []

        # CTO ha accesso completo
        if chief_id != "cto" and file_allowed != ["*"]:
            for f in files_touched:
                allowed = not file_allowed  # se file_allowed è vuoto, nessun file consentito
                if file_allowed:
                    allowed = any(f.startswith(p) or p == "*" for p in file_allowed)
                if not allowed:
                    unauthorized_files.append(f)

        if tables_allowed != ["*"]:
            for t in tables_touched:
                if t in tables_forbidden:
                    unauthorized_tables.append(t)
                elif tables_allowed and t not in tables_allowed:
                    unauthorized_tables.append(t)

        sandbox_ok = not unauthorized_files and not unauthorized_tables

        sandbox_check = {
            "analysis": analysis,
            "unauthorized_files": unauthorized_files,
            "unauthorized_tables": unauthorized_tables,
            "perimeter": chief_id,
            "checked_at": now_rome().isoformat(),
        }

        if sandbox_ok:
            # Salva in code_tasks con sandbox_passed=True
            task_id = None
            try:
                result = supabase.table("code_tasks").insert({
                    "title": task_title or prompt_text[:100],
                    "prompt": prompt_text,
                    "requested_by": chief_id,
                    "status": "pending_approval",
                    "sandbox_check": json.dumps(sandbox_check),
                    "sandbox_passed": True,
                    "triggered_by_message": triggered_by_message[:500] if triggered_by_message else None,
                    "routing_chain": json.dumps([{"from": chief_id, "action": "sandbox_validate"}]),
                    "created_at": now_rome().isoformat(),
                }).execute()
                if result.data:
                    task_id = result.data[0].get("id")
            except Exception as e:
                logger.warning(f"[{self.name}] code_tasks insert error: {e}")

            # FIX 3 v5.11: Card approvazione a Mirco
            _meta = code_action_meta or {}
            _desc = _meta.get("description", task_title or prompt_text[:150])
            _files_list = _meta.get("files", files_touched[:5])
            _files_str = ", ".join(_files_list) if _files_list else "da determinare"
            _time_est = _meta.get("time_estimate", "da valutare")

            card_text = (
                f"\u26a1 AZIONE CODICE \u2014 {self.name} vuole agire\n\n"
                f"{_desc[:300]}\n"
                f"\U0001f4c1 File: {_files_str}\n"
                f"\u23f1\ufe0f Stima: {_time_est}"
            )
            approval_markup = {
                "inline_keyboard": [[
                    {"text": "\u2705 Valida", "callback_data": f"code_approve:{task_id}"},
                    {"text": "\u270f\ufe0f Cambia prompt", "callback_data": f"code_modify:{task_id}"},
                ], [
                    {"text": "\u274c Annulla", "callback_data": f"code_cancel:{task_id}"},
                    {"text": "\U0001f50d Dettaglio prompt", "callback_data": f"code_detail:{task_id}"},
                ]]
            }
            _chat_id_appr = _get_telegram_chat_id_sync()
            if _chat_id_appr and task_id:
                _send_telegram_message(str(_chat_id_appr), card_text, approval_markup)

            logger.info(f"[{self.name}] Sandbox OK: task_id={task_id}, card inviata")
            return {"ok": True, "task_id": task_id, "chief": chief_id}

        else:
            # Alert a Mirco — NON salva il task
            uf_str = ", ".join(unauthorized_files) if unauthorized_files else "nessuno"
            ut_str = ", ".join(unauthorized_tables) if unauthorized_tables else "nessuna"

            alert_text = (
                f"\U0001f6a8 Prompt bloccato \u2014 {self.name}\n\n"
                f"\u26a0\ufe0f Tocca aree fuori perimetro\n"
                f"\U0001f4c1 File non autorizzati: {uf_str}\n"
                f"\U0001f5c4\ufe0f Tabelle non autorizzate: {ut_str}"
            )
            # Salva in code_tasks come BLOCKED (solo per audit) con sandbox_passed=False
            task_id = None
            try:
                result = supabase.table("code_tasks").insert({
                    "title": task_title or prompt_text[:100],
                    "prompt": prompt_text,
                    "requested_by": chief_id,
                    "status": "blocked",
                    "sandbox_check": json.dumps(sandbox_check),
                    "sandbox_passed": False,
                    "triggered_by_message": triggered_by_message[:500] if triggered_by_message else None,
                    "created_at": now_rome().isoformat(),
                }).execute()
                if result.data:
                    task_id = result.data[0].get("id")
            except Exception as e:
                logger.warning(f"[{self.name}] code_tasks blocked insert error: {e}")

            reply_markup = {
                "inline_keyboard": [[
                    {"text": "\U0001f50d Vedi prompt", "callback_data": f"sandbox_view:{task_id or 0}"},
                    {"text": "\u2705 Autorizza (CTO only)", "callback_data": f"sandbox_override:{task_id or 0}"},
                    {"text": "\u274c Annulla", "callback_data": f"sandbox_cancel:{task_id or 0}"},
                ]]
            }
            chat_id = _get_telegram_chat_id_sync()
            if chat_id:
                _send_telegram_message(str(chat_id), alert_text, reply_markup)

            logger.warning(f"[{self.name}] Sandbox BLOCKED: files={unauthorized_files} tables={unauthorized_tables}")
            return {"ok": False, "chief": chief_id, "task_id": task_id,
                    "unauthorized_files": unauthorized_files, "unauthorized_tables": unauthorized_tables}


    # ============================================================
    # DAILY REPORT 08:00 — copre SOLO le ultime 24 ore
    # ============================================================

    # Nomi italiani giorno/mese
    _GIORNI_IT = [
        "Lunedì", "Martedì", "Mercoledì", "Giovedì",
        "Venerdì", "Sabato", "Domenica"
    ]
    _MESI_IT = {
        1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
        5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
        9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre"
    }

    def _format_daily_header(self, date=None) -> str:
        """Ritorna stringa 'Giovedì 27 febbraio 2026'. Se date=None usa ieri."""
        dt = date or now_rome()
        giorno = self._GIORNI_IT[dt.weekday()]
        mese = self._MESI_IT[dt.month]
        return f"{giorno} {dt.day} {mese} {dt.year}"

    def _chief_icon(self) -> str:
        """Ritorna icona fissa del Chief da CHIEF_ICONS."""
        return CHIEF_ICONS.get(self.chief_id, "\U0001f4ca")

    def _msg_header(self, title: str) -> str:
        """Header uniforme: icona NOME\\nTitolo\\n\\n"""
        return self._chief_icon() + " " + self.name + "\n" + title + "\n\n"

    def _daily_report_emoji(self) -> str:
        """Icona fissa da CHIEF_ICONS."""
        return CHIEF_ICONS.get(self.chief_id, "\U0001f4ca")

    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> List[str]:
        """
        Override nelle sottoclassi per sezioni domain-specific.
        Ritorna lista di stringhe (sezioni già formattate).
        ieri_inizio/ieri_fine: ISO datetime range [inizio, fine) del giorno solare precedente.
        """
        return []

    def generate_daily_report(self) -> Optional[str]:
        """
        Report giornaliero 08:00 — copre il GIORNO SOLARE PRECEDENTE.
        Range: ieri 00:00:00 → oggi 00:00:00 (Europe/Rome), estremo destro escluso.
        Si apre con 'Rapporto di ieri — Giovedì 27 febbraio 2026'.
        Si chiude con totale speso ieri vs budget giornaliero.
        """
        now = now_rome()
        oggi_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        ieri_dt = oggi_start - timedelta(days=1)
        ieri_inizio = ieri_dt.isoformat()
        ieri_fine = oggi_start.isoformat()
        budget_giornaliero_eur = 33.0  # €1000/mese ÷ 30

        # Costi API giorno precedente (comuni a tutti i Chief)
        cost_ieri_eur = 0.0
        try:
            r = supabase.table("agent_logs").select("cost_usd") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine).execute()
            total_usd = sum(float(row.get("cost_usd") or 0) for row in (r.data or []))
            cost_ieri_eur = round(total_usd * 0.92, 2)
        except Exception as e:
            logger.warning(f"[{self.name}] generate_daily_report costs error: {e}")

        # Sezioni domain-specific (giorno precedente)
        sections = []
        try:
            sections = self._get_daily_report_sections(ieri_inizio, ieri_fine)
        except Exception as e:
            logger.warning(f"[{self.name}] generate_daily_report sections error: {e}")

        # Se nessun dato ieri → ometti il report
        if not sections and cost_ieri_eur == 0.0:
            logger.info(f"[{self.name}] generate_daily_report: nessun dato ieri, skip")
            return None

        header = self._format_daily_header(ieri_dt)
        budget_pct = round(cost_ieri_eur / budget_giornaliero_eur * 100) if budget_giornaliero_eur > 0 else 0

        lines = [
            self._chief_icon() + " " + self.name,
            "Report Giornaliero " + header,
            "",
        ]
        for section in sections:
            lines.append(section)
        lines.extend([
            "",
            f"\U0001f4b6 IERI: \u20ac{cost_ieri_eur:.2f} / \u20ac{budget_giornaliero_eur:.0f} budget ({budget_pct}%)",
        ])

        text = "\n".join(lines)

        # Salva in chief_decisions
        try:
            supabase.table("chief_decisions").insert({
                "chief_domain": self.domain,
                "decision_type": "daily_report",
                "summary": text[:500],
                "full_text": text,
                "created_at": now.isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"[{self.name}] generate_daily_report save error: {e}")

        # Invia al topic del Chief
        self._send_to_chief_topic(text)
        logger.info(f"[{self.name}] generate_daily_report: inviato ({len(text)} chars)")
        return text


    # ============================================================
    # BRIEFING, ANOMALY, CAPABILITY (invariati)
    # ============================================================

    def generate_brief_report(self) -> Optional[str]:
        """Genera report di stato periodico del dominio (max 8 righe).
        Sempre inviato — usa get_domain_context() come base dati.
        Invia al Forum Topic del Chief. Ritorna il testo o None se errore.
        """
        four_hours_ago = (now_rome() - timedelta(hours=4)).isoformat()

        # Decisioni recenti (escludi brief_report)
        recent_decisions = []
        try:
            r = supabase.table("chief_decisions") \
                .select("summary,decision_type,created_at") \
                .eq("chief_domain", self.domain) \
                .neq("decision_type", "brief_report") \
                .gte("created_at", four_hours_ago) \
                .order("created_at", desc=True).limit(5).execute()
            recent_decisions = r.data or []
        except Exception:
            pass

        # Anomalie dominio
        anomalies = []
        try:
            anomalies = self.check_anomalies()
        except Exception:
            pass

        # Dati live del dominio
        domain_ctx = {}
        try:
            domain_ctx = self.get_domain_context()
        except Exception:
            pass

        # Costruisci contesto
        ctx_parts = []
        if recent_decisions:
            dec_text = "\n".join(
                f"- {d['decision_type']}: {d['summary'][:100]}"
                for d in recent_decisions[:3]
            )
            ctx_parts.append(f"Decisioni recenti (4h):\n{dec_text}")
        if anomalies:
            anom_text = "\n".join(
                f"- [{a.get('severity','?')}] {a.get('description','')}"
                for a in anomalies[:3]
            )
            ctx_parts.append(f"Anomalie:\n{anom_text}")
        # Aggiungi dati dominio significativi
        for k, v in domain_ctx.items():
            if k not in ("recent_decisions", "memory") and v:
                v_str = json.dumps(v, ensure_ascii=False, default=str)[:300]
                ctx_parts.append(f"{k}: {v_str}")

        ctx = "\n\n".join(ctx_parts) if ctx_parts else "Nessun dato significativo disponibile."

        today_str = now_rome().strftime("%d %b").lstrip("0")
        icon = self._chief_icon()
        prompt = (
            "Sei il " + self.name + " di brAIn. Genera un report di stato breve (max 10 righe, italiano).\n"
            "Dominio: " + self.domain + "\n"
            "Data: " + today_str + "\n"
            "Dati disponibili:\n" + ctx + "\n\n"
            "FORMATO OBBLIGATORIO (Telegram, NO Markdown):\n"
            "- Prima riga ESATTA: " + icon + " " + self.name + "\n"
            "- Seconda riga: titolo del report (es: Report di Stato)\n"
            "- Terza riga: vuota\n"
            "- Dalla quarta in poi: contenuto con dati concreti\n"
            "- VIETATO: ** grassetto **, ## titoli, ---- trattini, ___ separatori, parole inglesi (tranne termini tecnici)\n"
            "- Tutto in italiano. Zero fuffa. Vai al punto.\n"
            "Se non ci sono novita, comunica lo stato attuale del dominio."
        )

        try:
            text = self.call_claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=400)
        except Exception as e:
            logger.warning(f"[{self.name}] generate_brief_report call_claude error: {e}")
            return None

        if not text:
            return None

        # Salva in chief_decisions
        try:
            supabase.table("chief_decisions").insert({
                "chief_domain": self.domain,
                "decision_type": "brief_report",
                "summary": text[:500],
                "full_text": text,
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"[{self.name}] generate_brief_report save error: {e}")

        # Invia al topic del Chief
        self._send_to_chief_topic(text)

        logger.info(f"[{self.name}] generate_brief_report: inviato ({len(text)} chars)")
        return text

    def generate_weekly_briefing(self) -> Dict[str, Any]:
        """Genera un briefing settimanale nel dominio del Chief."""
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
            try:
                supabase.table("chief_decisions").insert({
                    "chief_domain": self.domain,
                    "decision_type": "weekly_briefing",
                    "summary": briefing_text[:1000],
                    "full_text": briefing_text,
                    "created_at": now_rome().isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"[{self.name}] Save briefing error: {e}")

            icon = self._chief_icon()
            self.notify_mirco(
                icon + " " + self.name + "\nBriefing Settimanale\n\n" + briefing_text[:800],
                level="info"
            )
            return {"status": "ok", "chief": self.name, "briefing": briefing_text[:500]}
        except Exception as e:
            logger.error(f"[{self.name}] generate_weekly_briefing error: {e}")
            return {"status": "error", "chief": self.name, "error": str(e)}

    def check_anomalies(self) -> List[Dict[str, Any]]:
        """Controlla anomalie nel dominio. Override nelle sottoclassi."""
        return []

    def receive_capability_update(self, capability: Dict[str, Any]) -> None:
        """Riceve aggiornamento da Capability Scout."""
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
            key = f"capability_{capability.get('name', 'unknown').replace(' ', '_')[:50]}"
            try:
                supabase.table("chief_memory").upsert({
                    "chief_domain": self.domain,
                    "key": key,
                    "value": assessment,
                    "updated_at": now_rome().isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"[{self.name}] Save capability error: {e}")

            logger.info(f"[{self.name}] Capability '{capability.get('name')}' assessed")
        except Exception as e:
            logger.warning(f"[{self.name}] receive_capability_update error: {e}")

    # ============================================================
    # v5.35 — TASK MANAGEMENT: pending tasks per Chief
    # ============================================================

    # Frasi vietate nelle risposte ai task — promettono output futuro
    _TASK_FORBIDDEN_PHRASES = [
        "sto cercando", "sto lavorando", "ci sto lavorando",
        "ti aggiorno dopo", "ti faccio sapere", "appena ho novita",
        "ci lavoro", "lo faccio", "me ne occupo",
        "risposta in elaborazione", "ti aggiorno",
        "ci penso io", "sto analizzando", "lo verifico",
    ]

    def _load_pending_tasks(self) -> List[Dict]:
        """Carica task pendenti per questo Chief da chief_pending_tasks."""
        try:
            r = supabase.table("chief_pending_tasks").select("*") \
                .eq("chief_id", self.chief_id) \
                .eq("status", "pending") \
                .order("created_at").limit(20).execute()
            return r.data or []
        except Exception as e:
            logger.warning("[%s] _load_pending_tasks error: %s", self.name, e)
            return []

    def _save_task(self, task_description: str, task_number: int = 1,
                   topic_id: int = None, project_slug: str = "",
                   source: str = "mirco") -> Optional[int]:
        """Salva un task ricevuto in chief_pending_tasks. Ritorna ID."""
        try:
            r = supabase.table("chief_pending_tasks").insert({
                "chief_id": self.chief_id,
                "topic_id": topic_id,
                "project_slug": project_slug,
                "task_description": task_description[:1000],
                "task_number": task_number,
                "status": "pending",
                "source": source,
                "created_at": now_rome().isoformat(),
            }).execute()
            if r.data:
                return r.data[0].get("id")
        except Exception as e:
            logger.warning("[%s] _save_task error: %s", self.name, e)
        return None

    def _complete_task(self, task_id: int, output_text: str = "") -> None:
        """Marca task come FATTO con output."""
        try:
            supabase.table("chief_pending_tasks").update({
                "status": "done",
                "output_text": output_text[:2000] if output_text else "",
                "completed_at": now_rome().isoformat(),
            }).eq("id", task_id).execute()
        except Exception as e:
            logger.warning("[%s] _complete_task error: %s", self.name, e)

    def _block_task(self, task_id: int, reason: str, blocked_by: str = "mirco") -> None:
        """Marca task come BLOCCATO con motivo."""
        try:
            supabase.table("chief_pending_tasks").update({
                "status": "blocked",
                "blocked_reason": reason[:500],
                "blocked_by": blocked_by,
                "completed_at": now_rome().isoformat(),
            }).eq("id", task_id).execute()
        except Exception as e:
            logger.warning("[%s] _block_task error: %s", self.name, e)

    def _format_pending_tasks_context(self, tasks: List[Dict]) -> str:
        """Formatta task pendenti come contesto per il system prompt."""
        if not tasks:
            return ""
        lines = ["TASK PENDENTI DA COMPLETARE (rispondi a OGNUNO con output concreto):"]
        for t in tasks:
            lines.append(
                "  Task #" + str(t.get("task_number", "?"))
                + " (id=" + str(t.get("id", "?")) + "): "
                + (t.get("task_description") or "?")[:200]
            )
        return "\n".join(lines)

    def _contains_task_forbidden(self, response: str) -> bool:
        """Controlla se la risposta contiene frasi vietate per i task."""
        lower = response.lower()
        return any(phrase in lower for phrase in self._TASK_FORBIDDEN_PHRASES)

    def save_decision(self, decision_type: str, summary: str, full_text: str = "") -> None:
        """Salva una decisione/raccomandazione in chief_decisions."""
        try:
            supabase.table("chief_decisions").insert({
                "chief_domain": self.domain,
                "decision_type": decision_type,
                "summary": summary[:1000],
                "full_text": full_text or summary,
                "created_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"[{self.name}] save_decision error: {e}")


# ============================================================
# FIX 5 v5.11 — COLLABORAZIONE INTER-AGENTE AUTONOMA
# ============================================================

def agent_to_agent_call(from_chief_id: str, to_chief_id: str,
                        task: str, context: str = "") -> Dict[str, Any]:
    """
    Chiamata diretta tra Chief. Mirco non vede questi scambi.
    from_chief_id: es. "cso"
    to_chief_id: es. "cto", "cmo"
    task: descrizione del task
    context: contesto aggiuntivo
    Ritorna: {status, result, from, to}
    """
    try:
        from csuite import _chiefs
        chief_map = {
            "cso": "strategy", "cfo": "finance", "cmo": "marketing",
            "cto": "tech", "coo": "ops", "clo": "legal", "cpeo": "people",
        }
        dest_domain = chief_map.get(to_chief_id)
        target = _chiefs.get(dest_domain) if dest_domain else None
    except Exception:
        target = None

    if not target:
        logger.warning(f"[INTER_AGENT] Chief '{to_chief_id}' non trovato")
        return {"status": "error", "error": f"Chief {to_chief_id} non trovato"}

    logger.info(f"[INTER_AGENT] {from_chief_id} -> {to_chief_id}: {task[:60]}")

    try:
        # Se la destinazione e' il CTO e il task e' codice → genera e consegna prompt
        if to_chief_id == "cto" and hasattr(target, "generate_and_deliver_prompt"):
            result = target.generate_and_deliver_prompt(task, context)
        else:
            # Chief destinazione risponde come domanda
            result_text = target.answer_question(
                task, user_context=context,
            )
            result = {"status": "ok", "response": result_text}
    except Exception as e:
        logger.error(f"[INTER_AGENT] {from_chief_id} -> {to_chief_id} error: {e}")
        result = {"status": "error", "error": str(e)}

    # Log inter-agent call
    try:
        supabase.table("chief_decisions").insert({
            "chief_domain": chief_map.get(from_chief_id, "general"),
            "decision_type": f"inter_agent_{from_chief_id}_to_{to_chief_id}",
            "summary": f"{from_chief_id} -> {to_chief_id}: {task[:200]}",
            "full_text": json.dumps(result, ensure_ascii=False, default=str)[:2000],
            "created_at": now_rome().isoformat(),
        }).execute()
    except Exception:
        pass

    result["from"] = from_chief_id
    result["to"] = to_chief_id
    return result


# ============================================================
# HEALTH CHECK — funzione standalone, invia a #technology
# ============================================================

def send_system_health_check() -> Dict[str, Any]:
    """
    Genera e invia un health check del sistema brAIn al topic #technology.
    Controlla: DB, scheduler jobs, progetti attivi, ultimi errori.
    """
    now_str = now_rome().strftime("%d/%m %H:%M")

    checks: Dict[str, str] = {}
    details: List[str] = []

    # 1. DB Supabase
    try:
        supabase.table("org_config").select("key").limit(1).execute()
        checks["Supabase DB"] = "\u2705"
    except Exception as e:
        checks["Supabase DB"] = f"\u274c {str(e)[:40]}"

    # 2. Chief topics configurati
    try:
        r = supabase.table("org_config").select("key,value").ilike("key", "chief_topic_%").execute()
        chief_topics = {row["key"]: row["value"] for row in (r.data or [])}
        expected = {"chief_topic_cso", "chief_topic_coo", "chief_topic_cto",
                    "chief_topic_cmo", "chief_topic_cfo", "chief_topic_clo", "chief_topic_cpeo"}
        found = set(chief_topics.keys()) & expected
        checks["Chief Topics"] = f"\u2705 {len(found)}/7" if len(found) == 7 else f"\u26a0\ufe0f {len(found)}/7"
    except Exception as e:
        checks["Chief Topics"] = f"\u274c {str(e)[:40]}"

    # 3. Progetti attivi
    try:
        r = supabase.table("projects").select("id,name,status").neq("status", "archived").execute()
        active = r.data or []
        checks["Progetti attivi"] = f"\u2705 {len(active)}"
        if active:
            details.append("Cantieri: " + ", ".join(f"{p['name']} ({p['status']})" for p in active[:3]))
    except Exception as e:
        checks["Progetti attivi"] = f"\u274c {str(e)[:40]}"

    # 4. Errori recenti (ultimi 30 min)
    try:
        cutoff = (now_rome() - timedelta(minutes=30)).isoformat()
        r = supabase.table("agent_logs").select("agent_id,error").eq("status", "error") \
            .gte("created_at", cutoff).limit(5).execute()
        errors = r.data or []
        if errors:
            checks["Errori recenti (30m)"] = f"\u26a0\ufe0f {len(errors)}"
            details.append("Ultimi errori: " + "; ".join(
                f"{e['agent_id']}: {(e.get('error') or '')[:40]}" for e in errors[:2]
            ))
        else:
            checks["Errori recenti (30m)"] = "\u2705 0"
    except Exception as e:
        checks["Errori recenti (30m)"] = f"\u274c {str(e)[:40]}"

    # 5. Action queue pending
    try:
        r = supabase.table("action_queue").select("id").eq("status", "pending").execute()
        pending = len(r.data or [])
        checks["Action Queue"] = f"\u2705 {pending} pending"
    except Exception as e:
        checks["Action Queue"] = f"\u274c {str(e)[:40]}"

    # Costruisci messaggio
    checks_text = "\n".join(f"{icon} {name}" for name, icon in checks.items())
    # fix: rebuild for better display
    checks_lines = "\n".join(f"{name}: {icon}" for name, icon in checks.items())
    detail_text = ("\n" + "\n".join(details)) if details else ""

    msg = (
        "\U0001f527 CTO\n"
        "Health Check brAIn\n\n"
        + checks_lines
        + detail_text + "\n\n"
        + now_str
    )

    # Invia a #technology (chief_topic_cto)
    try:
        r = supabase.table("org_config").select("value").eq("key", "chief_topic_cto").execute()
        tech_topic_id = int(r.data[0]["value"]) if r.data else None
        r2 = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
        group_id = int(r2.data[0]["value"]) if r2.data else None

        if tech_topic_id and group_id and TELEGRAM_BOT_TOKEN:
            _requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": group_id,
                    "message_thread_id": tech_topic_id,
                    "text": msg[:4000],
                },
                timeout=15,
            )
            logger.info(f"[HEALTH_CHECK] Inviato a #technology topic_id={tech_topic_id}")
    except Exception as e:
        logger.warning(f"[HEALTH_CHECK] Invio Telegram error: {e}")

    return {"status": "ok", "checks": checks}

