"""Task routing: turn the PM's plan + stage definitions into TaskSpecs.

The router is deliberately dumb glue — the PM agent decides *what* to do; this
module just packages those decisions into :class:`TaskSpec` objects with stable
ids and the shared context attached, so workers receive a uniform input.
"""

from __future__ import annotations

import uuid
from typing import Any

from src.models import Domain, TaskSpec


def make_task(
    domain: Domain,
    instruction: str,
    context: dict[str, Any] | None = None,
    feedback: str | None = None,
) -> TaskSpec:
    """Build a single :class:`TaskSpec` with a unique id."""
    return TaskSpec(
        task_id=f"{domain.value}-{uuid.uuid4().hex[:8]}",
        domain=domain,
        instruction=instruction,
        context=context or {},
        feedback=feedback,
    )


def tasks_for_stage(
    domains: tuple[Domain, ...],
    plan: dict[str, Any],
    shared_context: dict[str, Any],
    feedback: str | None = None,
) -> list[TaskSpec]:
    """Build the TaskSpecs for a stage's domains.

    Pulls per-domain instructions out of the PM ``plan`` (its ``subtasks``
    list) when available, falling back to a generic instruction. The shared
    context snapshot is attached so workers can honour cross-domain constraints.
    """
    subtasks = {st.get("domain"): st.get("instruction", "") for st in plan.get("subtasks", [])}

    tasks: list[TaskSpec] = []
    for domain in domains:
        instruction = subtasks.get(domain.value) or _default_instruction(domain, plan)
        tasks.append(
            make_task(
                domain=domain,
                instruction=instruction,
                context=dict(shared_context),
                feedback=feedback,
            )
        )
    return tasks


def _default_instruction(domain: Domain, plan: dict[str, Any]) -> str:
    """Fallback instruction when the PM didn't emit a per-domain subtask."""
    summary = plan.get("summary", "")
    base = {
        Domain.DESIGN: "製品の外観コンセプトデザインとレンダリングを生成してください。",
        Domain.MECHA: "スプレッドシート駆動でパラメトリックな筐体ソリッドを作成してください。",
        Domain.CIRCUIT: "回路ブロックを組み立て、PCB配置・配線・BOMを生成してください。",
        Domain.SOFTWARE: "回路出力に基づき組み込みファームウェアとテストを生成してください。",
    }[domain]
    return f"{base}\n\n# プロジェクト概要\n{summary}".strip()
