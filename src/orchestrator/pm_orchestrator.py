"""PMOrchestrator — the L1 hub that drives the 9-stage manufacturing pipeline.

This is the heart of the hub-and-spoke architecture: the PM never lets workers
talk to each other directly. It plans, delegates, ingests results into the
shared :class:`ContextStore`, runs cross-domain consistency checks, escalates
low-confidence work to L2 (and to the owner via HITL when L2 can't resolve it),
and pauses at phase gates for owner approval. All long waits are ``await``-ed,
so a gate can block for days (Discord) without busy-looping.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from src.agents import PMAgent, SeniorAgent, build_worker
from src.agents.worker_agents import BaseWorker
from src.hitl import HitlManager
from src.models import (
    AgentResult,
    BomItem,
    Domain,
    HitlDecision,
    HitlRequest,
    ProjectState,
    StageStatus,
    TaskSpec,
)
from src.orchestrator.consistency import ConsistencyChecker
from src.orchestrator.context_store import ContextStore
from src.orchestrator.state_store import StateStore
from src.orchestrator.task_router import tasks_for_stage
from workflows.manufacturing_pipeline import (
    PIPELINE,
    Stage,
    StageKind,
    get_stage,
    normalize_project_type,
    select_pipeline,
    stage_count,
)


class PMOrchestrator:
    """Drives one product project through the full pipeline."""

    def __init__(
        self,
        *,
        pm: PMAgent,
        senior: SeniorAgent,
        workers: dict[Domain, BaseWorker],
        hitl: HitlManager,
        context: ContextStore,
        consistency: ConsistencyChecker,
        state_store: StateStore | None = None,
        confidence_threshold: int = 70,
        notify_progress: bool = True,
    ):
        self.pm = pm
        self.senior = senior
        self.workers = workers
        self.hitl = hitl
        self.context = context
        self.consistency = consistency
        self.state_store = state_store
        self.confidence_threshold = confidence_threshold
        self.notify_progress = notify_progress
        # Active pipeline — starts as hardware default, updated after PM stage.
        self._active_pipeline: tuple[Stage, ...] = PIPELINE
        # Intake-provided project type ("app"/"hardware"); when set it pins the
        # pipeline and overrides the PM's own classification. None → PM decides.
        self._forced_project_type: str | None = None

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    async def run(
        self,
        requirement: str,
        *,
        project_id: str | None = None,
        thread_id: str | None = None,
        project_type: str | None = None,
    ) -> ProjectState:
        """Run the whole pipeline for one natural-language requirement.

        ``project_type`` ("app"/"hardware"), when provided, comes from the intake
        (e.g. the Discord channel the request arrived on) and pins the pipeline up
        front — the PM is never asked to classify. Left as ``None``, the PM
        classifies at stage 2 as before.
        """
        forced_type = (
            normalize_project_type(project_type) if project_type is not None else None
        )
        self._forced_project_type = forced_type
        state = ProjectState(
            project_id=project_id or f"proj-{uuid.uuid4().hex[:8]}",
            thread_id=thread_id,
            requirement=requirement,
            project_type=forced_type or "hardware",
            status=StageStatus.RUNNING,
        )
        self._persist(state)

        # Pin the pipeline up front when intake gave the type; else start on the
        # hardware default and switch after the PM classifies. Stages 1–2 are
        # shared between both pipelines, so an early pin never skips shared work.
        self._active_pipeline = select_pipeline(forced_type) if forced_type else PIPELINE
        plan: dict[str, Any] = {}
        index = 1
        while index <= stage_count(self._active_pipeline):
            stage = get_stage(index, self._active_pipeline)
            state.current_stage = stage.index
            self._persist(state)

            next_index, plan = await self._run_stage(stage, state, plan)

            # After PM planning: pick the right pipeline for this project type.
            # An intake-provided type wins over the PM's own classification.
            if stage.kind == StageKind.PM:
                resolved_type = self._forced_project_type or plan.get(
                    "project_type", "hardware"
                )
                self._active_pipeline = select_pipeline(resolved_type)
                state.project_type = normalize_project_type(resolved_type)
                self._persist(state)
                source = "intake" if self._forced_project_type else "PM判定"
                await self._progress(
                    state, f"🗂️ プロジェクト種別: {state.project_type} ({source})"
                )

            if next_index is None:  # rejected / aborted
                state.status = StageStatus.REJECTED
                self._persist(state)
                return state
            index = next_index

        state.status = StageStatus.DONE
        self._persist(state)
        archive_label = "アプリ成果物" if state.project_type == "app" else "製造データ"
        await self._progress(state, f"✅ プロジェクト完了。{archive_label}をアーカイブしました。")
        await self._post_deliverables(state)
        return state

    # ------------------------------------------------------------------ #
    # Stage dispatch
    # ------------------------------------------------------------------ #
    async def _run_stage(
        self, stage: Stage, state: ProjectState, plan: dict[str, Any]
    ) -> tuple[int | None, dict[str, Any]]:
        """Execute one stage; return (next stage index | None, updated plan)."""
        if stage.kind == StageKind.INPUT:
            return stage.index + 1, plan

        if stage.kind == StageKind.PM:
            plan = await self.pm.plan(state.requirement)
            await self._progress(state, f"📋 マスター計画策定: {plan.get('summary', '')[:120]}")
            return stage.index + 1, plan

        if stage.kind in (StageKind.WORKER, StageKind.PARALLEL):
            await self._run_workers(stage, state, plan)
            return stage.index + 1, plan

        if stage.kind == StageKind.CONSISTENCY:
            return await self._run_consistency(stage, state), plan

        if stage.kind == StageKind.HITL:
            return await self._run_gate(stage, state), plan

        if stage.kind == StageKind.ARCHIVE:
            return stage.index + 1, plan

        raise ValueError(f"Unknown stage kind: {stage.kind}")

    # ------------------------------------------------------------------ #
    # Worker execution + escalation
    # ------------------------------------------------------------------ #
    async def _run_workers(
        self, stage: Stage, state: ProjectState, plan: dict[str, Any], feedback: str | None = None
    ) -> None:
        """Run all of a stage's domain workers (concurrently if PARALLEL)."""
        tasks = tasks_for_stage(
            stage, plan, self.context.snapshot(), feedback=feedback
        )

        async def _run_one(task: TaskSpec) -> AgentResult:
            worker = self.workers[task.domain]
            result = await worker.execute(task)
            if result.needs_escalation(self.confidence_threshold):
                result = await self._escalate(task, result, state)
            return result

        results = await asyncio.gather(*(_run_one(t) for t in tasks))

        for result in results:
            self.context.ingest_result(result)
            state.results[result.domain.value] = result
            await self._progress(
                state,
                f"🔧 {result.domain.value}: {result.summary[:80]} "
                f"(信頼度 {result.confidence_score})",
            )

        # Keep the shared BOM on the state snapshot.
        state.bom = self.context.bom()
        self._persist(state)

    async def _escalate(
        self, task: TaskSpec, result: AgentResult, state: ProjectState
    ) -> AgentResult:
        """L3 confidence too low → ask L2; if L2 can't resolve, ask the owner."""
        await self._progress(
            state,
            f"⚠️ {task.domain.value} の信頼度が低い({result.confidence_score})ためL2へエスカレーション\n"
            f"　L3サマリー: {result.summary[:120]}",
        )
        advice = await self.senior.advise(task, result)

        if advice.get("escalate_to_owner"):
            reason = advice.get("reason", "L2が解決できませんでした。")
            proposals = advice.get("proposals") or []
            options = [
                f"{p.get('label', '案')}: {p.get('action', '')}".strip(": ").strip()
                for p in proposals
                if isinstance(p, dict) and (p.get("label") or p.get("action"))
            ]
            await self._progress(
                state,
                f"🔺 L2判断: オーナー判断が必要（対応案 {len(options)} 件を提示）\n　理由: {reason[:200]}",
            )
            req = HitlRequest(
                request_id=f"esc-{uuid.uuid4().hex[:8]}",
                project_id=state.project_id,
                gate=f"escalation_{task.domain.value}",
                title=f"エスカレーション: {task.domain.value}",
                body=reason,
            )
            # Proposal-style escalation: owner picks a concrete remediation.
            if options:
                response = await self.hitl.request_choice(
                    state.thread_id or state.project_id, req, options
                )
            else:
                response = await self.hitl.request(state.thread_id or state.project_id, req)
            feedback = response.feedback or advice.get("guidance", "")
        else:
            guidance = advice.get("guidance", "")
            await self._progress(
                state,
                f"💡 L2ガイダンス ({task.domain.value}): {guidance[:200]}",
            )
            feedback = guidance

        # Re-run the worker once with the guidance folded in as feedback.
        retry = TaskSpec(
            task_id=task.task_id,
            domain=task.domain,
            instruction=task.instruction,
            context=task.context,
            feedback=feedback,
        )
        retry_result = await self.workers[task.domain].execute(retry)
        await self._progress(
            state,
            f"🔄 {task.domain.value} 再実行完了: {retry_result.summary[:120]} "
            f"(信頼度 {retry_result.confidence_score})",
        )
        return retry_result

    # ------------------------------------------------------------------ #
    # Consistency
    # ------------------------------------------------------------------ #
    async def _run_consistency(self, stage: Stage, state: ProjectState) -> int | None:
        """Cross-domain numeric check; loop back on interference."""
        mecha = state.results.get(Domain.MECHA.value)
        circuit = state.results.get(Domain.CIRCUIT.value)
        if mecha is None or circuit is None:
            await self._progress(state, "⚠️ 整合性チェックに必要な結果が不足。スキップします。")
            return stage.index + 1

        report = self.consistency.check_enclosure_vs_pcb(mecha, circuit)
        if report.compatible:
            await self._progress(state, "✅ ドメイン間整合性OK（筐体内寸 ⊇ 基板外形）")
            return stage.index + 1

        issues = "; ".join(report.issues)
        await self._progress(state, f"❌ 干渉検出: {issues}")

        # Proposal-style escalation: the owner picks the concrete remediation
        # rather than the system silently looping back.
        options = [
            "筐体を拡大: メカ設計に内寸を「基板外形＋クリアランス」以上へ拡大させる",
            "基板を縮小: 回路設計に基板外形を「筐体内寸−クリアランス」以下へ縮小させる",
            "現状を許容: 干渉を承知で次工程へ進める（製造前に要手修正）",
        ]
        req = HitlRequest(
            request_id=f"esc-consistency-{uuid.uuid4().hex[:6]}",
            project_id=state.project_id,
            gate="escalation_consistency",
            title="エスカレーション: 筐体↔基板の干渉",
            body=f"整合性チェックで干渉を検出しました。\n\n# 検出内容\n{issues}\n\n対応案をお選びください。",
        )
        response = await self.hitl.request_choice(
            state.thread_id or state.project_id, req, options
        )
        chosen = response.feedback or options[0]

        if chosen.startswith("現状を許容") or response.decision == HitlDecision.TIMEOUT:
            await self._progress(state, "▶️ オーナー選択: 現状許容で次工程へ（干渉は残存）")
            return stage.index + 1

        # Re-run engineering with the owner-chosen remediation as feedback.
        await self._progress(state, f"🔧 オーナー選択: {chosen[:120]} → 再設計")
        back = stage.on_reject_to or (stage.index - 1)
        back_stage = get_stage(back, self._active_pipeline)
        await self._run_workers(
            back_stage,
            state,
            plan={},
            feedback=f"整合性エラー対応（オーナー指示: {chosen}）。詳細: {issues}",
        )
        # Proceed after the corrective re-run; residual issues surface at the gate.
        return stage.index + 1

    # ------------------------------------------------------------------ #
    # HITL gates
    # ------------------------------------------------------------------ #
    async def _run_gate(self, stage: Stage, state: ProjectState) -> int | None:
        """Pause for owner approval; route on the decision."""
        state.status = StageStatus.WAITING_HITL
        self._persist(state)

        # Design gate: when sketch proposals exist, approve by *selecting* one.
        sketches = self._collect_sketches(state)
        if stage.gate == "design_approval" and len(sketches) >= 2:
            nxt = await self._run_design_selection(stage, state, sketches)
            state.status = StageStatus.RUNNING
            self._persist(state)
            return nxt

        req = self._build_gate_request(stage, state)
        response = await self.hitl.request(state.thread_id or state.project_id, req)

        state.status = StageStatus.RUNNING
        self._persist(state)

        if response.decision == HitlDecision.APPROVE:
            await self._progress(state, f"👍 「{stage.title}」承認")
            return stage.index + 1

        if response.decision == HitlDecision.TIMEOUT:
            await self._progress(state, f"⏳ 「{stage.title}」タイムアウト。安全に停止します。")
            return None

        if response.decision in (HitlDecision.REVISE, HitlDecision.REJECT):
            if response.decision == HitlDecision.REJECT:
                await self._progress(state, f"🛑 「{stage.title}」却下")
                return None
            target = stage.on_reject_to or stage.index
            await self._progress(
                state, f"✏️ 修正要求: {response.feedback or '(詳細なし)'} → ステージ{target}へ"
            )
            # Re-run the loop-back stage with feedback, then return to the gate.
            back_stage = get_stage(target, self._active_pipeline)
            if back_stage.kind in (StageKind.WORKER, StageKind.PARALLEL):
                await self._run_workers(
                    back_stage, state, plan={}, feedback=response.feedback
                )
            return stage.index  # re-present the same gate

        return stage.index + 1

    @staticmethod
    def _collect_sketches(state: ProjectState) -> list[str]:
        """Sketch image paths produced by the design worker, in order."""
        design = state.results.get(Domain.DESIGN.value)
        if design is None:
            return []
        items = sorted(
            (k, v)
            for k, v in (design.artifacts or {}).items()
            if isinstance(v, str) and k.startswith("sketch_image") and v.endswith(".png")
        )
        return [v for _, v in items]

    async def _run_design_selection(
        self, stage: Stage, state: ProjectState, sketches: list[str]
    ) -> int | None:
        """Image-based design approval: owner selects a sketch or regenerates."""
        options = [f"案{i}" for i in range(1, len(sketches) + 1)] + ["やり直し（別案を再生成）"]
        req = self._build_gate_request(stage, state)
        response = await self.hitl.request_choice(
            state.thread_id or state.project_id, req, options, image_paths=sketches
        )
        chosen = response.feedback or options[0]

        if chosen.startswith("やり直し") or response.decision == HitlDecision.TIMEOUT:
            await self._progress(state, "🔄 別の方向性でデザイン案を再生成します")
            back = stage.on_reject_to or (stage.index - 1)
            await self._run_workers(
                get_stage(back, self._active_pipeline),
                state,
                plan={},
                feedback="別の方向性で、改めて異なるデザイン案を出してください。",
            )
            return stage.index  # re-present the selection gate

        idx = options.index(chosen) if chosen in options else 0
        await self._progress(state, f"👍 デザイン案 {chosen} を選択しました")
        design = state.results.get(Domain.DESIGN.value)
        if design is not None and idx < len(sketches):
            design.metadata["selected_sketch"] = sketches[idx]
            design.metadata["selected_label"] = chosen
            self._persist(state)
        return stage.index + 1

    def _build_gate_request(self, stage: Stage, state: ProjectState) -> HitlRequest:
        """Assemble the artifacts shown to the owner at a gate."""
        bom: list[BomItem] = self.context.bom()
        total = self.context.total_cost()

        # Collect any render/preview image artifacts from results.
        images: list[str] = []
        for result in state.results.values():
            for key, val in result.artifacts.items():
                if isinstance(val, str) and ("image" in key or val.endswith(".png")):
                    images.append(val)

        summaries = "\n".join(
            f"- {d}: {r.summary}" for d, r in state.results.items()
        )
        return HitlRequest(
            request_id=f"gate-{stage.key}-{uuid.uuid4().hex[:6]}",
            project_id=state.project_id,
            gate=stage.gate or stage.key,
            title=stage.title,
            body=f"{stage.notes}\n\n# 現在の成果物\n{summaries}",
            image_paths=images,
            bom=bom,
            total_cost=total if bom else None,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _progress(self, state: ProjectState, message: str) -> None:
        if self.notify_progress:
            await self.hitl.channel.push_progress(
                state.thread_id or state.project_id, message
            )

    async def _post_deliverables(self, state: ProjectState) -> None:
        """Post the finished deliverables to the channel (mobile-readable inline).

        Renders each domain's summary + artifacts (code files for apps, specs for
        hardware) and the BOM as chunked messages so the owner can review the full
        output on a phone without opening files.
        """
        thread = state.thread_id or state.project_id
        label = "アプリ成果物" if state.project_type == "app" else "製造データ"
        await self.hitl.channel.push_progress(thread, f"📦 {label} ({state.project_id})")

        for domain, result in state.results.items():
            lines = [f"■ {domain} (信頼度 {result.confidence_score})", result.summary or ""]
            for key, val in (result.artifacts or {}).items():
                if isinstance(val, str) and (key.startswith("render_image") or val.endswith(".png")):
                    continue  # images were already shown at the gate
                if key == "files" and isinstance(val, dict):
                    for path, code in val.items():
                        lines.append(f"\n――― {path} ―――\n{code}")
                    continue
                rendered = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False, indent=2)
                lines.append(f"\n[{key}]\n{rendered}")
            await self.hitl.channel.push_progress(thread, "\n".join(lines))

        if state.bom:
            bom_lines = ["🧾 BOM"] + [
                f"- {b.domain.value} {b.part_number} x{b.quantity} @ {b.unit_cost} = {b.line_cost} ({b.description})"
                for b in state.bom
            ]
            await self.hitl.channel.push_progress(thread, "\n".join(bom_lines))

    def _persist(self, state: ProjectState) -> None:
        if self.state_store is not None:
            self.state_store.save(state)
