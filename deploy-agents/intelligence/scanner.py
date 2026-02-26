"""
brAIn module: intelligence/scanner.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re, hashlib
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, PERPLEXITY_API_KEY, logger
from core.utils import (log_to_supabase, notify_telegram, extract_json, search_perplexity,
                        get_telegram_chat_id, emit_event,
                        get_mirco_preferences, get_sector_preference_modifier,
                        get_pipeline_thresholds, get_scan_strategy, get_scan_schedule_strategy,
                        get_sector_with_fewest_problems, get_last_sector_rotation,
                        get_high_bos_problem_sectors, build_strategy_queries)


def scanner_make_fingerprint(title, sector):
    text = f"{title.lower().strip()}_{sector.lower().strip()}"
    return hashlib.md5(text.encode()).hexdigest()


def scanner_normalize_urgency(value):
    if isinstance(value, str):
        v = value.lower().strip()
        if v in ("low", "medium", "high", "critical"):
            return v
        try:
            value = float(v)
        except:
            return "medium"
    if isinstance(value, (int, float)):
        if value >= 0.85:
            return "critical"
        elif value >= 0.65:
            return "high"
        elif value >= 0.4:
            return "medium"
        else:
            return "low"
    return "medium"


SCANNER_GENERIC_TERMS = [
    "aziende", "companies", "persone", "people", "utenti", "users",
    "imprenditori", "entrepreneurs", "professionisti", "professionals",
    "individui", "individuals", "clienti", "customers", "lavoratori", "workers",
]

SCANNER_WEIGHTS = {
    "market_size": 0.20, "willingness_to_pay": 0.20, "urgency": 0.15,
    "competition_gap": 0.15, "ai_solvability": 0.15, "time_to_market": 0.10,
    "recurring_potential": 0.05,
}

SCANNER_ANALYSIS_PROMPT = """Sei il World Scanner di brAIn, un'organizzazione AI-native che cerca problemi SPECIFICI e AZIONABILI.

REGOLA FONDAMENTALE: ogni problema deve riguardare un segmento PRECISO di persone in un contesto geografico PRECISO con prove CONCRETE.

ESEMPIO SBAGLIATO (troppo generico, rifiutato):
"Le PMI faticano con la gestione finanziaria"

ESEMPIO CORRETTO (specifico, azionabile):
"Gli elettricisti autonomi italiani tra 30-45 anni non hanno accesso a corsi di aggiornamento normativo certificati a meno di 500 EUR"

Per ogni problema identificato (massimo 3), fornisci TUTTI questi campi:

1. IDENTIFICAZIONE TARGET (OBBLIGATORIO — rifiuta se non hai dati specifici):
   - target_customer: segmento SPECIFICO — professione + fascia d'eta' + contesto (NON "aziende" o "persone")
   - target_geography: paese/regione SPECIFICA + perche' proprio li'
   - problem_frequency: daily/weekly/monthly/quarterly

2. DESCRIZIONE PROBLEMA (OBBLIGATORIO):
   - current_workaround: come il target risolve OGGI il problema e perche' e' insufficiente
   - pain_intensity: 1 (fastidio) a 5 (blocca il business/la vita)
   - evidence: dato CONCRETO e verificabile — statistica con fonte, numero persone colpite, dimensione mercato

3. TIMING (OBBLIGATORIO):
   - why_now: perche' questo problema e' rilevante ORA (cambio normativo, tecnologia, comportamento)

4. DATI QUANTITATIVI — 7 score da 0.0 a 1.0 — usa TUTTA la scala, ogni problema DEVE avere almeno 2 score sotto 0.4:
   - market_size: 0.1=nicchia <1M EUR, 0.5=medio 10-100M EUR, 1.0=miliardi
   - willingness_to_pay: 0.1=difficile convincerli, 1.0=pagano gia' o chiedono attivamente
   - urgency: 0.1=fastidio, 1.0=perde soldi/clienti oggi
   - competition_gap: 1.0=nessuna soluzione, 0.0=mercato saturo
   - ai_solvability: 0.1=richiede umani, 1.0=100% automatizzabile
   - time_to_market: 1.0=1 settimana, 0.3=3 mesi, 0.0=anni
   - recurring_potential: 1.0=quotidiano, 0.3=mensile, 0.0=una tantum

