"""
brAIn module: marketing/agents.py
Auto-extracted from agents_runner.py
"""
from __future__ import annotations
import json, time, re
from datetime import datetime, timezone, timedelta
import requests
from core.config import supabase, claude, TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, logger
from core.utils import log_to_supabase, notify_telegram, get_telegram_chat_id, extract_json
from core.templates import now_rome


def _mkt_card(emoji, title, context, lines):
    """Card Telegram formato brAIn per notifiche marketing."""
    rows = [f"{emoji} *{title}*" + (f" \u2014 {context}" if context else ""), _MKT_SEP]
    for i, l in enumerate(lines):
        if not l:
            rows.append("")
            continue
        pfx = "\u2514" if i == len(lines) - 1 else "\u251c"
        rows.append(l if (l.startswith("\u2514") or l.startswith("\u251c") or l.startswith("\u2501")) else f"{pfx} {l}")
    rows.append(_MKT_SEP)
    return "\n".join(rows)


def _mkt_notify(text, reply_markup=None):
    """Invia card marketing a Mirco (DM)."""
    chat_id = get_telegram_chat_id()
    if not chat_id or not TELEGRAM_BOT_TOKEN:
        return
    payload = {"chat_id": chat_id, "text": "\U0001f3a8 CMO\n" + text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"[MKT_NOTIFY] {e}")


def _mkt_commit(repo, subfolder, filename, content, msg):
    """Commit file in /marketing/{subfolder}/{filename} nel repo progetto."""
    path = f"marketing/{subfolder}/{filename}" if subfolder else f"marketing/{filename}"
    return _commit_to_project_repo(repo, path, content, msg)


def _mkt_load_project(project_id):
    """Carica dati progetto da Supabase. Ritorna dict o None."""
    try:
        r = supabase.table("projects").select("*").eq("id", project_id).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        logger.error(f"[MKT] project load: {e}")
        return None


def _mkt_get_or_create_brand_asset(project_id, target="project"):
    """Ritorna ID brand_assets esistente o ne crea uno nuovo."""
    try:
        r = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if r.data:
            return r.data[0]["id"]
        ins = supabase.table("brand_assets").insert({
            "project_id": project_id, "target": target, "status": "in_progress",
        }).execute()
        return ins.data[0]["id"] if ins.data else None
    except Exception as e:
        logger.error(f"[MKT] brand_asset create: {e}")
        return None


def _mkt_update_brand_asset(asset_id, fields):
    try:
        fields["updated_at"] = now_rome().isoformat()
        supabase.table("brand_assets").update(fields).eq("id", asset_id).execute()
    except Exception as e:
        logger.warning(f"[MKT] brand_asset update: {e}")


# ---- AGENT 1: BRAND & CREATIVE ----

def run_brand_agent(project_id, target="project"):
    """Genera brand DNA, guidelines, logo SVG, kit. Commit su GitHub."""
    start = time.time()
    logger.info(f"[BRAND] Avvio brand_agent project={project_id} target={target}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")
    topic_id = project.get("topic_id")
    group_id = _get_telegram_group_id()

    asset_id = _mkt_get_or_create_brand_asset(project_id, target)

    # Ricerca competitiva per naming
    comp_query = f"top brand names {sector} startup 2026 naming trends"
    comp_info = search_perplexity(comp_query) or ""

    brand_prompt = f"""Sei il Chief Brand Officer di brAIn. Genera il brand DNA completo per questo progetto.

Progetto: {name}
Settore: {sector or "non specificato"}
SPEC (estratto): {spec_md[:2000]}
Ricerca naming mercato: {comp_info[:400]}

RISPONDI con JSON puro:
{{
  "naming_options": [
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}},
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}},
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}},
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}},
    {{"name": "...", "rationale": "...", "domain_availability": "da verificare"}}
  ],
  "recommended_name": "...",
  "tagline": "...",
  "brand_dna": {{
    "mission": "...",
    "vision": "...",
    "values": ["...", "...", "..."],
    "tone_of_voice": "...",
    "persona": "...",
    "positioning": "..."
  }},
  "visual_guidelines": {{
    "primary_color": "#RRGGBB",
    "secondary_color": "#RRGGBB",
    "accent_color": "#RRGGBB",
    "font_heading": "...",
    "font_body": "...",
    "visual_style": "..."
  }},
  "do_list": ["...", "...", "..."],
  "dont_list": ["...", "...", "..."]
}}"""

    tokens_in = tokens_out = 0
    brand_data = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": brand_prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_mkt
        m = _re_mkt.search(r'\{[\s\S]*\}', raw)
        brand_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[BRAND] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    brand_name = brand_data.get("recommended_name") or name
    tagline = brand_data.get("tagline", "")
    dna = brand_data.get("brand_dna", {})
    vis = brand_data.get("visual_guidelines", {})
    naming_options = brand_data.get("naming_options", [])

    # Verifica disponibilità domini via Perplexity
    domain_query = f"domain availability check {' '.join(o['name'] for o in naming_options[:3])} .io .com"
    domain_info = search_perplexity(domain_query) or "verifica manuale consigliata"

    # Genera BRAND_DNA.md
    brand_dna_md = f"""# Brand DNA — {brand_name}
> {tagline}

## Naming — 5 opzioni
{chr(10).join(f"**{i+1}. {o['name']}** — {o['rationale']}" for i, o in enumerate(naming_options))}

Disponibilità domini: {domain_info[:300]}

**Scelta consigliata: {brand_name}**

## Missione
{dna.get('mission', '')}

## Visione
{dna.get('vision', '')}

## Valori
{chr(10).join(f"- {v}" for v in dna.get('values', []))}

## Tone of Voice
{dna.get('tone_of_voice', '')}

## Persona
{dna.get('persona', '')}

## Posizionamento
{dna.get('positioning', '')}
"""

    # Genera BRAND_GUIDELINES.md
    brand_guidelines_md = f"""# Brand Guidelines — {brand_name}

## Palette Colori
- **Primario:** {vis.get('primary_color', '#000000')}
- **Secondario:** {vis.get('secondary_color', '#FFFFFF')}
- **Accento:** {vis.get('accent_color', '#0066FF')}

## Tipografia
- **Heading:** {vis.get('font_heading', 'Inter')}
- **Body:** {vis.get('font_body', 'Inter')}

## Stile Visivo
{vis.get('visual_style', '')}

## DO ✅
{chr(10).join(f"- {d}" for d in brand_data.get('do_list', []))}

## DON'T ❌
{chr(10).join(f"- {d}" for d in brand_data.get('dont_list', []))}
"""

    # Genera logo SVG base
    primary = vis.get('primary_color', '#0066FF')
    secondary = vis.get('secondary_color', '#FFFFFF')
    initials = ''.join(w[0].upper() for w in brand_name.split()[:2]) or brand_name[:2].upper()
    logo_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">
  <rect width="200" height="200" rx="40" fill="{primary}"/>
  <text x="100" y="120" font-family="Arial,sans-serif" font-size="72" font-weight="bold"
        text-anchor="middle" fill="{secondary}">{initials}</text>
</svg>"""

    # BRAND_KIT_SUMMARY.md (1 pagina)
    brand_kit_md = f"""# Brand Kit — {brand_name}
_{tagline}_

