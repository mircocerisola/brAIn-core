"""Test 3: verifica Pillow in requirements.txt e importabile."""
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

# 1. Pillow in requirements.txt
try:
    with open("deploy-agents/requirements.txt", "r") as f:
        req_content = f.read()
    check("pillow_in_requirements", "Pillow" in req_content or "pillow" in req_content,
          "not found in requirements.txt")
except Exception as e:
    check("pillow_in_requirements", False, str(e))

# 2. Pillow importabile
try:
    from PIL import Image, ImageDraw, ImageFont
    check("pillow_import", True)
except ImportError as e:
    check("pillow_import", False, str(e))

# 3. PIL version
try:
    import PIL
    version = PIL.__version__
    check("pillow_version", len(version) > 0, "version=" + version)
    # Versione >= 10.0.0
    major = int(version.split(".")[0])
    check("pillow_version_10plus", major >= 10, "major=" + str(major))
except Exception as e:
    check("pillow_version", False, str(e))

# 4. _HAS_PILLOW flag in CMO
try:
    from csuite.cmo import _HAS_PILLOW
    check("cmo_has_pillow_flag", _HAS_PILLOW is True, "_HAS_PILLOW=" + str(_HAS_PILLOW))
except Exception as e:
    check("cmo_has_pillow_flag", False, str(e))

# 5. Image.new funziona
try:
    img = Image.new("RGB", (100, 100), "#ff0000")
    check("image_new", img.size == (100, 100))
except Exception as e:
    check("image_new", False, str(e))

# 6. ImageDraw funziona
try:
    draw = ImageDraw.Draw(img)
    draw.rectangle([(10, 10), (90, 90)], fill="#00ff00")
    check("image_draw", True)
except Exception as e:
    check("image_draw", False, str(e))

# 7. ImageFont.load_default funziona
try:
    font = ImageFont.load_default()
    check("image_font_default", font is not None)
except Exception as e:
    check("image_font_default", False, str(e))

# Output
print("=== TEST PILLOW ===")
for r in results:
    print(r)
print("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===")

with open("test_pillow.txt", "w", encoding="utf-8") as f:
    f.write("=== TEST PILLOW ===\n")
    for r in results:
        f.write(r + "\n")
    f.write("\n=== TOTALE: " + str(pass_count) + " PASS, " + str(fail_count) + " FAIL ===\n")
