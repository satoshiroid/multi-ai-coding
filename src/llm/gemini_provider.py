"""Google Gemini provider — used for L3 implement/debug tiers (free tier).

Handles 429 rate-limit responses (common on the free tier) with exponential
backoff. When retries are exhausted the orchestrator's factory-level fallback
takes over.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.llm.provider import LLMProvider, LLMResponse, ProviderConfig
from src.models import LlmMessage


class GeminiRateLimitError(RuntimeError):
    """Raised when Gemini keeps returning 429 after all retries."""


class GeminiProvider(LLMProvider):
    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._model = None
        self._retries = int(config.extra.get("rate_limit_retries", 3))
        self._base_delay = float(config.extra.get("rate_limit_base_delay", 2.0))

    def _get_model(self):
        if self._model is None:
            try:
                import google.generativeai as genai
            except ImportError as exc:  # pragma: no cover - import guard
                raise RuntimeError(
                    "google-generativeai not installed. "
                    "Run `pip install google-generativeai`."
                ) from exc
            if not self.config.api_key:
                raise RuntimeError("GEMINI_API_KEY is not set. Gemini provider unavailable.")
            genai.configure(api_key=self.config.api_key)
            self._model = genai.GenerativeModel(self.config.model)
        return self._model

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return "429" in text or "quota" in text or "rate" in text or "resource_exhausted" in text

    async def complete(self, messages: list[LlmMessage], **opts: Any) -> LLMResponse:
        model = self._get_model()

        # Gemini: fold system prompts into the first turn; map roles.
        system_parts = [m.content for m in messages if m.role == "system"]
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            role = "user" if m.role == "user" else "model"
            contents.append({"role": role, "parts": [m.content]})

        if system_parts and contents:
            contents[0]["parts"][0] = "\n\n".join(system_parts) + "\n\n" + contents[0]["parts"][0]
        elif system_parts:
            contents.append({"role": "user", "parts": ["\n\n".join(system_parts)]})

        generation_config = {
            "max_output_tokens": opts.get("max_tokens", self.config.max_tokens),
            "temperature": opts.get("temperature", self.config.temperature),
        }

        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = await model.generate_content_async(
                    contents, generation_config=generation_config
                )
                return LLMResponse(
                    text=resp.text, provider=self.name, model=self.config.model, raw=resp
                )
            except Exception as exc:  # noqa: BLE001 - normalize SDK errors
                last_exc = exc
                if self._is_rate_limit(exc) and attempt < self._retries:
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                if self._is_rate_limit(exc):
                    raise GeminiRateLimitError(str(exc)) from exc
                raise

        raise GeminiRateLimitError(str(last_exc))  # pragma: no cover
