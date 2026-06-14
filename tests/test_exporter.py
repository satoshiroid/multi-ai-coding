"""Tests for the project exporter (state → committable files)."""

from __future__ import annotations

import json

from src.models import AgentResult, BomItem, Domain, ProjectState, StageStatus
from src.orchestrator.exporter import export_project


def _app_state() -> ProjectState:
    return ProjectState(
        project_id="proj-app-1",
        project_type="app",
        status=StageStatus.DONE,
        current_stage=8,
        requirement="メモ管理アプリ",
        results={
            "software": AgentResult(
                task_id="t1",
                domain=Domain.SOFTWARE,
                summary="MVP実装",
                confidence_score=84,
                artifacts={
                    "files": {"app/main.py": "print('hi')\n", "README.md": "# memo\n"},
                    "setup": "pip install -r requirements.txt",
                },
            )
        },
    )


def _hw_state() -> ProjectState:
    return ProjectState(
        project_id="proj-hw-1",
        project_type="hardware",
        status=StageStatus.DONE,
        current_stage=9,
        requirement="環境モニター",
        bom=[
            BomItem(
                domain=Domain.CIRCUIT,
                part_number="ESP32-WROOM",
                description="Wi-Fi MCU",
                quantity=1,
                unit_cost=3.5,
            )
        ],
        results={
            "circuit": AgentResult(
                task_id="t2",
                domain=Domain.CIRCUIT,
                summary="回路確定",
                confidence_score=80,
                artifacts={"gerber": "board.gbr の出力手順"},
            )
        },
    )


def test_export_app_writes_code_and_summary(tmp_path):
    written = export_project(_app_state(), tmp_path)
    assert (tmp_path / "PROJECT.md").is_file()
    assert (tmp_path / "summary.json").is_file()
    # Generated files land under code/ with their relative paths preserved.
    assert (tmp_path / "code" / "app" / "main.py").read_text() == "print('hi')\n"
    assert (tmp_path / "code" / "README.md").is_file()
    # No BOM for app projects.
    assert not (tmp_path / "bom.csv").exists()
    assert any(p.name == "main.py" for p in written)

    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["project_type"] == "app"
    assert data["domains"]["software"]["confidence_score"] == 84


def test_export_hardware_writes_bom_and_artifacts(tmp_path):
    export_project(_hw_state(), tmp_path)
    bom = (tmp_path / "bom.csv").read_text()
    assert "ESP32-WROOM" in bom
    # Textual artifacts dumped per-domain.
    assert (tmp_path / "artifacts" / "circuit.md").is_file()
    assert "gerber" in (tmp_path / "artifacts" / "circuit.md").read_text()


def test_export_rejects_path_traversal(tmp_path):
    state = _app_state()
    state.results["software"].artifacts["files"] = {"../escape.py": "x=1"}
    export_project(state, tmp_path)
    # The traversal target must not be written outside the export dir.
    assert not (tmp_path.parent / "escape.py").exists()
