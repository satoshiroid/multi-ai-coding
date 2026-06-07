"""Declarative definition of the 9-stage manufacturing pipeline (PDF1).

This module only *describes* the stages (data, no behaviour). The
:class:`~src.orchestrator.pm_orchestrator.PMOrchestrator` interprets this
description to drive the run, so the flow can be inspected/tested independently
of execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.models import Domain


class StageKind(str, Enum):
    """What the orchestrator does at a stage."""

    INPUT = "input"                  # owner provides the requirement
    PM = "pm"                        # L1 planning / synthesis
    WORKER = "worker"                # a single L3 worker runs
    PARALLEL = "parallel"            # several L3 workers run concurrently
    CONSISTENCY = "consistency"      # PM cross-domain numeric check
    HITL = "hitl"                    # owner phase-gate approval
    ARCHIVE = "archive"              # finalize / archive manufacturing data


@dataclass(frozen=True)
class Stage:
    """One node in the pipeline."""

    index: int
    key: str
    kind: StageKind
    title: str
    domains: tuple[Domain, ...] = ()
    gate: str | None = None                 # for HITL stages
    on_reject_to: int | None = None         # stage index to loop back to on revise/reject
    notes: str = ""
    extra: dict = field(default_factory=dict)


# The canonical 9-stage flow from PDF1's stage table.
PIPELINE: tuple[Stage, ...] = (
    Stage(
        index=1,
        key="requirements",
        kind=StageKind.INPUT,
        title="要件定義の開始",
        notes="オーナーが製品アイデア・目標コスト・機能要件を自然言語で入力。",
    ),
    Stage(
        index=2,
        key="architecture",
        kind=StageKind.PM,
        title="システムアーキテクチャ策定",
        notes="PMが要求を解析し、物理制約・電子部品・ソフト機能を特定しマスター計画を作成。",
    ),
    Stage(
        index=3,
        key="concept_design",
        kind=StageKind.WORKER,
        title="コンセプトデザイン",
        domains=(Domain.DESIGN,),
        notes="意匠ワーカーがBlenderで外観デザインを生成、空間推論で初期配置。",
    ),
    Stage(
        index=4,
        key="gate_design",
        kind=StageKind.HITL,
        title="第一承認ゲート(デザイン)",
        gate="design_approval",
        on_reject_to=3,
        notes="レンダリング画像と構成案をレビュー。拒否/修正でステージ3へループバック。",
    ),
    Stage(
        index=5,
        key="engineering",
        kind=StageKind.PARALLEL,
        title="エンジニアリング並列処理",
        domains=(Domain.MECHA, Domain.CIRCUIT),
        notes="メカ設計(筐体)と回路設計(論理・配置)を並列実行。",
    ),
    Stage(
        index=6,
        key="consistency",
        kind=StageKind.CONSISTENCY,
        title="ドメイン間の整合性同期",
        domains=(Domain.MECHA, Domain.CIRCUIT),
        on_reject_to=5,
        notes="筐体内寸(5A)と基板外形寸法(5B)を数値比較。干渉ありなら5へ差し戻し。",
    ),
    Stage(
        index=7,
        key="gate_spec",
        kind=StageKind.HITL,
        title="第二承認ゲート(仕様・コスト)",
        gate="spec_cost_approval",
        on_reject_to=5,
        notes="BOM総コスト・回路図・筐体3Dをレビュー。拒否でステージ5へループバック。",
    ),
    Stage(
        index=8,
        key="manufacturing",
        kind=StageKind.PARALLEL,
        title="製造データの確定",
        domains=(Domain.CIRCUIT, Domain.SOFTWARE),
        notes="回路側:Gerber出力 / ソフト側:ネットリスト参照でファームウェア生成。",
    ),
    Stage(
        index=9,
        key="final_signoff",
        kind=StageKind.HITL,
        title="最終サインオフ",
        gate="final_signoff",
        notes="全製造用ファイル(Gerber/STEP/ファームウェア/BOM)を確認。承認でアーカイブ。",
    ),
)


def get_stage(index: int) -> Stage:
    """Return the stage with the given 1-based index."""
    for stage in PIPELINE:
        if stage.index == index:
            return stage
    raise KeyError(f"No stage with index {index}")


def stage_count() -> int:
    return len(PIPELINE)
