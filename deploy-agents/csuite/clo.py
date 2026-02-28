"""CLO — Chief Legal Officer. Dominio: legale, compliance, contratti, rischi normativi."""
from core.base_chief import BaseChief
from core.config import supabase, logger


class CLO(BaseChief):
    name = "CLO"
    chief_id = "clo"
    domain = "legal"
    default_model = "claude-sonnet-4-6"
    MY_DOMAIN = ["legale", "compliance", "contratti", "gdpr", "privacy",
                 "rischio legale", "normativa", "ai act", "termini"]
    MY_REFUSE_DOMAINS = ["codice", "marketing", "finanza", "vendite", "hr", "dns", "deploy"]
    briefing_prompt_template = (
        "Sei il CLO di brAIn. Genera un briefing legale settimanale includendo: "
        "1) Violazioni etiche rilevate (ethics_violations), "
        "2) Progetti con review legale pendente, "
        "3) Nuove normative UE rilevanti (AI Act, GDPR updates), "
        "4) Rischi legali per progetti in corso, "
        "5) Raccomandazioni compliance."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            r = supabase.table("ethics_violations").select("project_id,principle_id,severity,blocked") \
                .eq("resolved", False).order("created_at", desc=True).limit(10).execute()
            ctx["open_violations"] = r.data or []
        except Exception:
            ctx["open_violations"] = []
        try:
            r = supabase.table("legal_reviews").select(
                "project_id,status,risks_found,created_at"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["legal_reviews"] = r.data if r.data else "nessun dato ancora registrato"
        except Exception:
            ctx["legal_reviews"] = "nessun dato ancora registrato"
        try:
            r = supabase.table("projects").select(
                "id,name,status,legal_status"
            ).neq("status", "archived").execute()
            ctx["projects_legal_status"] = r.data or []
        except Exception:
            ctx["projects_legal_status"] = []
        try:
            r = supabase.table("agent_logs").select("action,status,error").eq(
                "agent_id", "ethics_monitor"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["ethics_monitor_log"] = r.data or []
        except Exception:
            ctx["ethics_monitor_log"] = []
        return ctx


    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> list:
        """CLO: ethics violations, legal reviews, compliance log — giorno precedente."""
        sections = []

        # 1. Ethics violations (giorno precedente)
        try:
            r = supabase.table("ethics_violations").select(
                "project_id,principle_id,severity,blocked,resolved"
            ).gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).execute()
            if r.data:
                blocked = sum(1 for v in r.data if v.get("blocked"))
                unresolved = sum(1 for v in r.data if not v.get("resolved"))
                viol_lines = "\n".join(
                    f"  [{v.get('severity','?')}] proj #{v.get('project_id','?')} | {v.get('principle_id','?')}"
                    f"{' | BLOCCATO' if v.get('blocked') else ''}"
                    for v in r.data[:5]
                )
                sections.append(
                    f"\U0001f6ab VIOLATIONS ({len(r.data)} | {blocked} bloccate | {unresolved} aperte)\n{viol_lines}"
                )
        except Exception as e:
            logger.warning("[CLO] ethics_violations error: %s", e)

        # 2. Legal reviews (giorno precedente)
        try:
            r = supabase.table("legal_reviews").select(
                "project_id,status,risks_found,created_at"
            ).gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).limit(5).execute()
            if r.data:
                lr_lines = "\n".join(
                    f"  proj #{row.get('project_id','?')} | {row.get('status','?')} | rischi: {row.get('risks_found','?')}"
                    for row in r.data
                )
                sections.append(f"\U0001f4cb LEGAL REVIEWS ({len(r.data)})\n{lr_lines}")
        except Exception as e:
            logger.warning("[CLO] legal_reviews error: %s", e)

        # 3. Log ethics monitor (giorno precedente)
        try:
            r = supabase.table("agent_logs").select("action,status,error") \
                .eq("agent_id", "ethics_monitor") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).limit(5).execute()
            if r.data:
                errors = [l for l in r.data if l.get("status") == "error"]
                em_lines = "\n".join(
                    f"  {log.get('action','?')[:50]} [{log.get('status','?')}]"
                    for log in r.data[:5]
                )
                err_note = f" | {len(errors)} errori" if errors else ""
                sections.append(f"\U0001f916 ETHICS MONITOR ({len(r.data)}{err_note})\n{em_lines}")
        except Exception as e:
            logger.warning("[CLO] ethics_monitor error: %s", e)

        return sections

    def check_anomalies(self):
        anomalies = []
        try:
            r = supabase.table("ethics_violations").select("id").eq("blocked", True) \
                .eq("resolved", False).execute()
            blocked_count = len(r.data or [])
            if blocked_count > 0:
                anomalies.append({
                    "type": "ethics_blocked_projects",
                    "description": f"{blocked_count} progetti bloccati per violazioni etiche non risolte",
                    "severity": "critical",
                })
        except Exception:
            pass
        return anomalies
