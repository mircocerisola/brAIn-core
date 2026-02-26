# CODEBASE AUDIT — brAIn
Data: 2026-02-26
Versione sistema: 3.0
Revisione: agenti-runner 00036-x52 / command-center 00037-pc6

---

## 1. STRUTTURA FILE

### File di produzione (Cloud Run)

| File | Dimensione | Ultima modifica | Note |
|------|-----------|-----------------|------|
| `deploy-agents/agents_runner.py` | 335 KB (~8200 righe) | 2026-02-26 09:11 | **Servizio agents-runner** — tutti gli agenti inlined |
| `deploy/command_center_unified.py` | 157 KB (~3600 righe) | 2026-02-26 09:03 | **Servizio command-center** — bot Telegram + webhook |
| `deploy/command_center_cloud.py` | 26 KB | 2026-02-25 22:15 | Versione cloud precedente (non deployata) |

### Agenti standalone (solo CLI locale, non deployati)

| File | Dimensione | Ultima modifica |
|------|-----------|-----------------|
| `agents/world_scanner.py` | 24 KB | 2026-02-25 22:15 |
| `agents/solution_architect.py` | 16 KB | 2026-02-25 22:15 |
| `agents/feasibility_engine.py` | 18 KB | 2026-02-25 22:15 |
| `agents/finance_agent.py` | 12 KB | 2026-02-24 00:45 |
| `agents/knowledge_keeper.py` | 5 KB | 2026-02-25 22:15 |
| `agents/capability_scout.py` | 7 KB | 2026-02-25 22:15 |
| `agents/command_center.py` | 16 KB | 2026-02-25 22:15 |
| `agents/spec_generator.py` | 12 KB | 2026-02-25 22:51 |
| `agents/validation_agent.py` | 8 KB | 2026-02-25 22:52 |
| `agents/landing_page_generator.py` | 7 KB | 2026-02-25 23:25 |
| `agents/legal_agent.py` | 7 KB | 2026-02-26 08:33 |
| `agents/smoke_test_agent.py` | 8 KB | 2026-02-26 08:33 |
| `agents/marketing_coordinator.py` | 3 KB | 2026-02-26 09:03 |
| `agents/marketing/brand_agent.py` | 1.6 KB | 2026-02-26 09:03 |
| `agents/marketing/product_marketing_agent.py` | 1.4 KB | 2026-02-26 09:03 |
| `agents/marketing/content_agent.py` | 1.7 KB | 2026-02-26 09:03 |
| `agents/marketing/demand_gen_agent.py` | 1.4 KB | 2026-02-26 09:03 |
| `agents/marketing/social_agent.py` | 1.4 KB | 2026-02-26 09:03 |
| `agents/marketing/pr_agent.py` | 1.4 KB | 2026-02-26 09:04 |
| `agents/marketing/customer_marketing_agent.py` | 1.5 KB | 2026-02-26 09:04 |
| `agents/marketing/marketing_ops_agent.py` | 1.4 KB | 2026-02-26 09:04 |

### Utility

| File | Dimensione | Ultima modifica |
|------|-----------|-----------------|
| `utils/migrate.py` | 5 KB | 2026-02-26 07:31 |

### Migrations SQL

| File | Applicata |
|------|-----------|
| `20260225_create_action_queue.sql` | Si |
| `20260225_create_pipeline_thresholds.sql` | Si |
| `20260225_source_ids_and_thresholds.sql` | Si |
| `20260225_specificity_fields.sql` | Si |
| `20260225_archive_and_status_detail.sql` | Si |
| `20260226_projects.sql` | Si |
| `20260226_project_members.sql` | Si |
| `20260226_legal_smoke_spec.sql` | Si |
| `20260226_kpi_daily.sql` | Si |
| `20260226_marketing.sql` | Si |

---

## 2. AGENTI ESISTENTI

### CORTEX (Sistema Nervoso)

#### command_center_unified.py — Command Center
- **Trigger**: webhook Telegram POST /webhook
- **Funzioni principali**:
  - `ask_claude()` — chat con Mirco (Haiku per chat, Sonnet per code/analisi)
  - `handle_message()` — routing messaggi: report/marketing/code/azioni/cantiere
  - `handle_callback_query()` — gestione tutti i pulsanti inline keyboard
  - `handle_project_message()` — messaggi nel topic Forum di un cantiere
  - `_make_card()` — formattazione card standard con separatori ━━━
  - `_call_agents_runner_sync()` — chiamata OIDC a agents-runner
  - `build_system_prompt()` — contesto dinamico per Claude (problemi, soluzioni, org_config)
