# DIAGNOSTICA brAIn — 2026-02-28

## 1. FILE DEL PROGETTO

**Totale: 209 file, 46.816 righe di codice**

| Directory | File | Righe |
|-----------|------|-------|
| deploy-agents/ | 58 | 29.381 |
| deploy/ | 5 | 5.591 |
| agents/ (legacy) | 21 | 3.763 |
| supabase/migrations/ | 33 | 2.277 |
| utils/ | 2 | 257 |
| Root (config) | 13 | 1.594 |
| Root (test .py) | 48 | 3.502 |
| Root (test .txt) | 27 | 451 |

File piu grandi:
- agents_runner_monolith_v4.py: 8.243 righe (LEGACY, non deployato)
- command_center_unified.py: 5.518 righe
- coo.py: 2.047 righe
- base_chief.py: 1.525 righe
- cto.py: 1.360 righe
- endpoints.py: 1.313 righe
- cpeo.py: 1.166 righe
- execution/pipeline.py: 1.111 righe
- execution/builder.py: 1.023 righe
- cmo.py: 990 righe

Ultimo commit: 6c2f1eb (2026-02-28 12:46) "feat: v5.36 — Architecture Review"

---

## 2. AGENTI

### Infrastruttura comune
- BaseAgent (base_agent.py): call_claude con retry 3x + jitter, circuit breaker 60s, prompt caching, auto-log tokens/costi
- BaseChief (base_chief.py): build_system_prompt (CULTURA_BRAIN + profilo DB + knowledge + memoria episodica + dati live), answer_question con routing, model selection, task management
- cultura.py: CULTURA_BRAIN (regole condivise), CHIEF_PERSONALITY (7 personalita), get_chief_system_prompt()

### Mappa Chief

| Chief | File | Dominio | Temperature | answer_question override | Metodi principali |
|-------|------|---------|-------------|--------------------------|-------------------|
| COO | coo.py (2047 righe) | ops | 0.5 | SI (context + delegation + forbidden regen) | orchestrate, delegate_to_chief, domain_setup, rename_cantiere, daily_snapshot, accelerate, TODO list |
| CSO | cso.py (771 righe) | strategy | 0.7 | NO (build_system_prompt override) | plan_smoke_test, analyze_bos, auto_pipeline, find_real_prospects |
| CMO | cmo.py (990 righe) | marketing | 0.7 | SI (bozza keyword trigger) | design_landing_concept, generate_bozza_visiva, plan_paid_ads, publish_landing_brief |
| CTO | cto.py (1360 righe) | tech | 0.4 | SI (code task intercept) | build_landing_from_brief, phoenix_snapshot, security_report, trigger_code_job |
| CFO | cfo.py (266 righe) | finance | 0.3 | SI (auto pricing search) | get_costs_breakdown, check_anomalies |
| CLO | clo.py (467 righe) | legal | 0.3 | NO | legal_gate_check, generate_legal_documents, daily_legal_scan |
| CPeO | cpeo.py (1166 righe) | people | 0.6 | NO | create_training_plan, daily_gap_analysis, post_task_learning, track_version |

Tutti parlano in PRIMA PERSONA (mandato da CULTURA_BRAIN).
COO e l'unico con MY_REFUSE_DOMAINS = [] (non rifiuta nulla).

---

## 3. MESSAGGI TELEGRAM

### Punti SENZA identita Chief (PROBLEMI)

**Critici (messaggi utente senza prefisso Chief):**

1. command_center_unified.py:3000 — "Risposta in elaborazione, riprova tra poco" (timeout handler)
2. command_center_unified.py:4898 — "Risposta in elaborazione, riprova tra un momento" (timeout)
3. command_center_unified.py:4986 — "Risposta in elaborazione, riprova tra un momento" (timeout)
4. base_agent.py:59 — Circuit breaker alert: "[agent_name] Circuit breaker aperto..." (brackets, no icon)
5. base_chief.py:781 — Routing card: usa self.name ma non fmt()
6. base_chief.py:904 — Code approval card: "AZIONE CODICE" senza Chief
7. base_chief.py:931 — Sandbox BLOCKED: "Prompt bloccato" senza Chief
8. cto.py:908 — "File HTML landing {brand}" senza prefisso CTO
9. coo.py:386 — Project daily report senza COO header

