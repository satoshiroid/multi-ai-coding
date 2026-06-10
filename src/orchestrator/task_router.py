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
        Domain.DESIGN: (
            "製品の外観コンセプトデザインとレンダリングを生成してください。\n"
            "artifacts: 外観仕様のみ（コード不要）。"
        ),
        Domain.MECHA: (
            "筐体のパラメトリック設計仕様を生成してください（コード・スクリプト不要）。\n"
            "【必須】metadata に以下の数値(mm)を必ず含めること:\n"
            "  inner_dim_x_mm, inner_dim_y_mm, inner_dim_z_mm\n"
            "artifacts: 外形寸法・材質・公差などのキースペックのみ。"
        ),
        Domain.CIRCUIT: (
            "回路ブロック・PCBレイアウト仕様を生成してください（コード不要）。\n"
            "【必須】metadata に以下の数値(mm)を必ず含めること:\n"
            "  pcb_dim_x_mm, pcb_dim_y_mm\n"
            "artifacts: 主要部品・MCU・インターフェース仕様のみ。"
        ),
        Domain.SOFTWARE: (
            "ファームウェアの設計仕様とキーロジックを生成してください（完全コード不要）。\n"
            "artifacts: アーキテクチャ・主要モジュール・インターフェース仕様のみ。"
        ),
    }[domain]
    return f"{base}\n\n# プロジェクト概要\n{summary}".strip()
