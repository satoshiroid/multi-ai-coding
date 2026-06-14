"""Anthropic (Claude) provider — used for L1/L2 high-reasoning tiers.

Requires a pay-as-you-go API key (ANTHROPIC_API_KEY). A Claude Pro/Max
subscription does NOT grant API access.
"""

from __future__ import annotations

from typing import Any

from src.llm.provider import LLMProvider, LLMResponse, ProviderConfig
from src.models import LlmMessage


class AnthropicProvider(LLMProvider):
    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None  # lazy — avoid importing SDK unless used

    def _get_client(self):
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - import guard
                raise RuntimeError(
                    "anthropic package not installed. Run `pip install anthropic`."
                ) from exc
            if not self.config.api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Anthropic provider unavailable."
                )
            self._client = AsyncAnthropic(api_key=self.config.api_key)
        return self._client

    async def complete(self, messages: list[LlmMessage], **opts: Any) -> LLMResponse:
        client = self._get_client()

        # Anthropic takes `system` separately from the message list.
        system_parts = [m.content for m in messages if m.role == "system"]
        chat = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        # claude-opus-4 and claude-sonnet-4 series deprecated the temperature
        # parameter (extended-thinking models default to 1.0 and ignore it).
        # Only include temperature for older model families.
        _model = self.config.model.lower()
        _skip_temp = any(
            _model.startswith(prefix)
            for prefix in ("claude-opus-4", "claude-sonnet-4", "claude-haiku-4")
        )
        extra: dict[str, Any] = {}
        if not _skip_temp:
            extra["temperature"] = opts.get("temperature", self.config.temperature)

        resp = await client.messages.create(
            model=self.config.model,
            max_tokens=opts.get("max_tokens", self.config.max_tokens),
            system="\n\n".join(system_parts) if system_parts else "",
            messages=chat,
            **extra,
        )

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        return LLMResponse(text=text, provider=self.name, model=self.config.model, raw=resp)
