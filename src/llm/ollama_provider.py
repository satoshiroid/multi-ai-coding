"""Ollama provider — fully free / offline local LLMs (e.g. qwen2.5-coder).

Talks to a local Ollama server over HTTP. Ideal for air-gapped runs where
confidential design data must never leave the machine (PDF3 architecture).
"""

from __future__ import annotations

from typing import Any

import httpx

from src.llm.provider import LLMProvider, LLMResponse, ProviderConfig
from src.models import LlmMessage


class OllamaProvider(LLMProvider):
    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._base_url = (config.base_url or "http://localhost:11434").rstrip("/")

    async def complete(self, messages: list[LlmMessage], **opts: Any) -> LLMResponse:
        payload = {
            "model": self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "temperature": opts.get("temperature", self.config.temperature),
                "num_predict": opts.get("max_tokens", self.config.max_tokens),
            },
        }

        async with httpx.AsyncClient(timeout=opts.get("timeout", 300.0)) as client:
            resp = await client.post(f"{self._base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = data.get("message", {}).get("content", "")
        return LLMResponse(text=text, provider=self.name, model=self.config.model, raw=data)
