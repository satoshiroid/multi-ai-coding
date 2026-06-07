#!/usr/bin/env python3
"""CLI entry point — run one pipeline from the terminal.

Usage::

    # Fully offline, deterministic mock stack (no API keys / CAD / Discord):
    python examples/run_pipeline.py --mock "Wi-Fi環境モニターを作りたい"

    # Real LLMs (reads .env / config), interactive CLI approval gates:
    python examples/run_pipeline.py "Wi-Fi環境モニターを作りたい"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_agents, load_env, load_settings, resolve_state_db_path
from src.hitl.channels.cli_channel import CliChannel
from src.orchestrator.builder import build_orchestrator


async def _main(requirement: str, *, mock: bool, auto_approve: bool) -> int:
    load_env()
    settings = load_settings()
    agents_cfg = load_agents()
    db_path = resolve_state_db_path(settings)

    channel = CliChannel(auto_approve=auto_approve or mock)
    orchestrator = build_orchestrator(
        settings,
        agents_cfg,
        channel=channel,
        state_db_path=db_path,
        force_mock=mock,
    )

    state = await orchestrator.run(requirement)
    print("\n" + "=" * 60)
    print(f"Project   : {state.project_id}")
    print(f"Status    : {state.status.value}")
    print(f"Stage     : {state.current_stage}/9")
    print(f"Domains   : {', '.join(state.results.keys())}")
    print(f"BOM lines : {len(state.bom)}  total cost: {orchestrator.context.total_cost()}")
    print("=" * 60)
    return 0 if state.status.value == "done" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the manufacturing pipeline.")
    parser.add_argument("requirement", nargs="?", default="Wi-Fi対応のバッテリー駆動環境モニターを作りたい。")
    parser.add_argument("--mock", action="store_true", help="use deterministic mock LLM/CAD stack")
    parser.add_argument(
        "--auto-approve", action="store_true", help="auto-approve HITL gates (non-interactive)"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.requirement, mock=args.mock, auto_approve=args.auto_approve)))


if __name__ == "__main__":
    main()