**Colori:** {vis.get('primary_color','#000')} (primario) · {vis.get('secondary_color','#fff')} (secondario) · {vis.get('accent_color','#06f')} (accento)
**Font:** {vis.get('font_heading','Inter')} (heading) · {vis.get('font_body','Inter')} (body)
**Missione:** {dna.get('mission','')}
**Tone:** {dna.get('tone_of_voice','')}
**Persona:** {dna.get('persona','')}
**Do:** {' / '.join(brand_data.get('do_list',[])[:3])}
**Don't:** {' / '.join(brand_data.get('dont_list',[])[:3])}
"""

    # Commit su GitHub
    ts = now_rome().strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "brand", "BRAND_DNA.md", brand_dna_md, f"mkt: Brand DNA {brand_name} — {ts}")
        _mkt_commit(github_repo, "brand", "BRAND_GUIDELINES.md", brand_guidelines_md, f"mkt: Brand Guidelines — {ts}")
        _mkt_commit(github_repo, "brand", "logo.svg", logo_svg, f"mkt: Logo SVG — {ts}")
        _mkt_commit(github_repo, "brand", "BRAND_KIT_SUMMARY.md", brand_kit_md, f"mkt: Brand Kit Summary — {ts}")

    # Salva in brand_assets
    if asset_id:
        _mkt_update_brand_asset(asset_id, {"brand_name": brand_name, "tagline": tagline,
                                           "brand_dna_md": brand_dna_md})

    # Notifica Mirco con card + bottone
    card = _mkt_card("\U0001f3a8", "BRAND IDENTITY PRONTA", brand_name, [
        f"Nome consigliato: {brand_name}",
        f"Tagline: {tagline}",
        f"Colore primario: {vis.get('primary_color', 'N/A')}",
        f"Tono: {dna.get('tone_of_voice', 'N/A')[:60]}",
    ])
    _mkt_notify(card, reply_markup={"inline_keyboard": [[
        {"text": "\U0001f4c4 Brand Kit", "callback_data": f"mkt_brand_kit:{project_id}"},
        {"text": "\u27a1\ufe0f Avanti", "callback_data": f"mkt_next:{project_id}:product"},
    ]]})

    # Invia BRAND_KIT_SUMMARY anche come file
    _mkt_send_file(brand_kit_md, f"BRAND_KIT_{brand_name.replace(' ','_')}.md")

    # Aggiorna avatar bot con logo (best-effort)
    _update_bot_avatar_svg(logo_svg)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("brand_agent", "brand_generate", 3,
                    f"project={project_id}", f"brand={brand_name} tagline={tagline}",
                    "claude-sonnet-4-6", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[BRAND] Completato project={project_id} brand={brand_name} in {duration_ms}ms")
    return {"status": "ok", "project_id": project_id, "brand_name": brand_name, "tagline": tagline,
            "asset_id": asset_id, "cost_usd": round(cost, 5)}


def _mkt_send_file(content, filename):
    """Invia file .md a Mirco via sendDocument."""
    chat_id = get_telegram_chat_id()
    if not chat_id or not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
            data={"chat_id": chat_id},
            files={"document": (filename, content.encode("utf-8"), "text/plain")},
            timeout=20,
        )
    except Exception as e:
        logger.warning(f"[MKT_FILE] {e}")


def _update_bot_avatar_svg(svg_content):
    """Aggiorna immagine profilo bot Telegram con logo SVG (best-effort)."""
    # Telegram setChatPhoto richiede PNG/JPEG. Tentiamo inviando come PNG placeholder.
    # In produzione usare libreria Pillow/cairosvg per convertire SVG → PNG.
    # Per ora: solo log dell'intent
    logger.info("[BRAND] Avatar update: richiede conversione SVG→PNG (installare cairosvg+Pillow per deploy)")


# ---- AGENT 2: PRODUCT MARKETING ----

def run_product_marketing_agent(project_id):
    """Genera positioning, messaging, analisi competitiva, sales enablement."""
    start = time.time()
    logger.info(f"[PRODUCT_MKT] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Carica brand DNA se disponibile
    brand_dna_md = ""
    try:
        ba = supabase.table("brand_assets").select("brand_name,brand_dna_md,tagline").eq("project_id", project_id).execute()
        if ba.data:
            brand_dna_md = ba.data[0].get("brand_dna_md", "")
    except:
        pass

    # Ricerca competitiva via Perplexity
    comp_query = f"top 5 competitor '{name}' settore '{sector}' 2026 pricing differenziatori"
    comp_info = search_perplexity(comp_query) or "Dati competitivi non disponibili."

    prompt = f"""Sei il VP Product Marketing di brAIn. Genera framework completo di product marketing.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:2500]}
Brand DNA: {brand_dna_md[:800]}
Dati competitivi: {comp_info[:600]}

Genera in JSON:
{{
  "icp": {{
    "profile": "...",
    "demographics": "...",
    "psychographics": "...",
    "pain_points": ["...", "..."],
    "buying_triggers": ["...", "..."]
  }},
  "value_proposition": "...",
  "unique_differentiators": ["...", "...", "..."],
  "competitors": [
    {{"name": "...", "strengths": "...", "weaknesses": "...", "price": "..."}},
    {{"name": "...", "strengths": "...", "weaknesses": "...", "price": "..."}},
    {{"name": "...", "strengths": "...", "weaknesses": "...", "price": "..."}}
  ],
  "messaging": {{
    "awareness": "...",
    "consideration": "...",
    "decision": "...",
    "retention": "..."
  }},
  "pricing_tiers": [
    {{"name": "...", "price": "...", "features": ["..."], "target": "..."}},
    {{"name": "...", "price": "...", "features": ["..."], "target": "..."}},
    {{"name": "...", "price": "...", "features": ["..."], "target": "..."}}
  ],
  "top_objections": [
    {{"objection": "...", "response": "..."}},
    {{"objection": "...", "response": "..."}},
    {{"objection": "...", "response": "..."}},
    {{"objection": "...", "response": "..."}},
    {{"objection": "...", "response": "..."}}
  ]
}}"""

    tokens_in = tokens_out = 0
    pm_data = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6", max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_mkt2
        m = _re_mkt2.search(r'\{[\s\S]*\}', raw)
        pm_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[PRODUCT_MKT] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    icp = pm_data.get("icp", {})
    competitors = pm_data.get("competitors", [])
    objections = pm_data.get("top_objections", [])
    pricing = pm_data.get("pricing_tiers", [])
    msg_fw = pm_data.get("messaging", {})

    # Genera file
    positioning_md = f"""# Positioning — {name}

## ICP (Ideal Customer Profile)
{icp.get('profile', '')}
- **Demographics:** {icp.get('demographics', '')}
- **Psychographics:** {icp.get('psychographics', '')}
- **Pain points:** {', '.join(icp.get('pain_points', []))}
- **Buying triggers:** {', '.join(icp.get('buying_triggers', []))}

## Value Proposition
{pm_data.get('value_proposition', '')}

## Differenziatori Unici
{chr(10).join(f"- {d}" for d in pm_data.get('unique_differentiators', []))}
"""

    messaging_md = f"""# Messaging Framework — {name}

| Stage | Messaggio |
|-------|-----------|
| Awareness | {msg_fw.get('awareness', '')} |
| Consideration | {msg_fw.get('consideration', '')} |
| Decision | {msg_fw.get('decision', '')} |
| Retention | {msg_fw.get('retention', '')} |
"""

    comp_md = f"""# Analisi Competitiva — {name}

