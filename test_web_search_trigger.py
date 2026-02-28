"""Test v5.29: web search trigger detection in csuite/utils.py."""
import sys
sys.path.insert(0, "deploy-agents")

def test_detect_web_search():
    from csuite.utils import detect_web_search, WEB_SEARCH_TRIGGERS

    # Trigger presente -> restituisce query
    result = detect_web_search("cerca online quanto costa Anthropic Max")
    assert result is not None, "detect_web_search deve trovare trigger 'cerca online'"
    assert len(result) >= 5, f"Query troppo corta: {result}"
    print("PASS: 'cerca online' trigger rilevato, query:", result)

    # Trigger 'vai a vedere online'
    result2 = detect_web_search("vai a vedere online il piano Supabase Pro")
    assert result2 is not None, "detect_web_search deve trovare 'vai a vedere online'"
    print("PASS: 'vai a vedere online' trigger rilevato")

    # Nessun trigger -> None
    result3 = detect_web_search("quanto costa il piano Max di Anthropic?")
    assert result3 is None, f"Non dovrebbe triggerare senza keyword esplicita: {result3}"
    print("PASS: domanda senza trigger -> None")

    # Tutti i trigger devono essere stringhe lowercase
    for t in WEB_SEARCH_TRIGGERS:
        assert t == t.lower(), f"Trigger non lowercase: {t}"
    print(f"PASS: tutti {len(WEB_SEARCH_TRIGGERS)} trigger sono lowercase")

def test_web_search_function_exists():
    from csuite.utils import web_search
    assert callable(web_search), "web_search deve essere una funzione"
    # Senza API key, deve restituire messaggio di errore (non crashare)
    import os
    old_key = os.environ.get("PERPLEXITY_API_KEY", "")
    os.environ["PERPLEXITY_API_KEY"] = ""
    # Reimporta per aggiornare la variabile
    import importlib
    import csuite.utils
    importlib.reload(csuite.utils)
    result = csuite.utils.web_search("test query")
    assert "non configurata" in result.lower() or "api key" in result.lower(), f"Senza key deve dire errore: {result}"
    print("PASS: web_search senza API key restituisce errore chiaro")
    # Ripristina
    if old_key:
        os.environ["PERPLEXITY_API_KEY"] = old_key

if __name__ == "__main__":
    test_detect_web_search()
    test_web_search_function_exists()
    print("\nTutti i test PASS")
