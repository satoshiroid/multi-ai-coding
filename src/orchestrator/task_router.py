"""Task routing: turn the PM's plan + stage definitions into TaskSpecs.

The router is deliberately dumb glue — the PM agent decides *what* to do; this
module just packages those decisions into :class:`TaskSpec` objects with stable
ids and the shared context attached, so workers receive a uniform input.

Instruction priority per domain at a stage:

1. The stage's ``instruction_hints[domain]`` when defined — stage intent wins,
   because the same domain can run at multiple stages with different goals
   (e.g. software "architecture" vs "implementation" in the app pipeline). The
   PM's subtask, when present, is appended as context.
2. The PM plan's per-domain subtask instruction.
3. A generic per-domain fallback instruction.
"""

from __future__ import annotations

import uuid
from typing import Any

from src.models import Domain, TaskSpec
from workflows.manufacturing_pipeline import Stage


def make_task(
    domain: Domain,
    instruction: str,
    context: dict[str, Any] | None = None,
    feedback: str | None = None,
    allow_code: bool = False,
) -> TaskSpec:
    """Build a single :class:`TaskSpec` with a unique id."""
    return TaskSpec(
        task_id=f"{domain.value}-{uuid.uuid4().hex[:8]}",
        domain=domain,
        instruction=instruction,
        context=context or {},
        feedback=feedback,
        allow_code=allow_code,
    )


def tasks_for_stage(
    stage: Stage,
    plan: dict[str, Any],
    shared_context: dict[str, Any],
    feedback: str | None = None,
) -> list[TaskSpec]:
    """Build the TaskSpecs for a stage's domains.

    Resolves each domain's instruction per the priority documented in the
    module docstring, and attaches the shared context snapshot so workers can
    honour cross-domain constraints.
    """
    subtasks = {st.get("domain"): st.get("instruction", "") for st in plan.get("subtasks", [])}

    tasks: list[TaskSpec] = []
    for domain in stage.domains:
        subtask = subtasks.get(domain.value) or ""
        hint = stage.instruction_hints.get(domain, "")

        if hint:
            instruction = hint
            if subtask:
                instruction += f"\n\n# PM計画からの指示\n{subtask}"
            elif plan.get("summary"):
                instruction += f"\n\n# プロジェクト概要\n{plan['summary']}"
        else:
            instruction = subtask or _default_instruction(domain, plan)

        tasks.append(
            make_task(
                domain=domain,
                instruction=instruction,
                context=dict(shared_context),
                feedback=feedback,
                allow_code=stage.allow_code,
            )
        )
    return tasks


def _default_instruction(domain: Domain, plan: dict[str, Any]) -> str:
    """Fallback instruction when neither stage hint nor PM subtask exists."""
    summary = plan.get("summary", "")
    base = {
        Domain.DESIGN: (
            "製品の外観コンセプトデザインを生成してください。\n"
            "artifacts.blender_script: 製品の3Dモックアップを作成するBlender Pythonスクリプト"
            "（40行以内・基本プリミティブのみ・bpy使用・シーンクリア→モデル作成→マテリアル設定のみ）。\n"
            "artifacts.design_spec: 外観仕様（コンパクト）。"
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