| Competitor | Punti di Forza | Debolezze | Prezzo |
|-----------|----------------|-----------|--------|
{chr(10).join(f"| {c.get('name','')} | {c.get('strengths','')} | {c.get('weaknesses','')} | {c.get('price','')} |" for c in competitors)}
"""

    _obj_lines = "".join(f"**Obiezione {i+1}:** {o.get('objection','')}  \n**Risposta:** {o.get('response','')}\n\n" for i, o in enumerate(objections))
    objections_md = f"""# Objection Handler — {name}

{_obj_lines}"""

    _pricing_lines = "".join(f"## {t.get('name','')} — {t.get('price','')}\n**Target:** {t.get('target','')}\n**Features:** {', '.join(t.get('features',[]))}\n\n" for t in pricing)
    pricing_md = f"""# Pricing Strategy — {name}

{_pricing_lines}"""

    sales_deck_md = f"""# Sales Deck Outline — {name}

1. **Problem** — {icp.get('pain_points', ['pain point'])[0] if icp.get('pain_points') else ''}
2. **Solution** — {pm_data.get('value_proposition', '')[:200]}
3. **Differenziatori** — {', '.join(pm_data.get('unique_differentiators', [])[:3])}
4. **Social proof** — [da aggiungere post-lancio]
5. **Pricing** — {pricing[0].get('name','') if pricing else ''} da {pricing[0].get('price','') if pricing else ''}
6. **CTA** — Inizia gratis / Prenota demo
"""

    # Commit su GitHub
    ts = now_rome().strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "product", "POSITIONING.md", positioning_md, f"mkt: Positioning — {ts}")
        _mkt_commit(github_repo, "product", "MESSAGING_FRAMEWORK.md", messaging_md, f"mkt: Messaging Framework — {ts}")
        _mkt_commit(github_repo, "product", "COMPETITIVE_ANALYSIS.md", comp_md, f"mkt: Competitive Analysis — {ts}")
        _mkt_commit(github_repo, "product", "OBJECTION_HANDLER.md", objections_md, f"mkt: Objection Handler — {ts}")
        _mkt_commit(github_repo, "product", "PRICING_STRATEGY.md", pricing_md, f"mkt: Pricing Strategy — {ts}")
        _mkt_commit(github_repo, "product", "SALES_DECK_OUTLINE.md", sales_deck_md, f"mkt: Sales Deck — {ts}")

    # Salva positioning in brand_assets
    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"positioning_md": positioning_md})
    except:
        pass

    card = _mkt_card("\U0001f3af", "PRODUCT MARKETING PRONTO", name, [
        f"ICP: {icp.get('profile','')[:60]}",
        f"Value prop: {pm_data.get('value_proposition','')[:60]}",
        f"Competitor analizzati: {len(competitors)}",
        f"Tier pricing: {len(pricing)}",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("product_marketing_agent", "pm_generate", 3, f"project={project_id}",
                    f"icp={icp.get('profile','')[:80]}", "claude-sonnet-4-6", tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[PRODUCT_MKT] Completato project={project_id} in {duration_ms}ms")
    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 3: CONTENT & SEO ----

def run_content_agent(project_id):
    """Genera copy kit, email sequences, SEO strategy, editorial calendar."""
    start = time.time()
    logger.info(f"[CONTENT] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Carica positioning se disponibile
    positioning_ctx = ""
    try:
        ba = supabase.table("brand_assets").select("positioning_md,brand_dna_md").eq("project_id", project_id).execute()
        if ba.data:
            positioning_ctx = ba.data[0].get("positioning_md", "")[:600]
    except:
        pass

    # SEO keyword research via Perplexity
    seo_query = f"top keyword {name} {sector} SEO 2026 search volume intent"
    seo_info = search_perplexity(seo_query) or ""

    prompt = f"""Sei il Content Director di brAIn. Genera tutto il copy e la strategia SEO.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1500]}
Positioning: {positioning_ctx}
SEO research: {seo_info[:400]}

Genera JSON:
{{
  "headline": "...",
  "subheadline": "...",
  "cta_primary": "...",
  "cta_secondary": "...",
  "elevator_pitch": "...",
  "one_liner": "...",
  "about_us": "...",
  "seo_keywords": [
    {{"keyword": "...", "intent": "informational/commercial/transactional", "difficulty": "low/medium/high"}},
    {{"keyword": "...", "intent": "...", "difficulty": "..."}},
    {{"keyword": "...", "intent": "...", "difficulty": "..."}},
    {{"keyword": "...", "intent": "...", "difficulty": "..."}},
    {{"keyword": "...", "intent": "...", "difficulty": "..."}}
  ],
  "blog_post_title": "...",
  "blog_post_content": "...",
  "email_onboarding": [
    {{"subject": "...", "preview": "...", "body_summary": "..."}},
    {{"subject": "...", "preview": "...", "body_summary": "..."}},
    {{"subject": "...", "preview": "...", "body_summary": "..."}},
    {{"subject": "...", "preview": "...", "body_summary": "..."}},
    {{"subject": "...", "preview": "...", "body_summary": "..."}}
  ],
  "cold_outreach_template": "..."
}}"""

    tokens_in = tokens_out = 0
    cnt_data = {}
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6", max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_cnt
        m = _re_cnt.search(r'\{[\s\S]*\}', raw)
        cnt_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[CONTENT] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

    keywords = cnt_data.get("seo_keywords", [])
    emails = cnt_data.get("email_onboarding", [])

    copy_kit_md = f"""# Copy Kit — {name}

## Headline
{cnt_data.get('headline', '')}

## Subheadline
{cnt_data.get('subheadline', '')}

## CTA Primaria
{cnt_data.get('cta_primary', '')}

## CTA Secondaria
{cnt_data.get('cta_secondary', '')}

## Elevator Pitch (30 secondi)
{cnt_data.get('elevator_pitch', '')}

## One-liner
{cnt_data.get('one_liner', '')}

## About Us
{cnt_data.get('about_us', '')}
"""

    _email_lines = "".join(f"### Email {i+1}: {e.get('subject','')}\n*Preview:* {e.get('preview','')}\n{e.get('body_summary','')}\n\n" for i, e in enumerate(emails))
    email_md = f"""# Email Sequences — {name}

## Onboarding (5 email)
{_email_lines}

## Cold Outreach Template
{cnt_data.get('cold_outreach_template', '')}
"""

    _seo_content_lines = "".join(f"- Articolo SEO: \"{k.get('keyword','')}\" ({k.get('intent','')})\n" for k in keywords[:5])
    seo_md = f"""# SEO Strategy — {name}

## Top Keywords
| Keyword | Intent | Difficoltà |
|---------|--------|-----------|
{chr(10).join(f"| {k.get('keyword','')} | {k.get('intent','')} | {k.get('difficulty','')} |" for k in keywords)}

## Content da Creare
{_seo_content_lines}"""

    # Blog post SEO
    blog_md = f"""# {cnt_data.get('blog_post_title', f'Blog Post — {name}')}

{cnt_data.get('blog_post_content', '')}
"""

    # Editorial calendar 90gg
    editorial_md = f"""# Editorial Calendar — {name} (90 giorni)

