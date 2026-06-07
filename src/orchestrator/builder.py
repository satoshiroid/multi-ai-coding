"""Assemble a fully-wired :class:`PMOrchestrator` from config.

Keeps construction (which providers, which channel, which thresholds) in one
place so entry points (CLI / server) stay tiny and tests can build a mock
orchestrator with a single call.
"""

from __future__ import annotations

from typing import Any

from src.agents import PMAgent, SeniorAgent, build_worker
from src.agents.worker_agents import BaseWorker
from src.hitl import HitlManager
from src.hitl.channels.base_channel import BaseChannel
from src.hitl.channels.cli_channel import CliChannel
from src.llm.factory import build_tiered_llms
from src.models import Domain
from src.orchestrator.consistency import ConsistencyChecker
from src.orchestrator.context_store import ContextStore
from src.orchestrator.pm_orchestrator import PMOrchestrator
from src.orchestrator.state_store import StateStore

# Map agents.yaml worker keys → Domain enum.
_WORKER_DOMAINS = {
    "design": Domain.DESIGN,
    "mecha": Domain.MECHA,
    "circuit": Domain.CIRCUIT,
    "software": Domain.SOFTWARE,
}


def build_orchestrator(
    settings: dict[str, Any],
    agents_cfg: dict[str, Any],
    *,
    channel: BaseChannel | None = None,
    state_db_path: str | None = None,
    force_mock: bool = False,
) -> PMOrchestrator:
    """Wire LLMs, agents, HITL channel, stores into a ready orchestrator.

    ``force_mock`` swaps every tier to the deterministic mock provider and uses
    an auto-approving CLI channel — the configuration used by ``--mock`` runs
    and the test suite.
    """
    llms = build_tiered_llms(settings, force_mock=force_mock)

    pm = PMAgent(
        name="pm",
        system_prompt=agents_cfg["pm"]["system_prompt"],
        llm=llms["L1"],
    )
    senior = SeniorAgent(
        name="senior",
        system_prompt=agents_cfg["senior"]["system_prompt"],
        llm=llms["L2"],
    )

    workers: dict[Domain, BaseWorker] = {}
    for key, domain in _WORKER_DOMAINS.items():
        wcfg = agents_cfg["workers"][key]
        workers[domain] = build_worker(
            domain=domain,
            system_prompt=wcfg["system_prompt"],
            llm=llms["L3"],
            name=f"{key}_worker",
        )

    if channel is None:
        channel = CliChannel(auto_approve=force_mock)

    hitl = HitlManager(
        channel=channel,
        timeout_hours=float(settings.get("hitl", {}).get("timeout_hours", 72)),
    )

    consistency = ConsistencyChecker(
        clearance_margin_mm=float(
            settings.get("consistency", {}).get("clearance_margin_mm", 1.0)
        ),
        mount_tolerance_mm=float(
            settings.get("consistency", {}).get("mount_tolerance_mm", 0.5)
        ),
    )

    state_store = StateStore(state_db_path) if state_db_path else None

    return PMOrchestrator(
        pm=pm,
        senior=senior,
        workers=workers,
        hitl=hitl,
        context=ContextStore(),
        consistency=consistency,
        state_store=state_store,
        confidence_threshold=int(
            settings.get("escalation", {}).get("confidence_threshold", 70)
        ),
        notify_progress=bool(settings.get("hitl", {}).get("notify_progress", True)),
    )
