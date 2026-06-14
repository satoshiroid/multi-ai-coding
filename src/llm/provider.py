"""Abstract LLM provider interface.

All concrete providers (Anthropic, Gemini, Ollama, mock) implement this so the
rest of the system is provider-agnostic. The tier→provider mapping lives in
``config/settings.yaml`` and is resolved by :mod:`src.llm.factory`.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.models import LlmMessage


@dataclass
class ProviderConfig:
    """Resolved configuration for a single provider instance."""

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.2
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Normalized response from any provider."""

    text: str
    provider: str
    model: str
    raw: Any = None

    def parse_json(self) -> dict[str, Any]:
        """Best-effort extraction of a JSON object from the response text.

        Handles bare JSON, ```json fenced blocks, and leading/trailing prose.
        """
        return extract_json(self.text)


class LLMProvider(ABC):
    """Common interface implemented by every provider."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.provider

    @abstractmethod
    async def complete(self, messages: list[LlmMessage], **opts: Any) -> LLMResponse:
        """Run a chat completion and return the assistant text."""

    async def complete_structured(
        self, messages: list[LlmMessage], schema_hint: str | None = None, **opts: Any
    ) -> dict[str, Any]:
        """Run a completion and coerce the output into a JSON object.

        Default implementation appends a JSON-only instruction and parses the
        result. Providers with native structured output may override this.
        """
        instruction = (
            "You must respond with a single valid JSON object only. "
            "Do not wrap it in markdown fences or add prose."
        )
        if schema_hint:
            instruction += f" The JSON must match this schema: {schema_hint}"

        augmented = list(messages)
        if augmented and augmented[-1].role == "user":
            augmented[-1] = LlmMessage(
                role="user", content=f"{augmented[-1].content}\n\n{instruction}"
            )
        else:
            augmented.append(LlmMessage(role="user", content=instruction))

        response = await self.complete(augmented, **opts)
        return response.parse_json()


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from ``text``.

    Tolerant of ```json fences and surrounding prose so it works across
    providers that don't honour "JSON only" perfectly.
    """
    stripped = text.strip()

    # Strip a leading/trailing markdown fence if present.
    if stripped.startswith("```"):
        # remove first fence line
        newline = stripped.find("\n")
        if newline != -1:
            stripped = stripped[newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -3]
        stripped = stripped.strip()

    # Fast path: the whole thing is JSON.
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Fallback: find the outermost balanced {...} span.
    start = stripped.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response: {text!r}")

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : i + 1]
                return json.loads(candidate)

    raise ValueError(f"Unbalanced JSON object in response: {text!r}")