## Settimane 1-4 (Lancio)
- Settimana 1: Blog post "{cnt_data.get('blog_post_title','')}"
- Settimana 2: Email onboarding setup
- Settimana 3: Cold outreach batch 1
- Settimana 4: Review performance, ottimizza CTA

## Settimane 5-8 (Crescita)
- Articoli SEO su keyword priorità 2-3
- Email nurturing su lead freddi
- A/B test headline landing page

## Settimane 9-12 (Scale)
- 2 articoli/settimana
- Newsletter settimanale ai subscriber
- Case study primo cliente
"""

    landing_copy_md = f"""# Landing Page Copy — {name}

## HERO
**Headline:** {cnt_data.get('headline', '')}
**Subheadline:** {cnt_data.get('subheadline', '')}
**CTA:** {cnt_data.get('cta_primary', '')}

## VALUE PROP
{cnt_data.get('elevator_pitch', '')}

## CTA FOOTER
{cnt_data.get('cta_secondary', '')}
"""

    ts = now_rome().strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "content", "COPY_KIT.md", copy_kit_md, f"mkt: Copy Kit — {ts}")
        _mkt_commit(github_repo, "content", "EMAIL_SEQUENCES.md", email_md, f"mkt: Email Sequences — {ts}")
        _mkt_commit(github_repo, "content", "LANDING_PAGE_COPY.md", landing_copy_md, f"mkt: Landing Copy — {ts}")
        _mkt_commit(github_repo, "content", "SEO_STRATEGY.md", seo_md, f"mkt: SEO Strategy — {ts}")
        _mkt_commit(github_repo, "content", "EDITORIAL_CALENDAR.md", editorial_md, f"mkt: Editorial Calendar — {ts}")
        _mkt_commit(github_repo, "content", "BLOG_POST_1.md", blog_md, f"mkt: Blog Post 1 — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"content_kit_md": copy_kit_md})
    except:
        pass

    card = _mkt_card("\u270d\ufe0f", "CONTENT & SEO PRONTO", name, [
        f"Headline: {cnt_data.get('headline','')[:60]}",
        f"Keyword SEO: {len(keywords)} identificate",
        f"Email onboarding: {len(emails)} scritte",
        "Blog post 1 pronto da pubblicare",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("content_agent", "content_generate", 3, f"project={project_id}",
                    f"keywords={len(keywords)} emails={len(emails)}", "claude-sonnet-4-6", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 4: DEMAND GENERATION ----

def run_demand_gen_agent(project_id):
    """Genera growth strategy, paid media plan, funnel map, email automation."""
    start = time.time()
    logger.info(f"[DEMAND_GEN] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Ricerca canali più efficaci per il settore
    channel_query = f"best acquisition channels {sector} B2B B2C 2026 CAC benchmark"
    channel_info = search_perplexity(channel_query) or ""

    prompt = f"""Sei il Head of Growth di brAIn. Genera strategia demand generation completa.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1500]}
Ricerca canali: {channel_info[:400]}

Genera JSON:
{{
  "top_channels": [
    {{"channel": "...", "score": 8, "cac_estimate_eur": 0, "rationale": "..."}},
    {{"channel": "...", "score": 7, "cac_estimate_eur": 0, "rationale": "..."}},
    {{"channel": "...", "score": 6, "cac_estimate_eur": 0, "rationale": "..."}}
  ],
  "paid_platforms": [
    {{"platform": "...", "budget_pct": 0, "targeting": "...", "ad_format": "..."}},
    {{"platform": "...", "budget_pct": 0, "targeting": "...", "ad_format": "..."}}
  ],
  "funnel_stages": {{
    "tofu": {{"content": "...", "cta": "...", "conversion_target": "..."}},
    "mofu": {{"content": "...", "cta": "...", "conversion_target": "..."}},
    "bofu": {{"content": "...", "cta": "...", "conversion_target": "..."}}
  }},
  "ab_tests": [
    {{"test": "...", "hypothesis": "...", "priority": "high/medium"}},
    {{"test": "...", "hypothesis": "...", "priority": "high/medium"}},
    {{"test": "...", "hypothesis": "...", "priority": "high/medium"}}
  ],
  "kpi": {{"cac_target_eur": 0, "ltv_estimate_eur": 0, "month3_users_target": 0}}
}}"""

    tokens_in = tokens_out = 0
    dg_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_dg
        m = _re_dg.search(r'\{[\s\S]*\}', raw)
        dg_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[DEMAND_GEN] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    channels = dg_data.get("top_channels", [])
    paid = dg_data.get("paid_platforms", [])
    funnel = dg_data.get("funnel_stages", {})
    ab_tests = dg_data.get("ab_tests", [])
    kpi = dg_data.get("kpi", {})

    growth_md = f"""# Growth Strategy — {name}

## Top 5 Canali (score costo/efficacia)
| Canale | Score | CAC Est. | Rationale |
|--------|-------|----------|-----------|
{chr(10).join(f"| {c.get('channel','')} | {c.get('score',0)}/10 | €{c.get('cac_estimate_eur',0)} | {c.get('rationale','')[:60]} |" for c in channels)}

## KPI Target
- CAC target: €{kpi.get('cac_target_eur',0)}
- LTV stimato: €{kpi.get('ltv_estimate_eur',0)}
- Utenti mese 3: {kpi.get('month3_users_target',0)}

## Piano 30/60/90 giorni
- **Giorno 1-30:** Setup tracking, lancio 1 canale principale, 10 lead
- **Giorno 31-60:** Ottimizza CAC, aggiungi 2° canale, 50 lead
- **Giorno 61-90:** Scale canale migliore, A/B test, 200 lead
"""

    _paid_lines = "".join(f"## {p.get('platform','')} ({p.get('budget_pct',0)}% budget)\n- Targeting: {p.get('targeting','')}\n- Formato: {p.get('ad_format','')}\n\n" for p in paid)
    paid_md = f"""# Paid Media Plan — {name}

{_paid_lines}"""

    funnel_md = f"""# Funnel Map — {name}

## TOFU (Top of Funnel — Awareness)
{funnel.get('tofu', {}).get('content', '')}
CTA: {funnel.get('tofu', {}).get('cta', '')}
Target conversion: {funnel.get('tofu', {}).get('conversion_target', '')}

## MOFU (Middle of Funnel — Consideration)
{funnel.get('mofu', {}).get('content', '')}
CTA: {funnel.get('mofu', {}).get('cta', '')}
Target conversion: {funnel.get('mofu', {}).get('conversion_target', '')}

## BOFU (Bottom of Funnel — Decision)
{funnel.get('bofu', {}).get('content', '')}
CTA: {funnel.get('bofu', {}).get('cta', '')}
Target conversion: {funnel.get('bofu', {}).get('conversion_target', '')}
"""

    ab_md = f"""# A/B Test Plan — {name}

| Test | Ipotesi | Priorità |
|------|---------|----------|
{chr(10).join(f"| {t.get('test','')} | {t.get('hypothesis','')} | {t.get('priority','')} |" for t in ab_tests)}
"""

    email_auto_md = f"""# Email Automation — {name}

## Sequenze Trigger-Based
- **Onboarding** (trigger: signup) → 5 email in 14 giorni
- **Win-back** (trigger: 30gg inattività) → 3 email
- **Upsell** (trigger: 60gg attivo) → 2 email
- **Referral** (trigger: successo feature chiave) → 1 email

