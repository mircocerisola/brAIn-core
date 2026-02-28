# brAIn Snapshot

Generato: 2026-02-28 ore 10:00 CET
Versione: brAIn v5.22

## Organizzazione

brAIn e' un organismo AI-native: 1 umano (Mirco, CEO) + team di agenti AI.
Scansiona problemi globali, genera soluzioni, le testa sul mercato, scala quelle che funzionano.

## C-Suite — 7 Chief AI

### CSO (Chief Strategy Officer) — csuite/cso.py
Dominio: strategia, mercati, competizione.
Funzioni implementate:
- get_domain_context: pipeline soluzioni + idee founder + top problemi
- find_real_prospects: cerca prospect reali via Perplexity (10 citta italiane)
- start_smoke_test: avvia smoke test con prospect reali
- send_smoke_relaunch: rilancia smoke test con pipeline reset
- select_smoke_test_method: scelta metodo (cold_outreach, landing_page_ads, concierge, pre_order, paid_ads)
- Daily report: problemi scansionati, soluzioni generate, smoke test, anomalie pipeline

### COO (Chief Operations & Revenue Officer) — csuite/coo.py
Dominio: operazioni, cantieri, pipeline, prodotto.
Funzioni implementate:
- get_domain_context: cantieri attivi, action queue, prodotti live, KPI, log agenti
- ensure_project_topic: crea topic Telegram per progetto
- send_project_daily_report: report giornaliero cantiere con progress bar e task breakdown
- send_all_project_daily_reports: report per tutti i cantieri aperti
- accelerate_open_cantieri: controlla task e invia reminder se bloccati >24h
- send_daily_brain_snapshot: genera snapshot giornaliero con Drive, email, Supabase
- rename_cantiere: rinomina progetto su Supabase e topic Telegram
- Daily report: action queue, log agenti, cantieri, KPI

### CTO (Chief Technology Officer) — csuite/cto.py
Dominio: infrastruttura, codice, deploy, sicurezza.
Funzioni implementate:
- get_domain_context: errori settimanali, capability recenti, code tasks
- answer_question: intercetta pattern "dangerously-skip-permissions" e crea CODEACTION card
- execute_approved_task: triggera Cloud Run Job, monitor output_log ogni 5min
- interrupt_task: status interrupt_requested per stop job
- build_technical_prompt: trasforma richiesta funzionale in prompt tecnico
- generate_and_deliver_prompt: genera prompt + CODEACTION card per inter-agente
- project_context_builder: contesto compatto max 20 righe per progetto
- send_preview_card: card anteprima con Avvia/Annulla
- handle_cancel_preview: gestisce annullamento, notifica COO
- Daily report: errori, code tasks, capability

### CMO (Chief Marketing Officer) — csuite/cmo.py
Dominio: marketing, brand, growth, conversion.
Funzioni implementate:
- get_domain_context: brand assets, marketing reports
- load_marketing_data: carica dati marketing reali
- generate_brief_report: report CMO con dati reali
- generate_landing_page_html: genera HTML landing via Claude
- generate_bozza_visiva: genera bozza visiva PNG con Pillow (3 palette)
- publish_landing_brief_for_cto: pubblica evento landing_brief_ready per CTO
- Daily report: prospect, smoke events, marketing reports, brand assets

### CFO (Chief Financial Officer) — csuite/cfo.py
Dominio: costi, revenue, marginalita'.
Funzioni implementate:
- get_domain_context: costi settimanali per agente/modello, 24h breakdown, finance metrics, costi fissi
- get_costs_breakdown: breakdown costi real-time per Chief/Progetto/Modello
- check_anomalies: spike costi vs budget giornaliero
- Daily report: costi per Chief/modello/progetto, anomalie, metriche finanziarie

### CLO (Chief Legal Officer) — csuite/clo.py
Dominio: legale, compliance, GDPR, AI Act.
Funzioni: briefing settimanale, anomalie. DA ESPANDERE.

### CPeO (Chief People Officer) — csuite/cpeo.py
Dominio: coaching, sviluppo Chief, cultura.
Funzioni: coaching automatico dei Chief, report coaching.

