"""Test 2: verifica generate_bozza_visiva genera PNG 1200x675."""
import sys
sys.path.insert(0, "deploy-agents")

results = []
pass_count = 0
fail_count = 0

def check(label, condition, detail=""):
    global pass_count, fail_count
    if condition:
        pass_count += 1
        results.append("  " + label + ": PASS")
    else:
        fail_count += 1
        results.append("  " + label + ": FAIL" + (": " + detail if detail else ""))

# Verifica che CMO ha la nuova signature
try:
    from csuite.cmo import CMO
    import inspect
    sig = inspect.signature(CMO.generate_bozza_visiva)
    params = list(sig.parameters.keys())
    check("new_signature", params == ["self", "project_name", "tagline", "thread_id", "project_id"],
          "params=" + str(params))
except Exception as e:
    check("new_signature", False, str(e))

# Verifica Pillow disponibile
try:
    from PIL import Image, ImageDraw, ImageFont
    check("pillow_available", True)
except ImportError:
    check("pillow_available", False, "PIL not installed")

# Genera bozza (senza Telegram send — TELEGRAM_BOT_TOKEN non settato in test)
try:
    cmo = CMO()
    result = cmo.generate_bozza_visiva("TestBrand", "Tagline di test", None, None)
    check("generate_ok", result.get("status") == "ok", str(result))
    check("size_correct", result.get("size") == "1200x675", "size=" + str(result.get("size")))

    path = result.get("path", "")
    check("path_exists", len(path) > 0, "path=" + path)

    # Verifica il file PNG esiste e ha dimensioni corrette
    if path:
        img = Image.open(path)
        w, h = img.size
        check("png_width", w == 1200, "w=" + str(w))
        check("png_height", h == 675, "h=" + str(h))
        check("png_mode", img.mode == "RGB", "mode=" + img.mode)

        # Verifica non e' un'immagine completamente nera
        pixels = list(img.getdata())
        non_black = sum(1 for p in pixels[:1000] if sum(p) > 30)
        check("not_all_black", non_black > 0, "non_black_in_1000=" + str(non_black))

        # Cleanup
        import os
        os.remove(path)
except Exception as e:
    check("generate_ok", False, str(e))

# Output
print("=== TEST BOZZA PNG ===")
for r in results:
    print(r)
print("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===")

with open("test_bozza_png.txt", "w", encoding="utf-8") as f:
    f.write("=== TEST BOZZA PNG ===\n")
    for r in results:
        f.write(r + "\n")
    f.write("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===\n")