Vedi EMAIL_SEQUENCES.md nel folder /content per i copy completi.
"""

    ts = now_rome().strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "demand_gen", "GROWTH_STRATEGY.md", growth_md, f"mkt: Growth Strategy — {ts}")
        _mkt_commit(github_repo, "demand_gen", "PAID_MEDIA_PLAN.md", paid_md, f"mkt: Paid Media — {ts}")
        _mkt_commit(github_repo, "demand_gen", "FUNNEL_MAP.md", funnel_md, f"mkt: Funnel Map — {ts}")
        _mkt_commit(github_repo, "demand_gen", "AB_TEST_PLAN.md", ab_md, f"mkt: A/B Tests — {ts}")
        _mkt_commit(github_repo, "demand_gen", "EMAIL_AUTOMATION.md", email_auto_md, f"mkt: Email Automation — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"growth_strategy_md": growth_md})
    except:
        pass

    card = _mkt_card("\U0001f4e3", "DEMAND GEN PRONTO", name, [
        f"Canali top: {', '.join(c.get('channel','') for c in channels[:3])}",
        f"CAC target: €{kpi.get('cac_target_eur',0)}",
        f"A/B test pianificati: {len(ab_tests)}",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("demand_gen_agent", "demand_gen_generate", 3, f"project={project_id}",
                    f"channels={len(channels)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 5: SOCIAL MEDIA ----

def run_social_agent(project_id):
    """Identifica canali social giusti, genera strategy e template post."""
    start = time.time()
    logger.info(f"[SOCIAL] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Identifica canali più attivi nel settore
    social_query = f"social media channels most active {sector} target audience 2026 engagement"
    social_info = search_perplexity(social_query) or ""

    prompt = f"""Sei il Social Media Director di brAIn. Genera strategia social completa.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1000]}
Ricerca social: {social_info[:400]}

Genera JSON:
{{
  "selected_channels": [
    {{"channel": "...", "reason": "...", "frequency": "...", "tone": "..."}},
    {{"channel": "...", "reason": "...", "frequency": "...", "tone": "..."}}
  ],
  "content_templates": [
    {{"channel": "...", "type": "...", "template": "...", "visual_note": "..."}},
    {{"channel": "...", "type": "...", "template": "...", "visual_note": "..."}},
    {{"channel": "...", "type": "...", "template": "...", "visual_note": "..."}}
  ],
  "hashtag_sets": {{
    "brand": ["#...", "#...", "#..."],
    "sector": ["#...", "#...", "#..."],
    "niche": ["#...", "#...", "#..."]
  }},
  "launch_posts": [
    {{"channel": "...", "text": "...", "visual_note": "..."}},
    {{"channel": "...", "text": "...", "visual_note": "..."}},
    {{"channel": "...", "text": "...", "visual_note": "..."}}
  ]
}}"""

    tokens_in = tokens_out = 0
    soc_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_soc
        m = _re_soc.search(r'\{[\s\S]*\}', raw)
        soc_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[SOCIAL] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    channels = soc_data.get("selected_channels", [])
    templates = soc_data.get("content_templates", [])
    hashtags = soc_data.get("hashtag_sets", {})
    launch_posts = soc_data.get("launch_posts", [])

    _soc_channel_lines = "".join(f"### {c.get('channel','')}\n- Motivazione: {c.get('reason','')}\n- Frequenza: {c.get('frequency','')}\n- Tono: {c.get('tone','')}\n\n" for c in channels)
    social_strategy_md = f"""# Social Strategy — {name}

## Canali Selezionati
{_soc_channel_lines}"""

    _tmpl_lines = "".join(f"## Template {i+1} — {t.get('channel','')} ({t.get('type','')})\n```\n{t.get('template','')}\n```\n_Note visual: {t.get('visual_note','')}_\n\n" for i, t in enumerate(templates))
    templates_md = f"""# Content Templates — {name}

{_tmpl_lines}"""

    hashtag_md = f"""# Hashtag Strategy — {name}

## Brand: {' '.join(hashtags.get('brand', []))}
## Settore: {' '.join(hashtags.get('sector', []))}
## Nicchia: {' '.join(hashtags.get('niche', []))}

**Mix consigliato per post:** 3 brand + 4 settore + 3 nicchia = 10 hashtag
"""

    community_md = f"""# Community Playbook — {name}

## Rispondere ai Commenti
- Rispondere entro 2h nei giorni lavorativi
- Tono: {channels[0].get('tone','') if channels else 'professionale ma accessibile'}
- Escalation problemi: tagga @team

## Gestione Crisi
1. Non eliminare commenti negativi
2. Rispondere pubblicamente: "Capisco la tua preoccupazione, ti contatto in privato"
3. Risolvere in DM, poi follow-up pubblico

## Reward Advocates
- Like e repost contenuti utenti positivi
- DM personale ai top advocates
- Tag in post se usano il prodotto
"""

    _post_lines = "".join(f"## Post {i+1} — {p.get('channel','')}\n{p.get('text','')}\n_Visual: {p.get('visual_note','')}_\n\n" for i, p in enumerate(launch_posts))
    launch_posts_md = f"""# Launch Posts — {name}

{_post_lines}"""

    ts = now_rome().strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "social", "SOCIAL_STRATEGY.md", social_strategy_md, f"mkt: Social Strategy — {ts}")
        _mkt_commit(github_repo, "social", "CONTENT_TEMPLATES.md", templates_md, f"mkt: Content Templates — {ts}")
        _mkt_commit(github_repo, "social", "HASHTAG_STRATEGY.md", hashtag_md, f"mkt: Hashtags — {ts}")
        _mkt_commit(github_repo, "social", "COMMUNITY_PLAYBOOK.md", community_md, f"mkt: Community Playbook — {ts}")
        _mkt_commit(github_repo, "social", "LAUNCH_POSTS.md", launch_posts_md, f"mkt: Launch Posts — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"social_strategy_md": social_strategy_md})
    except:
        pass

    card = _mkt_card("\U0001f4f1", "SOCIAL MEDIA PRONTO", name, [
        f"Canali: {', '.join(c.get('channel','') for c in channels)}",
        f"Template: {len(templates)} | Launch posts: {len(launch_posts)}",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("social_agent", "social_generate", 3, f"project={project_id}",
                    f"channels={len(channels)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 6: PR & COMUNICAZIONE ----

def run_pr_agent(project_id):
    """Genera press kit, media list, press release, outreach sequence."""
    start = time.time()
    logger.info(f"[PR] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    # Identifica media target via Perplexity
    media_query = f"top media blogger giornalisti tech startup {sector} Italia 2026 contatti pitch"
    media_info = search_perplexity(media_query) or ""

    prompt = f"""Sei il PR Director di brAIn. Genera materiali PR completi.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1200]}
Media landscape: {media_info[:400]}