- **Dipendenze**: Supabase, Anthropic API (Haiku+Sonnet), GitHub API, agents-runner (OIDC)

### SENSES (Sistema Sensoriale)

#### World Scanner (`run_world_scanner()`)
- **Trigger**: `/cycle/scan` ogni 2h da Cloud Scheduler
- **Funzioni principali**:
  - `run_scan()` — scansiona 289 fonti via Perplexity Sonar
  - `scanner_calculate_weighted_score()` — 7 parametri pesati
  - `normalize_batch_scores()` — normalizzazione aggressiva anti-clustering
  - `run_auto_pipeline()` — attiva pipeline automatica SA→FE→BOS se score >= soglia
  - `scanner_make_fingerprint()` — deduplicazione problemi
- **Strategia dinamica**: `get_scan_strategy()` usa `scan_schedule` table per rotazione settori
- **Dipendenze**: Perplexity API (Sonar), Supabase (scan_sources, problems)

#### Capability Scout (`run_capability_scout()`)
- **Trigger**: `/cycle/capability` ogni giorno 06:00
- **Funzioni**: ricerca nuovi tool/modelli AI via Perplexity, salva in `capability_log`
- **Dipendenze**: Perplexity API, Supabase

### THINKING (Sistema Cognitivo)

#### Solution Architect (`run_solution_architect()`)
- **Trigger**: pipeline automatica da World Scanner (problema approvato o score >= soglia)
- **Funzioni principali**:
  - `research_problem()` — ricerca approfondita problema
  - `generate_solutions_unconstrained()` — genera 3-5 soluzioni (Sonnet)
  - `assess_feasibility()` — valutazione rapida fattibilità (Haiku)
  - `save_solution_v2()` — salva soluzioni con score in DB
- **Modelli**: Sonnet per generazione, Haiku per valutazione
- **Dipendenze**: Perplexity API, Supabase (problems, solutions, solution_scores)

#### Feasibility Engine (`run_feasibility_engine()`)
- **Trigger**: pipeline automatica dopo Solution Architect
- **Funzioni principali**:
  - `feasibility_calculate_score()` — calcola score tecnico+economico
  - `calculate_bos()` — calcola Business Opportunity Score
  - `enqueue_bos_action()` — inserisce BOS in action_queue per approvazione Mirco
- **Dipendenze**: Supabase (solutions, solution_scores, action_queue)

#### Pipeline Auto (`run_auto_pipeline()`)
- **Trigger**: chiamato da World Scanner dopo nuovo problema ad alto score
- **Funzioni**: orchestrazione SA → FE → BOS → notifica Mirco
- **Soglie dinamiche**: da tabella `pipeline_thresholds`, aggiornate ogni lunedì

### HANDS (Sistema Motorio)

#### Spec Generator (`run_spec_generator()`)
- **Trigger**: `/project/build_prompt` da command-center dopo approvazione BOS
- **Funzioni**:
  - Genera SPEC tecnica (~8000 token, Sonnet)
  - Genera SPEC_HUMAN leggibile (Haiku)
  - Estrae JSON metadati (stack, KPI, MVP build time/cost)
  - Crea repo GitHub `brain-[slug]`
  - Crea Forum Topic nel gruppo Telegram
  - Salva `spec_md` + `spec_human_md` in projects table
  - Invia card con pulsanti [Valida] [Modifica] [Versione completa]
- **Dipendenze**: Anthropic Sonnet+Haiku, GitHub API, Telegram API, Supabase

#### Landing Page Generator (`run_landing_page_generator()`)
- **Trigger**: chiamato da `init_project()` dopo SPEC generata
- **Funzioni**: genera HTML landing page (Haiku), salva in `projects.landing_page_html`
- **Dipendenze**: Anthropic Haiku, Supabase

#### Build Agent (`run_build_agent()`)
- **Trigger**: `trigger_build_start()` da command-center dopo validazione SPEC
- **Funzioni**: genera Fase 1 codice MVP (struttura: main.py, requirements.txt, Dockerfile), commit su GitHub
- **Dipendenze**: Anthropic Sonnet, GitHub API, Supabase

#### Continue Build Agent (`continue_build_agent()`)
- **Trigger**: `/project/continue_build` con feedback da Mirco o collaboratore
- **Funzioni**: genera fase successiva (build_phase 1→4) con feedback integrato, commit GitHub
- **Dipendenze**: Anthropic Sonnet, GitHub API, Supabase

