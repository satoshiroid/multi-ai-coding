"""Build LLM providers from configuration, with tier mapping and fallback.

``config/settings.yaml`` maps each tier (L1/L2/L3) to a primary provider+model
and an optional fallback. :class:`TieredLLM` wraps the pair so a worker simply
asks for its tier and transparently gets failover (e.g. Gemini 429 → Ollama).
"""

from __future__ import annotations

import os
from typing import Any

from src.llm.provider import LLMProvider, LLMResponse, ProviderConfig
from src.models import LlmMessage

# Provider registry — name → class. Imported lazily inside build to keep the
# import graph light, but the mapping itself is cheap.
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.mock_provider import MockProvider
from src.llm.ollama_provider import OllamaProvider

_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
    "mock": MockProvider,
}


class TieredLLM:
    """A primary provider with an optional fallback for one tier."""

    def __init__(self, tier: str, primary: LLMProvider, fallback: LLMProvider | None = None):
        self.tier = tier
        self.primary = primary
        self.fallback = fallback

    async def complete(self, messages: list[LlmMessage], **opts: Any) -> LLMResponse:
        try:
            return await self.primary.complete(messages, **opts)
        except Exception:  # noqa: BLE001 - fail over on any primary error
            if self.fallback is None:
                raise
            return await self.fallback.complete(messages, **opts)

    async def complete_structured(
        self, messages: list[LlmMessage], schema_hint: str | None = None, **opts: Any
    ) -> dict[str, Any]:
        try:
            return await self.primary.complete_structured(messages, schema_hint, **opts)
        except Exception:  # noqa: BLE001
            if self.fallback is None:
                raise
            return await self.fallback.complete_structured(messages, schema_hint, **opts)


def _resolve_provider_config(
    provider: str, model: str, providers_cfg: dict[str, Any]
) -> ProviderConfig:
    """Turn a provider name + model + global provider settings into a config."""
    pcfg = providers_cfg.get(provider, {})

    api_key = None
    if "api_key_env" in pcfg:
        api_key = os.environ.get(pcfg["api_key_env"])

    base_url = None
    if "base_url_env" in pcfg:
        base_url = os.environ.get(pcfg["base_url_env"])

    extra = {
        k: v
        for k, v in pcfg.items()
        if k not in ("api_key_env", "base_url_env", "max_tokens", "temperature")
    }

    return ProviderConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_tokens=int(pcfg.get("max_tokens", 4096)),
        temperature=float(pcfg.get("temperature", 0.2)),
        extra=extra,
    )


def build_provider(
    provider: str, model: str, providers_cfg: dict[str, Any]
) -> LLMProvider:
    """Instantiate a single provider by name."""
    cls = _PROVIDER_CLASSES.get(provider)
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {provider!r}")
    config = _resolve_provider_config(provider, model, providers_cfg)
    return cls(config)


def _tier_env_override(tier: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay env-provided LLM selection on a tier's settings.yaml config.

    Lets the provider/model live in Git (GitHub Actions *variables*) or ``.env``
    rather than only in ``settings.yaml`` — API keys stay in Secrets. Precedence:
    tier-specific env (``LLM_L3_PROVIDER``) > global env (``LLM_PROVIDER``) >
    settings.yaml. Set provider and model together when switching providers, so
    you never pair (e.g.) ``anthropic`` with a leftover Gemini model name.
    """
    tu = tier.upper()
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg.items()}

    provider = os.environ.get(f"LLM_{tu}_PROVIDER") or os.environ.get("LLM_PROVIDER")
    model = os.environ.get(f"LLM_{tu}_MODEL") or os.environ.get("LLM_MODEL")
    if provider:
        out["provider"] = provider
    if model:
        out["model"] = model

    fb_provider = os.environ.get(f"LLM_{tu}_FALLBACK_PROVIDER") or os.environ.get(
        "LLM_FALLBACK_PROVIDER"
    )
    fb_model = os.environ.get(f"LLM_{tu}_FALLBACK_MODEL") or os.environ.get(
        "LLM_FALLBACK_MODEL"
    )
    if fb_provider or fb_model:
        fb = dict(out.get("fallback") or {})
        if fb_provider:
            fb["provider"] = fb_provider
        if fb_model:
            fb["model"] = fb_model
        out["fallback"] = fb

    return out


def build_tiered_llms(
    settings: dict[str, Any], *, force_mock: bool = False
) -> dict[str, TieredLLM]:
    """Build a ``{tier: TieredLLM}`` map from the full settings dict.

    Per-tier provider/model can be overridden via environment variables (see
    :func:`_tier_env_override`) so the LLM choice is selectable from Git without
    editing ``settings.yaml``. When ``force_mock`` is set, every tier uses the
    deterministic mock provider (used for ``--mock`` E2E runs and tests).
    """
    tiers_cfg: dict[str, Any] = settings.get("tiers", {})
    providers_cfg: dict[str, Any] = settings.get("providers", {})

    result: dict[str, TieredLLM] = {}
    for tier, raw_cfg in tiers_cfg.items():
        if force_mock:
            result[tier] = TieredLLM(tier, MockProvider())
            continue

        cfg = _tier_env_override(tier, raw_cfg)
        primary = build_provider(cfg["provider"], cfg["model"], providers_cfg)

        fallback = None
        fb = cfg.get("fallback")
        if fb:
            fallback = build_provider(fb["provider"], fb["model"], providers_cfg)

        result[tier] = TieredLLM(tier, primary, fallback)

    return result
