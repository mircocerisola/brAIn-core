"""Aggiorna i profili Chief in chief_knowledge con la lista agenti operativi."""
import urllib.request
import urllib.error
import json
import re
import os

SUPA_URL = os.getenv("SUPABASE_URL", "")
SUPA_KEY = os.getenv("SUPABASE_KEY", "")

SECTION = "AGENTI OPERATIVI SOTTO DI TE"

AGENTS = {
    "cso": (
        "Hai sotto di te: world_scanner, solution_architect, feasibility_engine."
    ),
    "coo": (
        "Hai sotto di te: spec_generator, build_agent, validation_agent, smoke_test_agent."
    ),
    "cto": (
        "Non hai agenti operativi diretti. Supervisioni il CDO come funzione interna, "
        "non un agente separato. Ricevi aggiornamenti tecnici tramite CPeO/Capability Scout."
    ),
    "cmo": (
        "Hai sotto di te 8 agenti operativi: brand_agent, product_marketing_agent, "
        "content_agent, demand_gen_agent, social_agent, pr_agent, customer_marketing_agent, "
        "marketing_ops_agent. Puoi attivarli tramite /marketing/run. "
        "NON dire mai che agenti marketing non esistono."
    ),
    "cfo": (
        "Hai sotto di te: finance_agent."
    ),
    "clo": (
        "Hai sotto di te: legal_agent, ethics_monitor."
    ),
    "cpeo": (
        "Hai sotto di te: knowledge_keeper, capability_scout, idea_recycler. "
        "Il capability_scout e' tuo, non del CTO. "
        "Lo usi per formare tutti i Chief incluso il CTO."
    ),
}

BASE_HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": "Bearer " + SUPA_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


class PatchRequest(urllib.request.Request):
    def get_method(self):
        return "PATCH"


def main():
    # Carica profili attuali
    req = urllib.request.Request(
        SUPA_URL + "/rest/v1/chief_knowledge?knowledge_type=eq.profile&select=id,chief_id,content",
        headers=BASE_HEADERS,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read())

    updated = 0
    for row in rows:
        rid = row["id"]
        cid = row["chief_id"]
        content = row.get("content") or ""
        agent_line = AGENTS.get(cid)
        if not agent_line:
            print(f"[SKIP] {cid}: nessun agente definito")
            continue

        new_section = "\n\n" + SECTION + ": " + agent_line
        if SECTION in content:
            # Sostituisci sezione esistente
            pattern = SECTION + r":.*?(\n\n|$)"
            replacement = SECTION + ": " + agent_line + r"\1"
            new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        else:
            new_content = content + new_section

        patch_req = PatchRequest(
            SUPA_URL + "/rest/v1/chief_knowledge?id=eq." + str(rid),
            data=json.dumps({"content": new_content}).encode("utf-8"),
            headers=BASE_HEADERS,
        )
        try:
            with urllib.request.urlopen(patch_req, timeout=15) as resp:
                resp.read()
                delta = len(new_content) - len(content)
                print(f"[OK] {cid} (id={rid}): +{delta} chars")
                updated += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            print(f"[ERROR] {cid}: HTTP {e.code} — {body}")

    print(f"\nTotale: {updated}/7 profili aggiornati")


if __name__ == "__main__":
    main()