Genera JSON:
{{
  "company_overview": "...",
  "founder_quote": "...",
  "product_description": "...",
  "key_stats": ["...", "...", "..."],
  "media_faq": [
    {{"q": "...", "a": "..."}},
    {{"q": "...", "a": "..."}}
  ],
  "media_targets": [
    {{"name": "...", "type": "blog/magazine/newsletter", "angle": "...", "contact": "..."}},
    {{"name": "...", "type": "...", "angle": "...", "contact": "..."}},
    {{"name": "...", "type": "...", "angle": "...", "contact": "..."}}
  ],
  "press_release_title": "...",
  "press_release_body": "...",
  "outreach_email_subject": "...",
  "outreach_email_body": "..."
}}"""

    tokens_in = tokens_out = 0
    pr_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_pr
        m = _re_pr.search(r'\{[\s\S]*\}', raw)
        pr_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[PR] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    media_targets = pr_data.get("media_targets", [])
    _press_faq_lines = "".join(f"**Q:** {fq.get('q','')}  \n**A:** {fq.get('a','')}\n\n" for fq in pr_data.get('media_faq', []))

    press_kit_md = f"""# Press Kit — {name}

## Company Overview
{pr_data.get('company_overview', '')}

## Descrizione Prodotto
{pr_data.get('product_description', '')}

## Key Stats
{chr(10).join(f"- {s}" for s in pr_data.get('key_stats', []))}

## Quote Fondatore
_{pr_data.get('founder_quote', '')}_

## FAQ Media
{_press_faq_lines}
"""

    media_list_md = f"""# Media List — {name}

| Media | Tipo | Angle Suggerito | Contatto |
|-------|------|----------------|---------|
{chr(10).join(f"| {m.get('name','')} | {m.get('type','')} | {m.get('angle','')} | {m.get('contact','')} |" for m in media_targets)}
"""

    press_release_md = f"""# {pr_data.get('press_release_title', f'LANCIO — {name}')}

{pr_data.get('press_release_body', '')}

---
_Per informazioni: [contatto press]_
"""

    outreach_md = f"""# PR Outreach Sequence — {name}

## Email 1 — Pitch Iniziale
**Oggetto:** {pr_data.get('outreach_email_subject', '')}

{pr_data.get('outreach_email_body', '')}

## Email 2 — Follow-up (7 giorni dopo)
Oggetto: Re: {pr_data.get('outreach_email_subject', '')}
"Volevo assicurarmi che la mia email precedente non fosse andata persa..."

## Email 3 — Ultimo tentativo (14 giorni dopo)
"Ultima email da parte mia — capisco che sia molto occupato..."
"""

    crisis_md = f"""# Crisis Comms Playbook — {name}

## Principi Fondamentali
1. Rispondere entro 2h da menzione negativa significativa
2. Non eliminare mai contenuti negativi legittimi
3. Ammettere errori onestamente quando necessario

## Protocollo
1. **Valuta**: critica legittima o trolling?
2. **Rispondi**: tono empatico, no difensivo
3. **Risolvi**: offri soluzione concreta
4. **Follow-up**: verifica che l'issue sia chiuso

## Template Risposta Crisi
"Grazie per il feedback. Capisco la tua frustrazione con [issue]. Stiamo [azione] per risolvere. Ti contatto in privato per aiutarti direttamente."
"""

    ts = now_rome().strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "pr", "PRESS_KIT.md", press_kit_md, f"mkt: Press Kit — {ts}")
        _mkt_commit(github_repo, "pr", "MEDIA_LIST.md", media_list_md, f"mkt: Media List — {ts}")
        _mkt_commit(github_repo, "pr", "PRESS_RELEASE_LAUNCH.md", press_release_md, f"mkt: Press Release — {ts}")
        _mkt_commit(github_repo, "pr", "PR_OUTREACH_SEQUENCE.md", outreach_md, f"mkt: PR Outreach — {ts}")
        _mkt_commit(github_repo, "pr", "CRISIS_COMMS_PLAYBOOK.md", crisis_md, f"mkt: Crisis Comms — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"pr_kit_md": press_kit_md})
    except:
        pass

    card = _mkt_card("\U0001f4f0", "PR KIT PRONTO", name, [
        f"Media target: {len(media_targets)}",
        "Press release pronto",
        "Crisis playbook pronto",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("pr_agent", "pr_generate", 3, f"project={project_id}",
                    f"media={len(media_targets)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 7: CUSTOMER MARKETING ----

def run_customer_marketing_agent(project_id):
    """Genera onboarding journey, retention, referral, upsell strategies."""
    start = time.time()
    logger.info(f"[CUSTOMER_MKT] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    prompt = f"""Sei il Customer Marketing Director di brAIn. Genera strategia lifecycle completa.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1200]}

Genera JSON:
{{
  "onboarding_steps": [
    {{"day": 0, "action": "...", "goal": "...", "metric": "..."}},
    {{"day": 1, "action": "...", "goal": "...", "metric": "..."}},
    {{"day": 7, "action": "...", "goal": "...", "metric": "..."}},
    {{"day": 14, "action": "...", "goal": "...", "metric": "..."}}
  ],
  "aha_moment": "...",
  "retention_tactics": ["...", "...", "...", "...", "..."],
  "churn_signals": ["...", "...", "..."],
  "referral_mechanic": "...",
  "referral_incentive": "...",
  "upsell_triggers": ["...", "...", "..."],
  "upsell_message": "..."
}}"""

    tokens_in = tokens_out = 0
    cm_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_cm
        m = _re_cm.search(r'\{[\s\S]*\}', raw)
        cm_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[CUSTOMER_MKT] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    onboarding_steps = cm_data.get("onboarding_steps", [])

    _onb_steps_lines = "".join(f"### Giorno {s.get('day',0)}\n**Azione:** {s.get('action','')}\n**Goal:** {s.get('goal','')}\n**Metrica:** {s.get('metric','')}\n\n" for s in onboarding_steps)
    onboarding_md = f"""# Onboarding Journey — {name}

## Aha Moment
_{cm_data.get('aha_moment', '')}_

## Step-by-Step
{_onb_steps_lines}"""

    retention_md = f"""# Retention Playbook — {name}

## Tattiche Anti-Churn
{chr(10).join(f"- {t}" for t in cm_data.get('retention_tactics', []))}

## Segnali da Monitorare
{chr(10).join(f"- ⚠️ {s}" for s in cm_data.get('churn_signals', []))}
"""

    referral_md = f"""# Referral Program — {name}

## Meccanica
{cm_data.get('referral_mechanic', '')}

## Incentivo
{cm_data.get('referral_incentive', '')}

## Copy Landing Referral
"Invita un amico e {cm_data.get('referral_incentive', 'ottieni un bonus')}"
"""

    upsell_md = f"""# Upsell Strategy — {name}

## Trigger per Proposta Upgrade
{chr(10).join(f"- {t}" for t in cm_data.get('upsell_triggers', []))}

## Messaggio Upsell
_{cm_data.get('upsell_message', '')}_
"""

    churn_md = f"""# Churn Prevention — {name}

## Segnali di Abbandono
{chr(10).join(f"- 🚨 {s}" for s in cm_data.get('churn_signals', []))}