5. CLASSIFICAZIONE:
   - sector: uno tra food, health, finance, education, legal, ecommerce, hr, real_estate, sustainability, cybersecurity, entertainment, logistics
   - geographic_scope: global, continental, national, regional
   - top_markets: lista 3-5 codici paese ISO
   - who_is_affected, real_world_example, why_it_matters: testo descrittivo in italiano

SCARTA qualsiasi problema senza target_customer specifico, evidence con dati numerici, o why_now chiaro.
REGOLA DIVERSITA SETTORI: i problemi devono riguardare settori DIVERSI.

{preferences_block}

Rispondi SOLO con JSON:
{{"problems":[{{"title":"titolo specifico","description":"descrizione","target_customer":"elettricisti autonomi italiani 30-45 anni","target_geography":"Italia nord e centro","problem_frequency":"monthly","current_workaround":"cercano corsi online generici","pain_intensity":4,"evidence":"In Italia ci sono 180.000 elettricisti autonomi (CGIA 2024)","why_now":"Norma CEI 64-8/7 del 2023 obbligatoria dal 2025","who_is_affected":"chi soffre","real_world_example":"storia concreta","why_it_matters":"perche conta","sector":"education","geographic_scope":"national","top_markets":["IT"],"market_size":0.4,"willingness_to_pay":0.7,"urgency":0.8,"competition_gap":0.7,"ai_solvability":0.8,"time_to_market":0.8,"recurring_potential":0.6,"source_name":"CGIA Mestre","source_url":"https://cgia.it"}}],"new_sources":[{{"name":"nome","url":"url","category":"tipo","sectors":["settore"]}}]}}
SOLO JSON."""


def scanner_calculate_weighted_score(problem):
    base_score = 0.0
    for param, weight in SCANNER_WEIGHTS.items():
        value = problem.get(param, 0.5)
        if isinstance(value, (int, float)):
            base_score += float(value) * weight

    adjustments = 0.0
    multiplier = 1.0

    target_customer = problem.get("target_customer", "").lower()
    evidence = problem.get("evidence", "")
    why_now = problem.get("why_now", "")
    pain_intensity = problem.get("pain_intensity", 3)

    # Penalita' per genericita'
    generic_count = sum(1 for t in SCANNER_GENERIC_TERMS if t in target_customer.split())
    if generic_count > 0 and len(target_customer.split()) <= 3:
        adjustments -= 0.20
    if not evidence or len(evidence) < 30:
        adjustments -= 0.15
    if not why_now or len(why_now) < 20:
        adjustments -= 0.10
    if isinstance(pain_intensity, (int, float)) and pain_intensity < 3:
        multiplier *= 0.7

    # Bonus per specificita'
    has_age = any(c.isdigit() for c in target_customer)
    has_many_words = len(target_customer.split()) >= 4
    if has_age or has_many_words:
        adjustments += 0.10
    has_number = any(c.isdigit() for c in evidence)
    has_source = any(t in evidence.lower() for t in ["fonte", "source", "report", "%", "milion", "miliard"])
    if has_number and (has_source or len(evidence) > 80):
        adjustments += 0.10

    final_score = (base_score + adjustments) * multiplier
    return round(max(0.0, min(1.0, final_score)), 4)


def normalize_batch_scores(problems_data):
    if len(problems_data) < 2:
        return problems_data
    problems_data.sort(key=lambda x: x["_weighted"], reverse=True)
    n = len(problems_data)
    best_score = min(problems_data[0]["_weighted"], 0.92)
    worst_score = max(best_score - (n * 0.12), 0.25)
    if n == 1:
        problems_data[0]["_weighted"] = best_score
    elif n == 2:
        problems_data[0]["_weighted"] = best_score
        problems_data[1]["_weighted"] = round(best_score - 0.15, 4)
    else:
        step = (best_score - worst_score) / (n - 1)
        for i, p in enumerate(problems_data):
            p["_weighted"] = round(best_score - (i * step), 4)
            p["_weighted"] = max(0.15, min(1.0, p["_weighted"]))
    return problems_data


def get_standard_queries(sources):
    all_sectors = set()
    for s in sources:
        sectors = s.get("sectors", [])
        if isinstance(sectors, str):
            sectors = json.loads(sectors)
        all_sectors.update(sectors)

    sector_queries = {
        "food": "food waste restaurants expired inventory unsold meals problem",
        "health": "patients waiting time mental health access rural areas problem",
        "finance": "small business cash flow invoicing late payments problem",
        "education": "tutoring affordable access learning disabilities students problem",
        "legal": "small business contract disputes legal costs too high problem",
        "ecommerce": "product returns fraud fake reviews online sellers problem",
        "hr": "employee burnout retention turnover small companies problem",
        "real_estate": "rental scams tenant landlord disputes maintenance problem",
        "sustainability": "food packaging waste recycling confusion consumers problem",
        "cybersecurity": "password reuse data breach small business protection problem",
        "entertainment": "independent creators monetization copyright content theft problem",
        "logistics": "last mile delivery cost small business shipping rural problem",
    }

    queries = []
    for sector in all_sectors:
        if sector in sector_queries:
            queries.append((sector, sector_queries[sector]))
    queries.append(("cross", "most frustrating everyday problems people pay to solve"))
    queries.append(("cross", "biggest complaints small business owners daily operations"))
    queries.append(("cross", "underserved customer needs no good solution exists"))
    return queries


def run_scan(queries, max_problems=None):
    """
    Core scan logic con soglie dinamiche da DB.
    max_problems: se impostato, si ferma dopo aver salvato N problemi di qualità.
    """
    thresholds = get_pipeline_thresholds()
    soglia_problema = thresholds["problema"]

    try:
        sources = supabase.table("scan_sources").select("*").eq("status", "active").order("relevance_score", desc=True).limit(10).execute()
        sources = sources.data or []
    except:
        sources = []

    try:
        fps_result = supabase.table("problems").select("fingerprint").not_.is_("fingerprint", "null").execute()
        existing_fps = {r["fingerprint"] for r in fps_result.data}
    except:
        existing_fps = set()

    source_map = {s["name"]: s["id"] for s in sources}

    # Preferenze per il prompt
    preferences = get_mirco_preferences()
    sector_mods = get_sector_preference_modifier()

    preferences_block = ""
    if preferences:
        preferences_block = f"PREFERENZE DI MIRCO (calibra la ricerca di conseguenza):\n{preferences}\n"
    if sector_mods:
        favored = [s for s, v in sector_mods.items() if v > 0]
        disfavored = [s for s, v in sector_mods.items() if v < -1]
        if favored:
            preferences_block += f"Settori preferiti: {', '.join(favored)}\n"
        if disfavored:
            preferences_block += f"Settori poco interessanti: {', '.join(disfavored)} — riduci priorita\n"

    analysis_prompt = SCANNER_ANALYSIS_PROMPT.replace("{preferences_block}", preferences_block)

    search_results = []
    for sector, query in queries:
        result = search_perplexity(query)
        if result:
            search_results.append((sector, query, result))
        time.sleep(1)

    if not search_results:
        return {"status": "no_results", "saved": 0}

    total_saved = 0
    all_scores = []
    saved_problem_ids = []
    source_problem_scores = {}  # {source_id: [weighted_score, ...]} per aggiornamento mirato

    batch_size = 4
    for i in range(0, len(search_results), batch_size):
        batch = search_results[i:i + batch_size]
        combined = "\n\n---\n\n".join([
            f"Settore: {sector}\nQuery: {query}\nRisultati: {result}"
            for sector, query, result in batch
        ])

        start = time.time()
        try:
            response = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=analysis_prompt,
                messages=[{"role": "user", "content": f"Analizza e identifica problemi. SOLO JSON:\n\n{combined}"}]
            )
            duration = int((time.time() - start) * 1000)
            reply = response.content[0].text

            log_to_supabase("world_scanner", "scan_v2", 1,
                f"Batch {len(batch)} ricerche", reply[:500],
                "claude-haiku-4-5-20251001",
                response.usage.input_tokens, response.usage.output_tokens,
                (response.usage.input_tokens * 1.0 + response.usage.output_tokens * 5.0) / 1_000_000,
                duration)

            data = extract_json(reply)
            if data:
                batch_problems = []
                for prob in data.get("problems", []):
                    title = prob.get("title", "")
                    sector = prob.get("sector", "general")
                    if sector not in SCANNER_SECTORS:
                        sector = "ecommerce"

                    fp = scanner_make_fingerprint(title, sector)
                    if fp in existing_fps:
                        continue

                    weighted = scanner_calculate_weighted_score(prob)

                    # Sector preference modifier
                    mod = sector_mods.get(sector, 0)
                    if mod > 2:
                        weighted = round(weighted * 1.05, 4)
                    elif mod < -2:
                        weighted = round(weighted * 0.90, 4)

                    low_count = sum(1 for param in SCANNER_WEIGHTS if prob.get(param, 0.5) < 0.5 and isinstance(prob.get(param, 0.5), (int, float)))
                    if low_count == 0:
                        weighted = round(weighted * 0.8, 4)

                    batch_problems.append({
                        "_weighted": weighted, "_prob": prob,
                        "_title": title, "_sector": sector, "_fp": fp,
                    })

                batch_problems = normalize_batch_scores(batch_problems)

                for bp in batch_problems:
                    prob = bp["_prob"]
                    title = bp["_title"]
                    sector = bp["_sector"]
                    fp = bp["_fp"]
                    weighted = bp["_weighted"]

                    # Determina status in base alla soglia dinamica
                    # Sotto soglia: archiviato (nessuna notifica, nessuna pipeline)
                    # Sopra soglia: new (va in pipeline automatica)
                    save_status = "new" if weighted >= soglia_problema else "archived"

                    urgency_text = scanner_normalize_urgency(prob.get("urgency", 0.5))

                    source_id = None
                    source_name = prob.get("source_name", "")
                    for sname, sid in source_map.items():
                        if sname.lower() in source_name.lower() or source_name.lower() in sname.lower():
                            source_id = sid
                            break

                    top_markets = prob.get("top_markets", [])
                    if isinstance(top_markets, str):
                        top_markets = json.loads(top_markets)

                    pain_intensity_val = prob.get("pain_intensity", None)
                    if isinstance(pain_intensity_val, (int, float)):
                        pain_intensity_val = int(pain_intensity_val)

                    try:
                        insert_result = supabase.table("problems").insert({
                            "title": title,
                            "description": prob.get("description", ""),
                            "domain": sector, "sector": sector,
                            "geographic_scope": prob.get("geographic_scope", "global"),
                            "top_markets": json.dumps(top_markets),
                            "market_size": float(prob.get("market_size", 0.5)),
                            "willingness_to_pay": float(prob.get("willingness_to_pay", 0.5)),
                            "urgency": urgency_text,
                            "competition_gap": float(prob.get("competition_gap", 0.5)),
                            "ai_solvability": float(prob.get("ai_solvability", 0.5)),
                            "time_to_market": float(prob.get("time_to_market", 0.5)),
                            "recurring_potential": float(prob.get("recurring_potential", 0.5)),
                            "weighted_score": weighted, "score": weighted,
                            "who_is_affected": prob.get("who_is_affected", ""),
                            "real_world_example": prob.get("real_world_example", ""),
                            "why_it_matters": prob.get("why_it_matters", ""),
                            # Nuovi campi specificita' v3.0
                            "target_customer": prob.get("target_customer", ""),
                            "target_geography": prob.get("target_geography", ""),
                            "problem_frequency": prob.get("problem_frequency", ""),
                            "current_workaround": prob.get("current_workaround", ""),
                            "pain_intensity": pain_intensity_val,
                            "evidence": prob.get("evidence", ""),
                            "why_now": prob.get("why_now", ""),
                            "fingerprint": fp, "source_id": source_id,
                            "source_ids": json.dumps([source_id] if source_id else []),
                            "status": save_status,
                            "status_detail": "active" if save_status == "new" else "archived",
                            "created_by": "world_scanner_v3",
                        }).execute()

                        existing_fps.add(fp)
                        if save_status == "new":
                            total_saved += 1
                            all_scores.append(weighted)
                            # Traccia score per aggiornamento mirato del relevance_score
                            if source_id is not None:
                                source_problem_scores.setdefault(source_id, []).append(weighted)
                            if insert_result.data:
                                saved_problem_ids.append(insert_result.data[0]["id"])
                        else:
                            logger.debug(f"[SCAN] '{title[:50]}': score={weighted:.2f} < soglia {soglia_problema} → archived")

                    except Exception as e:
                        if "idx_problems_fingerprint" not in str(e):
                            logger.error(f"[SAVE ERROR] {e}")

                for ns in data.get("new_sources", []):
                    try:
                        name = ns.get("name", "")
                        if name:
                            supabase.table("scan_sources").insert({
                                "name": name, "url": ns.get("url", ""),
                                "category": ns.get("category", "other"),
                                "sectors": json.dumps(ns.get("sectors", [])),
                                "relevance_score": 0.4, "status": "active",
                                "notes": "Scoperta automatica",
                            }).execute()
                    except:
                        pass

        except Exception as e:
            logger.error(f"[BATCH ERROR] {e}")
        time.sleep(1)

    # Aggiorna statistiche fonti — solo quelle che hanno contribuito problemi
    now_iso = datetime.now(timezone.utc).isoformat()
    for source in sources:
        sid = source.get("id")
        try:
            if sid in source_problem_scores:
                # Fonte che ha prodotto almeno un problema: aggiorna stats
                scores = source_problem_scores[sid]
                avg_score = sum(scores) / len(scores)
                old_found = source.get("problems_found", 0)
                old_avg = source.get("avg_problem_score", 0) or 0
                new_found = old_found + len(scores)
                new_avg = (old_avg * old_found + sum(scores)) / new_found if new_found > 0 else avg_score
                old_rel = source.get("relevance_score", 0.5)
                new_rel = min(1.0, old_rel + 0.02) if avg_score > 0.6 else max(0.1, old_rel - 0.02) if avg_score < 0.4 else old_rel
                supabase.table("scan_sources").update({
                    "problems_found": new_found,
                    "avg_problem_score": round(new_avg, 4),
                    "relevance_score": round(new_rel, 4),
                    "last_scanned": now_iso,
                }).eq("id", sid).execute()
            else:
                # Fonte scansionata ma senza problemi: aggiorna solo last_scanned
                supabase.table("scan_sources").update({
                    "last_scanned": now_iso,
                }).eq("id", sid).execute()
        except:
            pass

    # Emetti eventi
    if total_saved > 0:
        emit_event("world_scanner", "scan_completed", None,
            {"problems_saved": total_saved, "problem_ids": saved_problem_ids,
             "avg_score": sum(all_scores) / len(all_scores) if all_scores else 0})
        high_score_ids = [pid for pid, sc in zip(saved_problem_ids, all_scores) if sc >= MIN_SCORE_THRESHOLD]
        if high_score_ids:
            emit_event("world_scanner", "problems_found", "command_center",
                {"problem_ids": high_score_ids, "count": len(high_score_ids)})

    if total_saved >= 3:
        emit_event("world_scanner", "batch_scan_complete", "knowledge_keeper",
            {"problems_saved": total_saved, "avg_score": sum(all_scores) / len(all_scores) if all_scores else 0})

    return {"status": "completed", "saved": total_saved, "saved_ids": saved_problem_ids, "max_hit": max_problems is not None and total_saved >= max_problems}


def run_world_scanner():
    """
    Scan ogni 2 ore con rotazione intelligente da scan_schedule.
    Obiettivo: esattamente 1 problema di alta qualità per scan.
    Se il primo tentativo non trova un problema valido, riprova con una fonte alternativa.
    """
    strategy = get_scan_schedule_strategy()
    logger.info(f"World Scanner v3.0 starting — strategia: {strategy}")

    log_to_supabase("world_scanner", f"scan_v3_{strategy}", 1,
        f"Strategia: {strategy}", None, "none")

    # Costruisci query basate sulla strategia
    queries_primary, _ = build_strategy_queries(strategy)

    # Tenta con strategia primaria — limita a max 4 query per scan
    queries_primary = queries_primary[:4]
    result = run_scan(queries_primary, max_problems=1)

    # Se non trovato nulla, riprova con top_sources come fallback
    if result.get("saved", 0) == 0 and strategy != "top_sources":
        logger.info(f"[SCANNER] Nessun problema valido con '{strategy}', ritento con top_sources")
        log_to_supabase("world_scanner", "scan_retry_fallback", 1,
            f"Retry da {strategy}", "top_sources", "none")
        try:
            sources = supabase.table("scan_sources").select("*").eq("status", "active")\
                .order("relevance_score", desc=True).limit(5).execute()
            fallback_queries = get_standard_queries(sources.data or [])[:3]
        except:
            fallback_queries = [("cross", "specific niche professional problem concrete evidence 2026")]
        result = run_scan(fallback_queries, max_problems=1)

    # Pipeline automatica in background
    saved_ids = result.get("saved_ids", [])
    if saved_ids:
        import threading
        threading.Thread(target=run_auto_pipeline, args=(saved_ids,), daemon=True).start()
        logger.info(f"[PIPELINE] Avviata per {len(saved_ids)} problemi")
    else:
        logger.info(f"[SCANNER] Nessun problema salvato questo ciclo (qualità insufficiente — OK)")

    return result


def run_custom_scan(topic):
    logger.info(f"World Scanner custom scan: {topic}")
    queries = [
        ("custom", f"{topic} biggest problems pain points"),
        ("custom", f"{topic} unsolved needs market gap"),
        ("custom", f"{topic} consumers complaints frustrations"),
    ]
    result = run_scan(queries)
    saved_ids = result.get("saved_ids", [])
    if saved_ids:
        import threading
        threading.Thread(target=run_auto_pipeline, args=(saved_ids,), daemon=True).start()
    elif result.get("saved", 0) == 0:
        notify_telegram(f"Scan su '{topic}' completato ma non ho trovato problemi nuovi.")
    return result


# ============================================================
# SOLUTION ARCHITECT v2.0 — 3 fasi + BOS SQ
# ============================================================

RESEARCH_PROMPT = """Sei un analista di mercato esperto. Dati i risultati di ricerca sul web, crea un DOSSIER COMPETITIVO per il problema dato.