#### Legal Agent (`run_legal_review()`)
- **Trigger**: `spec_validate:` callback — automatico dopo validazione SPEC
- **Funzioni**:
  - Review legale (GDPR, AI Act, Direttiva E-Commerce, normativa italiana)
  - Genera Privacy Policy + ToS + Contratto Cliente
  - Aggiorna `projects.status` → `legal_ok` | `legal_blocked`
- **Dipendenze**: Anthropic Sonnet/Haiku, Supabase (legal_reviews)

#### Smoke Test Agent (`run_smoke_test_setup()`)
- **Trigger**: `smoke_approve:` callback — manuale da Mirco
- **Funzioni**:
  - Trova 20 prospect via Perplexity (email/LinkedIn)
  - Salva in `smoke_test_prospects`
  - `analyze_feedback_for_spec()` — analizza risposte, genera SPEC_UPDATES.md
- **Dipendenze**: Perplexity API, Anthropic Haiku+Sonnet, Supabase

#### Marketing System (`run_marketing()`)
- **Trigger**: `smoke_proceed:` callback (automatico), "marketing NomeProgetto" (manuale)
- **8 agenti eseguiti in sequenza/parallelo**:
  1. `run_brand_agent()` — brand DNA, naming, logo SVG, brand kit (Sonnet)
  2. `run_product_marketing_agent()` — positioning, messaging, competitive analysis (Sonnet)
  3. `run_content_agent()` + `run_demand_gen_agent()` + `run_social_agent()` + `run_pr_agent()` — parallelo (Haiku)
  4. `run_customer_marketing_agent()` + `run_marketing_ops_agent()` — sequenziale (Haiku)
- **Output**: ~30 file .md in `/marketing/` del repo GitHub + brand_assets table
- **Dipendenze**: Anthropic Sonnet+Haiku, Perplexity API, GitHub API, Supabase (brand_assets, marketing_reports)

#### Validation Agent (`run_validation_agent()`)
- **Trigger**: `/validation` ogni lunedì 09:00 + dopo `handle_launch()`
- **Funzioni**: analisi metriche MVP, verdetto SCALE/PIVOT/KILL, notifica Mirco
- **Dipendenze**: Anthropic Sonnet, Supabase (project_metrics, projects)

### MEMORY (Sistema di Memoria)

#### Knowledge Keeper (`run_knowledge_keeper()`)
- **Trigger**: `/cycle/knowledge` ogni 12h
- **Funzioni**: estrae lezioni da `agent_logs`, categorizza, salva in `org_knowledge`
- **Dipendenze**: Anthropic Haiku, Supabase

#### Idea Recycler (`run_idea_recycler()`)
- **Trigger**: `/cycle/recycle` ogni lunedì 08:00
- **Funzioni**: rivaluta idee archiviate in `problems_archive` e `solutions_archive`
- **Dipendenze**: Anthropic Haiku, Supabase

#### Source Refresh (`run_source_refresh()`)
- **Trigger**: `/cycle/sources` ogni giorno 07:00
- **Funzioni**: aggiorna reliability_score fonti in `scan_sources`
- **Dipendenze**: Supabase

#### Sources Cleanup (`run_sources_cleanup_weekly()`)
- **Trigger**: `/cycle/sources-cleanup` ogni lunedì 08:10
- **Funzioni**: archivia fonti sotto soglia dinamica da `source_thresholds`
- **Dipendenze**: Supabase

### METABOLISM (Sistema Metabolico)

#### Finance Agent (`run_finance_agent()`)
- **Trigger**: `/finance/morning` 08:00 daily, `/finance/weekly` domenica 20:00, `/finance/monthly` 1° del mese 08:00
- **Funzioni**:
  - `finance_morning_report()` — costi giornalieri, burn rate, proiezioni
  - `finance_weekly_report()` — analisi settimanale, trend, anomalie
  - `finance_monthly_report()` — CFO report completo, suggerimenti ottimizzazione
  - `finance_detect_anomalies()` — rilevamento spike anomali
  - `finance_runway()` — calcola mesi di runway sul budget
- **Dipendenze**: Supabase (agent_logs, finance_metrics), Telegram

#### Cost Report (`generate_cost_report_v2()`)
- **Trigger**: "costi" da Mirco o `report_cost_ondemand` callback
- **Funzioni**: report costi 4h + 24h aggregati da agent_logs
- **Dipendenze**: Supabase

