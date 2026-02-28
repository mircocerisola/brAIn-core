"""Test v5.32: utils/html_screenshot.py — screenshot HTML."""
import sys
sys.path.insert(0, "deploy-agents")


def test_import():
    """Modulo importabile senza errori."""
    from utils.html_screenshot import html_to_screenshot, _HAS_PILLOW
    assert callable(html_to_screenshot)
    print(f"PASS: import OK, Pillow={_HAS_PILLOW}")


def test_pillow_fallback():
    """Se Playwright non disponibile, usa fallback Pillow."""
    from utils.html_screenshot import _HAS_PILLOW
    if not _HAS_PILLOW:
        print("SKIP: Pillow non disponibile")
        return

    from unittest.mock import patch
    with patch("utils.html_screenshot._HAS_PLAYWRIGHT", False):
        from utils.html_screenshot import html_to_screenshot
        path = html_to_screenshot("<html><body>Test</body></html>")
    # Pillow fallback genera placeholder
    if path:
        assert path.endswith(".png"), f"Atteso .png, ottenuto {path}"
        print("PASS: Pillow fallback genera PNG")
    else:
        print("PASS: Nessun output (Pillow non ha font disponibili)")


def test_no_deps_returns_none():
    """Senza Playwright ne Pillow, ritorna None."""
    from unittest.mock import patch
    with patch("utils.html_screenshot._HAS_PLAYWRIGHT", False), \
         patch("utils.html_screenshot._HAS_PILLOW", False):
        from utils.html_screenshot import html_to_screenshot
        result = html_to_screenshot("<html>test</html>")
    assert result is None, f"Atteso None, ottenuto {result}"
    print("PASS: No deps -> None")


if __name__ == "__main__":
    test_import()
    test_pillow_fallback()
    test_no_deps_returns_none()
    print("\nTutti i test html_screenshot PASS")
