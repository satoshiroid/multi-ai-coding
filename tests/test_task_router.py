"""Unit tests for the task router — instruction priority and TaskSpec construction."""

from __future__ import annotations

import pytest

from src.models import Domain, TaskSpec
from src.orchestrator.task_router import make_task, tasks_for_stage
from workflows.manufacturing_pipeline import PIPELINE, APP_PIPELINE, get_stage


# ── make_task ───────────────────────────────────────────────────────────── #

def test_make_task_sets_unique_ids():
    t1 = make_task(Domain.MECHA, "design the enclosure")
    t2 = make_task(Domain.MECHA, "design the enclosure")
    assert t1.task_id != t2.task_id
    assert t1.task_id.startswith("mecha-")


def test_make_task_propagates_allow_code():
    t = make_task(Domain.SOFTWARE, "implement", allow_code=True)
    assert t.allow_code is True


def test_make_task_defaults_allow_code_false():
    t = make_task(Domain.DESIGN, "create model")
    assert t.allow_code is False


# ── tasks_for_stage — instruction priority ──────────────────────────────── #

def test_stage_hint_wins_over_pm_subtask():
    """Stage instruction_hints take priority over PM subtask instructions."""
    # Stage 7 in APP_PIPELINE has instruction_hints for Domain.SOFTWARE.
    stage = get_stage(7, APP_PIPELINE)
    plan = {
        "summary": "ECサイト",
        "subtasks": [{"domain": "software", "instruction": "PM subtask instruction"}],
    }
    tasks = tasks_for_stage(stage, plan, {})
    assert len(tasks) == 1
    task = tasks[0]
    # Hint is present → hint is the base, PM subtask appended as context.
    assert "index.html" in task.instruction  # from the stage-7 instruction_hints
    assert "PM subtask instruction" in task.instruction   # PM appended


def test_pm_subtask_used_when_no_hint():
    """PM subtask instruction is used when stage has no instruction_hints."""
    # Stage 5 of PIPELINE: parallel mecha+circuit, no instruction_hints.
    stage = get_stage(5, PIPELINE)
    plan = {
        "summary": "IoTデバイス",
        "subtasks": [
            {"domain": "mecha", "instruction": "Design a 100x70mm enclosure"},
            {"domain": "circuit", "instruction": "Design ESP32 board"},
        ],
    }
    tasks = tasks_for_stage(stage, plan, {})
    by_domain = {t.domain: t for t in tasks}
    assert "Design a 100x70mm enclosure" in by_domain[Domain.MECHA].instruction
    assert "Design ESP32 board" in by_domain[Domain.CIRCUIT].instruction


def test_default_instruction_fallback():
    """Falls back to _default_instruction when neither hint nor PM subtask exists."""
    stage = get_stage(5, PIPELINE)
    tasks = tasks_for_stage(stage, {"summary": "Wi-Fi monitor"}, {})
    by_domain = {t.domain: t for t in tasks}
    # Default instruction for mecha requires inner_dim metadata.
    assert "inner_dim_x_mm" in by_domain[Domain.MECHA].instruction
    # Default instruction for circuit requires pcb_dim metadata.
    assert "pcb_dim_x_mm" in by_domain[Domain.CIRCUIT].instruction


def test_shared_context_attached():
    """Context snapshot is attached to every TaskSpec."""
    stage = get_stage(3, PIPELINE)
    ctx = {"enclosure_width_mm": 80, "project_summary": "test"}
    tasks = tasks_for_stage(stage, {}, ctx)
    assert tasks[0].context == ctx


def test_feedback_forwarded():
    stage = get_stage(3, PIPELINE)
    tasks = tasks_for_stage(stage, {}, {}, feedback="Add ventilation holes")
    assert tasks[0].feedback == "Add ventilation holes"


def test_allow_code_from_stage():
    """allow_code is taken from Stage.allow_code, not hardcoded."""
    stage = get_stage(7, APP_PIPELINE)   # allow_code=True in APP_PIPELINE stage 7
    tasks = tasks_for_stage(stage, {}, {})
    assert all(t.allow_code is True for t in tasks)


def test_allow_code_false_for_hardware_stages():
    stage = get_stage(5, PIPELINE)   # PARALLEL engineering — no code
    tasks = tasks_for_stage(stage, {}, {})
    assert all(t.allow_code is False for t in tasks)


# ── pipeline stage count sanity ─────────────────────────────────────────── #

def test_hardware_pipeline_has_9_stages():
    assert len(PIPELINE) == 9


def test_app_pipeline_has_8_stages():
    assert len(APP_PIPELINE) == 8
