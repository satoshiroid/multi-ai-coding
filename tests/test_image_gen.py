"""Tests for the design sketch image-generation config + error handling."""

from __future__ import annotations

import pytest

from src.image_gen import ImageGenError, generate_images, resolve_image_config


def _settings() -> dict:
    return {
        "image": {
            "enabled": True,
            "provider": "gemini",
            "model": "gemini-2.5-flash-image",
            "api_key_env": "IMG_KEY_ENV",
            "count": 3,
            "size": "1024x1024",
        }
    }


def test_resolve_uses_settings_and_key_env(monkeypatch):
    monkeypatch.setenv("IMG_KEY_ENV", "secret-123")
    cfg = resolve_image_config(_settings())
    assert cfg["provider"] == "gemini"
    assert cfg["model"] == "gemini-2.5-flash-image"
    assert cfg["api_key"] == "secret-123"
    assert cfg["count"] == 3 and cfg["enabled"] is True


def test_env_overrides_provider_and_model(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("IMAGE_MODEL", "gpt-image-1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = resolve_image_config(_settings())
    assert cfg["provider"] == "openai"
    assert cfg["model"] == "gpt-image-1"
    # Falls back to OPENAI_API_KEY when api_key_env doesn't match the new provider.
    monkeypatch.delenv("IMG_KEY_ENV", raising=False)


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("IMAGE_ENABLED", "false")
    assert resolve_image_config(_settings())["enabled"] is False


@pytest.mark.asyncio
async def test_generate_images_unknown_provider(tmp_path):
    with pytest.raises(ImageGenError):
        await generate_images(["a"], provider="midjourney", model=None, api_key="k", out_dir=tmp_path)


@pytest.mark.asyncio
async def test_generate_images_missing_key(tmp_path):
    with pytest.raises(ImageGenError):
        await generate_images(["a"], provider="openai", model=None, api_key=None, out_dir=tmp_path)
