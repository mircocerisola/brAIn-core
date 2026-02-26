"""
brAIn BaseChief — classe base per i Chief Agent del C-Suite.
Eredita da BaseAgent. Aggiunge: domain context, briefing settimanale,
anomaly detection, receive_capability_update, sandbox sicurezza, routing automatico.
"""
from __future__ import annotations
import json
import requests as _requests
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.base_agent import BaseAgent


# ============================================================
# SANDBOX PERIMETERS — hardcoded, non modificabili da prompt
# ============================================================

SANDBOX_PERIMETERS: Dict[str, Dict[str, Any]] = {
    "cso": {
        "file_allowed": [],
        "tables_allowed": ["problems", "solutions", "solution_scores", "bos_archive",
                           "pipeline_thresholds", "chief_memory", "chief_decisions"],
        "tables_forbidden": [],
    },
    "coo": {
        "file_allowed": ["core/", "execution/", "deploy-agents/execution/"],
        "tables_allowed": ["agent_logs", "agent_events", "action_queue", "scan_schedule",
                           "projects", "project_metrics", "kpi_daily", "smoke_tests",
                           "smoke_test_prospects", "smoke_test_events",
                           "chief_memory", "chief_decisions"],
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
                           "chief_memory", "chief_decisions"],
        "tables_forbidden": ["agent_logs", "org_config", "scan_sources", "solutions", "projects", "code_tasks"],
    },
    "cfo": {
        "file_allowed": ["finance/", "deploy-agents/finance/"],
        "tables_allowed": ["finance_metrics", "kpi_daily", "exchange_rates",
                           "chief_memory", "chief_decisions", "manager_revenue_share"],
        "tables_forbidden": ["agent_logs", "org_config", "scan_sources", "projects", "code_tasks"],
    },
    "clo": {
        "file_allowed": ["execution/legal_agent.py", "ethics/"],
        "tables_allowed": ["legal_reviews", "ethics_violations", "authorization_matrix",
                           "chief_memory", "chief_decisions"],
        "tables_forbidden": ["agent_logs", "org_config", "scan_sources", "solutions", "brand_assets", "code_tasks"],
    },
    "cpeo": {
        "file_allowed": ["memory/"],
        "tables_allowed": ["project_members", "org_knowledge", "capability_log",
                           "training_materials", "training_plans", "chief_memory", "chief_decisions"],
        "tables_forbidden": ["agent_logs", "org_config", "scan_sources", "solutions", "brand_assets", "code_tasks"],
    },
}

# ============================================================
# ROUTING KEYWORDS — mappa keyword → chief_id
# ============================================================

