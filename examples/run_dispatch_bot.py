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
    hardware_channel = int(_require("HARDWARE_CHANNEL_ID"))
    app_channel = int(_require("APP_CHANNEL_ID"))
    github_repo = _require("GITHUB_REPO")
    github_token = _require("GITHUB_DISPATCH_TOKEN")

    channel_map = {hardware_channel: "hardware", app_channel: "app"}

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
