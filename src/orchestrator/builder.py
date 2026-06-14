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
from src.llm.factory import build_agent_llm
from src.mcp.client import McpServerSpec
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
    # Each agent resolves its own LLM ("virtual employee"): per-agent settings
    # win over the tier default, so PM/senior/each worker can run a different
    # model. See src.llm.factory.resolve_agent_cfg for the precedence chain.
    pm = PMAgent(
        name="pm",
        system_prompt=agents_cfg["pm"]["system_prompt"],
        llm=build_agent_llm("pm", "L1", settings, force_mock=force_mock),
    )
    senior = SeniorAgent(
        name="senior",
        system_prompt=agents_cfg["senior"]["system_prompt"],
        llm=build_agent_llm("senior", "L2", settings, force_mock=force_mock),
    )

    # Build Blender MCP spec (injected into DesignWorker when enabled).
    blender_cfg = settings.get("mcp", {}).get("blender", {})
    blender_spec = McpServerSpec(
        name="blender",
        enabled=bool(blender_cfg.get("enabled", False)),
        transport=str(blender_cfg.get("transport", "stdio")),
        command=blender_cfg.get("command") or None,
        args=list(blender_cfg.get("args", [])),
        url=blender_cfg.get("url") or None,
        host=blender_cfg.get("host") or None,
        port=int(blender_cfg["port"]) if blender_cfg.get("port") else None,
    ) if blender_cfg else None

    # Design sketch image-gen config (off under mock so tests make no calls).
    from src.image_gen import resolve_image_config

    image_cfg = None if force_mock else resolve_image_config(settings)

    workers: dict[Domain, BaseWorker] = {}
    for key, domain in _WORKER_DOMAINS.items():
        wcfg = agents_cfg["workers"][key]
        workers[domain] = build_worker(
            domain=domain,
            system_prompt=wcfg["system_prompt"],
            llm=build_agent_llm(key, "L3", settings, force_mock=force_mock),
            name=f"{key}_worker",
            blender_spec=blender_spec if domain == Domain.DESIGN else None,
            image_cfg=image_cfg if domain == Domain.DESIGN else None,
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
