"""Image generation utility — fallback chain: OpenAI → Cloudflare FLUX → Replicate.
Tutto sync via requests. Ritorna path file PNG locale.
"""
import os
import tempfile
import time
import requests as _requests
from core.config import logger

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")


def generate_image(prompt, size="1024x1024", filename_prefix="mockup"):
    """Genera immagine da prompt testuale. Ritorna path file PNG o None.
    Fallback chain: OpenAI DALL-E 3 → Cloudflare FLUX → Replicate FLUX.
    """
    path = None

    # 1. OpenAI DALL-E 3
    if OPENAI_API_KEY:
        path = _openai_generate(prompt, size, filename_prefix)
        if path:
            return path

    # 2. Cloudflare FLUX
    if CF_ACCOUNT_ID and CF_API_TOKEN:
        path = _cloudflare_generate(prompt, filename_prefix)
        if path:
            return path

    # 3. Replicate FLUX
    if REPLICATE_API_TOKEN:
        path = _replicate_generate(prompt, filename_prefix)
        if path:
            return path

    logger.warning("[IMG] Nessun provider di image generation disponibile")
    return None


def _openai_generate(prompt, size, prefix):
    """Genera immagine via OpenAI DALL-E 3."""
    try:
        resp = _requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": "Bearer " + OPENAI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": "dall-e-3",
                "prompt": prompt,
                "n": 1,
                "size": size,
                "response_format": "url",
            },
            timeout=60,
        )
        if resp.status_code != 200:
            logger.warning("[IMG] OpenAI status %d: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        image_url = data["data"][0]["url"]

        # Scarica immagine
        img_resp = _requests.get(image_url, timeout=30)
        if img_resp.status_code != 200:
            return None

        path = _save_temp(img_resp.content, prefix)
        logger.info("[IMG] OpenAI DALL-E 3 OK: %s", path)
        return path

    except Exception as e:
        logger.warning("[IMG] OpenAI error: %s", e)
        return None


def _cloudflare_generate(prompt, prefix):
    """Genera immagine via Cloudflare Workers AI (FLUX)."""
    try:
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            + CF_ACCOUNT_ID
            + "/ai/run/@cf/black-forest-labs/FLUX.1-schnell"
        )
        resp = _requests.post(
            url,
            headers={"Authorization": "Bearer " + CF_API_TOKEN},
            json={"prompt": prompt},
            timeout=60,
        )
        if resp.status_code != 200:
            logger.warning("[IMG] Cloudflare status %d", resp.status_code)
            return None

        # Cloudflare ritorna immagine binaria direttamente
        content_type = resp.headers.get("content-type", "")
        if "image" in content_type:
            path = _save_temp(resp.content, prefix)
            logger.info("[IMG] Cloudflare FLUX OK: %s", path)
            return path

        # Potrebbe essere JSON con base64
        try:
            data = resp.json()
            if data.get("result", {}).get("image"):
                import base64
                img_bytes = base64.b64decode(data["result"]["image"])
                path = _save_temp(img_bytes, prefix)
                logger.info("[IMG] Cloudflare FLUX (base64) OK: %s", path)
                return path
        except Exception:
            pass

        logger.warning("[IMG] Cloudflare response non riconosciuta")
        return None

    except Exception as e:
        logger.warning("[IMG] Cloudflare error: %s", e)
        return None


def _replicate_generate(prompt, prefix):
    """Genera immagine via Replicate (FLUX schnell)."""
    try:
        # Crea prediction
        resp = _requests.post(
            "https://api.replicate.com/v1/predictions",
            headers={
                "Authorization": "Bearer " + REPLICATE_API_TOKEN,
                "Content-Type": "application/json",
            },
            json={
                "version": "black-forest-labs/flux-schnell",
                "input": {"prompt": prompt},
            },
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            logger.warning("[IMG] Replicate create status %d", resp.status_code)
            return None

        prediction = resp.json()
        prediction_url = prediction.get("urls", {}).get("get", "")
        if not prediction_url:
            return None

        # Poll per risultato (max 60 sec)
        for _ in range(12):
            time.sleep(5)
            poll = _requests.get(
                prediction_url,
                headers={"Authorization": "Bearer " + REPLICATE_API_TOKEN},
                timeout=15,
            )
            if poll.status_code != 200:
                continue
            status = poll.json().get("status", "")
            if status == "succeeded":
                output = poll.json().get("output")
                if isinstance(output, list) and output:
                    image_url = output[0]
                elif isinstance(output, str):
                    image_url = output
                else:
                    return None
                # Scarica
                img_resp = _requests.get(image_url, timeout=30)
                if img_resp.status_code == 200:
                    path = _save_temp(img_resp.content, prefix)
                    logger.info("[IMG] Replicate FLUX OK: %s", path)
                    return path
                return None
            elif status == "failed":
                logger.warning("[IMG] Replicate prediction failed")
                return None

        logger.warning("[IMG] Replicate timeout")
        return None

    except Exception as e:
        logger.warning("[IMG] Replicate error: %s", e)
        return None


def _save_temp(content, prefix):
    """Salva bytes come file PNG temporaneo."""
    ts = str(int(time.time()))
    path = tempfile.gettempdir() + "/" + prefix + "_" + ts + ".png"
    with open(path, "wb") as f:
        f.write(content)
    return path