#### KPI Daily (`update_kpi_daily()`)
- **Trigger**: `/kpi/update` (non schedulato esplicitamente, chiamato da pipeline)
- **Funzioni**: aggiorna tabella kpi_daily con metriche giornaliere
- **Dipendenze**: Supabase

---

## 3. DATABASE

**Supabase Pro** — `db.rcwawecswjzpnycuirpx.supabase.co`
**50 tabelle** totali, RLS attivo su tutte.

### Tabelle principali

| Tabella | Righe | Scopo |
|---------|-------|-------|
| `problems` | 21 | Problemi scansionati (score, 7 parametri, status, embedding) |
| `problems_archive` | 94 | Problemi archiviati (copia + archive_reason) |
| `solutions` | 13 | Soluzioni generate (BOS score, feasibility, MVP details) |
| `solutions_archive` | 24 | Soluzioni archiviate |
| `solution_scores` | 12 | Score dettagliati per soluzione |
| `bos_archive` | 21 | Business Opportunity Score archivio |
| `scan_sources` | 289 | Fonti (URL, reliability_score, settori, last_scanned) |
| `agent_logs` | 579 | Log ogni azione agente (model, tokens, cost_usd, status) |
| `agent_events` | 180 | Event bus inter-agenti (source_agent → target_agent) |
| `action_queue` | 6 | Azioni in attesa di approvazione Mirco |
| `org_knowledge` | 64 | Lezioni estratte da Knowledge Keeper |
| `capability_log` | 20 | Nuovi tool/modelli scoperti |
| `org_config` | 16 | Configurazione organizzativa (vedere sezione apposita) |
| `projects` | 1 | Cantieri (spec_md, github_repo, topic_id, status, build_phase) |
| `pipeline_thresholds` | 2 | Soglie dinamiche pipeline (soglia_bos=0.80) |
| `finance_metrics` | 4 | Report finanziari giornalieri aggregati |
| `kpi_daily` | 1 | KPI giornalieri (problems, BOS, MVP, costi) |

### Tabelle Layer 3 (Execution Pipeline)

| Tabella | Righe | Scopo |
|---------|-------|-------|
| `legal_reviews` | 0 | Review legali per progetto |
| `smoke_tests` | 0 | Test di fumo per validazione mercato |
| `smoke_test_prospects` | 0 | Prospect trovati via Perplexity |
| `smoke_test_events` | 0 | Events tracking smoke test |
| `project_members` | 0 | Collaboratori cantiere (Telegram) |
| `project_metrics` | 0 | Metriche settimanali cantiere |
| `brand_assets` | 0 | Asset marketing (brand DNA, positioning, copy kit, ...) |
| `marketing_reports` | 0 | Report marketing settimanali |

### Tabelle memoria vettoriale (pgvector)

| Tabella | Righe | Scopo |
|---------|-------|-------|
| `agent_semantic_memory` | 0 | Memoria semantica agenti (embedding) |
| `agent_episodic_memory` | 0 | Memoria episodica agenti (embedding) |
| `agent_working_memory` | 0 | Memoria working session (scade) |
| `org_knowledge` | 64 | Lezioni con embedding (usato da Knowledge Keeper) |
| `feedback` | 0 | Feedback utenti con embedding |
| `bos_archive` | 21 | BOS con embedding per similarità |
| `patterns` | 0 | Pattern ricorrenti identificati |

### Tabelle secondarie / supporto

| Tabella | Righe | Scopo |
|---------|-------|-------|
| `authorization_matrix` | 18 | Livelli verde/giallo/arancione/rosso per azione |
| `exchange_rates` | 1 | USD/EUR aggiornato |
| `scan_schedule` | 12 | Piano rotazione scan per ora del giorno |
| `scan_logs` | 3 | Log scansioni |
| `source_thresholds` | 1 | Soglia dinamica fonti (absolute_threshold=0.25) |
| `reevaluation_log` | 0 | Log rivalutazioni Idea Recycler |
| `cost_tracking` | 0 | Tracking costi per servizio (non ancora usata) |
| `experiments` | 0 | A/B test (non ancora usata) |
| `migration_history` | 3 | Migration applicate |
| `org_decisions` | 0 | Decisioni strategiche (non ancora usata) |
| `process_changes` | 0 | Cambi di processo (non ancora usata) |
| `agent_capabilities` | 0 | Config agenti (non ancora usata) |
| `agent_performance` | 0 | Performance metriche (non ancora usata) |
| `training_materials` | 0 | Materiale training (non ancora usata) |
| `training_plans` | 0 | Piani training (non ancora usata) |
| `problem_sources` | 0 | Sorgenti per problema (non ancora usata) |
| `project_users` | 0 | Utenti prodotti dei cantieri |
| `performance_history` | 0 | Storico performance (non ancora usata) |

