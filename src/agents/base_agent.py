"""Base class shared by every tier of agent (L1 PM / L2 Senior / L3 workers).

All agents are thin wrappers around a :class:`TieredLLM`: they own a fixed
system prompt (their persona/role) and convert a per-call user prompt into the
two-message chat shape every provider expects. Keeping this glue in one place
means the higher tiers only differ in *which* prompt they build, not in *how*
they talk to the model.
"""

from __future__ import annotations

from typing import Any

from src.llm.factory import TieredLLM
from src.models import LlmMessage


class BaseAgent:
    """An LLM-backed agent with a persistent system prompt."""

    def __init__(self, name: str, tier: str, system_prompt: str, llm: TieredLLM):
        self.name = name
        self.tier = tier
        self.system_prompt = system_prompt
        self.llm = llm

    def _messages(self, user_prompt: str) -> list[LlmMessage]:
        """Compose the standard [system, user] message pair for a call."""
        return [
            LlmMessage(role="system", content=self.system_prompt),
            LlmMessage(role="user", content=user_prompt),
        ]

    async def run_text(self, user_prompt: str, **opts: Any) -> str:
        """Free-form completion — returns the assistant's raw text."""
        response = await self.llm.complete(self._messages(user_prompt), **opts)
        return response.text

    async def run_structured(
        self, user_prompt: str, schema_hint: str | None = None, **opts: Any
    ) -> dict[str, Any]:
        """Structured completion — returns a parsed JSON object.

        Delegates JSON coercion/parsing to the provider so each tier just states
        the schema it wants via ``schema_hint``.
        """
        return await self.llm.complete_structured(
            self._messages(user_prompt), schema_hint, **opts
        )
