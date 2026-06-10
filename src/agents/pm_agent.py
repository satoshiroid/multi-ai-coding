"""L1 Project Manager agent.

The PM sits above the senior managers: it turns the owner's natural-language
requirement into a structured master plan (which domains, which subtasks) and
later interprets cross-domain consistency reports into an actionable decision.
It never executes engineering work itself — it only decomposes and adjudicates.
"""

from __future__ import annotations

from src.agents.base_agent import BaseAgent
from src.llm.factory import TieredLLM


class PMAgent(BaseAgent):
    """Top-tier (L1) planner and cross-domain arbiter."""

    def __init__(self, name: str, system_prompt: str, llm: TieredLLM):
        super().__init__(name=name, tier="L1", system_prompt=system_prompt, llm=llm)

    async def plan(self, requirement: str) -> dict:
        """Decompose an owner requirement into a master plan.

        The returned ``subtasks`` feed directly into the L3 workers, so the
        ``domain`` strings must match the :class:`~src.models.Domain` values.
        """
        schema_hint = (
            '{"project_type": "hardware" | "app", "domains": [str], "summary": str, '
            '"subtasks": [{"domain": str, "instruction": str}]}'
        )
        prompt = (
            "# Owner requirement\n"
            f"{requirement.strip()}\n\n"
            "# Your job\n"
            "Decompose this into a master development plan.\n\n"
            "First, classify the project:\n"
            "- project_type = \"hardware\" — involves physical devices, electronics, "
            "enclosures, PCBs, sensors, actuators, or embedded firmware.\n"
            "- project_type = \"app\" — purely software: web/mobile/desktop/CLI/SaaS "
            "application with no custom hardware.\n\n"
            "Then decide which engineering domains are needed:\n"
            "  Hardware: any of design, mecha, circuit, software\n"
            "  App: design (UI/UX) and software (backend/frontend/mobile)\n\n"
            "Write one concrete, self-contained instruction per subtask. "
            "Each subtask's 'domain' must be one of: design, mecha, circuit, software.\n"
            "Provide a short overall summary of the plan."
        )
        return await self.run_structured(prompt, schema_hint=schema_hint)

    async def review_consistency(
        self, mecha_summary: str, circuit_summary: str, report: str
    ) -> dict:
        """Interpret a mecha/circuit consistency report into a decision.

        ``report`` is the output of the consistency tool; the PM decides whether
        the two domains are compatible and what action (if any) to take.
        """
        schema_hint = '{"compatible": bool, "action": str, "notes": str}'
        prompt = (
            "# Mechanical design summary\n"
            f"{mecha_summary.strip()}\n\n"
            "# Circuit design summary\n"
            f"{circuit_summary.strip()}\n\n"
            "# Automated consistency report\n"
            f"{report.strip()}\n\n"
            "# Your job\n"
            "Judge whether the mechanical and circuit designs are physically "
            "compatible. Set 'compatible' accordingly, recommend a concrete "
            "'action' (e.g. which domain must revise and how), and add any "
            "'notes' the team should know."
        )
        return await self.run_structured(prompt, schema_hint=schema_hint)