### org_config valori attuali

```
budget_monthly_eur: 1000
circuit_breaker_cooldown_hours: 24
circuit_breaker_threshold: 5
circuit_breaker_window_hours: 1
founder_role: anonymous
god_telegram_user_id: 8307106544
max_cost_per_run_usd: 0.5
max_tokens_per_run: 50000
model_routing: {opus: 0.05, haiku: 0.75, sonnet: 0.2}
org_name: brAIn
retry_max: 3
review_frequency: weekly
scan_frequency: daily
telegram_group_id: -1003799456981
telegram_user_id: 8307106544
version: 3.0
```

---

## 4. SCHEDULER

**Tipo**: Cloud Scheduler (GCP), tutti ENABLED, timezone Europe/Rome, regione europe-west3.
**Nessun pg_cron** — non installato su Supabase.

| Job | Schedule | Endpoint | Descrizione |
|-----|----------|----------|-------------|
| `brain-events-process` | `* * * * *` (ogni minuto) | /events/process | Event bus inter-agenti |
| `brain-cycle-scan` | `0 */2 * * *` (ogni 2h) | /cycle/scan | World Scanner |
| `brain-cycle-sources` | `0 7 * * *` (07:00 daily) | /cycle/sources | Source Refresh |
| `brain-cycle-capability` | `0 6 * * *` (06:00 daily) | /cycle/capability | Capability Scout |
| `brain-finance-morning` | `0 8 * * *` (08:00 daily) | /finance/morning | Finance morning report |
| `brain-report-daily` | `0 20 * * *` (20:00 daily) | /report/daily | Report giornaliero |
| `brain-cycle-knowledge` | `0 */12 * * *` (ogni 12h) | /cycle/knowledge | Knowledge Keeper |
| `brain-finance-weekly` | `0 20 * * 0` (domenica 20:00) | /finance/weekly | Finance weekly report |
| `brain-cycle-recycle` | `0 8 * * 1` (lun 08:00) | /cycle/recycle | Idea Recycler |
| `brain-sources-cleanup` | `10 8 * * 1` (lun 08:10) | /cycle/sources-cleanup | Sources Cleanup |
| `brain-validation` | `0 9 * * 1` (lun 09:00) | /validation | Validation Agent cantieri |
| `brain-marketing-weekly` | `0 9 * * 1` (lun 09:00) | /marketing/report | Marketing weekly report |
| `brain-finance-monthly` | `0 8 1 * *` (1° mese 08:00) | /finance/monthly | Finance monthly report |

**Nota**: `brain-events-process` gira ogni minuto ma agents-runner ha min_instances=0, quindi spesso risponde con cold start. Valutare se alzare a min=1 se latenza è problema.

---

## 5. DEPLOYMENT

### Servizio: agents-runner
- **URL**: https://agents-runner-402184600300.europe-west3.run.app
- **Revisione corrente**: agents-runner-00036-x52
- **Image**: europe-west3-docker.pkg.dev/brain-core-487914/brain-repo/agents-runner:latest
- **Risorse**: 512Mi RAM, 1 vCPU, max 20 istanze, min 0 istanze
- **Timeout**: 300s, Concurrency: 80
- **Accesso**: solo Cloud Scheduler (OIDC) + command-center (OIDC). HTTP 403 da internet pubblico = normale.

**Variabili d'ambiente**:
```
ANTHROPIC_API_KEY   — Claude API key
COMMAND_CENTER_URL  — https://command-center-402184600300.europe-west3.run.app
DB_PASSWORD         — Password DB Supabase psycopg2
PERPLEXITY_API_KEY  — Perplexity Sonar API key
SUPABASE_KEY        — Supabase service role key
SUPABASE_URL        — https://rcwawecswjzpnycuirpx.supabase.co
TELEGRAM_BOT_TOKEN  — Bot token (per notifiche dirette)
SUPABASE_ACCESS_TOKEN — Secret Manager: gestione Supabase via Management API
```

