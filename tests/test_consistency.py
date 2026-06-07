"""Tests for cross-domain consistency checking (enclosure vs PCB)."""

from __future__ import annotations

from src.models import AgentResult, Domain
from src.orchestrator.consistency import ConsistencyChecker


def _mecha(x: float, y: float, z: float = 25.0) -> AgentResult:
    return AgentResult(
        task_id="m",
        domain=Domain.MECHA,
        summary="enclosure",
        confidence_score=85,
        metadata={"inner_dim_x_mm": x, "inner_dim_y_mm": y, "inner_dim_z_mm": z},
    )


def _circuit(x: float, y: float) -> AgentResult:
    return AgentResult(
        task_id="c",
        domain=Domain.CIRCUIT,
        summary="pcb",
        confidence_score=85,
        metadata={"pcb_dim_x_mm": x, "pcb_dim_y_mm": y},
    )


def test_compatible_when_pcb_fits_with_clearance():
    checker = ConsistencyChecker(clearance_margin_mm=1.0)
    report = checker.check_enclosure_vs_pcb(_mecha(100.0, 70.0), _circuit(98.0, 68.0))
    assert report.compatible is True
    assert report.issues == []


def test_incompatible_when_pcb_too_large():
    checker = ConsistencyChecker(clearance_margin_mm=1.0)
    # PCB 99.5 needs 99.5+2 = 101.5 > 100 inner → fail on X
    report = checker.check_enclosure_vs_pcb(_mecha(100.0, 70.0), _circuit(99.5, 68.0))
    assert report.compatible is False
    assert len(report.issues) >= 1


def test_missing_dimensions_treated_as_failure():
    checker = ConsistencyChecker()
    empty_mecha = AgentResult(task_id="m", domain=Domain.MECHA, summary="", confidence_score=50)
    report = checker.check_enclosure_vs_pcb(empty_mecha, _circuit(98.0, 68.0))
    assert report.compatible is False
    assert report.issues  # an explanatory issue is present


def test_details_contains_compared_numbers():
    checker = ConsistencyChecker(clearance_margin_mm=1.0)
    report = checker.check_enclosure_vs_pcb(_mecha(100.0, 70.0), _circuit(98.0, 68.0))
    assert isinstance(report.details, dict)
    assert report.details  # has the compared values
