"""Vision tool: analyze images/screenshots with a vision-capable provider.

Uses the `vision` block in config.yaml to pick the provider + model.
The API key comes from the matching .env variable (e.g. GEMINI_API_KEY),
same as every other provider — never stored in config.yaml.

Supports multiple providers with ordered fallback: if the primary vision
provider fails (quota/auth/network), it tries the configured `fallbacks`
in order, so vision keeps working even when one provider runs dry.
"""
from __future__ import annotations
import base64
import io
import os
from pathlib import Path

from openai import OpenAI

from brain.config import load_config, load_dotenv, get_effort_tier

# Keep payloads sane: most vision models cap the long side around 1568px
# and prefer JPEG for photos / screenshots.
_MAX_SIDE = 1568
_JPEG_QUALITY = 85

# Gemini's OpenAI-compatible endpoint (no custom base_url needed in config).
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _resolve_path(path: str) -> Path:
    """Resolve a user-supplied path, expanding ~ and checking common bases."""
    p = Path(os.path.expanduser(path)).expanduser()
    if p.is_absolute():
        return p
    for base in (Path.home() / "Desktop", Path.cwd(), Path.home()):
        cand = base / path
        if cand.exists():
            return cand
    return Path.home() / "Desktop" / path


def _load_image_b64(path: Path) -> str:
    """Load + downscale an image, return base64 JPEG data (no data-URI prefix)."""
    from PIL import Image

    img = Image.open(path)
    img.load()
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, _MAX_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _provider_candidates(cfg: dict) -> list[dict]:
    """Ordered list of vision candidates: primary first, then fallbacks.

    The primary provider/model can be overridden per effort tier via
    ``vision.tiers.<tier>`` in config.yaml — Ultra pins the heavy thinking
    vision model, every other tier uses the fast cheap default. Fallbacks
    are shared across tiers.

    Each entry: {"provider", "model", "base_url", "api_key"}.
    """
    vision_cfg = dict(cfg.get("vision", {}) or {})
    providers = cfg.get("providers", {}) or {}
    env = load_dotenv()

    # Effort tier override: only swaps provider/model, never the fallbacks.
    tier_ovr = ((vision_cfg.get("tiers") or {}).get(get_effort_tier()) or {})
    if tier_ovr:
        vision_cfg["provider"] = tier_ovr.get("provider", vision_cfg.get("provider", ""))
        if tier_ovr.get("model"):
            vision_cfg["model"] = tier_ovr["model"]

    def resolve(provider_key: str, model: str | None) -> dict | None:
        prov = providers.get(provider_key, {}) or {}
        base_url = prov.get("base_url", "")
        if provider_key == "gemini" and not base_url:
            base_url = _GEMINI_BASE
        api_key = env.get(f"{provider_key.upper()}_API_KEY", "") or prov.get("api_key", "")
        if not api_key:
            return None
        return {
            "provider": provider_key,
            "model": model or prov.get("model", ""),
            "base_url": base_url,
            "api_key": api_key,
        }

    cands: list[dict] = []
    primary_key = vision_cfg.get("provider", "gemini")
    primary = resolve(
        primary_key,
        vision_cfg.get("model") or providers.get(primary_key, {}).get("model"),
    )
    if primary:
        cands.append(primary)

    for fb in vision_cfg.get("fallbacks", []) or []:
        pk = fb.get("provider", "")
        if not pk or pk == primary_key:
            continue
        cand = resolve(pk, fb.get("model"))
        if cand:
            cands.append(cand)

    return cands


def analyze_image(path: str, prompt: str = "Describe this image in detail.") -> str:
    """Analyze an image file with a vision model. Returns the model's answer."""
    try:
        p = _resolve_path(path)
        if not p.exists():
            return (
                f"Image not found: {path} (checked Desktop, current dir, and home). "
                "Use screenshot() first, or give the full path."
            )
        b64 = _load_image_b64(p)
    except Exception as e:
        return f"Could not load image {path}: {e}"

    cfg = load_config()
    candidates = _provider_candidates(cfg)
    if not candidates:
        return (
            "No vision provider is configured with an API key. Add one of "
            "GEMINI_API_KEY / DOUBAO_API_KEY to .env, or set the `vision` block "
            "in config.yaml."
        )

    errors: list[str] = []
    for cand in candidates:
        try:
            client = OpenAI(
                base_url=cand["base_url"], api_key=cand["api_key"], timeout=90.0
            )
            resp = client.chat.completions.create(
                model=cand["model"],
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                        }},
                    ],
                }],
                temperature=0.2,
            )
            return resp.choices[0].message.content or "(empty response)"
        except Exception as e:
            errors.append(f"{cand['provider']} ({cand['model']}): {str(e)[:140]}")

    return "All vision providers failed:\n" + "\n".join(errors)
