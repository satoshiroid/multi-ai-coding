"""Image generation backends for design sketches.

The design worker uses this to turn short text briefs into sketch proposals
(cheaper and more reviewable than generating Blender/bpy code directly). Two
providers, selectable from config/env — same shape as the LLM provider layer:

    * ``openai``  — Images API (gpt-image-1), returns base64 PNG.
    * ``gemini``  — Generative Language image model ("nanobanana",
      e.g. gemini-2.5-flash-image), returns inline base64 image data.

Everything goes over httpx (already a dependency) so no extra SDK is required.
Failures degrade gracefully: ``generate_images`` returns the paths it managed to
produce and records nothing fatal, so the pipeline keeps going with the text
spec even when image generation is unavailable.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx

_OPENAI_URL = "https://api.openai.com/v1/images/generations"
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_MODELS = {
    "openai": "gpt-image-1",
    "gemini": "gemini-2.5-flash-image",
}


class ImageGenError(RuntimeError):
    """Raised for a hard configuration error (unknown provider / missing key)."""


async def generate_images(
    prompts: list[str],
    *,
    provider: str,
    model: str | None,
    api_key: str | None,
    out_dir: str | Path,
    size: str = "1024x1024",
    timeout: float = 120.0,
) -> list[str]:
    """Generate one image per prompt; return the saved PNG paths (in order).

    Prompts that fail are skipped (their slot is omitted), so a partial set still
    lets the owner choose. Raises :class:`ImageGenError` only for misconfiguration.
    """
    provider = (provider or "").lower()
    if provider not in ("openai", "gemini"):
        raise ImageGenError(f"unknown image provider: {provider!r}")
    if not api_key:
        raise ImageGenError(f"no API key for image provider {provider!r}")

    model = model or DEFAULT_MODELS[provider]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for i, prompt in enumerate(prompts, start=1):
            try:
                data = (
                    await _gen_openai(client, prompt, model, api_key, size)
                    if provider == "openai"
                    else await _gen_gemini(client, prompt, model, api_key)
                )
            except Exception as exc:  # noqa: BLE001 - per-image best effort
                print(f"[image_gen] {provider} image {i} failed: {type(exc).__name__}: {exc}")
                continue
            if data:
                path = out / f"sketch_{i}.png"
                path.write_bytes(data)
                paths.append(str(path))
    return paths


async def _gen_openai(
    client: httpx.AsyncClient, prompt: str, model: str, api_key: str, size: str
) -> bytes | None:
    resp = await client.post(
        _OPENAI_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "prompt": prompt, "n": 1, "size": size},
    )
    resp.raise_for_status()
    items = resp.json().get("data", [])
    if items and items[0].get("b64_json"):
        return base64.b64decode(items[0]["b64_json"])
    return None


async def _gen_gemini(
    client: httpx.AsyncClient, prompt: str, model: str, api_key: str
) -> bytes | None:
    resp = await client.post(
        _GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "content-type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
        },
    )
    resp.raise_for_status()
    for cand in resp.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


# --------------------------------------------------------------------------- #
# Config resolution (settings.yaml `image:` + env overrides)
# --------------------------------------------------------------------------- #
def resolve_image_config(settings: dict) -> dict:
    """Resolve the image-gen provider/model/api_key, honouring env overrides.

    Precedence: IMAGE_PROVIDER/IMAGE_MODEL env > settings.yaml ``image``.
    Returns ``{enabled, provider, model, api_key, count, size}``.
    """
    cfg = dict(settings.get("image", {}) or {})
    provider = os.environ.get("IMAGE_PROVIDER") or cfg.get("provider", "gemini")
    model = os.environ.get("IMAGE_MODEL") or cfg.get("model") or DEFAULT_MODELS.get(provider)

    # API key: explicit image key env, else the provider's standard LLM key env.
    key_env = cfg.get("api_key_env") or {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(provider)
    api_key = os.environ.get(key_env) if key_env else None

    enabled = cfg.get("enabled", True)
    if os.environ.get("IMAGE_ENABLED") is not None:
        enabled = os.environ["IMAGE_ENABLED"].lower() not in ("0", "false", "no")

    return {
        "enabled": bool(enabled),
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "count": int(cfg.get("count", 3)),
        "size": str(cfg.get("size", "1024x1024")),
    }
