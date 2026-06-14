#!/usr/bin/env python3
"""Server entry point — Discord bot (+ optional FastAPI) for production use.

Runs the Discord bot so owners drive projects from their phone: a new forum
post starts a pipeline, buttons resolve HITL gates. Requires DISCORD_BOT_TOKEN,
DISCORD_FORUM_CHANNEL_ID and OWNER_USER_ID in the environment / .env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_agents, load_env, load_settings, resolve_state_db_path


def main() -> None:
    load_env()

    token = os.environ.get("DISCORD_BOT_TOKEN")
    forum_id = os.environ.get("DISCORD_FORUM_CHANNEL_ID")
    owner_id = os.environ.get("OWNER_USER_ID")

    missing = [
        name
        for name, val in (
            ("DISCORD_BOT_TOKEN", token),
            ("DISCORD_FORUM_CHANNEL_ID", forum_id),
            ("OWNER_USER_ID", owner_id),
        )
        if not val
    ]
    if missing:
        print(f"ERROR: missing required env vars: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your Discord credentials.")
        raise SystemExit(2)

    settings = load_settings()
    agents_cfg = load_agents()
    db_path = resolve_state_db_path(settings)

    from src.interfaces.discord_bot import run_bot

    print("Starting Discord bot... (Ctrl+C to stop)")
    run_bot(
        settings,
        agents_cfg,
        bot_token=token,
        forum_channel_id=int(forum_id),
        owner_user_id=int(owner_id),
        state_db_path=db_path,
    )


if __name__ == "__main__":
    main()