LINGUA: Rispondi SEMPRE in italiano.

Il dossier deve includere:
1. SOLUZIONI ESISTENTI: chi gia' risolve questo problema? Nome, cosa fa, prezzo, punti deboli.
2. GAP DI MERCATO: cosa manca nelle soluzioni attuali?
3. TENTATIVI FALLITI: qualcuno ha provato e fallito? Perche'?
4. INSIGHT ESPERTI: cosa dicono ricercatori, analisti, utenti su Reddit/forum?
5. DIMENSIONE OPPORTUNITA: quanto vale questo mercato?

Rispondi SOLO con JSON:
{"existing_solutions":[{"name":"nome","what_it_does":"cosa fa","price":"costo","weaknesses":"punti deboli","market_share":"stima"}],"market_gaps":["gap1","gap2"],"failed_attempts":[{"who":"chi","why_failed":"perche"}],"expert_insights":["insight1","insight2"],"market_size_estimate":"stima valore mercato","key_finding":"la scoperta piu' importante in una frase"}
SOLO JSON."""

GENERATION_PROMPT = """Sei il Solution Architect di brAIn, un'organizzazione AI-native.
Genera 3 soluzioni MVP-ready basandoti su:
1. Business Model Canvas (Osterwalder): value prop + segmento + revenue + canali + costi
2. Principio YC "10x better": la soluzione DEVE essere 10x migliore dello status quo su almeno una dimensione
3. "Paradox of Specificity" (First Round): piu' e' specifica per un segmento, piu' e' forte il moat
4. Dossier competitivo fornito: identifica il GAP reale e costruisci su quello

