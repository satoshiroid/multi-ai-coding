"""L2 Senior manager agent.

A senior sits between the PM (L1) and a domain worker (L3). It is invoked only
when a worker's confidence falls below the escalation threshold: its job is to
read the low-confidence result and either unblock the worker with actionable
guidance or escalate to the human owner when the issue is out of scope for an
automated fix.
"""

from __future__ import annotations

import json

from src.agents.base_agent import BaseAgent
from src.llm.factory import TieredLLM
from src.models import AgentResult, TaskSpec


class SeniorAgent(BaseAgent):
    """Mid-tier (L2) reviewer that resolves worker escalations."""

    def __init__(self, name: str, system_prompt: str, llm: TieredLLM):
        super().__init__(name=name, tier="L2", system_prompt=system_prompt, llm=llm)

    async def advise(self, task: TaskSpec, worker_result: AgentResult) -> dict:
        """Advise on a worker result that escalated due to low confidence.

        Returns whether the senior could resolve it, the guidance to feed back
        into a worker re-run, and — when the blocker needs a human call — a
        request to escalate to the owner with a reason.
        """
        schema_hint = (
            '{"resolved": bool, "guidance": str, '
            '"escalate_to_owner": bool, "reason": str, '
            '"proposals": [{"label": str (short option name), '
            '"action": str (concrete instruction the worker follows if chosen)}]}'
        )

        # Surface the worker's artifacts/metadata so the senior reviews evidence,
        # not just the prose summary.
        artifacts_json = json.dumps(worker_result.artifacts, ensure_ascii=False, indent=2)

        prompt = (
            f"# Escalated task ({task.domain.value})\n"
            f"{task.instruction.strip()}\n\n"
            "# Worker result (escalated: confidence below threshold)\n"
            f"confidence_score: {worker_result.confidence_score}\n"
            f"summary: {worker_result.summary}\n"
            f"artifacts:\n{artifacts_json}\n\n"
            "# Your job\n"
            "Diagnose why confidence is low and decide the next step. If you can "
            "unblock the worker, set 'resolved' true and put concrete, actionable "
            "'guidance' for a re-run. If the blocker requires an owner decision "
            "(scope, budget, missing requirement), set 'escalate_to_owner' true, "
            "explain why in 'reason', and ALWAYS provide 2-4 distinct, concrete "
            "'proposals' the owner can choose between — each with a short 'label' "
            "and a precise 'action' the worker will execute if selected (not vague "
            "advice). Make the trade-offs between proposals clear in 'reason'."
        )
        return await self.run_structured(prompt, schema_hint=schema_hint)
