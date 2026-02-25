"""
brAIn Landing Page Generator
Genera HTML single-file per landing page MVP.
Non deploya — salva in projects.landing_page_html.
"""

import os
import json
import time
import logging
from dotenv import load_dotenv
import anthropic
from supabase import create_client

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 3000


def _log(action, input_summary, output_summary, model, tokens_in, tokens_out, cost, duration_ms, status="success", error=None):
    try:
        supabase.table("agent_logs").insert({
            "agent_id": "landing_page_generator",
            "action": action,
            "layer": 3,
            "input_summary": (input_summary or "")[:500],
            "output_summary": (output_summary or "")[:500],
            "model_used": model,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "status": status,
            "error": error,
        }).execute()
    except Exception as e:
        logger.error(f"[LOG] {e}")


LP_SYSTEM_PROMPT = """Sei un designer/copywriter esperto. Genera HTML single-file per una landing page MVP.

REQUISITI:
- HTML completo (<!DOCTYPE html> ... </html>), CSS inline nel <style>, nessuna dipendenza esterna
- Mobile-first, responsive, caricamento istantaneo
- Colori: bianco + un colore primario coerente col settore
- Font: system-ui / -apple-system (nessun Google Fonts — no richieste esterne)
- NO JavaScript complesso — solo il form contatto basico

STRUTTURA OBBLIGATORIA:
1. Hero section: headline + sottotitolo + CTA button
2. 3 benefit cards con icona emoji + titolo + descrizione 1 riga
3. Social proof placeholder: "[NUMERO] clienti gia' iscritti" (testo statico)
4. Form contatto: nome + email + messaggio + button "Voglio saperne di piu'"
5. Footer: "Prodotto da brAIn — AI-native organization"

REGOLE COPYWRITING:
- Headline: beneficio concreto in < 8 parole (NON il nome del prodotto)
- CTA: verbo d'azione + beneficio (es: "Inizia gratis oggi")
- Nessun gergo tecnico — linguaggio del target customer

Rispondi SOLO con il codice HTML, senza spiegazioni, senza blocchi markdown."""


def run(project_id):
    """Genera HTML landing page e salva in projects.landing_page_html."""
    start = time.time()
    logger.info(f"[LP] Avvio per project_id={project_id}")

    # 1. Carica dati progetto
    try:
        proj = supabase.table("projects").select("*").eq("id", project_id).execute()
        if not proj.data:
            logger.error(f"[LP] Progetto {project_id} non trovato")
            return {"status": "error", "error": "project not found"}
        project = proj.data[0]
    except Exception as e:
        logger.error(f"[LP] DB load error: {e}")
        return {"status": "error", "error": str(e)}

    name = project.get("name", "MVP")
    spec_md = project.get("spec_md", "")
    kpis = project.get("kpis") or {}
    stack = project.get("stack") or []

    # Estrai value proposition e target dal spec_md (sezione 2 e 3)
    value_prop = ""
    target_customer = ""
    if spec_md:
        import re
        # Estrai sezione 2 e 3 (prime 1000 chars del spec)
        value_prop = spec_md[:1500]

    # Carica info soluzione per contesto aggiuntivo
    solution_desc = ""
    problem_desc = ""
    bos_id = project.get("bos_id")
    if bos_id:
        try:
            sol = supabase.table("solutions").select("title,description,problem_id").eq("id", bos_id).execute()
            if sol.data:
                solution_desc = sol.data[0].get("description", "")[:400]
                prob_id = sol.data[0].get("problem_id")
                if prob_id:
                    prob = supabase.table("problems").select("title,description,target_customer,target_geography").eq("id", prob_id).execute()
                    if prob.data:
                        target_customer = prob.data[0].get("target_customer", "")
                        problem_desc = prob.data[0].get("description", "")[:300]
        except Exception as e:
            logger.warning(f"[LP] Solution/problem load: {e}")

    # 2. Genera HTML con Haiku
    user_prompt = f"""Progetto: {name}

Target customer: {target_customer or "professionisti e PMI"}

Problema risolto: {problem_desc or "inefficienza nel flusso di lavoro"}

Soluzione: {solution_desc or spec_md[:300]}

Genera la landing page HTML completa per questo MVP."""

    tokens_in = tokens_out = 0
    html = ""
    try:
        response = claude.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=LP_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_html = response.content[0].text.strip()
        if raw_html.startswith("```"):
            lines = raw_html.split("\n")
            lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        else:
            lines = raw_html.split("\n")
        html = "\n".join(lines).strip()
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except Exception as e:
        logger.error(f"[LP] Claude error: {e}")
        _log("lp_generate", f"project={project_id}", str(e), MODEL, 0, 0, 0,
             int((time.time() - start) * 1000), "error", str(e))
        return {"status": "error", "error": str(e)}

    cost = (tokens_in * 0.8 + tokens_out * 4.0) / 1_000_000

    # 3. Valida HTML base
    if not html.lower().startswith("<!doctype") and "<html" not in html.lower():
        logger.warning(f"[LP] HTML non valido: inizio='{html[:50]}'")

    # 4. Salva in DB
    try:
        supabase.table("projects").update({
            "landing_page_html": html,
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"[LP] DB update error: {e}")

    duration_ms = int((time.time() - start) * 1000)
    _log("lp_generate", f"project={project_id}", f"HTML {len(html)} chars", MODEL,
         tokens_in, tokens_out, cost, duration_ms)

    logger.info(f"[LP] Completato project={project_id} in {duration_ms}ms, {len(html)} chars")
    return {
        "status": "ok",
        "project_id": project_id,
        "html_length": len(html),
        "cost_usd": round(cost, 6),
    }


if __name__ == "__main__":
    import sys
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    result = run(pid)
    print(json.dumps(result, indent=2, ensure_ascii=False))