**Moduli interni senza Chief identity (tutti usano notify_telegram raw):**
- execution/builder.py: 9 punti senza prefisso
- execution/validator.py: 6 punti
- execution/legal.py: 3 punti
- finance/reports.py: 2 punti
- finance/finance.py: 6 punti
- ethics_monitor.py: 2 punti
- memory/ (knowledge, scout, thresholds, sources): 5 punti
- intelligence/ (scanner, pipeline, feasibility): 5 punti
- marketing/agents.py: 2 punti
- csuite/__init__.py:130 — anomaly check senza fmt()
- endpoints.py:1165 — pending BOS actions

**Messaggi di sistema (accettabili, sono card infrastrutturali):**
- command_center: /start, /code, STOP, cantiere welcome, deploy, reports — tutti usano _make_card() senza Chief (corretto, sono comandi di sistema)

### Punti CON identita Chief (CORRETTI):
- CTO: tutti i send via fmt("cto", ...) tranne 2
- CMO: tutti via fmt("cmo", ...)
- CSO: quasi tutti via fmt("cso", ...)
- COO: quasi tutti via fmt("coo", ...)
- CLO: tutti via fmt("clo", ...)
- command_center routing (4989, 5003): usa _format_chief_response() — CORRETTO

---

## 4. DATABASE

### Schema Supabase: 67 tabelle

| Tabella | Righe | Note |
|---------|-------|------|
| scan_sources | 365 | Fonti world scanner |
| topic_conversation_history | 492 | L1 Working Memory |
| agent_events | 230 | Inter-agent events |
| agent_capabilities | 195 | Competenze 7 Chief |
| chief_knowledge | 139 | Profili + knowledge |
| problems_archive | 94 | Problemi archiviati |
| org_shared_knowledge | 78 | Knowledge condivisa |
| org_knowledge | 73 | Lezioni apprese |
| problems | 69 | Problemi attivi |
| agent_logs | 860 | Log API calls |
| org_config | 27 | Config chiave-valore |
| chief_decisions | 25 | Decisioni Chief |
| solutions_archive | 24 | Soluzioni archiviate |
| solution_scores_archive | 24 | Score archiviati |
| code_tasks | 22 | Task codice CTO |
| gap_analysis_log | 22 | Gap analysis CPeO |
| bos_archive | 21 | BOS archiviati |
| migration_history | 21 | Migration applicate |
| authorization_matrix | 18 | Matrice autorizz. |
| solutions | 16 | Soluzioni attive |
| episodic_memory | 15 | L2 Episodic Memory |
| training_plans | 15 | Piani formazione |
| solution_scores | 15 | Score soluzioni |
| scan_schedule | 12 | Schedule scanner |
| smoke_test_prospects | 10 | Prospect smoke |
| action_queue | 8 | Coda azioni |
| project_tasks | 6 | Task progetto |
| finance_metrics | 6 | Metriche finanziarie |
| capability_log | 30 | Tool scoperti |
| scan_logs | 3 | Log scan |
| pipeline_thresholds | 3 | Soglie pipeline |
| brain_snapshots | 2 | Snapshot giornalieri |
| topic_context_summary | 2 | Riassunti topic |
| projects | 1 | Progetti attivi |
| users | 1 | Utenti (Mirco) |
| exchange_rates | 1 | Tassi cambio |
| kpi_daily | 1 | KPI giornalieri |
| active_session | 1 | Sessione attiva |
| brain_config | 1 | Config brain |
| conversation_state | 1 | Stato conversazione |
| coo_project_state | 1 | Stato COO |
| source_thresholds | 1 | Soglie fonti |

