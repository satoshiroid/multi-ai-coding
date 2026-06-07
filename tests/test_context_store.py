"""Tests for the shared ContextStore (constraints overwrite, BOM append)."""

from __future__ import annotations

from src.models import AgentResult, BomItem, Constraint, Domain
from src.orchestrator.context_store import ContextStore


def test_constraint_overwrite_semantics():
    store = ContextStore()
    store.set_constraint(Constraint(name="pcb_x", value=98.0, owner_domain=Domain.CIRCUIT))
    store.set_constraint(Constraint(name="pcb_x", value=100.0, owner_domain=Domain.CIRCUIT))
    c = store.get_constraint("pcb_x")
    assert c is not None
    assert c.value == 100.0  # later value wins


def test_bom_append_and_total_cost():
    store = ContextStore()
    store.append_bom([BomItem(domain=Domain.CIRCUIT, part_number="R1", description="res", quantity=2, unit_cost=0.5)])
    store.append_bom([BomItem(domain=Domain.MECHA, part_number="M3", description="screw", quantity=4, unit_cost=0.25)])
    assert len(store.bom()) == 2
    # 2*0.5 + 4*0.25 = 1.0 + 1.0 = 2.0
    assert store.total_cost() == 2.0


def test_ingest_result_extracts_constraints_and_bom():
    store = ContextStore()
    result = AgentResult(
        task_id="t1",
        domain=Domain.CIRCUIT,
        summary="pcb done",
        confidence_score=90,
        metadata={
            "pcb_dim_x_mm": 98.0,
            "pcb_dim_y_mm": 68.0,
            "bom": [
                {"domain": "circuit", "part_number": "U1", "description": "MCU", "quantity": 1, "unit_cost": 3.5}
            ],
        },
    )
    store.ingest_result(result)
    assert store.get_constraint("pcb_dim_x_mm") is not None
    assert store.get_constraint("pcb_dim_x_mm").value == 98.0
    assert len(store.bom()) == 1
    assert store.bom()[0].part_number == "U1"


def test_ingest_result_skips_malformed_bom():
    store = ContextStore()
    result = AgentResult(
        task_id="t1",
        domain=Domain.CIRCUIT,
        summary="x",
        confidence_score=50,
        metadata={"bom": ["not-a-dict", {"part_number": "ok", "description": "d", "domain": "circuit"}]},
    )
    store.ingest_result(result)  # must not raise
    # at most the valid entry is appended
    assert all(isinstance(b, BomItem) for b in store.bom())


def test_snapshot_is_jsonable():
    store = ContextStore()
    store.set_constraint(Constraint(name="z", value=25.0, owner_domain=Domain.MECHA))
    snap = store.snapshot()
    assert "constraints" in snap and "bom" in snap and "total_cost" in snap
