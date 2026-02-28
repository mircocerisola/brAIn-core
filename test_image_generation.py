"""Test v5.32: utils/image_generation.py — fallback chain."""
import sys
sys.path.insert(0, "deploy-agents")

from unittest.mock import patch, MagicMock


def test_generate_image_no_keys():
    """Senza API key, ritorna None."""
    with patch("utils.image_generation.OPENAI_API_KEY", ""), \
         patch("utils.image_generation.CF_ACCOUNT_ID", ""), \
         patch("utils.image_generation.CF_API_TOKEN", ""), \
         patch("utils.image_generation.REPLICATE_API_TOKEN", ""):
        from utils.image_generation import generate_image
        result = generate_image("test prompt")
    assert result is None, f"Atteso None, ottenuto {result}"
    print("PASS: No API keys -> None")


def test_openai_fallback_to_cloudflare():
    """Se OpenAI fallisce, prova Cloudflare."""
    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 500
    mock_resp_fail.text = "error"

    mock_resp_cf = MagicMock()
    mock_resp_cf.status_code = 200
    mock_resp_cf.headers = {"content-type": "image/png"}
    mock_resp_cf.content = b"\x89PNG\r\n" + b"\x00" * 100

    call_count = {"n": 0}
    original_post = None

    def mock_post(url, **kwargs):
        call_count["n"] += 1
        if "openai.com" in url:
            return mock_resp_fail
        if "cloudflare.com" in url:
            return mock_resp_cf
        return mock_resp_fail

    with patch("utils.image_generation.OPENAI_API_KEY", "sk-test"), \
         patch("utils.image_generation.CF_ACCOUNT_ID", "acc123"), \
         patch("utils.image_generation.CF_API_TOKEN", "cf-token"), \
         patch("utils.image_generation._requests.post", side_effect=mock_post), \
         patch("utils.image_generation._requests.get", return_value=mock_resp_fail):
        from utils.image_generation import generate_image
        result = generate_image("test prompt")

    assert result is not None, "Atteso path file, ottenuto None"
    assert result.endswith(".png"), f"Atteso .png, ottenuto {result}"
    print("PASS: OpenAI fail -> Cloudflare fallback OK")


def test_save_temp():
    """_save_temp salva file correttamente."""
    from utils.image_generation import _save_temp
    path = _save_temp(b"test content 12345", "test_prefix")
    assert path.endswith(".png"), f"Atteso .png, ottenuto {path}"
    with open(path, "rb") as f:
        content = f.read()
    assert content == b"test content 12345"
    print("PASS: _save_temp OK")


if __name__ == "__main__":
    test_generate_image_no_keys()
    test_openai_fallback_to_cloudflare()
    test_save_temp()
    print("\nTutti i test image_generation PASS")
