"""Server-side Pollinations image generation with a local cache."""
import os
import re
from pathlib import Path
from urllib.parse import quote

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
IMAGE_DIR = BASE_DIR / "static" / "img" / "foods"
BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://gen.pollinations.ai").rstrip("/")
DEFAULT_MODEL = "black-forest-labs/flux.1-schnell"


def cached_image(recipe_id):
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(recipe_id))
    for ext in ("webp", "jpg", "jpeg", "png"):
        path = IMAGE_DIR / f"{safe_id}.{ext}"
        if path.is_file() and path.stat().st_size > 1024:
            return path
    return None


def generate_recipe_image(recipe_id, recipe_name):
    """Generate one realistic food photo and cache it. Returns local path or None."""
    existing = cached_image(recipe_id)
    if existing:
        return existing
    api_key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    if not api_key:
        return None

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    prompt = (
        f"Professional editorial food photography of {recipe_name}, authentic Indian home-cooked dish, "
        "realistic natural food texture, appetizing but believable portion, served in simple ceramic tableware, "
        "soft natural window light, warm neutral tabletop, overhead three-quarter angle, shallow depth of field, "
        "high detail, no people, no hands, no text, no lettering, no watermark, square composition. "
        "The image must depict the named dish accurately, not a collage or illustration."
    )
    model = os.getenv("POLLINATIONS_IMAGE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    url = f"{BASE_URL}/image/{quote(prompt, safe='')}"
    try:
        response = requests.get(url, headers={"Authorization": f"Bearer {api_key}"},
                                params={"model": model, "width": 1024, "height": 1024}, timeout=(10, 120))
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
        ext = next((v for k, v in extensions.items() if k in content_type), ".jpg")
        if not response.content or len(response.content) < 1024 or not content_type.startswith("image/"):
            raise ValueError("Pollinations returned an empty or non-image response")
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(recipe_id))
        target = IMAGE_DIR / f"{safe_id}{ext}"
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(response.content)
        temp.replace(target)
        return target
    except (requests.RequestException, OSError, ValueError):
        # The app should continue to show its existing fallback illustration.
        return None