**21 tabelle con 0 righe** (mai usate o svuotate): agent_episodic_memory, agent_semantic_memory, agent_working_memory, brain_versions, brand_assets, chief_memory, chief_pending_tasks, coo_pending_actions, coo_project_tasks, cost_tracking, cto_architecture_*, cto_security_reports, ethics_violations, experiments, feedback, improvement_log, legal_reviews, manager_revenue_share, marketing_reports, project_assets, project_members, project_metrics, project_reports, project_users, reevaluation_log, smoke_tests, smoke_test_events, training_materials.

### MISMATCH CRITICI (colonne nel codice che NON esistono nella tabella)

**CRITICAL:**

1. agent_events: codice usa `agent_from`/`agent_to` ma le colonne reali sono `source_agent`/`target_agent` — 9 file affetti (base_chief, clo, cmo, coo, cso, cto). Tutti gli eventi inter-agent perdono source/target.

2. agent_events: 14 colonne extra inserite come top-level ma non esistono (action, assigned_to, brand, brief, description, etc.) — dovrebbero essere dentro `payload` JSONB.

3. projects: `legal_status` non esiste — CLO non puo tracciare approvazione legale.

4. projects: `updated_at` non esiste — COO daily report e snapshot filtrano su colonna inesistente, query vuote.

5. solutions: `brand_brief` non esiste — CMO non carica mai brand briefs.

**HIGH:**

6. agent_performance: schema completamente sbagliato nel codice vs DB (8 colonne diverse).
7. solutions: 10 colonne extra nell'insert dell'Architect (biggest_risk, competitive_moat, etc.).
8. solution_scores: 13 colonne extra nell'insert dell'Architect.
9. code_tasks: `checked_at`, `source`, `task_description` non esistono.
10. kpi_daily: `project_id`, `metric_name`, `value` non esistono — COO riceve dati vuoti.
11. finance_metrics: `metric_name`, `value` non esistono — CFO riceve dati vuoti.
12. capability_log: `name` non esiste (si chiama `tool_name`).

**MEDIUM:**

13. legal_reviews: `created_at` non esiste (e `reviewed_at`).
14. marketing_reports: `channel` non esiste (e `channel_breakdown`).
15. smoke_test_events: `project_id` non esiste (e `smoke_test_id`).
16. smoke_test_prospects: `created_at` non esiste (e `sent_at`/`updated_at`).
17. training_plans: `length`, `type` non esistono.
18. solutions: `bos_approved` non esiste.
19. action_queue: 5 colonne extra (action, problem, time_estimate, etc.).
20. reevaluation_log: `new_data`/`problem_id` vs `what_changed`/`item_id`.

---

## 5. CHIAMATE API

### Claude API: 78+ siti di chiamata

| Pattern | Siti | Retry | Note |
|---------|------|-------|------|
| call_claude() (BaseAgent wrapper) | 23 | SI (3x + jitter) | Auto-log tokens/costi |
| claude.messages.create() diretto | 48 | NO | Nessun retry, nessun log automatico |
| command_center_unified.py | 7 | NO | Chat loop + Code Agent |

**Modelli usati:**
- claude-haiku-4-5-20251001: memoria, routing, sandbox, scoring, riassunti, task minori
- claude-sonnet-4-6: answer_question (CSO/CMO/CPeO/CTO sempre; CFO/CLO/COO per complessi), landing HTML, SPEC, soluzioni, brand, ads
- claude-opus-4-6: definito nel pricing ma MAI usato

**Gestione errori:**
- base_chief.answer_question (line 566): OTTIMA — messaggio italiano human-friendly + ticket CTO
- 48 chiamate dirette senza retry — single-attempt, se fallisce perso
- 3 siti espongono str(e) raw: execution/legal.py, execution/builder.py, command_center:4765

### Perplexity API: 17+ callers