## Azioni Automatiche
- Segnale 1 → Email re-engagement personalizzata
- Segnale 2 → DM da founder
- Segnale 3 → Offerta speciale + call
"""

    ts = now_rome().strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "customer", "ONBOARDING_JOURNEY.md", onboarding_md, f"mkt: Onboarding — {ts}")
        _mkt_commit(github_repo, "customer", "RETENTION_PLAYBOOK.md", retention_md, f"mkt: Retention — {ts}")
        _mkt_commit(github_repo, "customer", "REFERRAL_PROGRAM.md", referral_md, f"mkt: Referral — {ts}")
        _mkt_commit(github_repo, "customer", "UPSELL_STRATEGY.md", upsell_md, f"mkt: Upsell — {ts}")
        _mkt_commit(github_repo, "customer", "CHURN_PREVENTION.md", churn_md, f"mkt: Churn Prevention — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {"customer_marketing_md": onboarding_md})
    except:
        pass

    card = _mkt_card("\U0001f91d", "CUSTOMER MARKETING PRONTO", name, [
        f"Aha moment: {cm_data.get('aha_moment','')[:60]}",
        f"Tattiche retention: {len(cm_data.get('retention_tactics',[]))}",
        "Referral + upsell strategy pronti",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("customer_marketing_agent", "customer_mkt_generate", 3, f"project={project_id}",
                    f"steps={len(onboarding_steps)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- AGENT 8: MARKETING OPERATIONS ----

def run_marketing_ops_agent(project_id):
    """Genera tracking plan, attribution model, KPI dashboard, martech stack."""
    start = time.time()
    logger.info(f"[MKT_OPS] Avvio project={project_id}")

    project = _mkt_load_project(project_id)
    if not project:
        return {"status": "error", "error": "project not found"}

    name = project.get("name", f"Progetto {project_id}")
    spec_md = project.get("spec_md", "")
    sector = project.get("sector", "")
    github_repo = project.get("github_repo", "")

    prompt = f"""Sei il Marketing Ops Lead di brAIn. Genera sistema di misurazione completo.

Progetto: {name} | Settore: {sector}
SPEC: {spec_md[:1000]}

Genera JSON:
{{
  "tracking_events": [
    {{"event": "...", "trigger": "...", "properties": ["...", "..."]}},
    {{"event": "...", "trigger": "...", "properties": ["...", "..."]}},
    {{"event": "...", "trigger": "...", "properties": ["...", "..."]}}
  ],
  "utm_convention": "...",
  "north_star_metric": "...",
  "kpis": [
    {{"kpi": "...", "frequency": "daily/weekly", "target": "...", "tool": "..."}},
    {{"kpi": "...", "frequency": "...", "target": "...", "tool": "..."}},
    {{"kpi": "...", "frequency": "...", "target": "...", "tool": "..."}}
  ],
  "martech_stack": [
    {{"tool": "...", "purpose": "...", "cost_eur": 0, "priority": "must/nice"}},
    {{"tool": "...", "purpose": "...", "cost_eur": 0, "priority": "must/nice"}},
    {{"tool": "...", "purpose": "...", "cost_eur": 0, "priority": "must/nice"}}
  ],
  "attribution_model": "...",
  "attribution_rationale": "..."
}}"""

    tokens_in = tokens_out = 0
    ops_data = {}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
        import re as _re_ops
        m = _re_ops.search(r'\{[\s\S]*\}', raw)
        ops_data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        logger.error(f"[MKT_OPS] Claude: {e}")
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    tracking_events = ops_data.get("tracking_events", [])
    kpis = ops_data.get("kpis", [])
    martech = ops_data.get("martech_stack", [])

    tracking_md = f"""# Tracking Plan — {name}

## UTM Convention
`{ops_data.get('utm_convention', 'utm_source=canale&utm_medium=tipo&utm_campaign=nome')}`

## Eventi da Tracciare
| Evento | Trigger | Properties |
|--------|---------|-----------|
{chr(10).join(f"| {e.get('event','')} | {e.get('trigger','')} | {', '.join(e.get('properties',[]))} |" for e in tracking_events)}
"""

    attribution_md = f"""# Attribution Model — {name}

## Modello: {ops_data.get('attribution_model', 'Last-touch')}
{ops_data.get('attribution_rationale', '')}

## Setup Consigliato
- Usa UTM su tutti i link
- Tieni source nel cookie per 30 giorni
- Primo touchpoint per awareness, ultimo per conversione
"""

    kpi_dashboard_md = f"""# Marketing KPI Dashboard — {name}

## North Star Metric
**{ops_data.get('north_star_metric', '')}**

## KPI da Monitorare
| KPI | Frequenza | Target | Tool |
|-----|-----------|--------|------|
{chr(10).join(f"| {k.get('kpi','')} | {k.get('frequency','')} | {k.get('target','')} | {k.get('tool','')} |" for k in kpis)}
"""

    martech_md = f"""# Martech Stack — {name}

| Tool | Scopo | Costo/mese | Priorità |
|------|-------|-----------|---------|
{chr(10).join(f"| {t.get('tool','')} | {t.get('purpose','')} | €{t.get('cost_eur',0)} | {t.get('priority','')} |" for t in martech)}