ROUTING_KEYWORDS: Dict[str, str] = {
    # CMO
    "logo": "cmo", "immagine": "cmo", "design": "cmo", "avatar": "cmo",
    "brand identity": "cmo", "grafica": "cmo", "visual": "cmo",
    # CFO
    "costi": "cfo", "budget": "cfo", "spese": "cfo", "fatturato": "cfo",
    "burn rate": "cfo", "marginalità": "cfo", "revenue share": "cfo",
    # CLO
    "contratto": "clo", "gdpr": "clo", "privacy policy": "clo", "compliance": "clo",
    "legale": "clo", "rischio legale": "clo", "termini": "clo",
    # CTO
    "sicurezza": "cto", "vulnerabilità": "cto", "infrastruttura": "cto",
    "deploy": "cto", "codice": "cto", "architettura": "cto", "bug": "cto",
    # CPeO
    "manager": "cpeo", "onboarding": "cpeo", "formazione": "cpeo",
    "persone": "cpeo", "collaboratori": "cpeo", "team building": "cpeo",
    # COO (ex-CPO)
    "cantieri": "coo", "build": "coo", "spec": "coo", "lancio": "coo",
    "mvp": "coo", "roadmap": "coo", "feature": "coo",
    # CSO
    "pipeline": "cso", "opportunità": "cso", "mercato": "cso",
    "bos": "cso", "problemi globali": "cso", "competizione": "cso", "pivot": "cso",
    # COO
    "processi": "coo", "operazioni": "coo", "efficienza": "coo",
    "coda": "coo", "bottleneck": "coo", "flusso": "coo",
}


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
    briefing_prompt_template: str = ""  # Override nelle sottoclassi

    # FIX 5 — model routing
    _ALWAYS_SONNET = {"cso", "cmo", "cpeo", "cto"}
    _ADAPTIVE_MODEL = {"cfo", "clo", "coo"}

    def _select_model(self, question: str) -> str:
        """FIX 5: Seleziona modello ottimale.
        CSO/CMO/CPeO/CTO → sempre Sonnet (dominio creativo/strategico).
        CFO/CLO/COO → Haiku se query semplice, Sonnet se complessa.
        """
        chief_id = self.chief_id or ""
        if chief_id in self._ALWAYS_SONNET:
            return "claude-sonnet-4-6"
        if chief_id in self._ADAPTIVE_MODEL:
            classify_prompt = (
                f"Classifica questa domanda per il {self.name}:\n\"{question[:200]}\"\n\n"
                "Risposta: SOLO una parola: simple (lookup/calcolo/status/dato diretto) "
                "oppure complex (analisi/raccomandazione/strategia/pianificazione)"
            )
            try:
                raw = self.call_claude(
                    classify_prompt, model="claude-haiku-4-5-20251001", max_tokens=10
                )
                if "simple" in raw.lower():
                    return "claude-haiku-4-5-20251001"
            except Exception:
                pass
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
            context["recent_decisions"] = []
        try:
            r = supabase.table("chief_memory") \
                .select("key,value,updated_at") \
                .eq("chief_domain", self.domain) \
                .order("updated_at", desc=True).limit(10).execute()
            context["memory"] = {row["key"]: row["value"] for row in (r.data or [])}
        except Exception as e:
            logger.warning(f"[{self.name}] chief_memory error: {e}")
            context["memory"] = {}
        return context

    def build_system_prompt(self, project_context: Optional[str] = None,
                            topic_scope_id: Optional[str] = None,
                            project_scope_id: Optional[str] = None,
                            recent_messages: Optional[List[Dict]] = None) -> str:
        """
        Assembla il system prompt dinamico per il Chief:
        1. Profilo Chief (da chief_knowledge knowledge_type='profile')
        2. Top 10 org_shared_knowledge (importanza DESC)
        3. Top 20 chief_knowledge specialistica (escluso 'profile')
        4. Episodic memory topic (sessioni precedenti)
        5. Episodic memory progetto (storia cantiere)
        6. Messaggi recenti verbatim (L1)
        7. Contesto progetto (se presente)
        8. Regola fondamentale
        """
        sep = "\u2501" * 20
        parts: List[str] = []

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
            logger.warning(f"[{self.name}] build_system_prompt profile: {e}")

        if profile_text:
            parts.append(f"=== PROFILO E RUOLO ===\n{profile_text}")
        else:
            parts.append(
                f"Sei il {self.name} di brAIn, responsabile del dominio '{self.domain}'."
            )

        # 2. Conoscenza condivisa brAIn (top 10 per importanza)
        try:
            r = supabase.table("org_shared_knowledge") \
                .select("category,title,content") \
                .order("importance", desc=True).limit(10).execute()
            if r.data:
                osk_lines = []
                for row in r.data:
                    osk_lines.append(f"[{row['category'].upper()}] {row['title']}: {row['content'][:300]}")
                parts.append("=== CONOSCENZA brAIn ===\n" + "\n\n".join(osk_lines))
        except Exception as e:
            logger.warning(f"[{self.name}] build_system_prompt org_knowledge: {e}")

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
                    ck_lines.append(f"[{row['knowledge_type'].upper()}] {row['title']}: {row['content'][:400]}")
                parts.append(f"=== CONOSCENZA SPECIALISTICA {self.name} ===\n" + "\n\n".join(ck_lines))
        except Exception as e:
            logger.warning(f"[{self.name}] build_system_prompt chief_knowledge: {e}")

        # 4. Episodic memory — topic (sessioni precedenti)
        if topic_scope_id:
            try:
                from intelligence.memory import get_episodes
                episodes = get_episodes("topic", topic_scope_id, limit=5)
                if episodes:
                    parts.append("=== SESSIONI PRECEDENTI ===\n" + "\n---\n".join(episodes))
            except Exception as e:
                logger.warning(f"[{self.name}] episodic topic memory error: {e}")

        # 5. Episodic memory — project (storia cantiere)
        if project_scope_id:
            try:
                from intelligence.memory import get_episodes
                proj_ep = get_episodes("project", str(project_scope_id), limit=5)
                if proj_ep:
                    parts.append("=== STORIA CANTIERE ===\n" + "\n---\n".join(proj_ep))
            except Exception as e:
                logger.warning(f"[{self.name}] episodic project memory error: {e}")

        # 6. Messaggi recenti verbatim (L1 working memory)
        if recent_messages:
            msgs_text = "\n".join(
                f"{m['role'].upper()}: {m['text'][:300]}"
                for m in recent_messages[-10:]
            )
            parts.append(f"=== CONVERSAZIONE RECENTE ===\n{msgs_text}")

        # 7. Contesto progetto (se presente)
        if project_context:
            parts.append(f"=== CONTESTO CANTIERE ===\n{project_context}")

        # 8. Regola fondamentale
        parts.append(
            "=== REGOLA FONDAMENTALE ===\n"
            "Rispondi SOLO su argomenti del tuo dominio come descritto nel PROFILO E RUOLO. "
            "Se la richiesta non è di tua competenza, attiva check_domain_routing(). "
            "Rispondi sempre in italiano, conciso, formato card con separatori \u2501\u2501\u2501. "
            "Zero fuffa, vai al punto. UNA sola domanda alla volta se devi chiedere."
        )

        return "\n\n".join(parts)

    def answer_question(self, question: str, user_context: Optional[str] = None,
                        project_context: Optional[str] = None,
                        topic_scope_id: Optional[str] = None,
                        project_scope_id: Optional[str] = None,
                        recent_messages: Optional[List[Dict]] = None) -> str:
        """Risponde a una domanda nel proprio dominio usando system prompt dinamico.
        FIX 4: inietta get_domain_context() live prima di rispondere.
        FIX 5: usa modello ottimale via _select_model().
        """
        if self.is_circuit_open():
            return f"[{self.name}] Sistema temporaneamente non disponibile. Riprova tra qualche minuto."

        system = self.build_system_prompt(
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )

        # FIX 4: inietta dati live dal dominio
        try:
            domain_ctx = self.get_domain_context()
            live_parts = []
            for k, v in domain_ctx.items():
                if k not in ("recent_decisions", "memory") and v:
                    live_parts.append(
                        f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:400]}"
                    )
            if live_parts:
                system += "\n\n=== DATI LIVE ===\n" + "\n\n".join(live_parts)
        except Exception as e:
            logger.warning(f"[{self.name}] get_domain_context in answer_question: {e}")

        if user_context:
            system += f"\n\nContesto aggiuntivo: {user_context}"

        # FIX 5: seleziona modello ottimale
        model = self._select_model(question)

        try:
            return self.call_claude(question, system=system, max_tokens=1500, model=model)
        except Exception as e:
            logger.error(f"[{self.name}] answer_question error: {e}")
            return f"[{self.name}] Errore nella risposta: {e}"

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
        sep = "\u2501" * 15

        # Fast keyword pre-check: se match univoco a un altro chief → skip Claude
        question_lower = question.lower()
        keyword_target = None
        for kw, target_id in ROUTING_KEYWORDS.items():
            if kw in question_lower and target_id != chief_id:
                keyword_target = target_id
                break

        # Verifica con Claude Haiku per routing preciso
        routing_prompt = (
            f"Sei un sistema di routing per il C-Suite di un'organizzazione AI.\n"
            f"Chief corrente: {chief_id} (dominio: {self.domain})\n"
            f"Domanda ricevuta: \"{question}\"\n\n"
            f"Chief disponibili e loro domini:\n"
            f"cso=strategy/pipeline, cfo=finance/budget, cmo=marketing/brand,\n"
            f"cto=tech/code/infra, coo=operations/processes/product/build/projects,\n"
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
            f"\U0001f4e8 {self.name} \u2192 {dest_chief.name}\n"
            f"{sep}\n"
            f"Hai chiesto: \"{question[:100]}\"\n"
            f"Competenza: {dest_chief.name}\n"
            f"{sep}\n"
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
                                triggered_by_message: str = "") -> Dict[str, Any]:
        """
        Valida un prompt prima di salvarlo in code_tasks.
        1. Chiama Claude Haiku per analizzare files e tabelle toccate
        2. Confronta col perimetro hardcoded del Chief
        3. Se OK → salva in code_tasks con sandbox_passed=True e ritorna {ok: True, task_id: X}
        4. Se violazione → alerta Mirco con card + inline keyboard, NON salva, ritorna {ok: False}
        Solo CTO può autorizzare override (callback sandbox_override:task_id).
        """
        chief_id = self.chief_id or self.name.lower()
        perimeter = SANDBOX_PERIMETERS.get(chief_id, {})
        sep = "\u2501" * 15

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
            "checked_at": datetime.now(timezone.utc).isoformat(),
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
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
                if result.data:
                    task_id = result.data[0].get("id")
            except Exception as e:
                logger.warning(f"[{self.name}] code_tasks insert error: {e}")

            logger.info(f"[{self.name}] Sandbox OK: task_id={task_id}")
            return {"ok": True, "task_id": task_id, "chief": chief_id}

        else:
            # Alert a Mirco — NON salva il task
            uf_str = ", ".join(unauthorized_files) if unauthorized_files else "nessuno"
            ut_str = ", ".join(unauthorized_tables) if unauthorized_tables else "nessuna"

            alert_text = (
                f"\U0001f6a8 Prompt bloccato \u2014 {self.name}\n"
                f"{sep}\n"
                f"\u26a0\ufe0f Tocca aree fuori perimetro\n"
                f"\U0001f4c1 File non autorizzati: {uf_str}\n"
                f"\U0001f5c4\ufe0f Tabelle non autorizzate: {ut_str}\n"
                f"{sep}"
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
                    "created_at": datetime.now(timezone.utc).isoformat(),
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
    # BRIEFING, ANOMALY, CAPABILITY (invariati)
    # ============================================================

    def generate_brief_report(self) -> Optional[str]:
        """FIX 2: Genera report breve (max 8 righe) se ci sono aggiornamenti nelle ultime 4h.
        Invia al Forum Topic del Chief. Ritorna il testo o None se nulla da segnalare.
        Override nelle sottoclassi per dati specifici di dominio.
        """
        four_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()

        # Controlla decisioni recenti
        try:
            r = supabase.table("chief_decisions") \
                .select("summary,decision_type,created_at") \
                .eq("chief_domain", self.domain) \
                .neq("decision_type", "brief_report") \
                .gte("created_at", four_hours_ago) \
                .order("created_at", desc=True).limit(5).execute()
            recent_decisions = r.data or []
        except Exception:
            recent_decisions = []

        # Controlla anomalie
        try:
            anomalies = self.check_anomalies()
        except Exception:
            anomalies = []

        # Se nulla di nuovo → skip silenzioso
        if not recent_decisions and not anomalies:
            logger.info(f"[{self.name}] generate_brief_report: nulla da segnalare, skip")
            return None

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

        ctx = "\n\n".join(ctx_parts)
        prompt = (
            f"Sei il {self.name} di brAIn. Genera un report breve di aggiornamento (max 8 righe, italiano).\n"
            f"Dominio: {self.domain}\n"
            f"Dati ultimi 4 ore:\n{ctx}\n\n"
            "Formato: usa separatori ━━━━━━━━━━━━━━━, emoji rilevanti, dati concreti. "
            "Zero fuffa. Solo novità reali."
        )

        try:
            text = self.call_claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=400)
        except Exception as e:
            logger.warning(f"[{self.name}] generate_brief_report call_claude error: {e}")
            return None

        # Salva in chief_decisions
        try:
            supabase.table("chief_decisions").insert({
                "chief_domain": self.domain,
                "decision_type": "brief_report",
                "summary": text[:500],
                "full_text": text,
                "created_at": datetime.now(timezone.utc).isoformat(),
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
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"[{self.name}] Save briefing error: {e}")

            self.notify_mirco(
                f"\U0001f4ca *{self.name} Briefing Settimanale*\n\n{briefing_text[:800]}",
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
