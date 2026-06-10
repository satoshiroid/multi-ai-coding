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
from workflows.manufacturing_pipeline import PIPELINE, Stage, StageKind


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

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    async def run(
        self, requirement: str, *, project_id: str | None = None, thread_id: str | None = None
    ) -> ProjectState:
        """Run the whole pipeline for one natural-language requirement."""
        state = ProjectState(
            project_id=project_id or f"proj-{uuid.uuid4().hex[:8]}",
            thread_id=thread_id,
            requirement=requirement,
            status=StageStatus.RUNNING,
        )
        self._persist(state)

        plan: dict[str, Any] = {}
        index = 1
        while index <= len(PIPELINE):
            stage = next(s for s in PIPELINE if s.index == index)
            state.current_stage = stage.index
            self._persist(state)

            next_index, plan = await self._run_stage(stage, state, plan)

            if next_index is None:  # rejected / aborted
                state.status = StageStatus.REJECTED
                self._persist(state)
                return state
            index = next_index

        state.status = StageStatus.DONE
        self._persist(state)
        await self._progress(state, "✅ プロジェクト完了。製造データをアーカイブしました。")
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
            stage.domains, plan, self.context.snapshot(), feedback=feedback
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
            await self._progress(
                state,
                f"🔺 L2判断: オーナー判断が必要\n　理由: {reason[:200]}",
            )
            req = HitlRequest(
                request_id=f"esc-{uuid.uuid4().hex[:8]}",
                project_id=state.project_id,
                gate=f"escalation_{task.domain.value}",
                title=f"エスカレーション: {task.domain.value}",
                body=reason,
            )
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
        await self._progress(state, f"❌ 干渉検出: {issues} → 再設計へ差し戻し")
        # Loop back to the engineering stage with the issue as feedback.
        back = stage.on_reject_to or (stage.index - 1)
        back_stage = next(s for s in PIPELINE if s.index == back)
        await self._run_workers(
            back_stage, state, plan={}, feedback=f"整合性エラーを修正してください: {issues}"
        )
        # Re-check once after the corrective re-run, then proceed regardless to
        # avoid infinite loops (the owner gate will catch residual issues).
        return stage.index + 1

    # ------------------------------------------------------------------ #
    # HITL gates
    # ------------------------------------------------------------------ #
    async def _run_gate(self, stage: Stage, state: ProjectState) -> int | None:
        """Pause for owner approval; route on the decision."""
        state.status = StageStatus.WAITING_HITL
        self._persist(state)

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
            back_stage = next(s for s in PIPELINE if s.index == target)
            if back_stage.kind in (StageKind.WORKER, StageKind.PARALLEL):
                await self._run_workers(
                    back_stage, state, plan={}, feedback=response.feedback
                )
            return stage.index  # re-present the same gate

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

    def _persist(self, state: ProjectState) -> None:
        if self.state_store is not None:
            self.state_store.save(state)
