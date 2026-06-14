"""OpenAI (GPT) provider.

Requires OPENAI_API_KEY. Uses the official ``openai`` SDK (AsyncOpenAI) lazily
so importing this module never forces the dependency — mirrors the Anthropic
provider's structure.
"""

from __future__ import annotations

from typing import Any

from src.llm.provider import LLMProvider, LLMResponse, ProviderConfig
from src.models import LlmMessage


class OpenAIProvider(LLMProvider):
    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None  # lazy — avoid importing SDK unless used

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - import guard
                raise RuntimeError(
                    "openai package not installed. Run `pip install openai`."
                ) from exc
            if not self.config.api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set. OpenAI provider unavailable."
                )
            self._client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url or None,
            )
        return self._client

    async def complete(self, messages: list[LlmMessage], **opts: Any) -> LLMResponse:
        client = self._get_client()

        chat = [{"role": m.role, "content": m.content} for m in messages]

        # Reasoning models (o-series, gpt-5) reject `temperature` and use
        # `max_completion_tokens` exclusively; classic chat models accept both.
        # Use `max_completion_tokens` everywhere (current standard) and only send
        # temperature for the classic families.
        _model = self.config.model.lower()
        _reasoning = _model.startswith(("o1", "o3", "o4", "gpt-5"))
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": chat,
            "max_completion_tokens": opts.get("max_tokens", self.config.max_tokens),
        }
        if not _reasoning:
            kwargs["temperature"] = opts.get("temperature", self.config.temperature)

        resp = await client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, provider=self.name, model=self.config.model, raw=resp)
