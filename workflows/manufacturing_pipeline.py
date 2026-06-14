"""Declarative definition of the development pipelines.

This module only *describes* the stages (data, no behaviour). The
:class:`~src.orchestrator.pm_orchestrator.PMOrchestrator` interprets this
description to drive the run, so the flow can be inspected/tested independently
of execution.

Two pipelines are defined:

* :data:`PIPELINE` — the canonical 9-stage hardware manufacturing flow (PDF1):
  design → mecha+circuit → consistency → firmware, with three owner gates.
* :data:`APP_PIPELINE` — a software-application flow for projects with no
  physical product: UI design → architecture → implementation, with the same
  three owner gates. Selected when the PM classifies the requirement as
  ``project_type: "app"`` (see :func:`select_pipeline`).

Both pipelines share stages 1–2 (requirement input, PM planning), so the
orchestrator can switch pipelines right after the PM's plan is produced.
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
    # Stage-specific default instruction per domain. Overrides the PM subtask
    # when set (the PM subtask is appended as context) — used when the same
    # domain runs at multiple stages with different intent (e.g. software
    # "architecture" at stage 5 vs "implementation" at stage 7 in APP_PIPELINE).
    instruction_hints: dict = field(default_factory=dict)
    # Implementation stages may let workers emit code files in artifacts.
    allow_code: bool = False
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Hardware pipeline — the canonical 9-stage flow from PDF1's stage table.
# --------------------------------------------------------------------------- #
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
        instruction_hints={
            Domain.CIRCUIT: (
                "承認済み仕様に基づき製造データを確定してください。\n"
                "artifacts: Gerber出力（ファイル名または出力手順）・最終ドリル/外形データの仕様。\n"
                "metadata.bom は前ステージから変更があった部品のみ記載すること（重複追加しない）。"
            ),
            Domain.SOFTWARE: (
                "承認済みの回路仕様（MCU・ピンマッピング・インターフェース）にグラウンディングして、"
                "ファームウェアの実装設計を確定してください。\n"
                "artifacts: モジュール構成・主要ロジック・ビルド/書き込み手順（コンパクトに）。"
            ),
        },
    ),
    Stage(
        index=9,
        key="final_signoff",
        kind=StageKind.HITL,
        title="最終サインオフ",
        gate="final_signoff",
        on_reject_to=8,
        notes="全製造用ファイル(Gerber/STEP/ファームウェア/BOM)を確認。承認でアーカイブ。",
    ),
)


# --------------------------------------------------------------------------- #
# App pipeline — software-only projects (web / mobile / desktop / CLI).
# No mecha/circuit work, no physical consistency check; instead the flow is
# UI design → architecture → implementation with the same three owner gates.
# --------------------------------------------------------------------------- #
APP_PIPELINE: tuple[Stage, ...] = (
    PIPELINE[0],   # 1: requirements (shared)
    PIPELINE[1],   # 2: PM architecture (shared)
    Stage(
        index=3,
        key="ui_design",
        kind=StageKind.WORKER,
        title="UI/UXデザイン",
        domains=(Domain.DESIGN,),
        notes="デザインワーカーが画面設計・ワイヤーフレーム・遷移フローを作成。",
        instruction_hints={
            Domain.DESIGN: (
                "アプリのUI/UXデザインを生成してください。\n"
                "artifacts.sketch_prompts: 主要画面のUIモックアップを描くための画像生成プロンプトを"
                "2-3案（異なるレイアウト/トーンの方向性。各々が1枚のUIモックアップ画像になる具体的な"
                "視覚描写。例: 配色・主要コンポーネント配置・画面構成）。\n"
                "artifacts.design_spec: 画面一覧・各画面の主要コンポーネント・"
                "画面遷移フロー・配色/タイポグラフィ（コンパクトに）。\n"
                "metadata: target_platform (web/ios/android/desktop/cli) を必ず含めること。"
            ),
        },
    ),
    Stage(
        index=4,
        key="gate_design",
        kind=StageKind.HITL,
        title="第一承認ゲート(UIデザイン)",
        gate="design_approval",
        on_reject_to=3,
        notes="画面設計・遷移フローをレビュー。拒否/修正でステージ3へループバック。",
    ),
    Stage(
        index=5,
        key="app_architecture",
        kind=StageKind.WORKER,
        title="アプリアーキテクチャ設計",
        domains=(Domain.SOFTWARE,),
        notes="技術スタック選定・モジュール構成・データモデル・API設計。",
        instruction_hints={
            Domain.SOFTWARE: (
                "アプリのアーキテクチャ設計を生成してください（完全コード不要）。\n"
                "artifacts: 技術スタック・モジュール構成・データモデル・"
                "API/画面インターフェース仕様（コンパクトに）。\n"
                "metadata: tech_stack（言語・フレームワーク）を必ず含めること。"
            ),
        },
    ),
    Stage(
        index=6,
        key="gate_spec",
        kind=StageKind.HITL,
        title="第二承認ゲート(アーキテクチャ)",
        gate="spec_approval",
        on_reject_to=5,
        notes="技術スタック・モジュール構成・データモデルをレビュー。拒否でステージ5へ。",
    ),
    Stage(
        index=7,
        key="implementation",
        kind=StageKind.WORKER,
        title="実装",
        domains=(Domain.SOFTWARE,),
        allow_code=True,
        notes="承認済みアーキテクチャに基づくMVPコード生成。",
        instruction_hints={
            Domain.SOFTWARE: (
                "承認済みアーキテクチャに基づき、アプリのMVP実装を生成してください。\n"
                "artifacts.files: {\"ファイルパス\": \"コード\"} 形式。"
                "最重要ファイルのみ・合計200行以内（プロジェクト骨格＋中核ロジック）。\n"
                "artifacts.setup: セットアップ/実行手順（簡潔に）。"
            ),
        },
    ),
    Stage(
        index=8,
        key="final_signoff",
        kind=StageKind.HITL,
        title="最終サインオフ",
        gate="final_signoff",
        on_reject_to=7,
        notes="生成コード・セットアップ手順を確認。承認でアーカイブ。",
    ),
)


# --------------------------------------------------------------------------- #
# Pipeline selection
# --------------------------------------------------------------------------- #
_APP_TYPE_ALIASES = {"app", "software", "application", "web", "mobile", "saas"}


def normalize_project_type(value: object) -> str:
    """Map the PM's free-form project_type onto "app" or "hardware".

    Defaults to "hardware" (the richer pipeline) on anything unrecognized, so a
    misclassification still routes through the stricter physical flow.
    """
    if isinstance(value, str) and value.strip().lower() in _APP_TYPE_ALIASES:
        return "app"
    return "hardware"


def select_pipeline(project_type: object) -> tuple[Stage, ...]:
    """Return the stage list for the PM-classified project type."""
    return APP_PIPELINE if normalize_project_type(project_type) == "app" else PIPELINE


def get_stage(index: int, pipeline: tuple[Stage, ...] = PIPELINE) -> Stage:
    """Return the stage with the given 1-based index."""
    for stage in pipeline:
        if stage.index == index:
            return stage
    raise KeyError(f"No stage with index {index}")


def stage_count(pipeline: tuple[Stage, ...] = PIPELINE) -> int:
    return len(pipeline)