LINGUA: Rispondi SEMPRE in italiano. Tutto in italiano.

VINCOLI: 1 persona, 20h/settimana, competenza tecnica minima. Budget: sotto 200 EUR/mese primo progetto.

REGOLE CRITICHE:
- Il customer_segment DEVE coincidere esattamente con il target_customer del problema
- Sii SPECIFICO: NON "app per PMI" ma "bot Telegram per elettricisti che risponde a query sulla normativa CEI"
- NON proporre soluzioni che gia' esistono e funzionano bene — cerca gli spazi vuoti

Per ogni soluzione fornisci:

BUSINESS MODEL CANVAS:
- title, description
- value_proposition: frase unica — "aiutiamo [target specifico] a [fare X] senza [pain attuale]"
- target_segment, job_to_be_done
- revenue_model: SaaS_mensile | marketplace | one_time | freemium | transactional
- price_point_eur: prezzo EUR/mese con giustificazione
- distribution_channel: come raggiungiamo i primi 100 clienti senza paid ads

MVP SPEC:
- mvp_features: lista 3 funzionalita' MINIME per validare l'ipotesi di valore
- mvp_build_time_days: giorni per costruire MVP con agenti AI (20h/settimana)
- mvp_cost_eur: costo totale MVP (hosting + API + tools)
- unfair_advantage: perche' AI-native batte team tradizionale su questa soluzione
- competitive_gap: cosa mancano ai competitor che noi copriamo

