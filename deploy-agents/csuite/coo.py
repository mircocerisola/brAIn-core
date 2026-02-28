"""COO — Chief Operations & Revenue Officer. Dominio: operazioni, cantieri, pipeline, prodotto, revenue."""
import requests as _requests
from datetime import timedelta
from core.base_chief import BaseChief
from core.config import supabase, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome


class COO(BaseChief):
    name = "COO"
    domain = "ops"
    chief_id = "coo"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il COO di brAIn — Chief Operations & Revenue Officer. "
        "Genera un briefing operativo settimanale includendo: "
        "1) Status cantieri attivi (fase, blocchi, build_phase), "
        "2) Prodotti live e metriche chiave (KPI, conversione, smoke test), "
        "3) Pipeline problemi→soluzioni→BOS (velocità, colli di bottiglia), "
        "4) SLA rispettati/violati e action_queue pending, "
        "5) Manager di cantiere attivi e loro performance, "
        "6) Azioni operative e di prodotto prioritarie."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        # Cantieri attivi — FULL data (v5.11)
        try:
            r = supabase.table("projects").select(
                "id,name,status,build_phase,pipeline_step,pipeline_territory,"
                "pipeline_locked,topic_id,smoke_test_url,created_at,updated_at"
            ).neq("status", "archived").execute()
            ctx["active_projects"] = r.data or []
        except Exception as e:
            ctx["active_projects"] = f"errore lettura DB: {e}"
        # Action queue pending — FULL details (v5.11)
        try:
            r = supabase.table("action_queue").select(
                "id,action_type,title,description,project_id,status,created_at"
            ).eq("status", "pending").order("created_at", desc=True).execute()
            ctx["pending_actions"] = r.data or []
        except Exception as e:
            ctx["pending_actions"] = f"errore lettura DB: {e}"
        # Prodotti live (ex-CPO)
        try:
            r = supabase.table("projects").select("id,name,status,build_phase") \
                .in_("status", ["build_complete", "launch_approved", "live"]).execute()
            ctx["products_live"] = r.data or []
        except Exception as e:
            ctx["products_live"] = f"errore lettura DB: {e}"
        # KPI recenti (ex-CPO)
        try:
            r = supabase.table("kpi_daily").select("project_id,metric_name,value,recorded_at") \
                .order("recorded_at", desc=True).limit(20).execute()
            ctx["recent_kpis"] = r.data or []
        except Exception as e:
            ctx["recent_kpis"] = f"errore lettura DB: {e}"
        # Agent logs recenti (v5.11) — ultimi 20
        try:
            r = supabase.table("agent_logs").select(
                "agent_id,action,status,cost_usd,created_at"
            ).order("created_at", desc=True).limit(20).execute()
            ctx["recent_logs"] = r.data or []
        except Exception as e:
            ctx["recent_logs"] = f"errore lettura DB: {e}"
        return ctx


    def _daily_report_emoji(self) -> str:
        return "\u2699\ufe0f"

    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> list:
        """COO: azioni queue, log agenti, task completati — giorno precedente."""
        sections = []

        # 1. Azioni queue create (giorno precedente)
        try:
            r = supabase.table("action_queue").select("id,action_type,title,status") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine) \
                .order("created_at", desc=True).execute()
            if r.data:
                by_status = {}
                for a in r.data:
                    s = a.get("status", "?")
                    by_status[s] = by_status.get(s, 0) + 1
                status_lines = "\n".join(f"  {s}: {cnt}" for s, cnt in by_status.items())
                sections.append(f"\U0001f4cb ACTION QUEUE ({len(r.data)} azioni)\n{status_lines}")
        except Exception as e:
            logger.warning("[COO] action_queue error: %s", e)

        # 2. Log agenti (giorno precedente)
        try:
            r = supabase.table("agent_logs").select("agent_id,action,status,cost_usd") \
                .gte("created_at", ieri_inizio).lt("created_at", ieri_fine).execute()
            if r.data:
                by_agent = {}
                errors = 0
                for log in r.data:
                    a = log.get("agent_id", "?")
                    by_agent[a] = by_agent.get(a, 0) + 1
                    if log.get("status") == "error":
                        errors += 1
                top5 = sorted(by_agent.items(), key=lambda x: x[1], reverse=True)[:5]
                agent_lines = "\n".join(f"  {a}: {n} azioni" for a, n in top5)
                err_note = f" | {errors} errori" if errors > 0 else ""
                sections.append(f"\U0001f4dd LOG AGENTI ({len(r.data)} totale{err_note})\n{agent_lines}")
        except Exception as e:
            logger.warning("[COO] agent_logs error: %s", e)

        # 3. Progetti aggiornati (giorno precedente)
        try:
            r = supabase.table("projects").select("id,name,pipeline_step,status") \
                .gte("updated_at", ieri_inizio).lt("updated_at", ieri_fine) \
                .neq("status", "archived").execute()
            if r.data:
                proj_lines = "\n".join(
                    f"  {row.get('name','?')[:40]} → {row.get('pipeline_step') or row.get('status','?')}"
                    for row in r.data[:5]
                )
                sections.append(f"\U0001f3d7 CANTIERI AGGIORNATI ({len(r.data)})\n{proj_lines}")
        except Exception as e:
            logger.warning("[COO] projects error: %s", e)

        # 4. KPI registrati (giorno precedente)
        try:
            r = supabase.table("kpi_daily").select("project_id,metric_name,value") \
                .gte("recorded_at", ieri_inizio).lt("recorded_at", ieri_fine).limit(10).execute()
            if r.data:
                kpi_lines = "\n".join(
                    f"  #{row.get('project_id','?')} {row.get('metric_name','?')}: {row.get('value','?')}"
                    for row in r.data[:5]
                )
                sections.append(f"\U0001f4ca KPI REGISTRATI ({len(r.data)})\n{kpi_lines}")
        except Exception as e:
            logger.warning("[COO] kpi_daily error: %s", e)

        return sections

    # ============================================================
    # FIX 5: AUTO-OPEN TOPIC #cantiere per progetto
    # ============================================================

    def ensure_project_topic(self, project_id, project_name=""):
        """Crea topic Telegram per il progetto se non esiste. Salva topic_id in projects."""
        try:
            r = supabase.table("projects").select("id,name,topic_id").eq("id", project_id).execute()
            if not r.data:
                return None
            project = r.data[0]
            if project.get("topic_id"):
                return project["topic_id"]
            name = project_name or project.get("name", "Progetto")
        except Exception as e:
            logger.warning("[COO] ensure_project_topic read: %s", e)
            return None

        if not TELEGRAM_BOT_TOKEN:
            return None

        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if not group_r.data:
                return None
            group_id = int(group_r.data[0]["value"])

            resp = _requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/createForumTopic",
                json={
                    "chat_id": group_id,
                    "name": "\U0001f3d7 " + name[:60],
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("ok") and data.get("result", {}).get("message_thread_id"):
                new_topic_id = data["result"]["message_thread_id"]
                supabase.table("projects").update({
                    "topic_id": new_topic_id,
                }).eq("id", project_id).execute()
                logger.info("[COO] Topic creato per project #%d: thread_id=%d", project_id, new_topic_id)
                return new_topic_id
        except Exception as e:
            logger.warning("[COO] ensure_project_topic create: %s", e)
        return None

    # ============================================================
    # STEP 5: REPORT GIORNALIERO MIGLIORATO nel #cantiere
    # ============================================================

    def _get_project_tasks(self, project_id):
        """Carica tutti i task di un progetto."""
        try:
            r = supabase.table("project_tasks").select("*") \
                .eq("project_id", project_id).order("priority").execute()
            return r.data or []
        except Exception as e:
            logger.warning("[COO] _get_project_tasks: %s", e)
            return []

    def _progress_bar(self, done, total):
        """Genera barra progresso: [====----] 3/6"""
        if total == 0:
            return "[--------] 0/0"
        filled = round(done / total * 8)
        bar = "\u2588" * filled + "\u2591" * (8 - filled)
        return "[" + bar + "] " + str(done) + "/" + str(total)

    def send_project_daily_report(self, project_id):
        """Invia report giornaliero di un progetto nel suo topic #cantiere (solo cantieri aperti)."""
        now = now_rome()
        oggi_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        ieri_dt = oggi_start - timedelta(days=1)
        ieri_inizio = ieri_dt.isoformat()
        ieri_fine = oggi_start.isoformat()

        # Carica progetto
        try:
            r = supabase.table("projects").select(
                "id,name,brand_name,pipeline_step,status,topic_id,cantiere_status"
            ).eq("id", project_id).execute()
            if not r.data:
                return None
            project = r.data[0]
        except Exception as e:
            logger.warning("[COO] project_daily_report read: %s", e)
            return None

        if project.get("status") == "archived":
            return None
        if project.get("cantiere_status") != "open":
            return None

        topic_id = project.get("topic_id")
        if not topic_id:
            topic_id = self.ensure_project_topic(project_id, project.get("name", ""))
        if not topic_id:
            return None

        brand = project.get("brand_name") or project.get("name", "Progetto")
        step = project.get("pipeline_step") or project.get("status", "?")

        # Carica task del progetto
        tasks = self._get_project_tasks(project_id)
        done_tasks = [t for t in tasks if t.get("status") == "completed"]
        progress_tasks = [t for t in tasks if t.get("status") == "in_progress"]
        mirco_tasks = [t for t in tasks if t.get("assigned_to") == "mirco" and t.get("status") != "completed"]
        pending_tasks = [t for t in tasks if t.get("status") == "pending" and t.get("assigned_to") != "mirco"]

        # Completati ieri
        done_ieri = []
        for t in done_tasks:
            ca = t.get("completed_at") or ""
            if ca >= ieri_inizio and ca < ieri_fine:
                done_ieri.append(t)

        # Costruisci messaggio
        mese = self._MESI_IT[ieri_dt.month]
        header_date = str(ieri_dt.day) + " " + mese

        lines = [
            "\U0001f3d7 " + brand + " \u2014 " + header_date,
            "",
            "\U0001f4cd Step: " + step,
            "\U0001f4ca " + self._progress_bar(len(done_tasks), len(tasks)),
        ]

        if done_ieri:
            lines.append("")
            lines.append("\u2705 COMPLETATI IERI:")
            for t in done_ieri:
                lines.append("  " + t.get("title", "?")[:50])

        if progress_tasks:
            lines.append("")
            lines.append("\U0001f7e1 IN CORSO:")
            for t in progress_tasks:
                lines.append("  " + t.get("assigned_to", "?").upper() + ": " + t.get("title", "?")[:45])

        if mirco_tasks:
            lines.append("")
            lines.append("\U0001f534 ATTESA MIRCO:")
            for t in mirco_tasks:
                lines.append("  " + t.get("title", "?")[:50])

        if pending_tasks:
            lines.append("")
            lines.append("\u26AA PROSSIMI:")
            for t in pending_tasks[:2]:
                lines.append("  " + t.get("assigned_to", "?").upper() + ": " + t.get("title", "?")[:45])

        # Priorita' oggi
        priority_today = None
        for t in tasks:
            if t.get("status") != "completed":
                priority_today = t
                break
        if priority_today:
            lines.append("")
            lines.append("\U0001f3af PRIORITA' OGGI: " + priority_today.get("title", "?")[:40])

        text = "\n".join(lines)

        # Invia nel topic del progetto
        self._send_to_topic(topic_id, text)
        logger.info("[COO] Project daily report inviato project #%d", project_id)
        return text

    def _send_to_topic(self, topic_id, text):
        """Invia messaggio nel topic Telegram."""
        if not TELEGRAM_BOT_TOKEN:
            return
        try:
            group_r = supabase.table("org_config").select("value").eq("key", "telegram_group_id").execute()
            if group_r.data:
                group_id = int(group_r.data[0]["value"])
                _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                    json={"chat_id": group_id, "message_thread_id": topic_id, "text": text},
                    timeout=10,
                )
        except Exception as e:
            logger.warning("[COO] _send_to_topic: %s", e)

    def send_all_project_daily_reports(self):
        """Invia report giornaliero per TUTTI i progetti con cantiere aperto."""
        try:
            r = supabase.table("projects").select("id") \
                .eq("cantiere_status", "open").not_.is_("topic_id", "null").execute()
            reports = []
            for project in (r.data or []):
                report = self.send_project_daily_report(project["id"])
                if report:
                    reports.append(project["id"])
            logger.info("[COO] Daily reports inviati per %d progetti", len(reports))
            return {"status": "ok", "projects_reported": reports}
        except Exception as e:
            logger.warning("[COO] send_all_project_daily_reports: %s", e)
            return {"error": str(e)}

    # ============================================================
    # STEP 4: COO ACCELERATORE — check task + reminder
    # ============================================================

    def accelerate_open_cantieri(self):
        """Controlla task di tutti i cantieri aperti, invia reminder se bloccati."""
        try:
            r = supabase.table("projects").select("id,name,brand_name,topic_id") \
                .eq("cantiere_status", "open").not_.is_("topic_id", "null").execute()
            results = []
            for project in (r.data or []):
                result = self._check_and_remind(project)
                if result:
                    results.append(result)
            logger.info("[COO] Accelerator: %d cantieri controllati", len(results))
            return {"status": "ok", "cantieri_checked": results}
        except Exception as e:
            logger.warning("[COO] accelerate_open_cantieri: %s", e)
            return {"error": str(e)}

    def _check_and_remind(self, project):
        """Controlla task di un progetto e invia reminder per quelli bloccati."""
        project_id = project["id"]
        topic_id = project.get("topic_id")
        brand = project.get("brand_name") or project.get("name", "Progetto")
        tasks = self._get_project_tasks(project_id)
        if not tasks:
            return None

        done = [t for t in tasks if t.get("status") == "completed"]
        in_progress = [t for t in tasks if t.get("status") == "in_progress"]
        pending = [t for t in tasks if t.get("status") == "pending"]

        reminders = []
        now = now_rome()

        for t in in_progress:
            # Se in_progress da piu' di 24h senza update
            updated = t.get("updated_at") or t.get("created_at") or ""
            if updated:
                try:
                    from dateutil.parser import parse as parse_dt
                    age_h = (now - parse_dt(updated)).total_seconds() / 3600
                    if age_h > 24:
                        assignee = t.get("assigned_to", "?")
                        reminders.append(
                            "\u23f0 " + assignee.upper() + ": " + t.get("title", "?")[:40]
                            + " (fermo da " + str(int(age_h)) + "h)"
                        )
                except Exception:
                    pass

        # Mirco task pending → reminder speciale
        mirco_pending = [t for t in pending if t.get("assigned_to") == "mirco"]
        for t in mirco_pending:
            reminders.append(
                "\U0001f534 MIRCO: " + t.get("title", "?")[:40] + " [da fare]"
            )

        if not reminders:
            return {"project_id": project_id, "reminders": 0}

        text = (
            "\u23f0 REMINDER " + brand + "\n\n"
            + self._progress_bar(len(done), len(tasks)) + "\n"
            + "\n".join(reminders)
        )
        if topic_id:
            self._send_to_topic(topic_id, text)
        logger.info("[COO] Reminder inviato project #%d (%d items)", project_id, len(reminders))
        return {"project_id": project_id, "reminders": len(reminders)}

    def check_anomalies(self):
        anomalies = []
        # Azioni stale
        try:
            from datetime import datetime, timezone
            r = supabase.table("action_queue").select("created_at").eq("status", "pending").execute()
            for row in (r.data or []):
                created = row.get("created_at", "")
                if created:
                    from dateutil.parser import parse as parse_dt
                    age = (now_rome() - parse_dt(created)).days
                    if age > 7:
                        anomalies.append({
                            "type": "stale_action",
                            "description": f"Azione pending da {age} giorni",
                            "severity": "medium",
                        })
                        break
        except Exception:
            pass
        # Cantieri bloccati
        try:
            r = supabase.table("projects") \
                .select("id,name,status,updated_at") \
                .in_("status", ["review_phase1", "review_phase2", "review_phase3"]).execute()
            for row in (r.data or []):
                updated = row.get("updated_at", "")
                if updated:
                    from datetime import datetime, timezone
                    from dateutil.parser import parse as parse_dt
                    age = (now_rome() - parse_dt(updated)).days
                    if age > 5:
                        anomalies.append({
                            "type": "stale_build",
                            "description": f"Cantiere {row.get('name','?')} in {row.get('status','?')} da {age} giorni senza aggiornamenti",
                            "severity": "high",
                        })
        except Exception:
            pass
        return anomalies