**Endpoint esposti** (34 totali):
```
GET  /                          health check
POST /scanner                   World Scanner manuale
POST /scanner/custom            Scan topic custom
POST /scanner/targeted          Scan fonte specifica
POST /architect                 Solution Architect manuale
POST /knowledge                 Knowledge Keeper
POST /scout                     Capability Scout
POST /finance                   Finance Agent manuale
POST /finance/morning           Finance morning report
POST /finance/weekly            Finance weekly report
POST /finance/monthly           Finance monthly report
POST /feasibility               Feasibility Engine manuale
POST /bos                       BOS calcolo manuale
POST /pipeline                  Pipeline auto completa
POST /events/process            Event bus processing
POST /report/daily              Report giornaliero
POST /report/cost               Cost report on-demand
POST /report/activity           Activity report on-demand
POST /report/auto               Report automatico
POST /kpi/update                Aggiorna KPI daily
POST /cycle/scan                Alias /scanner (scheduler)
POST /cycle/knowledge           Alias /knowledge (scheduler)
POST /cycle/capability          Alias /scout (scheduler)
POST /cycle/sources             Source refresh
POST /cycle/sources-cleanup     Sources cleanup weekly
POST /cycle/recycle             Idea Recycler
POST /thresholds/weekly         Aggiornamento soglie pipeline
POST /project/init              Inizializza nuovo cantiere
POST /project/build_prompt      Avvia spec+landing page generator
POST /spec/update               Aggiorna SPEC con istruzione
POST /validation                Validation Agent cantieri
POST /project/continue_build    Prosegue build fase successiva
POST /project/generate_invite   Genera invite link Telegram
POST /migration/apply           Applica migration SQL
POST /legal/review              Legal review progetto
POST /legal/docs                Genera docs legali (PP, ToS, contratto)
POST /legal/compliance          Compliance check brAIn
POST /smoke/setup               Setup smoke test (cerca prospect)
POST /smoke/analyze             Analizza feedback smoke test
POST /marketing/run             Marketing coordinator (full/brand/gtm/retention)
POST /marketing/brand           Solo brand identity
POST /marketing/report          Marketing weekly report
POST /all                       Esegue tutti gli agenti principali
```

---

### Servizio: command-center
- **URL**: https://command-center-402184600300.europe-west3.run.app
- **Revisione corrente**: command-center-00037-pc6
- **Image**: europe-west3-docker.pkg.dev/brain-core-487914/brain-repo/command-center:latest
- **Risorse**: 512Mi RAM, 1 vCPU, max 20 istanze, **min 1 istanza** (sempre attivo per webhook)
- **Timeout**: 300s, Concurrency: 80

**Variabili d'ambiente**:
```
AGENTS_RUNNER_URL   — https://agents-runner-402184600300.europe-west3.run.app
ANTHROPIC_API_KEY   — Claude API key
GITHUB_TOKEN        — ghp_... (repo mircocerisola/brAIn-core e brain-[slug])
SUPABASE_KEY        — Supabase service role key
SUPABASE_URL        — https://rcwawecswjzpnycuirpx.supabase.co
TELEGRAM_BOT_TOKEN  — Bot token webhook
WEBHOOK_URL         — https://command-center-402184600300.europe-west3.run.app
```

**Endpoint esposti**:
```
POST /webhook        Telegram webhook (messaggi + callback queries)
POST /alert          Notifica alert dagli agenti a Mirco
POST /action/enqueue Inserisce azione in action_queue
POST /action/set     Imposta azione corrente per Mirco
GET  /               health check
```

---

## 6. TELEGRAM

### Bot
- **Token**: `8419743890:AAGF_...`
- **Mirco user_id**: 8307106544
- **Gruppo Forum**: -1003799456981 (Forum Topics abilitati)
- **Modalità**: Webhook su command-center /webhook

### Comandi slash
```
/start     — Welcome card con istruzioni
/code      — Attiva Code Agent (scrivi codice nel repo)
/problems  — Remap → "mostrami i problemi nuovi"
/solutions — Remap → "mostrami le soluzioni"
/status    — Remap → "come sta il sistema?"
/costs     — Remap → "quanto stiamo spendendo?"
/help      — Lista funzionalità
```

### Keyword routing in handle_message()
```
"costi" / "report costi" / "cost report"     → /report/cost
"attività" / "status" / "stato sistema"       → /report/activity
"report" / "dashboard"                        → card con [Costi][Attività]
"marketing [nome]"                            → /marketing/run {project_id}
"crea brand identity" / "brand brAIn"         → /marketing/brand {target=brain}
"quante azioni" / "azioni in coda"            → count action_queue
"vedi tutte le azioni"                        → list action_queue
"salta" / "skip"                              → salta azione corrente
"prossima azione" / "next"                    → prossima azione
"si/ok/vai/deploy/builda"                     → conferma deploy pendente
"no/annulla/stop/cancel"                      → annulla deploy pendente
STOP                                          → blocco emergenza sistema
```