## Stack Tecnologico

- Claude API: Haiku (80% chat), Sonnet (20% code agent), Opus (Claude Code headless)
- Perplexity API Sonar: ricerca web per World Scanner e CSO prospect
- Supabase Pro: PostgreSQL + pgvector + RLS. 35 tabelle.
- Telegram: bot brAIn (Command Center unificato + Forum Topics C-Suite)
- Python 3.11: linguaggio agenti (Cloud Run container)
- GitHub privato: mircocerisola/brAIn-core
- Google Cloud Run EU Frankfurt: 2 servizi + 1 job
- Pillow: generazione bozze visive PNG
- Google Drive API: storage snapshot giornalieri

## Tabelle Supabase (35)

| Tabella | Righe | Colonne principali |
|---------|-------|--------------------|
| problems | 54 | id, title, description, weighted_score, status, sector, source_urls, fingerprint |
| solutions | 16 | id, problem_id, title, status, bos_score, sector, customer_segment, source |
| scan_sources | 346 | id, name, url, reliability_score, status, sector |
| agent_logs | 674 | id, agent_id, action, status, cost_usd, tokens_input, tokens_output, model_used |
| org_knowledge | 64 | id, category, content, source_agent |
| org_shared_knowledge | 57 | id, category, content, source |
| chief_knowledge | 35 | id, chief_id, category, content, source |
| capability_log | 25 | id, name, description, created_at |
| org_config | 27 | key, value |
| authorization_matrix | 18 | id, action_type, risk_level, auto_approve |
| solution_scores | 15 | id, solution_id, score_type, value |
| reevaluation_log | 0 | id, solution_id, reason, created_at |
| topic_conversation_history | 279 | id, scope_id, role, text, created_at |
| episodic_memory | 13 | id, scope_type, scope_id, summary, importance |
| code_tasks | 18 | id, title, prompt, status, requested_by, output_log |
| chief_memory | 0 | id, chief_id, key, value |
| chief_decisions | 20 | id, chief_domain, decision_type, summary, full_text |
| projects | 1 | id, name, brand_name, status, pipeline_step, cantiere_status, topic_id |
| project_tasks | 6 | id, project_id, title, status, assigned_to, priority |
| smoke_tests | 0 | id, project_id, method, kpi_success, kpi_failure, duration_days |
| smoke_test_prospects | 10 | id, project_id, name, company, contact, channel, status |
| smoke_test_events | 0 | id, event_type, project_id |
| brand_assets | 0 | id, project_id, brand_name, tagline, status |
| marketing_reports | 0 | id, project_id, channel, recorded_at |
| kpi_daily | 1 | id, project_id, metric_name, value, recorded_at |
| action_queue | 8 | id, action_type, title, project_id, status, payload |
| legal_reviews | 0 | id, project_id, review_type, status |
| finance_metrics | 5 | id, metric_name, value, created_at |
| active_session | 1 | id, telegram_user_id, context_type, project_id |
| project_members | 0 | id, project_id, telegram_phone, role |
| source_thresholds | 1 | id, dynamic_threshold, absolute_threshold, target_active_pct |
| migration_history | 10 | id, filename, applied_at |
| agent_events | 214 | id, event_type, agent_from, agent_to, payload, created_at |
| brain_config | 0 | key, value, updated_at |
| users | 1 | id, name, role, email, telegram_id |
| brain_snapshots | 0 | id, snapshot_date, snapshot_md, sommario, drive_url, filename |

## Cloud Run Services

| Servizio | Revisione | URL |
|----------|-----------|-----|
| command-center | rev 00072-55g | https://command-center-402184600300.europe-west3.run.app |
| agents-runner | rev 00092-dk7 | https://agents-runner-402184600300.europe-west3.run.app |
| brain-code-executor (Job) | — | Cloud Run Job, trigger on-demand |

Region: europe-west3 (Frankfurt)
Project ID: brain-core-487914

## Agent Events — Tipi Registrati

