#!/usr/bin/env python3
"""Intake bot entry — Discord channels → GitHub Actions dispatch.

Runs the thin intake bot: a new forum post in #hardware or #app fires a
``repository_dispatch`` that kicks off ``.github/workflows/build.yml`` on the
right runner. The heavy pipeline runs in Actions, not here.

Required environment (see .env.example):
    DISCORD_BOT_TOKEN       bot token
    HARDWARE_CHANNEL_ID     forum channel id mapped to project_type "hardware"
    APP_CHANNEL_ID          forum channel id mapped to project_type "app"
    GITHUB_REPO             "owner/repo" to dispatch to
    GITHUB_DISPATCH_TOKEN   PAT with repo (write) scope to fire dispatches
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_env
from src.interfaces.dispatch_bot import build_dispatch_bot


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: missing required env var: {name}")
        print("Copy .env.example to .env and fill in the intake/dispatch settings.")
        raise SystemExit(2)
    return val


def main() -> None:
    load_env()

    token = _require("DISCORD_BOT_TOKEN")
    github_repo = _require("GITHUB_REPO")
    github_token = _require("GITHUB_DISPATCH_TOKEN")

    # Two channels (channel→type) if configured; otherwise a single forum where
    # the /app or /hardware post prefix decides the type.
    hw = os.environ.get("HARDWARE_CHANNEL_ID")
    app = os.environ.get("APP_CHANNEL_ID")
    if hw and app:
        channel_map = {int(hw): "hardware", int(app): "app"}
    else:
        forum = _require("DISCORD_FORUM_CHANNEL_ID")
        # Default to "app" (hardware needs a self-hosted runner); /hardware prefix
        # overrides. Most posts are apps and app routes to the hosted runner.
        channel_map = {int(forum): "app"}
        print(f"Single-channel intake on {forum} (prefix /app or /hardware; default app).")

    bot = build_dispatch_bot(
        bot_token=token,
        channel_map=channel_map,
        github_repo=github_repo,
        github_token=github_token,
    )
    print("Starting intake bot... (Ctrl+C to stop)")
    bot.run(token)


if __name__ == "__main__":
    main()
