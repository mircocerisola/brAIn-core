"""CTO — Chief Technology Officer. Dominio: infrastruttura, codice, deploy, sicurezza tecnica.
v5.11: execute_in_cloud, build_technical_prompt, pattern detection "manda questo prompt:".
"""
import json
import re
import os
import requests as _requests
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from core.base_chief import BaseChief
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, logger

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "mircocerisola/brAIn-core"
GCP_PROJECT = "brain-core-487914"
GCP_REGION = "europe-west3"

# Pattern per "manda questo prompt:", "esegui questo:", "prompt:", "lancia:"
_PROMPT_PATTERN = re.compile(
    r'(?:manda questo prompt|esegui questo|incolla in code|prompt|lancia)\s*[:]\s*(.+)',
    re.IGNORECASE | re.DOTALL,
)

# Azioni di routine che il CTO esegue direttamente senza card approvazione
_ROUTINE_KEYWORDS = [
    "fix bug", "fix errore", "correggi", "aggiorna import", "rimuovi log",
    "aggiorna commento", "rinomina", "aggiorna version",
]


class CTO(BaseChief):
    name = "CTO"
    chief_id = "cto"
    domain = "tech"
    default_model = "claude-sonnet-4-6"
    briefing_prompt_template = (
        "Sei il CTO di brAIn. Genera un briefing tecnico settimanale includendo: "
        "1) Salute dei servizi Cloud Run (uptime, errori), "
        "2) Nuove capability tecnologiche scoperte da Capability Scout, "
        "3) Debito tecnico identificato, "
        "4) Aggiornamenti modelli AI disponibili, "
        "5) Raccomandazioni architetturali."
    )

    def get_domain_context(self):
        ctx = super().get_domain_context()
        try:
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            r = supabase.table("agent_logs").select("agent_id,status,error") \
                .eq("status", "error").gte("created_at", week_ago).execute()
            errors = {}
            for row in (r.data or []):
                agent = row.get("agent_id", "unknown")
                errors[agent] = errors.get(agent, 0) + 1
            ctx["weekly_errors_by_agent"] = sorted(errors.items(), key=lambda x: x[1], reverse=True)[:5]
        except Exception as e:
            ctx["weekly_errors_by_agent"] = f"errore lettura DB: {e}"
        try:
            r = supabase.table("capability_log").select("name,description,created_at") \
                .order("created_at", desc=True).limit(5).execute()
            ctx["recent_capabilities"] = r.data or []
        except Exception as e:
            ctx["recent_capabilities"] = f"errore lettura DB: {e}"
        # Code tasks recenti
        try:
            r = supabase.table("code_tasks").select(
                "id,title,status,requested_by,created_at"
            ).order("created_at", desc=True).limit(10).execute()
            ctx["recent_code_tasks"] = r.data or []
        except Exception as e:
            ctx["recent_code_tasks"] = f"errore lettura DB: {e}"
        return ctx

    def check_anomalies(self):
        anomalies = []
        try:
            hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            r = supabase.table("agent_logs").select("id,status").eq("status", "error") \
                .gte("created_at", hour_ago).execute()
            error_count = len(r.data or [])
            if error_count > 10:
                anomalies.append({
                    "type": "high_error_rate",
                    "description": f"{error_count} errori nell'ultima ora",
                    "severity": "critical" if error_count > 20 else "high",
                })
        except Exception:
            pass
        return anomalies

    # ============================================================
    # FIX 2 — PATTERN DETECTION: "manda questo prompt:"
    # ============================================================

    def answer_question(self, question, user_context=None,
                        project_context=None, topic_scope_id=None,
                        project_scope_id=None, recent_messages=None):
        """Override: intercetta pattern 'manda questo prompt:' PRIMA di qualsiasi logica."""
        match = _PROMPT_PATTERN.search(question)
        if match:
            raw_prompt = match.group(1).strip()
            logger.info("[CTO] Pattern 'manda prompt' rilevato (%d chars)", len(raw_prompt))
            result = self.execute_in_cloud(raw_prompt)
            if result.get("status") == "ok":
                files_str = ", ".join(result.get("files_changed", [])[:5]) or "nessuno"
                return (
                    "\u2705 Prompt eseguito.\n"
                    "File modificati: " + files_str + "\n"
                    "Commit: " + (result.get("commit_sha", "N/A")[:8]) + "\n"
                    + (("Build: " + result.get("build_status", "N/A")) if result.get("build_status") else "")
                )
            else:
                return "\u274c Errore esecuzione: " + result.get("error", "sconosciuto")

        # Nessun pattern → risposta Chief normale
        return super().answer_question(
            question, user_context=user_context,
            project_context=project_context,
            topic_scope_id=topic_scope_id,
            project_scope_id=project_scope_id,
            recent_messages=recent_messages,
        )

    # ============================================================
    # FIX 3 — GENERA PROMPT TECNICI PER ALTRI CHIEF
    # ============================================================

    def build_technical_prompt(self, task_description: str, context: str = "") -> str:
        """Trasforma una richiesta funzionale in prompt tecnico completo per Claude Code."""
        system = (
            "Sei un tech lead senior di brAIn, un organismo AI-native.\n"
            "Trasforma questa richiesta funzionale in un prompt tecnico completo per Claude Code.\n"
            "Il prompt deve:\n"
            "- Essere preciso, completo, con path file esatti\n"
            "- Specificare COSA modificare e COME\n"
            "- Includere esempi di codice dove necessario\n"
            "- Indicare i file coinvolti\n"
            "Stack: Python, Supabase, Cloud Run, Telegram Bot API.\n"
            "Repo: deploy-agents/ (agents-runner), deploy/ (command-center).\n"
            "Rispondi SOLO con il prompt tecnico, nient'altro."
        )
        prompt = f"Richiesta: {task_description}"
        if context:
            prompt += f"\n\nContesto: {context}"

        try:
            technical = self.call_claude(prompt, system=system, max_tokens=3000, model="claude-sonnet-4-6")
            return technical
        except Exception as e:
            logger.error(f"[CTO] build_technical_prompt error: {e}")
            return task_description

    def generate_and_execute_prompt(self, task_description: str, context: str = "",
                                     requires_approval: bool = True) -> Dict[str, Any]:
        """
        1. Genera prompt tecnico completo
        2. Se requires_approval → manda card a Mirco
        3. Se !requires_approval (routine) → esegui direttamente in cloud
        """
        technical_prompt = self.build_technical_prompt(task_description, context)

        # Valuta se routine
        is_routine = any(kw in task_description.lower() for kw in _ROUTINE_KEYWORDS)
        if is_routine:
            requires_approval = False

        if requires_approval:
            # Salva in code_tasks e manda card approvazione
            result = self.validate_prompt_sandbox(
                prompt_text=technical_prompt,
                task_title=task_description[:100],
                triggered_by_message=task_description[:500],
                code_action_meta={
                    "title": task_description[:100],
                    "description": task_description[:300],
                    "files": [],
                    "time_estimate": "da valutare",
                    "prompt": technical_prompt,
                },
            )
            return {
                "status": "pending_approval",
                "task_id": result.get("task_id"),
                "prompt_preview": technical_prompt[:200],
            }
        else:
            return self.execute_in_cloud(technical_prompt)

    # ============================================================
    # FIX 4 — ESECUZIONE CLAUDE CODE IN CLOUD
    # ============================================================

    def execute_in_cloud(self, prompt: str) -> Dict[str, Any]:
        """
        Esegui codice in cloud:
        1. Claude Sonnet genera i file modificati
        2. Committa su GitHub via REST API
        3. Opzionalmente triggera Cloud Build + Deploy
        """
        logger.info("[CTO] execute_in_cloud: %s", prompt[:80])

        # 1. Genera codice con Claude Sonnet
        gen_system = (
            "Sei un programmatore senior. Genera SOLO le modifiche ai file richieste.\n"
            "Per ogni file, usa questo formato ESATTO:\n"
            "<<FILE:path/relativo/al/file.py>>\n"
            "contenuto completo del file\n"
            "<<END_FILE>>\n\n"
            "<<COMMIT_MESSAGE>>\n"
            "messaggio di commit descrittivo\n"
            "<<END_COMMIT_MESSAGE>>\n\n"
            "IMPORTANTE:\n"
            "- Path relativi alla root del repo (es. deploy-agents/csuite/cto.py)\n"
            "- Contenuto COMPLETO del file, non solo le diff\n"
            "- Se devi leggere un file prima, menziona <<READ:path>> e ti daro' il contenuto\n"
            "- Zero spiegazioni fuori dai tag. Solo codice."
        )

        try:
            raw_response = self.call_claude(
                prompt, system=gen_system, max_tokens=8096, model="claude-sonnet-4-6",
            )
        except Exception as e:
            logger.error(f"[CTO] execute_in_cloud claude error: {e}")
            return {"status": "error", "error": f"Claude API error: {e}"}

        # 2. Parsa file changes
        files_changed = self._parse_file_changes(raw_response)
        commit_msg = self._parse_commit_message(raw_response) or "chore: auto-update via CTO"

        if not files_changed:
            logger.warning("[CTO] execute_in_cloud: nessun file trovato nella risposta")
            return {
                "status": "ok",
                "files_changed": [],
                "commit_sha": "N/A",
                "raw_response": raw_response[:500],
            }

        # 3. Committa su GitHub
        try:
            commit_sha = self._github_commit_files(files_changed, commit_msg)
        except Exception as e:
            logger.error(f"[CTO] github commit error: {e}")
            return {"status": "error", "error": f"GitHub commit error: {e}",
                    "files_changed": list(files_changed.keys())}

        # 4. Triggera Cloud Build (best-effort)
        build_status = "skipped"
        try:
            # Determina quale servizio rebuilda
            services_to_build = set()
            for fpath in files_changed:
                if fpath.startswith("deploy-agents/") or fpath.startswith("deploy-agents\\"):
                    services_to_build.add("agents-runner")
                elif fpath.startswith("deploy/") or fpath.startswith("deploy\\"):
                    services_to_build.add("command-center")

            if services_to_build:
                build_results = []
                for svc in services_to_build:
                    b = self._trigger_cloud_build(svc)
                    build_results.append(f"{svc}={b}")
                build_status = ", ".join(build_results)
        except Exception as e:
            build_status = f"error: {e}"
            logger.warning(f"[CTO] cloud build error: {e}")

        # Log
        try:
            supabase.table("agent_logs").insert({
                "agent_id": "cto",
                "action": "execute_in_cloud",
                "status": "ok",
                "details": json.dumps({
                    "files": list(files_changed.keys()),
                    "commit": commit_sha[:8] if commit_sha else "N/A",
                    "build": build_status,
                }),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception:
            pass

        return {
            "status": "ok",
            "files_changed": list(files_changed.keys()),
            "commit_sha": commit_sha or "N/A",
            "build_status": build_status,
        }

    # ── GitHub helpers ──

    def _parse_file_changes(self, text: str) -> Dict[str, str]:
        """Parsa <<FILE:path>>content<<END_FILE>> dal testo."""
        files = {}
        pattern = re.compile(r'<<FILE:(.+?)>>\n(.*?)<<END_FILE>>', re.DOTALL)
        for match in pattern.finditer(text):
            fpath = match.group(1).strip()
            content = match.group(2)
            # Rimuovi trailing newline
            if content.endswith("\n"):
                content = content[:-1]
            files[fpath] = content
        return files

    def _parse_commit_message(self, text: str) -> str:
        """Parsa <<COMMIT_MESSAGE>>msg<<END_COMMIT_MESSAGE>> dal testo."""
        m = re.search(r'<<COMMIT_MESSAGE>>\n?(.*?)<<END_COMMIT_MESSAGE>>', text, re.DOTALL)
        return m.group(1).strip() if m else ""

    def _github_commit_files(self, files: Dict[str, str], message: str) -> str:
        """Committa multipli file su GitHub via REST API (tree API)."""
        if not GITHUB_TOKEN:
            raise RuntimeError("GITHUB_TOKEN non disponibile")

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        base = f"https://api.github.com/repos/{GITHUB_REPO}"

        # 1. Get HEAD ref
        ref_resp = _requests.get(f"{base}/git/ref/heads/main", headers=headers, timeout=15)
        ref_resp.raise_for_status()
        head_sha = ref_resp.json()["object"]["sha"]

        # 2. Get current commit tree
        commit_resp = _requests.get(f"{base}/git/commits/{head_sha}", headers=headers, timeout=15)
        commit_resp.raise_for_status()
        base_tree_sha = commit_resp.json()["tree"]["sha"]

        # 3. Create blobs for each file
        tree_items = []
        for fpath, content in files.items():
            blob_resp = _requests.post(
                f"{base}/git/blobs", headers=headers, timeout=30,
                json={"content": content, "encoding": "utf-8"},
            )
            blob_resp.raise_for_status()
            blob_sha = blob_resp.json()["sha"]
            tree_items.append({
                "path": fpath,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            })

        # 4. Create new tree
        tree_resp = _requests.post(
            f"{base}/git/trees", headers=headers, timeout=30,
            json={"base_tree": base_tree_sha, "tree": tree_items},
        )
        tree_resp.raise_for_status()
        new_tree_sha = tree_resp.json()["sha"]

        # 5. Create commit
        commit_create = _requests.post(
            f"{base}/git/commits", headers=headers, timeout=30,
            json={
                "message": message + "\n\nCo-Authored-By: CTO Agent <noreply@brain.ai>",
                "tree": new_tree_sha,
                "parents": [head_sha],
            },
        )
        commit_create.raise_for_status()
        new_commit_sha = commit_create.json()["sha"]

        # 6. Update ref
        _requests.patch(
            f"{base}/git/refs/heads/main", headers=headers, timeout=15,
            json={"sha": new_commit_sha},
        )

        logger.info(f"[CTO] GitHub commit {new_commit_sha[:8]}: {message[:60]}")
        return new_commit_sha

    def _trigger_cloud_build(self, service_name: str) -> str:
        """Triggera Cloud Build via REST API usando il service account del container."""
        try:
            # Get access token dal metadata server (solo su Cloud Run)
            token_resp = _requests.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
                timeout=5,
            )
            if token_resp.status_code != 200:
                return "skip_local"
            access_token = token_resp.json()["access_token"]
        except Exception:
            return "skip_local"

        # Configura build
        if service_name == "agents-runner":
            source_dir = "deploy-agents"
            image = f"{GCP_REGION}-docker.pkg.dev/{GCP_PROJECT}/brain-repo/agents-runner:latest"
        else:
            source_dir = "."
            image = f"{GCP_REGION}-docker.pkg.dev/{GCP_PROJECT}/brain-repo/command-center:latest"

        # Usa Cloud Build API per buildare dal repo GitHub
        build_config = {
            "source": {
                "repoSource": {
                    "projectId": GCP_PROJECT,
                    "repoName": "github_mircocerisola_brain-core",
                    "branchName": "main",
                    "dir_": source_dir if service_name == "agents-runner" else "",
                }
            },
            "steps": [{
                "name": "gcr.io/cloud-builders/docker",
                "args": ["build", "-t", image, "."],
            }],
            "images": [image],
        }

        try:
            build_resp = _requests.post(
                f"https://cloudbuild.googleapis.com/v1/projects/{GCP_PROJECT}/locations/{GCP_REGION}/builds",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"build": build_config},
                timeout=30,
            )
            if build_resp.status_code in (200, 201):
                build_id = build_resp.json().get("metadata", {}).get("build", {}).get("id", "unknown")
                logger.info(f"[CTO] Cloud Build triggered: {service_name} build_id={build_id}")
                # Deploy Cloud Run
                self._deploy_cloud_run(service_name, image, access_token)
                return f"triggered:{build_id[:8]}"
            else:
                logger.warning(f"[CTO] Cloud Build error {build_resp.status_code}: {build_resp.text[:200]}")
                return f"error:{build_resp.status_code}"
        except Exception as e:
            logger.warning(f"[CTO] Cloud Build request error: {e}")
            return f"error:{e}"

    def _deploy_cloud_run(self, service_name: str, image: str, access_token: str) -> None:
        """Deploy su Cloud Run via REST API."""
        try:
            # Get current service
            svc_url = (
                f"https://run.googleapis.com/v2/projects/{GCP_PROJECT}"
                f"/locations/{GCP_REGION}/services/{service_name}"
            )
            svc_resp = _requests.get(
                svc_url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            if svc_resp.status_code != 200:
                logger.warning(f"[CTO] Cloud Run get service error: {svc_resp.status_code}")
                return

            svc_data = svc_resp.json()
            # Update image in template
            containers = svc_data.get("template", {}).get("containers", [{}])
            if containers:
                containers[0]["image"] = image

            # Patch service
            _requests.patch(
                svc_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=svc_data,
                timeout=30,
            )
            logger.info(f"[CTO] Cloud Run deploy triggered: {service_name}")
        except Exception as e:
            logger.warning(f"[CTO] Cloud Run deploy error: {e}")