- core/utils.py search_perplexity(): rate limit 15/giorno, logging costi
- csuite/utils.py web_search(): rate limit 15/giorno, logging costi
- Monolith (legacy): NESSUN rate limit
- Modello: sonar per tutti

---

## 6. GENERAZIONE IMMAGINI

### Provider chain (3 fallback):
1. OpenAI DALL-E 3 (api.openai.com) — se OPENAI_API_KEY presente
2. Cloudflare FLUX.1-schnell — se CLOUDFLARE_* presenti
3. Replicate FLUX-schnell — se REPLICATE_API_TOKEN presente

File: deploy-agents/utils/image_generation.py (197 righe)
Usato da: CMO (design_landing_concept, line 449/464)
Fallback: Pillow mockup se nessun provider disponibile

### Screenshot HTML:
File: deploy-agents/utils/html_screenshot.py (110 righe)
Tech: Playwright headless Chromium (fallback Pillow)
Usato da: CTO (build_landing_from_brief, line 852)

### Pillow diretto:
File: csuite/cmo.py (generate_bozza_visiva, line 588-619)
Crea PNG 1200x675 con gradient + brand + tagline + 3 card

---

## 7. FRASI VIETATE TROVATE

### BAD (attivamente output a Mirco):

| File | Riga | Frase | Contesto |
|------|------|-------|----------|
| command_center_unified.py | 3000 | "Risposta in elaborazione, riprova tra poco" | asyncio.TimeoutError handler |
| command_center_unified.py | 4898 | "Risposta in elaborazione, riprova tra un momento" | timeout handler |
| command_center_unified.py | 4986 | "Risposta in elaborazione, riprova tra un momento" | timeout handler |

### GAP nell'enforcement programmatico:
- "riprova tra poco": in cultura.py come vietata ma NON in _TASK_FORBIDDEN_PHRASES (base_chief.py). Se un Chief la genera, il check code-level non la cattura.
- "monitoro tutto e ti avviso": in cultura.py come vietata ma NON in _TASK_FORBIDDEN_PHRASES. Stesso gap.
- "riprova tra un momento": variante usata dal command_center ma in NESSUNA lista vietata.

### GOOD (correttamente elencate come vietate):
- cultura.py: 13 frasi vietate nel prompt
- base_chief.py _TASK_FORBIDDEN_PHRASES: 13 frasi con check programmatico

### "IN CORSO" come stato task:
- coo.py:360,1504 — usato come label display nel daily report e TODO list. Accettabile (e un label, non uno stato di risposta).

### "brAIn:" come prefisso:
- Nessuna istanza usata come sender. Solo in contesti descrittivi (legal_agent.py, cto.py). OK.

---

## 8. DEPLOY

### Stato attuale:
| Servizio | Revisione | Deployato | Stato |
|----------|-----------|-----------|-------|
| agents-runner | 00115-2ls | 2026-02-28 11:51 UTC | ATTIVO |
| command-center | 00088-22n | 2026-02-28 11:52 UTC | ATTIVO |

### Dockerfile:
- Root Dockerfile (command-center): python:3.11-slim, copia deploy-agents/core, csuite, intelligence, utils
- deploy-agents/Dockerfile (agents-runner): python:3.11-slim, Node.js 22, Claude Code CLI, Playwright Chromium, utente non-root brainuser
- deploy/Dockerfile: LEGACY, NON USARE (non copia csuite, Chief non rispondono)

### CI/CD: NESSUNO
- No cloudbuild.yaml, no GitHub Actions, no Makefile
- Deploy completamente manuale via gcloud CLI

### Dependencies:
- agents-runner: 10 packages (anthropic, supabase, aiohttp, requests, psycopg2-binary, Pillow, google-api, playwright, python-dotenv)
- command-center: 7 packages (python-telegram-bot, anthropic, supabase, aiohttp, requests, openai, python-dotenv)

---

## 9. DATI COPERTI.AI

