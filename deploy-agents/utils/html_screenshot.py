"""HTML → Screenshot via Playwright (sync). Fallback Pillow se Playwright non disponibile.
Usato dal CTO per generare preview delle landing page.
"""
import tempfile
import time
from core.config import logger

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False


def html_to_screenshot(html_content, width=1200, height=675, filename_prefix="landing"):
    """Converte HTML in screenshot PNG. Ritorna path file o None.
    Usa Playwright se disponibile, altrimenti fallback Pillow placeholder.
    """
    if _HAS_PLAYWRIGHT:
        return _playwright_screenshot(html_content, width, height, filename_prefix)

    if _HAS_PILLOW:
        logger.warning("[SCREENSHOT] Playwright non disponibile, uso fallback Pillow")
        return _pillow_fallback(width, height, filename_prefix)

    logger.warning("[SCREENSHOT] Ne Playwright ne Pillow disponibili")
    return None


def _playwright_screenshot(html_content, width, height, prefix):
    """Screenshot via Playwright headless Chromium."""
    try:
        # Salva HTML in file temporaneo
        ts = str(int(time.time()))
        html_path = tempfile.gettempdir() + "/" + prefix + "_" + ts + ".html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        png_path = tempfile.gettempdir() + "/" + prefix + "_" + ts + ".png"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto("file://" + html_path, wait_until="networkidle")
            # Aspetta rendering
            page.wait_for_timeout(2000)
            page.screenshot(path=png_path, full_page=False)
            browser.close()

        logger.info("[SCREENSHOT] Playwright OK: %s (%dx%d)", png_path, width, height)
        return png_path

    except Exception as e:
        logger.warning("[SCREENSHOT] Playwright error: %s", e)
        if _HAS_PILLOW:
            return _pillow_fallback(width, height, prefix)
        return None


def _pillow_fallback(width, height, prefix):
    """Genera placeholder PNG con Pillow quando Playwright non e' disponibile."""
    try:
        img = Image.new("RGB", (width, height), "#0D1117")
        draw = ImageDraw.Draw(img)

        # Font
        font = None
        for fp in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]:
            try:
                font = ImageFont.truetype(fp, 28)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()

        # Testo placeholder
        draw.text(
            (width // 2, height // 2 - 20),
            "PREVIEW LANDING PAGE",
            fill="#52B788",
            font=font,
            anchor="mm",
        )
        draw.text(
            (width // 2, height // 2 + 30),
            "Screenshot completo disponibile dopo deploy Playwright",
            fill="#666666",
            font=font,
            anchor="mm",
        )

        ts = str(int(time.time()))
        path = tempfile.gettempdir() + "/" + prefix + "_fallback_" + ts + ".png"
        img.save(path, "PNG")
        logger.info("[SCREENSHOT] Pillow fallback: %s", path)
        return path

    except Exception as e:
        logger.warning("[SCREENSHOT] Pillow fallback error: %s", e)
        return None