### Callback Handlers in handle_callback_query()

| Callback | Azione |
|----------|--------|
| `spec_validate:{id}` | Valida SPEC → avvia /legal/review |
| `spec_full:{id}` | Invia SPEC completa come file .md |
| `spec_edit:{id}` | Attiva modalità modifica SPEC |
| `spec_build:{id}` | Legacy — avvia build (deprecato) |
| `team_add:{id}` | Aggiungi collaboratore (chiede telefono) |
| `team_skip:{id}` | Salta team setup → avvia build |
| `launch_confirm:{id}` | Conferma lancio → handle_launch() |
| `legal_read:{id}` | Invia report legale completo |
| `legal_proceed:{id}` | Procedi nonostante yellow points |
| `legal_block:{id}` | Blocca progetto (legal_blocked) |
| `smoke_approve:{id}` | Approva setup smoke test |
| `smoke_cancel:{id}` | Cancella smoke test |
| `smoke_proceed:{id}` | Procedi → build + marketing in parallelo |
| `smoke_spec_insights:{id}:{smoke_id}` | Mostra insights smoke test |
| `smoke_modify_spec:{id}` | Modifica SPEC basata su feedback |
| `build_continue:{id}:{phase}` | Continua build fase successiva |
| `build_modify:{id}` | Modifica build corrente |
| `mkt_report:{id}` | Genera marketing report |
| `mkt_brand_kit:{id}` | Mostra brand kit |
| `mkt_next:{id}` | Avvia fase GTM marketing |
| `mkt_report_detail:{id}` | Dettaglio metriche marketing |
| `mkt_report_trend:{id}` | Trend marketing (placeholder) |
| `mkt_report_optimize:{id}` | Avvia fase retention marketing |
| `report_cost_ondemand` | Genera cost report ora |
| `report_activ_ondemand` | Genera activity report ora |
| `bos_approve:{sol_id}:{action_id}` | Approva BOS → avvia init_project() |
| `bos_reject:{sol_id}:{action_id}` | Rifiuta BOS |
| `bos_detail:{id}` | Dettaglio BOS completo |
| `val_proceed:{id}` | Procedi con cantiere (validazione) |
| `val_wait:{id}` | Aspetta altri dati |
| `val_discuss:{id}` | Discuti con Claude |
| `source_reactivate:{name}` | Riattiva fonte disattivata |
| `source_archive_ok` | Conferma archiviazione fonti |
| `cost_detail_4h` | Report costi ultime 4h |
| `cost_trend_7d` | Trend costi 7gg |
| `act_problemi` | Mostra problemi nuovi |
| `act_top_bos` | Mostra top BOS |
| `act_cantieri` | Mostra cantieri attivi |

---

## 7. PROBLEMI NOTI

### Errori ricorrenti nei log

1. **`command_center` agent_id** — tutti gli errori sono ora loggati con `agent_id=command_center` (v5.4+).

2. **Modelli inesistenti (404 error)** — errori `claude-sonnet-4-5` (senza versione data). Il codice ora usa `claude-sonnet-4-6` / `claude-haiku-4-5-20251001`. Verificare se tutti i riferimenti model nei file sono aggiornati.

3. **Rate limit 429** — picchi di rate limit su Anthropic API (2026-02-23). Il circuit breaker è configurato (threshold=5 in 1h, cooldown=24h) ma i log indicano che si è attivato prima di essere implementato nel codice attuale.

4. **action_queue spec_review pending** — 4 azioni spec_review in stato `pending` (non inviate a Mirco). Problema: il bot invia l'azione alla creazione, ma se Mirco non risponde rimane pending. La coda mostra SPEC di "Agente AI prenotazioni ristorante" e "Test Forum Topic Fix" — queste ultime probabilmente test manuali da eliminare.

5. **`brain-events-process` cold start** — gira ogni minuto ma agents-runner ha min_instances=0. Ogni esecuzione può avere cold start di 5-15s. L'event bus è quindi lento ad attivarsi. Soluzione: alzare min_instances a 1 per agents-runner.

6. **Smoke test, Marketing Reports, Brand Assets vuoti** — tutte le tabelle Layer 3 marketing hanno 0 righe. Il sistema è deployato ma non è mai stato usato in produzione.

### Codice duplicato / deprecato

