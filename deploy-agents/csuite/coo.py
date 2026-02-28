"""COO — Chief Operations & Revenue Officer. Dominio: operazioni, cantieri, pipeline, prodotto, revenue.
v5.31: conversation_state helpers, handle_interruption, complete_task_and_handoff.
v5.28: orchestrate, delegate_to_chief, handle_domain_setup_flow.
v5.23: send_daily_brain_snapshot (Drive + email + Supabase), rename_cantiere.
"""
import json
import os
import smtplib
from datetime import timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

import requests as _requests
from core.base_chief import BaseChief
from csuite.cultura import CULTURA_BRAIN
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger
from core.templates import now_rome
from csuite.utils import fmt


COO_INTERVENTION_TRIGGERS = [
    "perche si intromette", "perché si intromette",
    "chi ha risposto", "non era per te",
    "confuso", "basta", "troppi messaggi",
    "stai zitto", "silenzio", "non ti ho chiesto",
    "rispondete tutti", "rispondete in troppi",
]

CONVERSATION_TIMEOUT_MINUTES = 30


class COO(BaseChief):
    name = "COO"
    domain = "ops"
    chief_id = "coo"
    default_model = "claude-sonnet-4-6"
    MY_DOMAIN = ["operazioni", "cantieri", "pipeline", "prodotto", "revenue",
                 "coordinamento", "task", "dominio", "accelerazione", "report"]
    MY_REFUSE_DOMAINS = []  # COO coordina tutto, non rifiuta nulla
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


    def _get_daily_report_sections(self, ieri_inizio: str, ieri_fine: str) -> list:
        """Azioni queue, log agenti, task completati — giorno precedente."""
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

    #
    # FIX 5: AUTO-OPEN TOPIC #cantiere per progetto
    #

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

    #
    # STEP 5: REPORT GIORNALIERO MIGLIORATO nel #cantiere
    #

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
        """Genera barra progresso: [████░░░░] 3/6"""
        if total == 0:
            return "[\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591] 0/0"
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
            "\u2699\ufe0f COO",
            "Report Cantiere " + brand + " " + header_date,
            "",
            "Step: " + step,
            self._progress_bar(len(done_tasks), len(tasks)),
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

    #
    # STEP 4: COO ACCELERATORE — check task + reminder
    #

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
            "\u2699\ufe0f COO\n"
            "Reminder " + brand + "\n\n"
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
                            "description": "Azione pending da " + str(age) + " giorni",
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
                            "description": "Cantiere " + row.get("name", "?") + " in " + row.get("status", "?") + " da " + str(age) + " giorni",
                            "severity": "high",
                        })
        except Exception:
            pass
        return anomalies

    #
    # SNAPSHOT GIORNALIERO — Drive + Email + Supabase
    #

    def send_daily_brain_snapshot(self):
        """Genera snapshot giornaliero, salva su Drive (brAIn/Snapshots/), Supabase, email a Mirco."""
        now = now_rome()
        today_str = now.strftime("%Y-%m-%d")
        filename = "BRAIN-SNAPSHOT-" + today_str + ".md"

        # 1. Genera contenuto snapshot da Supabase
        snapshot_md = self._generate_snapshot_content(now)

        # 2. Genera sommario (cambiamenti ultime 24h)
        sommario = self._generate_snapshot_sommario(now)

        # 3. Salva su Google Drive
        drive_url = self._upload_to_drive(filename, snapshot_md)

        # 4. Salva su Supabase
        try:
            supabase.table("brain_snapshots").insert({
                "snapshot_date": today_str,
                "snapshot_md": snapshot_md,
                "sommario": sommario,
                "drive_url": drive_url or "",
                "filename": filename,
                "created_at": now.isoformat(),
            }).execute()
            logger.info("[COO] Snapshot salvato in brain_snapshots: %s", filename)
        except Exception as e:
            logger.warning("[COO] brain_snapshots insert: %s", e)

        # 5. Invia email a Mirco
        mirco_email = os.getenv("MIRCO_EMAIL", "mircocerisola@gmail.com")
        self._send_snapshot_email(mirco_email, today_str, sommario, filename, snapshot_md)

        # 6. Assicurati record Mirco in users
        try:
            existing = supabase.table("users").select("id").eq("role", "ceo").execute()
            if not existing.data:
                supabase.table("users").insert({
                    "name": "Mirco",
                    "role": "ceo",
                    "email": mirco_email,
                    "telegram_id": "8307106544",
                }).execute()
            else:
                supabase.table("users").update({
                    "email": mirco_email,
                }).eq("role", "ceo").execute()
        except Exception as e:
            logger.warning("[COO] users upsert: %s", e)

        logger.info("[COO] Daily snapshot completato: %s", filename)
        return {
            "status": "ok",
            "filename": filename,
            "drive_url": drive_url or "",
            "sommario_lines": len(sommario.split("\n")),
        }

    def _generate_snapshot_content(self, now):
        """Genera contenuto markdown dello snapshot leggendo Supabase."""
        lines = [
            "# brAIn Snapshot",
            "",
            "Generato: " + now.strftime("%Y-%m-%d %H:%M") + " CET",
            "",
        ]

        # Progetti
        try:
            r = supabase.table("projects").select(
                "id,name,brand_name,status,pipeline_step,cantiere_status"
            ).neq("status", "archived").execute()
            lines.append("## Progetti Attivi")
            lines.append("")
            for p in (r.data or []):
                brand = p.get("brand_name") or p.get("name", "?")
                lines.append(
                    "- " + brand + " (id " + str(p["id"]) + "): "
                    + (p.get("status") or "?") + " / " + (p.get("pipeline_step") or "?")
                    + " / cantiere=" + (p.get("cantiere_status") or "closed")
                )
            if not r.data:
                lines.append("- Nessun progetto attivo")
            lines.append("")
        except Exception as e:
            lines.append("Progetti: errore " + str(e)[:50])
            lines.append("")

        # Task per progetto
        try:
            r = supabase.table("project_tasks").select(
                "id,project_id,title,status,assigned_to"
            ).execute()
            if r.data:
                lines.append("## Task Progetti")
                lines.append("")
                for t in (r.data or []):
                    lines.append(
                        "- [" + (t.get("status") or "?") + "] "
                        + (t.get("assigned_to") or "?").upper() + ": "
                        + (t.get("title") or "?")[:60]
                        + " (proj " + str(t.get("project_id")) + ")"
                    )
                lines.append("")
        except Exception:
            pass

        # Tabelle con conteggi
        tables_counts = [
            ("problems", "Problemi"), ("solutions", "Soluzioni"),
            ("agent_logs", "Agent Logs"), ("agent_events", "Agent Events"),
            ("code_tasks", "Code Tasks"), ("scan_sources", "Fonti"),
        ]
        lines.append("## Dati Supabase")
        lines.append("")
        for tbl, label in tables_counts:
            try:
                r = supabase.table(tbl).select("*", count="exact").limit(0).execute()
                cnt = r.count if r.count is not None else "?"
                lines.append("- " + label + ": " + str(cnt) + " record")
            except Exception:
                lines.append("- " + label + ": errore")
        lines.append("")

        # Errori ultime 24h
        try:
            ieri = (now - timedelta(hours=24)).isoformat()
            r = supabase.table("agent_logs").select("agent_id,action,error") \
                .eq("status", "error").gte("created_at", ieri).execute()
            if r.data:
                lines.append("## Errori ultime 24h (" + str(len(r.data)) + ")")
                lines.append("")
                for e in (r.data or [])[:10]:
                    lines.append(
                        "- " + (e.get("agent_id") or "?") + ": "
                        + (e.get("error") or "?")[:80]
                    )
                lines.append("")
        except Exception:
            pass

        # Costi ultime 24h
        try:
            ieri = (now - timedelta(hours=24)).isoformat()
            r = supabase.table("agent_logs").select("cost_usd") \
                .gte("created_at", ieri).execute()
            total = sum(float(row.get("cost_usd") or 0) for row in (r.data or []))
            lines.append("## Costi ultime 24h")
            lines.append("")
            eur = round(total * 0.92, 4)
            lines.append("- Totale: EUR " + str(eur))
            lines.append("")
        except Exception:
            pass

        return "\n".join(lines)

    def _generate_snapshot_sommario(self, now):
        """Genera sommario max 20 righe con cambiamenti ultime 24h."""
        ieri = (now - timedelta(hours=24)).isoformat()
        lines = []

        # Task completati
        try:
            r = supabase.table("project_tasks").select("title,project_id") \
                .eq("status", "completed").gte("updated_at", ieri).execute()
            if r.data:
                lines.append("Task completati: " + str(len(r.data)))
                for t in (r.data or [])[:3]:
                    lines.append("  - " + (t.get("title") or "?")[:50])
        except Exception:
            pass

        # Nuovi problemi
        try:
            r = supabase.table("problems").select("id", count="exact") \
                .gte("created_at", ieri).limit(0).execute()
            cnt = r.count if r.count is not None else 0
            if cnt > 0:
                lines.append("Nuovi problemi scansionati: " + str(cnt))
        except Exception:
            pass

        # Nuove soluzioni
        try:
            r = supabase.table("solutions").select("id", count="exact") \
                .gte("created_at", ieri).limit(0).execute()
            cnt = r.count if r.count is not None else 0
            if cnt > 0:
                lines.append("Nuove soluzioni generate: " + str(cnt))
        except Exception:
            pass

        # Errori
        try:
            r = supabase.table("agent_logs").select("id", count="exact") \
                .eq("status", "error").gte("created_at", ieri).limit(0).execute()
            cnt = r.count if r.count is not None else 0
            if cnt > 0:
                lines.append("Errori agenti: " + str(cnt))
        except Exception:
            pass

        # Costi
        try:
            r = supabase.table("agent_logs").select("cost_usd") \
                .gte("created_at", ieri).execute()
            total = sum(float(row.get("cost_usd") or 0) for row in (r.data or []))
            lines.append("Costi 24h: EUR " + str(round(total * 0.92, 4)))
        except Exception:
            pass

        # Progetti attivi
        try:
            r = supabase.table("projects").select("name,pipeline_step") \
                .neq("status", "archived").execute()
            for p in (r.data or []):
                lines.append("Progetto " + (p.get("name") or "?")[:30] + ": " + (p.get("pipeline_step") or "?"))
        except Exception:
            pass

        if not lines:
            lines.append("Nessun cambiamento significativo nelle ultime 24h.")

        return "\n".join(lines[:20])

    def _upload_to_drive(self, filename, content):
        """Carica file su Google Drive in brAIn/Snapshots/. Crea cartelle se necessario."""
        sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if not sa_json:
            logger.warning("[COO] GOOGLE_SERVICE_ACCOUNT_JSON non configurato, skip Drive upload")
            return ""

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaInMemoryUpload

            creds_dict = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=["https://www.googleapis.com/auth/drive"]
            )
            service = build("drive", "v3", credentials=creds)
        except Exception as e:
            logger.warning("[COO] Google Drive auth error: %s", e)
            return ""

        # Cartella brAIn — cerca o crea, salva ID in brain_config
        brain_folder_id = self._get_or_create_drive_folder(service, "brAIn", None)
        if not brain_folder_id:
            return ""

        # Sottocartella Snapshots
        snapshots_folder_id = self._get_or_create_drive_folder(service, "Snapshots", brain_folder_id)
        if not snapshots_folder_id:
            return ""

        # Upload file
        try:
            media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/markdown")
            file_meta = {
                "name": filename,
                "parents": [snapshots_folder_id],
                "mimeType": "text/markdown",
            }
            uploaded = service.files().create(
                body=file_meta, media_body=media, fields="id,webViewLink"
            ).execute()
            drive_url = uploaded.get("webViewLink", "")
            logger.info("[COO] Drive upload OK: %s -> %s", filename, drive_url)

            # Cleanup: mantieni ultimi 30 file
            self._cleanup_old_drive_files(service, snapshots_folder_id, keep=30)

            return drive_url
        except Exception as e:
            logger.warning("[COO] Drive upload error: %s", e)
            return ""

    def _get_or_create_drive_folder(self, service, name, parent_id):
        """Cerca o crea cartella Drive. Salva brain folder ID in brain_config."""
        is_root = parent_id is None

        # Se e' la cartella brAIn, cerca ID salvato
        if is_root:
            try:
                r = supabase.table("brain_config").select("value") \
                    .eq("key", "DRIVE_BRAIN_FOLDER_ID").execute()
                if r.data and r.data[0].get("value"):
                    return r.data[0]["value"]
            except Exception:
                pass

        # Cerca cartella esistente
        try:
            query = "name='" + name + "' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            if parent_id:
                query += " and '" + parent_id + "' in parents"
            results = service.files().list(q=query, fields="files(id)").execute()
            files = results.get("files", [])
            if files:
                folder_id = files[0]["id"]
                if is_root:
                    self._save_brain_folder_id(folder_id)
                return folder_id
        except Exception as e:
            logger.warning("[COO] Drive folder search error: %s", e)

        # Crea cartella
        try:
            meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
            if parent_id:
                meta["parents"] = [parent_id]
            folder = service.files().create(body=meta, fields="id").execute()
            folder_id = folder["id"]
            logger.info("[COO] Drive folder creata: %s (id=%s)", name, folder_id)
            if is_root:
                self._save_brain_folder_id(folder_id)
            return folder_id
        except Exception as e:
            logger.warning("[COO] Drive folder create error: %s", e)
            return None

    def _save_brain_folder_id(self, folder_id):
        """Salva l'ID della cartella brAIn in brain_config."""
        try:
            supabase.table("brain_config").upsert({
                "key": "DRIVE_BRAIN_FOLDER_ID",
                "value": folder_id,
                "updated_at": now_rome().isoformat(),
            }).execute()
        except Exception as e:
            logger.warning("[COO] brain_config save: %s", e)

    def _cleanup_old_drive_files(self, service, folder_id, keep=30):
        """Elimina file piu' vecchi di keep nella cartella."""
        try:
            results = service.files().list(
                q="'" + folder_id + "' in parents and trashed=false",
                fields="files(id,name,createdTime)",
                orderBy="createdTime desc",
                pageSize=100,
            ).execute()
            files = results.get("files", [])
            if len(files) > keep:
                for f in files[keep:]:
                    service.files().delete(fileId=f["id"]).execute()
                logger.info("[COO] Drive cleanup: eliminati %d file vecchi", len(files) - keep)
        except Exception as e:
            logger.warning("[COO] Drive cleanup error: %s", e)

    def _send_snapshot_email(self, to_email, date_str, sommario, filename, snapshot_md):
        """Invia email con sommario + allegato snapshot."""
        gmail_user = os.getenv("GMAIL_USER", "")
        gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
        if not gmail_user or not gmail_pass:
            logger.warning("[COO] GMAIL_USER o GMAIL_APP_PASSWORD non configurati, skip email")
            return

        msg = MIMEMultipart()
        msg["From"] = gmail_user
        msg["To"] = to_email
        msg["Subject"] = "brAIn Snapshot \u2014 " + date_str

        body = "Sommario giornaliero brAIn\n\n" + sommario
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Allegato
        attachment = MIMEBase("application", "octet-stream")
        attachment.set_payload(snapshot_md.encode("utf-8"))
        encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(attachment)

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(gmail_user, gmail_pass)
                server.sendmail(gmail_user, to_email, msg.as_string())
            logger.info("[COO] Email snapshot inviata a %s", to_email)
        except Exception as e:
            logger.warning("[COO] Email send error: %s", e)

    #
    # RENAME CANTIERE — Supabase + Telegram topic
    #

    def rename_cantiere(self, project_id, nuovo_nome):
        """Rinomina progetto su Supabase e topic Telegram. Conferma solo se entrambi OK."""
        # 1. Aggiorna nome in Supabase
        db_ok = False
        try:
            supabase.table("projects").update({
                "name": nuovo_nome,
                "brand_name": nuovo_nome,
            }).eq("id", project_id).execute()
            db_ok = True
            logger.info("[COO] DB rinominato project #%d -> %s", project_id, nuovo_nome)
        except Exception as e:
            logger.warning("[COO] rename_cantiere DB error: %s", e)
            return {"error": "DB update fallito: " + str(e)}

        # 2. Trova topic_id del cantiere
        topic_id = None
        group_id = None
        try:
            r = supabase.table("projects").select("topic_id,cantiere_thread_id") \
                .eq("id", project_id).execute()
            if r.data:
                topic_id = r.data[0].get("cantiere_thread_id") or r.data[0].get("topic_id")
        except Exception as e:
            logger.warning("[COO] rename_cantiere topic lookup: %s", e)

        try:
            group_r = supabase.table("org_config").select("value") \
                .eq("key", "telegram_group_id").execute()
            if group_r.data:
                group_id = int(group_r.data[0]["value"])
        except Exception:
            pass

        # 3. Rinomina topic Telegram
        tg_ok = False
        if TELEGRAM_BOT_TOKEN and group_id and topic_id:
            try:
                resp = _requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/editForumTopic",
                    json={
                        "chat_id": group_id,
                        "message_thread_id": topic_id,
                        "name": "\U0001f3d7 " + nuovo_nome[:60],
                    },
                    timeout=10,
                )
                result = resp.json()
                if result.get("ok"):
                    tg_ok = True
                    logger.info("[COO] Telegram topic rinominato: %s", nuovo_nome)
                    # Salva cantiere_thread_id se non presente
                    try:
                        supabase.table("projects").update({
                            "cantiere_thread_id": topic_id,
                        }).eq("id", project_id).execute()
                    except Exception:
                        pass
                else:
                    logger.warning("[COO] editForumTopic failed: %s", result.get("description", ""))
            except Exception as e:
                logger.warning("[COO] rename_cantiere Telegram error: %s", e)

        # 4. Conferma o errore nel topic
        if db_ok and tg_ok:
            confirm_text = (
                "\u2699\ufe0f COO\n"
                "Cantiere rinominato\n\n"
                "Nuovo nome: " + nuovo_nome
            )
            if topic_id:
                self._send_to_topic(topic_id, confirm_text)
            return {"status": "ok", "project_id": project_id, "nuovo_nome": nuovo_nome, "db": True, "telegram": True}
        elif db_ok and not tg_ok:
            err_text = (
                "\u2699\ufe0f COO\n"
                "Rinomina parziale\n\n"
                "Nome aggiornato in DB ma rinomina topic Telegram fallita."
            )
            if topic_id:
                self._send_to_topic(topic_id, err_text)
            return {"status": "partial", "project_id": project_id, "db": True, "telegram": False}
        else:
            return {"error": "Operazione fallita", "db": False, "telegram": False}

    #
    # ORCHESTRAZIONE — COO come coordinatore reale
    #

    HUMAN_ONLY_ACTIONS = [
        "comprare_dominio", "creare_account_esterno", "pagamento",
        "firma_contratto", "registrazione_servizio", "configurare_dns",
        "creare_email_provider", "acquisto_licenza",
    ]

    def orchestrate(self, message, thread_id=None):
        """Analizza richiesta, divide azioni umane/agente, delega.
        Usa Haiku per intent analysis con output JSON.
        """
        logger.info("[COO] orchestrate: %s", message[:200])

        classify_prompt = (
            "Sei il COO di brAIn. Analizza questa richiesta e dividi in azioni.\n"
            "Richiesta: " + message + "\n\n"
            "Rispondi SOLO JSON:\n"
            '{"intent": "breve descrizione",\n'
            ' "human_actions": [{"action": "...", "description": "..."}],\n'
            ' "agent_actions": [{"chief": "cmo/cto/cso/cfo/clo/cpeo", "action": "...", "description": "..."}]}\n\n'
            "Azioni umane: comprare dominio, configurare DNS, creare account esterno, pagamento.\n"
            "Azioni agente: landing page (CMO), deploy (CTO), strategia (CSO), analisi costi (CFO)."
        )

        try:
            raw = self.call_claude(classify_prompt, model="claude-haiku-4-5-20251001", max_tokens=500)
            import re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if m:
                plan = json.loads(m.group(0))
            else:
                plan = {"intent": message[:100], "human_actions": [], "agent_actions": []}
        except Exception as e:
            logger.warning("[COO] orchestrate classify: %s", e)
            plan = {"intent": message[:100], "human_actions": [], "agent_actions": []}

        human_actions = plan.get("human_actions", [])
        agent_actions = plan.get("agent_actions", [])

        # Delega azioni agente
        for action in agent_actions:
            self.delegate_to_chief(action)

        # Formatta risultato
        lines = [plan.get("intent", "")]
        if human_actions:
            lines.append("")
            lines.append(self.format_human_actions(human_actions))
        if agent_actions:
            lines.append("")
            lines.append("Delegato a: " + ", ".join(
                a.get("chief", "?").upper() for a in agent_actions
            ))

        result_text = fmt("coo", "Piano operativo", "\n".join(lines))

        if thread_id:
            self._send_to_topic(thread_id, result_text)

        logger.info("[COO] orchestrate: %d human, %d agent actions",
                    len(human_actions), len(agent_actions))
        return {
            "status": "ok",
            "intent": plan.get("intent", ""),
            "human_actions": human_actions,
            "agent_actions": agent_actions,
        }

    def delegate_to_chief(self, action):
        """Inserisce agent_event con event_type='task_delegation' per il Chief target."""
        chief = action.get("chief", "coo")
        try:
            supabase.table("agent_events").insert({
                "event_type": "task_delegation",
                "agent_from": "coo",
                "agent_to": chief,
                "payload": json.dumps({
                    "action": action.get("action", ""),
                    "description": action.get("description", ""),
                }),
                "created_at": now_rome().isoformat(),
            }).execute()
            logger.info("[COO] Delegato a %s: %s", chief, action.get("action", ""))
        except Exception as e:
            logger.warning("[COO] delegate_to_chief: %s", e)

    def handle_domain_setup_flow(self, project_slug, thread_id=None):
        """Crea checklist Mirco (DNS, email) + task agenti (CMO landing, CTO deploy)."""
        logger.info("[COO] handle_domain_setup_flow: %s", project_slug)

        # Azioni umane
        human_actions = [
            {"action": "comprare_dominio", "description": "Acquista dominio " + project_slug},
            {"action": "configurare_dns", "description": "Punta DNS al server"},
            {"action": "creare_email_provider", "description": "Crea email info@" + project_slug},
        ]

        # Azioni agente
        agent_actions = [
            {"chief": "cmo", "action": "genera_landing", "description": "Genera landing page HTML per " + project_slug},
            {"chief": "cto", "action": "deploy_landing", "description": "Deploy landing page su Cloud Run per " + project_slug},
        ]

        for action in agent_actions:
            self.delegate_to_chief(action)

        result_text = fmt("coo", "Setup dominio " + project_slug,
            self.format_human_actions(human_actions)
            + "\n\nDelegato a: CMO (landing), CTO (deploy)")

        if thread_id:
            self._send_to_topic(thread_id, result_text)

        return {
            "status": "ok",
            "project_slug": project_slug,
            "human_actions": human_actions,
            "agent_actions": agent_actions,
        }

    def format_human_actions(self, actions):
        """Lista numerata azioni per Mirco."""
        lines = ["AZIONI MIRCO:"]
        for i, a in enumerate(actions, 1):
            desc = a.get("description", a.get("action", "?"))
            lines.append(str(i) + ". " + desc)
        return "\n".join(lines)

    def monitor_and_report(self, actions, thread_id=None):
        """Check agent_events status per azioni delegate, report completamento."""
        completed = 0
        pending = 0
        for action in actions:
            chief = action.get("chief", "")
            try:
                r = supabase.table("agent_events").select("id,status") \
                    .eq("agent_from", "coo").eq("agent_to", chief) \
                    .eq("event_type", "task_delegation") \
                    .order("created_at", desc=True).limit(1).execute()
                if r.data and r.data[0].get("status") == "completed":
                    completed += 1
                else:
                    pending += 1
            except Exception:
                pending += 1

        text = fmt("coo", "Status deleghe",
                   "Completate: " + str(completed) + "\n"
                   "In attesa: " + str(pending))

        if thread_id:
            self._send_to_topic(thread_id, text)

        return {"completed": completed, "pending": pending}

    #
    # CONVERSATION STATE — routing intelligente per topic (v5.31)
    #

    def set_active_chief(self, topic_id, chief_id, project_slug="", context=""):
        """Imposta il Chief attivo per un topic. Upsert su conversation_state."""
        try:
            supabase.table("conversation_state").upsert({
                "topic_id": int(topic_id),
                "active_chief": chief_id,
                "last_message_at": now_rome().isoformat(),
                "project_slug": project_slug or "",
                "context": context or "",
            }).execute()
            logger.info("[COO] Active chief set: topic=%s chief=%s", topic_id, chief_id)
        except Exception as e:
            logger.warning("[COO] set_active_chief error: %s", e)

    def get_active_chief_for_topic(self, topic_id):
        """Restituisce il chief_id attivo per il topic, o None se scaduto/assente."""
        try:
            r = supabase.table("conversation_state").select("active_chief,last_message_at") \
                .eq("topic_id", int(topic_id)).execute()
            if not r.data:
                return None
            row = r.data[0]
            chief = row.get("active_chief")
            last_msg = row.get("last_message_at", "")
            if not chief or not last_msg:
                return None
            from dateutil.parser import parse as parse_dt
            last_dt = parse_dt(last_msg)
            now = now_rome()
            elapsed = (now - last_dt).total_seconds() / 60.0
            if elapsed > CONVERSATION_TIMEOUT_MINUTES:
                self.clear_active_chief(topic_id)
                logger.info("[COO] Conversation timeout: topic=%s chief=%s (%.0f min)", topic_id, chief, elapsed)
                return None
            return chief
        except Exception as e:
            logger.warning("[COO] get_active_chief_for_topic error: %s", e)
            return None

    def clear_active_chief(self, topic_id):
        """Rimuove il Chief attivo per il topic."""
        try:
            supabase.table("conversation_state").delete() \
                .eq("topic_id", int(topic_id)).execute()
            logger.info("[COO] Active chief cleared: topic=%s", topic_id)
        except Exception as e:
            logger.warning("[COO] clear_active_chief error: %s", e)

    def handle_interruption(self, topic_id, user_message, thread_id=None):
        """COO interviene se Mirco segnala confusione/intromissione."""
        lower = user_message.lower()
        triggered = any(t in lower for t in COO_INTERVENTION_TRIGGERS)
        if not triggered:
            return False

        active = self.get_active_chief_for_topic(topic_id)
        text = fmt("coo", "Ordine ristabilito",
                   "Ho capito il problema. D'ora in poi risponde solo "
                   + (active.upper() if active else "il Chief assegnato")
                   + " in questo topic.\n"
                   "Se vuoi cambiare, dimmelo.")
        target = thread_id or topic_id
        if target:
            self._send_to_topic(target, text)
        logger.info("[COO] Interruption handled: topic=%s trigger in message", topic_id)
        return True

    def complete_task_and_handoff(self, topic_id, from_chief, to_chief, task_summary="", thread_id=None):
        """Handoff esplicito: from_chief finisce, to_chief prende il controllo."""
        self.set_active_chief(topic_id, to_chief, context="handoff da " + from_chief)

        try:
            supabase.table("agent_events").insert({
                "agent_from": from_chief,
                "agent_to": to_chief,
                "event_type": "handoff",
                "payload": json.dumps({
                    "topic_id": int(topic_id),
                    "task_summary": task_summary or "",
                }),
                "status": "pending",
            }).execute()
        except Exception as e:
            logger.warning("[COO] handoff event insert error: %s", e)

        from csuite.utils import CHIEF_ICONS, CHIEF_NAMES
        to_icon = CHIEF_ICONS.get(to_chief, "")
        to_name = CHIEF_NAMES.get(to_chief, to_chief.upper())
        text = fmt("coo", "Passaggio consegne",
                   from_chief.upper() + " ha completato.\n"
                   "Ora prosegue " + to_icon + " " + to_name + ".")
        target = thread_id or topic_id
        if target:
            self._send_to_topic(target, text)
        logger.info("[COO] Handoff: %s -> %s topic=%s", from_chief, to_chief, topic_id)
        return {"from": from_chief, "to": to_chief, "topic_id": topic_id}
