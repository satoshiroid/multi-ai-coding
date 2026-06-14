"""Export a finished :class:`ProjectState` into committable files on disk.

The GitHub-centric workflow runs a pipeline head-to-tail with ``--auto-approve``
and then commits the result back to the repo as a pull request. The orchestrator
keeps everything in memory / SQLite, so this module is the bridge: it serializes
a project's outputs into a clean directory tree the CI job can ``git add``.

Layout produced under ``out_dir``::

    PROJECT.md            human-readable summary (type, status, per-domain notes)
    summary.json          machine-readable snapshot of the same
    bom.csv               bill of materials (hardware projects)
    code/<path>           generated source files (app projects: artifacts["files"])
    artifacts/<domain>.md per-domain textual artifacts (specs, setup, gerber notes)
    renders/<name>.png    copied render/preview images referenced by artifacts

Nothing here assumes app *or* hardware — it walks whatever the workers produced,
so both pipelines export through the same path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from src.models import ProjectState

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Artifact keys handled specially (so they are not re-dumped as generic text).
_SPECIAL_KEYS = {"files", "render_image"}


def export_project(state: ProjectState, out_dir: str | Path) -> list[Path]:
    """Write ``state`` into ``out_dir`` and return the list of files written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    written.append(_write_summary_json(state, out))
    written.append(_write_project_md(state, out))

    if state.bom:
        written.append(_write_bom_csv(state, out))

    for domain, result in state.results.items():
        artifacts: dict[str, Any] = result.artifacts or {}

        # 1) Generated source files → code/<path> (app implementation stage).
        files = artifacts.get("files")
        if isinstance(files, dict):
            written.extend(_write_code_files(files, out / "code"))

        # 2) Copy referenced render/preview images → renders/.
        written.extend(_copy_images(artifacts, out / "renders"))

        # 3) Any remaining textual artifacts → artifacts/<domain>.md.
        md = _write_domain_artifacts(domain, result, artifacts, out / "artifacts")
        if md is not None:
            written.append(md)

    return written


# --------------------------------------------------------------------------- #
# Individual writers
# --------------------------------------------------------------------------- #
def _write_summary_json(state: ProjectState, out: Path) -> Path:
    path = out / "summary.json"
    payload = {
        "project_id": state.project_id,
        "project_type": state.project_type,
        "status": state.status.value,
        "current_stage": state.current_stage,
        "requirement": state.requirement,
        "domains": {
            domain: {
                "summary": r.summary,
                "confidence_score": r.confidence_score,
                "metadata": r.metadata,
            }
            for domain, r in state.results.items()
        },
        "bom": [
            {
                "domain": b.domain.value,
                "part_number": b.part_number,
                "description": b.description,
                "quantity": b.quantity,
                "unit_cost": b.unit_cost,
                "line_cost": b.line_cost,
            }
            for b in state.bom
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_project_md(state: ProjectState, out: Path) -> Path:
    path = out / "PROJECT.md"
    lines = [
        f"# {state.project_id}",
        "",
        f"- **種別**: {state.project_type}",
        f"- **ステータス**: {state.status.value}",
        f"- **到達ステージ**: {state.current_stage}",
        "",
        "## 要件",
        "",
        state.requirement or "_(なし)_",
        "",
        "## ドメイン別成果",
        "",
    ]
    if state.results:
        for domain, r in state.results.items():
            lines.append(f"### {domain}  (confidence: {r.confidence_score})")
            lines.append("")
            lines.append(r.summary or "_(サマリなし)_")
            lines.append("")
    else:
        lines.append("_(成果なし)_")
        lines.append("")

    if state.bom:
        total = round(sum(b.line_cost for b in state.bom), 4)
        lines += ["## BOM 概要", "", f"合計 {len(state.bom)} 行 / 総コスト {total}", ""]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_bom_csv(state: ProjectState, out: Path) -> Path:
    import csv

    path = out / "bom.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["domain", "part_number", "description", "quantity", "unit_cost", "line_cost"]
        )
        for b in state.bom:
            w.writerow(
                [b.domain.value, b.part_number, b.description, b.quantity, b.unit_cost, b.line_cost]
            )
    return path


def _write_code_files(files: dict[str, Any], code_dir: Path) -> list[Path]:
    """Write ``{relative_path: source}`` into ``code_dir`` (path-traversal safe)."""
    written: list[Path] = []
    code_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        if not isinstance(rel, str) or not isinstance(content, str):
            continue
        # Keep everything inside code_dir even if the LLM emitted "../" or "/abs".
        target = (code_dir / rel.lstrip("/")).resolve()
        if code_dir.resolve() not in target.parents and target != code_dir.resolve():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written


def _copy_images(artifacts: dict[str, Any], renders_dir: Path) -> list[Path]:
    written: list[Path] = []
    for key, val in artifacts.items():
        if not isinstance(val, str):
            continue
        src = Path(val)
        if src.suffix.lower() not in _IMAGE_EXTS:
            continue
        if not src.is_file():
            continue  # mock references a name with no file on disk — skip silently
        renders_dir.mkdir(parents=True, exist_ok=True)
        dest = renders_dir / src.name
        shutil.copyfile(src, dest)
        written.append(dest)
    return written


def _write_domain_artifacts(
    domain: str, result: Any, artifacts: dict[str, Any], art_dir: Path
) -> Path | None:
    """Dump non-file, non-image artifacts (specs, setup, gerber notes) as markdown."""
    extras = {
        k: v
        for k, v in artifacts.items()
        if k not in _SPECIAL_KEYS and not _is_image_ref(v)
    }
    if not extras:
        return None

    art_dir.mkdir(parents=True, exist_ok=True)
    path = art_dir / f"{domain}.md"
    lines = [f"# {domain} artifacts", ""]
    for key, val in extras.items():
        lines.append(f"## {key}")
        lines.append("")
        if isinstance(val, (dict, list)):
            lines.append("```json")
            lines.append(json.dumps(val, ensure_ascii=False, indent=2))
            lines.append("```")
        else:
            lines.append(str(val))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _is_image_ref(val: Any) -> bool:
    return isinstance(val, str) and Path(val).suffix.lower() in _IMAGE_EXTS
