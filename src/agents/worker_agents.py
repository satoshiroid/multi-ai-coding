"""L3 domain workers — the agents that actually do engineering work.

Each worker turns a :class:`TaskSpec` into a structured :class:`AgentResult`.
The ``confidence_score`` on that result is the escalation signal the L2 senior
relies on, so parsing here is deliberately defensive: a malformed or partial
LLM response must still yield a valid (low-confidence) result rather than
crashing the pipeline.
"""

from __future__ import annotations

import json
from typing import Any

from src.agents.base_agent import BaseAgent
from src.llm.factory import TieredLLM
from src.models import AgentResult, Domain, TaskSpec

# JSON contract every worker asks the LLM to honour. Centralised so all four
# domains stay in sync with the AgentResult schema.
_RESULT_SCHEMA_HINT = (
    '{"summary": str, "confidence_score": int (0-100), '
    '"artifacts": object, "metadata": object}'
)


def _clamp_score(value: Any) -> int:
    """Coerce an arbitrary LLM value into a valid 0-100 confidence score.

    Anything non-numeric collapses to 0 (treated as "needs escalation") so a
    junk field never silently passes as high confidence.
    """
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


class BaseWorker(BaseAgent):
    """Common execution loop for all L3 workers; subclasses set ``domain``."""

    domain: Domain  # set by each concrete subclass

    def __init__(self, name: str, system_prompt: str, llm: TieredLLM):
        super().__init__(name=name, tier="L3", system_prompt=system_prompt, llm=llm)

    def _build_prompt(self, task: TaskSpec) -> str:
        """Render a task into a clear, sectioned prompt for the worker.

        Context and feedback are only included when present so the model isn't
        distracted by empty sections on a first-pass run.
        """
        sections = [f"# Task ({self.domain.value})", task.instruction.strip()]

        if task.context:
            # Pretty JSON keeps nested constraints/BOM readable for the model.
            context_json = json.dumps(task.context, ensure_ascii=False, indent=2)
            sections.append("# Context (shared constraints / prior results)")
            sections.append(context_json)

        if task.feedback:
            # Feedback means this is a re-run; make the revision request explicit.
            sections.append("# Feedback to address (this is a revision)")
            sections.append(task.feedback.strip())

        sections.append(
            "# Output\n"
            "Return your result as JSON. Be CONCISE — keep artifacts compact "
            "(key specs only, no exhaustive lists). Set confidence_score to your "
            "honest 0-100 confidence; a low score triggers senior review."
        )
        return "\n\n".join(sections)

    async def execute(self, task: TaskSpec) -> AgentResult:
        """Run the task and normalise the LLM output into an AgentResult."""
        prompt = self._build_prompt(task)
        try:
            data = await self.run_structured(prompt, schema_hint=_RESULT_SCHEMA_HINT)
        except (ValueError, json.JSONDecodeError) as exc:
            # Truncated / malformed JSON: return a zero-confidence result so the
            # pipeline continues and L2 escalation handles the retry.
            return AgentResult(
                task_id=task.task_id,
                domain=self.domain,
                summary=f"(JSON parse error — output was truncated: {exc})",
                confidence_score=0,
                artifacts={},
                metadata={"parse_error": str(exc)},
            )

        # Defensive extraction: never trust the model to return every field.
        summary = data.get("summary") or "(no summary produced)"
        artifacts = data.get("artifacts") or {}
        metadata = data.get("metadata") or {}

        return AgentResult(
            task_id=task.task_id,
            domain=self.domain,
            summary=str(summary),
            confidence_score=_clamp_score(data.get("confidence_score", 0)),
            artifacts=artifacts if isinstance(artifacts, dict) else {"value": artifacts},
            metadata=metadata if isinstance(metadata, dict) else {"value": metadata},
        )


class DesignWorker(BaseWorker):
    """Industrial design (Blender)."""

    domain = Domain.DESIGN


class MechaWorker(BaseWorker):
    """Mechanical design (FreeCAD)."""

    domain = Domain.MECHA


class CircuitWorker(BaseWorker):
    """Circuit / PCB design (KiCAD)."""

    domain = Domain.CIRCUIT


class SoftwareWorker(BaseWorker):
    """Firmware (C/C++)."""

    domain = Domain.SOFTWARE


_WORKER_CLASSES: dict[Domain, type[BaseWorker]] = {
    Domain.DESIGN: DesignWorker,
    Domain.MECHA: MechaWorker,
    Domain.CIRCUIT: CircuitWorker,
    Domain.SOFTWARE: SoftwareWorker,
}


def build_worker(
    domain: Domain, system_prompt: str, llm: TieredLLM, name: str | None = None
) -> BaseWorker:
    """Instantiate the worker for ``domain`` (defaults name to ``<domain>_worker``)."""
    cls = _WORKER_CLASSES.get(domain)
    if cls is None:
        raise ValueError(f"No worker registered for domain: {domain!r}")
    return cls(name=name or f"{domain.value}_worker", system_prompt=system_prompt, llm=llm)