| event_type | conteggio |
|------------|-----------|
| archive | 12 |
| auto_go | 14 |
| batch_scan_complete | 10 |
| bos_calculated | 26 |
| error_pattern_detected | 5 |
| feasibility_completed | 26 |
| mirco_feedback | 10 |
| problem_approved | 3 |
| problem_ready | 47 |
| problems_found | 17 |
| review_request | 14 |
| scan_completed | 27 |
| solution_selected | 1 |
| solutions_generated | 2 |

## Progetti Aperti

### Coperti.ai (id 5)
- Brand: RestaAI (in corso di rinomina a Coperti.ai)
- Status: active
- Pipeline step: smoke_test_designing
- Cantiere: open (topic #91)
- Prospect: 10 con email
- Task attive:
  - CSO: Trovare 50+ prospect ristoranti [in_progress]
  - CMO: Brand identity completa [pending]
  - Mirco: Landing page pubblicata [pending]
  - CSO: Sequenza email cold outreach [pending]
  - Mirco: Approvazione finale pre-lancio [pending]
  - COO: Coordinamento e monitoring [in_progress]

## Cron Jobs Schedulati (Cloud Scheduler)

| Job | Frequenza | Endpoint |
|-----|-----------|----------|
| brain-events-process | ogni minuto | /events/process |
| brain-cycle-scan | ogni 4h | /cycle/scan |
| brain-cycle-knowledge | ogni 12h | /cycle/knowledge |
| brain-cycle-capability | daily 06:00 | /cycle/capability |
| brain-cycle-sources | daily 07:00 | /cycle/sources |
| brain-cycle-recycle | lun 08:00 | /cycle/recycle |
| brain-finance-morning | daily 08:00 | /finance/morning |
| brain-finance-weekly | dom 20:00 | /finance/weekly |
| brain-finance-monthly | 1 mese 08:00 | /finance/monthly |
| brain-marketing-weekly | lun 09:00 | /marketing/report |
| brain-cycle-queue-cleanup | lun 09:00 | /cycle/queue-cleanup |
| brain-csuite-morning | daily 08:00 | /csuite/morning-report |
| brain-csuite-anomalies-morning | daily 06:30 | /csuite/anomalies |
| brain-csuite-anomalies-evening | daily 20:30 | /csuite/anomalies |
| brain-coo-project-daily | daily 08:15 | /coo/project-daily |
| brain-coo-accelerator | ogni 6h | /coo/accelerator |
| brain-smoke-daily-update | daily 10:00 | /smoke/daily-update |
| brain-ethics-morning | daily 09:00 | /ethics/check-active |
| brain-ethics-afternoon | daily 15:00 | /ethics/check-active |
| brain-ethics-evening | daily 21:00 | /ethics/check-active |
| brain-cdo-audit | lun 06:00 | /cto/data-audit |
| brain-cdo-monitor | gio 06:00 | /cto/knowledge-monitor |
| brain-cpeo-coaching | lun 06:30 | /cpeo/coaching |
| 7x brain-csuite-{chief} | lun mattina | /csuite/briefing |

## Problemi Noti / TODO nel Codice

- CLO: Legal Monitor con feed normativo DA COSTRUIRE
- Feasibility Engine: valutazione automatica fattibilita' DA ESPANDERE
- Portfolio Manager: raccomandazioni scale/pivot/kill DA COSTRUIRE
- Idea Recycler: rivalutazione periodica (tabella vuota, logica base presente)
- Customer Agent: supporto, feedback, retention DA COSTRUIRE
- Budget Guardian: alert se costi superano soglie DA COSTRUIRE
- Security Monitor: protezione dati, accessi, anomalie DA COSTRUIRE
- Revenue Tracker: monitora entrate per progetto DA COSTRUIRE
- smoke_tests: tabella vuota, usata da smoke.py ma nessun record creato
- brand_assets: tabella vuota, CMO non popola ancora
- marketing_reports: tabella vuota
- legal_reviews: tabella vuota, CLO non operativo
- Perplexity prospect: qualita' variabile (0-16 per query)
- Python 3.11 su Cloud Run vs 3.14 locale: attenzione f-string backslash
