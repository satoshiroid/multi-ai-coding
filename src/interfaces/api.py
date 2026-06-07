"""FastAPI app — health + REST control plane, runs alongside the Discord bot.

The Discord bot owns the owner interaction; this API exists for programmatic
control and observability: kick off a pipeline, inspect persisted project
state, list projects. It shares the same SQLite state store as the bot.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.config import load_agents, load_settings, resolve_state_db_path
from src.orchestrator.builder import build_orchestrator
from src.orchestrator.state_store import StateStore


class StartRequest(BaseModel):
    requirement: str
    project_id: str | None = None


def create_app(*, force_mock: bool = False) -> FastAPI:
    """Build the FastAPI application.

    ``force_mock`` runs pipelines with the deterministic mock stack — handy for
    smoke-testing the HTTP surface without API keys or Discord.
    """
    settings = load_settings()
    agents_cfg = load_agents()
    db_path = resolve_state_db_path(settings)

    app = FastAPI(title="Multi-AI Coding", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/projects")
    async def list_projects() -> dict[str, Any]:
        store = StateStore(db_path)
        try:
            return {"projects": store.list_projects()}
        finally:
            store.close()

    @app.get("/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        store = StateStore(db_path)
        try:
            state = store.load(project_id)
        finally:
            store.close()
        if state is None:
            raise HTTPException(status_code=404, detail="project not found")
        return state.model_dump(mode="json")

    @app.post("/projects")
    async def start_project(req: StartRequest) -> dict[str, Any]:
        """Start a pipeline in the background (CLI-channel, non-Discord)."""
        orchestrator = build_orchestrator(
            settings, agents_cfg, state_db_path=db_path, force_mock=force_mock
        )
        # Fire-and-forget; progress/state is observable via the GET endpoints.
        task = asyncio.create_task(
            orchestrator.run(req.requirement, project_id=req.project_id)
        )
        # Surface the generated id immediately without awaiting completion.
        await asyncio.sleep(0)
        return {"started": True, "task_done": task.done()}

    return app