METRICHE:
- monthly_revenue_potential, monthly_burn_rate, competitive_moat
- novelty_score (0-1), opportunity_score (0-1), defensibility_score (0-1)

BOS SOLUTION QUALITY SCORES (0.0-1.0, scala severa):
- uniqueness: penalizza se >3 competitor diretti con feature identiche
- moat_potential: network effects o dati proprietari = 1.0, solo brand = 0.3
- value_multiplier: 10x = 1.0, 5x = 0.7, 2x = 0.4, <2x = 0.1 (scala logaritmica)
- revenue_clarity: SaaS con prezzo definito = 1.0, "valutiamo" = 0.5, "vedremo" = 0.0
- ai_nativeness: togli AI e non funziona = 1.0, togli AI e funziona uguale = 0.0
- simplicity: utente capisce in <10 secondi = 1.0

{preferences_block}

Rispondi SOLO con JSON:
{{"solutions":[{{"title":"titolo specifico","description":"cosa fa in modo specifico","value_proposition":"aiutiamo X a fare Y senza Z","target_segment":"segmento preciso","job_to_be_done":"job da fare","revenue_model":"SaaS_mensile","price_point_eur":29,"distribution_channel":"community LinkedIn + SEO long-tail","mvp_features":["feature 1","feature 2","feature 3"],"mvp_build_time_days":14,"mvp_cost_eur":80,"unfair_advantage":"perche AI-native batte team tradizionale","competitive_gap":"cosa mancano i competitor","monthly_revenue_potential":"500-2000 EUR","monthly_burn_rate":"50 EUR","competitive_moat":"cosa ci rende difendibili","novelty_score":0.7,"opportunity_score":0.8,"defensibility_score":0.6,"uniqueness":0.7,"moat_potential":0.6,"value_multiplier":0.8,"simplicity":0.7,"revenue_clarity":0.8,"ai_nativeness":0.9}}],"ranking_rationale":"perche' hai messo la prima in cima"}}
SOLO JSON."""

SA_FEASIBILITY_PROMPT = """Sei un CTO pragmatico. Valuta la fattibilita' di ogni soluzione dati questi VINCOLI.

LINGUA: Rispondi SEMPRE in italiano.

VINCOLI ATTUALI:
- 1 persona, 20h/settimana, competenza tecnica minima
- Budget: 1000 euro/mese totale, primo progetto sotto 200 euro/mese
- Stack: Claude API, Supabase, Python, Google Cloud Run, Telegram Bot
- Obiettivo: revenue entro 3 mesi

Per ogni soluzione valuta:
- feasibility_score: 0.0-1.0
- complexity: low/medium/high
- time_to_mvp, cost_estimate, tech_stack_fit (0-1)
- biggest_risk, recommended_mvp, nocode_compatible (bool)

Rispondi SOLO con JSON:
{"assessments":[{"solution_title":"","feasibility_score":0.7,"complexity":"medium","time_to_mvp":"3 settimane","cost_estimate":"80 euro/mese","tech_stack_fit":0.8,"biggest_risk":"rischio","recommended_mvp":"cosa costruire","nocode_compatible":true}],"best_feasible":"quale e perche","best_overall":"quale in assoluto"}
SOLO JSON."""


