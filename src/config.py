"""Configuration loading: settings.yaml + agents.yaml + environment.

Centralizes how the app reads its YAML config so the orchestrator, factory and
entry points all see the same resolved values. ``.env`` is loaded if present.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:  # optional — only needed for local .env convenience
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _ROOT / "config"


def load_env() -> None:
    """Load a local ``.env`` file into the environment if python-dotenv exists."""
    if load_dotenv is not None:
        env_path = _ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    """Load ``config/settings.yaml`` (or an override path)."""
    p = Path(path) if path else _CONFIG_DIR / "settings.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_agents(path: str | Path | None = None) -> dict[str, Any]:
    """Load ``config/agents.yaml`` (or an override path)."""
    p = Path(path) if path else _CONFIG_DIR / "agents.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_state_db_path(settings: dict[str, Any]) -> str:
    """Resolve the SQLite path from env var or config default."""
    state_cfg = settings.get("state", {})
    env_name = state_cfg.get("db_path_env", "STATE_DB_PATH")
    default = state_cfg.get("db_path_default", "./data/state.db")
    return os.environ.get(env_name) or default