**Costo totale stimato (must-have only):** €{sum(t.get('cost_eur',0) for t in martech if t.get('priority')=='must')}/mese
"""

    ts = now_rome().strftime("%Y-%m-%d")
    if github_repo:
        _mkt_commit(github_repo, "ops", "TRACKING_PLAN.md", tracking_md, f"mkt: Tracking Plan — {ts}")
        _mkt_commit(github_repo, "ops", "ATTRIBUTION_MODEL.md", attribution_md, f"mkt: Attribution — {ts}")
        _mkt_commit(github_repo, "ops", "MARKETING_KPI_DASHBOARD.md", kpi_dashboard_md, f"mkt: KPI Dashboard — {ts}")
        _mkt_commit(github_repo, "ops", "MARTECH_STACK.md", martech_md, f"mkt: Martech Stack — {ts}")

    try:
        ba = supabase.table("brand_assets").select("id").eq("project_id", project_id).execute()
        if ba.data:
            _mkt_update_brand_asset(ba.data[0]["id"], {
                "marketing_ops_md": kpi_dashboard_md, "status": "completed"
            })
    except:
        pass

    card = _mkt_card("\U0001f4ca", "MARKETING OPS PRONTO", name, [
        f"North Star: {ops_data.get('north_star_metric','')[:60]}",
        f"KPI monitorati: {len(kpis)}",
        f"Martech stack: {len(martech)} tool",
    ])
    _mkt_notify(card)

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("marketing_ops_agent", "ops_generate", 3, f"project={project_id}",
                    f"kpis={len(kpis)}", "claude-haiku-4-5-20251001", tokens_in, tokens_out, cost, duration_ms)

    return {"status": "ok", "project_id": project_id, "cost_usd": round(cost, 5)}


# ---- MARKETING REPORT SETTIMANALE ----

def generate_marketing_report(project_id=None):
    """Report settimanale marketing. Solo se ci sono dati post-deploy."""
    start = time.time()
    logger.info(f"[MKT_REPORT] Avvio project={project_id}")

    # Cerca tutti i progetti attivi se project_id non specificato
    try:
        if project_id:
            projects = supabase.table("projects").select("id,name,status").eq("id", project_id).execute().data or []
        else:
            projects = supabase.table("projects").select("id,name,status").not_.in_(
                "status", ["init", "archived"]
            ).execute().data or []
    except:
        projects = []

    reported = 0
    for proj in projects:
        pid = proj["id"]
        pname = proj.get("name", f"Progetto {pid}")

        # Recupera metriche smoke test (proxy per metriche reali pre-deploy)
        try:
            st = supabase.table("smoke_tests").select("*").eq("project_id", pid).order("started_at", desc=True).limit(1).execute().data or []
        except:
            st = []

        if not st:
            continue  # Nessun dato, silenzio

        smoke = st[0]
        visits = smoke.get("landing_visits", 0) or 0
        conv = smoke.get("conversion_rate", 0) or 0
        messages = smoke.get("messages_sent", 0) or 0
        forms = smoke.get("forms_compiled", 0) or 0

        if visits == 0 and messages == 0:
            continue  # Nessun dato reale

        cac_est = round(50 / max(forms, 1), 2) if forms > 0 else None  # stima €50 costo / form
        north_star = conv

        # Salva in marketing_reports
        week_start = (now_rome() - timedelta(days=now_rome().weekday())).strftime("%Y-%m-%d")
        try:
            supabase.table("marketing_reports").insert({
                "project_id": pid,
                "week_start": week_start,
                "landing_visits": visits,
                "cac_eur": cac_est,
                "email_open_rate": None,
                "conversion_rate": conv,
                "north_star_value": north_star,
            }).execute()
        except Exception as e:
            logger.warning(f"[MKT_REPORT] insert: {e}")

        # Manda card a Mirco
        sep = _MKT_SEP
        report_text = (
            f"\U0001f4ca *MARKETING REPORT \u2014 {pname}*\n{sep}\n"
            f"\U0001f3af Visite landing:     {visits}\n"
            f"\U0001f4b6 CAC medio:          {'€' + str(cac_est) if cac_est else 'N/A'}\n"
            f"\U0001f4e7 Messaggi inviati:   {messages}\n"
            f"\U0001f504 Conversion rate:    {conv:.1f}%\n"
            f"\u2514 North Star Metric:   {north_star:.2f}\n"
            f"{sep}"
        )
        reply_markup = {"inline_keyboard": [[
            {"text": "\U0001f4cb Dettaglio canali", "callback_data": f"mkt_report_detail:{pid}"},
            {"text": "\U0001f4c8 Trend", "callback_data": f"mkt_report_trend:{pid}"},
            {"text": "\u26a1 Ottimizza", "callback_data": f"mkt_report_optimize:{pid}"},
        ]]}
        _mkt_notify(report_text, reply_markup=reply_markup)
        reported += 1

    duration_ms = int((time.time() - start) * 1000)
    log_to_supabase("marketing_ops_agent", "marketing_report", 1,
                    f"projects={len(projects)}", f"reported={reported}", "none", 0, 0, 0, duration_ms)

    return {"status": "ok", "reported_projects": reported}


# ---- MARKETING COORDINATOR ----

def run_marketing(project_id=None, target="project", phase="full"):
    """Orchestratore CMO-level. Esegue gli 8 agenti in sequenza/parallelo.
    phase: full | brand | gtm | retention
    target: project | brain
    """
    import threading as _mkt_threading

    logger.info(f"[MARKETING] Avvio coordinator project={project_id} target={target} phase={phase}")

    if project_id is None:
        # Crea/usa progetto brAIn stesso
        try:
            r = supabase.table("brand_assets").select("id,project_id").eq("target", "brain").execute()
            if r.data:
                project_id = r.data[0].get("project_id")
        except:
            pass
        if not project_id:
            # Inserisci record dummy per brand brAIn
            try:
                dummy = supabase.table("brand_assets").insert({
                    "target": "brain", "brand_name": "brAIn",
                    "tagline": "L'organismo AI che trasforma problemi in imprese",
                    "status": "in_progress",
                }).execute()
            except Exception as e:
                logger.warning(f"[MARKETING] brain asset: {e}")

    # Notifica avvio
    card_start = _mkt_card("\U0001f680", "MARKETING AVVIATO", f"phase={phase}",
                           [f"Target: {target}", f"Progetto: {project_id or 'brAIn'}",
                            "Step 1/3: Brand Identity in corso..."])
    _mkt_notify(card_start)

    results = {}
    total_cost = 0.0

    if project_id and phase in ("full", "brand"):
        r = run_brand_agent(project_id, target=target)
        results["brand"] = r
        total_cost += r.get("cost_usd", 0)

    if project_id and phase in ("full", "gtm"):
        r = run_product_marketing_agent(project_id)
        results["product"] = r
        total_cost += r.get("cost_usd", 0)

        # Content + demand_gen + social + pr in parallelo
        def _run_content():
            results["content"] = run_content_agent(project_id)
        def _run_demand():
            results["demand"] = run_demand_gen_agent(project_id)
        def _run_social():
            results["social"] = run_social_agent(project_id)
        def _run_pr():
            results["pr"] = run_pr_agent(project_id)

        threads = [_mkt_threading.Thread(target=f, daemon=True) for f in [_run_content, _run_demand, _run_social, _run_pr]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=300)  # max 5 min per agente parallelo

        for k in ("content", "demand", "social", "pr"):
            total_cost += results.get(k, {}).get("cost_usd", 0)

    if project_id and phase in ("full", "retention"):
        r = run_customer_marketing_agent(project_id)
        results["customer"] = r
        total_cost += r.get("cost_usd", 0)

        r = run_marketing_ops_agent(project_id)
        results["ops"] = r
        total_cost += r.get("cost_usd", 0)

    # Card completamento
    completed = [k for k, v in results.items() if v.get("status") == "ok"]
    failed = [k for k, v in results.items() if v.get("status") != "ok"]
    card_done = _mkt_card("\U0001f3c6", "MARKETING COMPLETATO", f"progetto {project_id or 'brAIn'}", [
        f"Agenti completati: {len(completed)}/8",
        f"File generati: /marketing/ nel repo",
        f"Costo totale: ${total_cost:.3f}",
        f"Falliti: {', '.join(failed) if failed else 'nessuno'}",
    ])
    reply_markup = {"inline_keyboard": [[
        {"text": "\U0001f4ca Report Marketing", "callback_data": f"mkt_report:{project_id or 0}"},
        {"text": "\U0001f3a8 Brand Kit", "callback_data": f"mkt_brand_kit:{project_id or 0}"},
    ]]}
    _mkt_notify(card_done, reply_markup=reply_markup)

    log_to_supabase("marketing_coordinator", "marketing_run", 3,
                    f"project={project_id} phase={phase}",
                    f"completed={len(completed)} cost=${total_cost:.3f}",
                    "mixed", 0, 0, total_cost, 0)

    logger.info(f"[MARKETING] Completato: {len(completed)}/8 agenti, costo=${total_cost:.3f}")
    return {"status": "ok", "project_id": project_id, "completed": completed, "failed": failed,
            "total_cost_usd": round(total_cost, 4)}


# ---- VALIDATION AGENT (inlined) ----

VALIDATION_SYSTEM_PROMPT_AR = """Sei il Portfolio Manager di brAIn. Analizza le metriche di un progetto MVP e dai un verdetto chiaro.

VERDETTO (scegli uno solo):
- SCALE: metriche >= target, crescita positiva, aumenta investimento
- PIVOT: metriche < 50% target ma segnali positivi, cambia angolo
- KILL: metriche < 30% target, 3+ settimane consecutive, nessun segnale, ferma e archivia

FORMATO RISPOSTA (testo piano, max 8 righe):
VERDETTO: [SCALE/PIVOT/KILL]
KPI attuale: [valore] vs target [valore] ([percentuale]%)
Trend: [crescente/stabile/decrescente]
Revenue settimana corrente: EUR [valore]
Motivo principale: [1 riga]
Azione raccomandata: [1 riga concreta]"""


