#!/usr/bin/env python3
"""Smoke-validate generated app code and write a markdown report.

Lightweight, dependency-free, per-language static checks so a generated PR
carries a quick "does this even parse?" signal:

    .py            -> py_compile (syntax)
    .json          -> json.loads
    .html/.htm     -> HTMLParser (parses without raising) + has a root-ish tag
    .js/.mjs/.ts   -> `node --check` when node is on PATH, else SKIP
    others         -> SKIP (presence only)

Report-only: always exits 0 so the PR still opens; FAILs are surfaced in the
report (and as a ::warning::) for the owner to see in review.

Usage: validate_generated.py <code_dir> <report_path>
"""

from __future__ import annotations

import json
import py_compile
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path


def _check_py(path: Path) -> tuple[str, str]:
    try:
        py_compile.compile(str(path), doraise=True)
        return "PASS", "syntax ok"
    except py_compile.PyCompileError as exc:
        return "FAIL", str(exc).splitlines()[-1][:200]


def _check_json(path: Path) -> tuple[str, str]:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return "PASS", "valid json"
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"{type(exc).__name__}: {exc}"[:200]


def _check_html(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    seen: list[str] = []

    class _P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            seen.append(tag)

    try:
        _P().feed(text)
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"parse error: {exc}"[:200]
    if not seen:
        return "FAIL", "no HTML tags found"
    return "PASS", f"parses ({len(seen)} tags)"


def _check_js(path: Path) -> tuple[str, str]:
    if not shutil.which("node"):
        return "SKIP", "node not available"
    proc = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True
    )
    if proc.returncode == 0:
        return "PASS", "node --check ok"
    return "FAIL", (proc.stderr.strip().splitlines() or ["error"])[-1][:200]


_CHECKERS = {
    ".py": _check_py,
    ".json": _check_json,
    ".html": _check_html,
    ".htm": _check_html,
    ".js": _check_js,
    ".mjs": _check_js,
    ".ts": _check_js,
}


def main() -> int:
    code_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "./out/code")
    report = Path(sys.argv[2] if len(sys.argv) > 2 else "./out/VALIDATION.md")

    rows: list[tuple[str, str, str]] = []
    if code_dir.is_dir():
        for f in sorted(p for p in code_dir.rglob("*") if p.is_file()):
            checker = _CHECKERS.get(f.suffix.lower())
            status, detail = checker(f) if checker else ("SKIP", "no checker")
            rows.append((str(f.relative_to(code_dir)), status, detail))

    fails = [r for r in rows if r[1] == "FAIL"]
    passes = [r for r in rows if r[1] == "PASS"]
    lines = [
        "# 生成コード検証レポート",
        "",
        f"- 検査: {len(rows)} ファイル / PASS {len(passes)} / FAIL {len(fails)} "
        f"/ SKIP {len(rows) - len(passes) - len(fails)}",
        "",
        "| ファイル | 結果 | 詳細 |",
        "|---|---|---|",
    ]
    lines += [f"| `{name}` | {status} | {detail} |" for name, status, detail in rows]
    if not rows:
        lines.append("| _(コードファイルなし)_ | - | - |")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"validation: {len(passes)} pass, {len(fails)} fail, {len(rows)} total -> {report}")
    for name, status, detail in fails:
        print(f"::warning::generated {name}: {detail}")
    return 0  # report-only; never block PR creation


if __name__ == "__main__":
    raise SystemExit(main())
