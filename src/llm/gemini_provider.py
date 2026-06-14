"""Google Gemini provider — used for L3 implement/debug tiers (free tier).

Handles 429 rate-limit responses (common on the free tier) with exponential
backoff. When retries are exhausted the orchestrator's factory-level fallback
takes over.

Uses the new google-genai SDK (google-generativeai is deprecated).
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
        self._client = None
        self._retries = int(config.extra.get("rate_limit_retries", 3))
        self._base_delay = float(config.extra.get("rate_limit_base_delay", 2.0))

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "google-genai not installed. Run `pip install google-genai`."
                ) from exc
            if not self.config.api_key:
                raise RuntimeError("GEMINI_API_KEY is not set. Gemini provider unavailable.")
            self._client = genai.Client(api_key=self.config.api_key)
        return self._client

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        return "429" in text or "quota" in text or "rate" in text or "resource_exhausted" in text

    async def complete(self, messages: list[LlmMessage], **opts: Any) -> LLMResponse:
        client = self._get_client()
        from google.genai import types  # noqa: PLC0415

        # Fold system prompts into the first user turn; map roles.
        system_parts = [m.content for m in messages if m.role == "system"]
        contents: list[types.Content] = []
        for m in messages:
            if m.role == "system":
                continue
            role = "user" if m.role == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=m.content)]))

        if system_parts and contents:
            prefix = "\n\n".join(system_parts) + "\n\n"
            contents[0] = types.Content(
                role=contents[0].role,
                parts=[types.Part(text=prefix + contents[0].parts[0].text)],
            )
        elif system_parts:
            contents.append(
                types.Content(role="user", parts=[types.Part(text="\n\n".join(system_parts))])
            )

        cfg = types.GenerateContentConfig(
            max_output_tokens=opts.get("max_tokens", self.config.max_tokens),
            temperature=opts.get("temperature", self.config.temperature),
        )

        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = await client.aio.models.generate_content(
                    model=self.config.model, contents=contents, config=cfg
                )
                return LLMResponse(
                    text=resp.text, provider=self.name, model=self.config.model, raw=resp
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if self._is_rate_limit(exc) and attempt < self._retries:
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                if self._is_rate_limit(exc):
                    raise GeminiRateLimitError(str(exc)) from exc
                raise

        raise GeminiRateLimitError(str(last_exc))  # pragma: no cover