### Stato progetto:
- ID: 5, slug: agente-ai-prenotazio (mai aggiornato)
- Status: active, pipeline_step: smoke_test_designing
- BOS score: 0.84, build_phase: 0, lines_of_code: 0
- Brand: Coperti.ai, email: info@coperti.ai
- Domain: agente-ai-prenotazio.com (SBAGLIATO, dovrebbe essere coperti.ai)
- Topic: 91, thread_id: 91
- Conversazione: 154 messaggi (77 user + 77 bot)

### Task attivi (project_tasks):
| # | Titolo | Assegnato | Status | Priorita |
|---|--------|-----------|--------|----------|
| 6 | Trovare 50+ prospect ristoranti | CSO | in_progress | P1 |
| 7 | Brand identity completa | CMO | pending | P1 |
| 8 | Landing page su dominio | Mirco | pending | P1 |
| 9 | Sequenza email cold outreach | CSO | pending | P2 |
| 10 | Approvazione Mirco pre-lancio | Mirco | pending | P3 |
| 11 | Coordinamento COO | COO | in_progress | P3 |

Tutti creati alle 03:20, nessuno aggiornato.

### Code tasks bloccati:
- 6 in pending_approval, 1 blocked, 1 error
- Include: landing page HTML, DNS polling, rename cantiere, legal docs

### INCONSISTENZE TROVATE:

1. brand_domain dice "agente-ai-prenotazio.com" invece di "coperti.ai"
2. Slug mai aggiornato da "agente-ai-prenotazio" a "coperti-ai" (task #18 bloccato in pending_approval)
3. 3 sistemi di task tracking (project_tasks, chief_pending_tasks, coo_project_tasks) ma solo project_tasks ha dati — gli altri 2 sono vuoti
4. coo_project_state vuoto: blocking_chief, blocking_reason, parallel_tasks, next_action tutti NULL
5. Nessun task aggiornato in 10+ ore nonostante attivita conversazione
6. project_assets vuoto nonostante CMO abbia tentato piu volte di creare landing brief
7. 6 code_tasks bloccati in pending_approval, nessuno li processa
8. topic_context_summary conta 12 messaggi ma ce ne sono 154
9. Pipeline in smoke_test_designing (territory CSO) ma conversation_state mostra active_chief: CMO
10. Zero build artifacts: build_phase=0, lines_of_code=0, files_count=0
11. Solution ancora "proposed" nonostante progetto "active" con cantiere aperto
12. Smoke test non iniziato, nessun URL, nessun risultato

---

## RIEPILOGO PROBLEMI CRITICI

### P0 — Da fixare subito:

1. **agent_events schema mismatch**: `agent_from`/`agent_to` vs `source_agent`/`target_agent` — TUTTI gli eventi inter-agent perdono source/target (9 file)
2. **command_center timeout handler**: 3 punti output "Risposta in elaborazione" (frase vietata) a Mirco
3. **projects.legal_status non esiste**: CLO non puo tracciare stato legale
4. **projects.updated_at non esiste**: COO daily report e snapshot producono risultati vuoti

### P1 — Questa settimana:

5. **48 chiamate Claude senza retry**: single-attempt, nessun log automatico
6. **agent_performance schema sbagliato**: post_task_learning non salva nulla
7. **kpi_daily/finance_metrics colonne sbagliate**: COO e CFO ricevono dati vuoti
8. **Coperti.ai slug/domain mai aggiornati**
9. **6 code_tasks bloccati** senza nessuno che li processa
10. **3 frasi vietate mancanti** da _TASK_FORBIDDEN_PHRASES

### P2 — Questo mese:

11. **21 tabelle vuote** (feature deployate ma mai usate)
12. **CI/CD inesistente** — deploy manuale
13. **solutions/solution_scores** perdono 23 colonne di dettaglio dall'Architect
14. **Execution module** invia tutti i messaggi Telegram senza Chief identity