1. **`deploy/command_center_cloud.py`** — vecchia versione del command center (27KB), non deployata. Può essere eliminata.
2. **`agents/command_center.py`** — versione locale originale (16KB), non deployata. Può essere eliminata o mantenuta solo come riferimento.
3. **Agenti standalone in `agents/`** — world_scanner.py, solution_architect.py, feasibility_engine.py, knowledge_keeper.py, capability_scout.py: sono versioni standalone non deployate. Il codice di produzione è tutto inlined in `agents_runner.py`. I file standalone possono diventare sfasati rispetto alla versione di produzione.
4. **`generate_daily_report`** — funzione eliminata in Fix v5.2. Verificare che nessun endpoint la chiami ancora (il job `brain-report-daily` chiama `/report/daily` che ora usa la v2).

### Agenti che non girano

- **Validation Agent** — schedulato lun 09:00, ma projects table ha 1 solo progetto in status `spec_generated` (non ancora `build_complete` o `launched`). Il validation agent si esegue ma non ha dati su cui lavorare.
- **Marketing Report** — schedulato lun 09:00, ma `marketing_reports` è vuota. Il generate_marketing_report() non farà nulla di utile finché non ci sono dati smoke test.
- **Brain Marketing Weekly** — stesso problema di sopra.

### Issues architetturali

1. **agents_runner.py monolitico** — 8200 righe, tutti gli agenti inlined. Difficile da manutenere e testare. Il Python 3.14 locale non rilevava errori di sintassi che fallivano su Python 3.11 (Cloud Run). Soluzione a lungo termine: separare in moduli.
2. **SUPABASE_ACCESS_TOKEN** — necessario per Supabase Management API (creazione DB separato per cantieri). Non è mai stato testato in produzione (_create_supabase_project è best-effort).
3. **Telegram setChatPhoto** — `_update_bot_avatar_svg()` è placeholder (richiede cairosvg + Pillow per SVG→PNG non installati nel container).
4. **GitHub token in env var** — il GITHUB_TOKEN è hard-coded in command-center env var. Se ruota bisogna aggiornare manualmente il servizio Cloud Run.

---

## 8. DIPENDENZE ESTERNE

| API | Utilizzo | Chiave | Note |
|-----|----------|--------|------|
| **Anthropic Claude** | Tutti gli agenti cognitivi | `ANTHROPIC_API_KEY` | Haiku (75%) per chat/analisi veloci, Sonnet (20%) per SPEC/BOS/Solution Architect, Opus (5%) — non usato in pratica |
| **Perplexity Sonar** | World Scanner (fonti web), Solution Architect (research), Smoke Test (prospect), Marketing (research) | `PERPLEXITY_API_KEY` | Modello `sonar` — ricerca real-time |
| **Supabase** | DB PostgreSQL + RLS + pgvector | `SUPABASE_URL` + `SUPABASE_KEY` + `DB_PASSWORD` | Connessione sia via client Python (CRUD) che psycopg2 diretto (migration, query complesse) |
| **Telegram Bot API** | Interfaccia CEO (messaggi, callback, Forum Topics, file invio) | `TELEGRAM_BOT_TOKEN` | Webhook mode su command-center. Bot ID: 8419743890 |
| **GitHub API** | Creazione repo `brain-[slug]`, commit codice/SPEC/marketing files | `GITHUB_TOKEN` | REST API v3, repo privati su mircocerisola |
| **Google Cloud Run** | Hosting agenti (agents-runner, command-center) | Service account IAM | europe-west3 Frankfurt |
| **Google Cloud Build** | Build Docker images | Service account | Build da directory locale → Artifact Registry |
| **Google Cloud Scheduler** | 13 job schedulati | Service account OIDC | Autenticazione OIDC verso agents-runner |
| **Google Secret Manager** | `SUPABASE_ACCESS_TOKEN` | `roles/secretmanager.secretAccessor` | Solo su agents-runner |
| **Supabase Management API** | Creazione DB separato per cantieri | `SUPABASE_ACCESS_TOKEN` | Best-effort in init_project(), non ancora testato in produzione |

### API non più usate (eliminate)
- ~~Looka API~~ — nessun endpoint pubblico, sostituito da Claude-generated SVG
- ~~n8n~~ — eliminato
- ~~Mem0~~ — eliminato
- ~~Airtable~~ — eliminato
- ~~Qdrant~~ — eliminato (pgvector Supabase)

---

*Fine audit — generato automaticamente da Claude Code il 2026-02-26*
