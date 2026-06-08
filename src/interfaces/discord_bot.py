"""Discord bot entry: forum posts start pipelines; buttons resolve HITL gates.

The bot maps the two halves of the HITL lifecycle onto Discord events. A new
forum thread in the configured channel is treated as a product requirement and
kicks off a pipeline; the owner's later button clicks are handled entirely by the
view in :mod:`src.hitl.channels.discord_channel`, so the bot's only job is to
construct a :class:`DiscordChannel` + orchestrator per project and keep the two
wired together via ``attach_manager``. The pipeline runs as a background task so
a long-running gate never blocks the event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.ext import commands


def build_bot(
    settings: dict[str, Any],
    agents_cfg: dict[str, Any],
    *,
    bot_token: str,
    forum_channel_id: int,
    owner_user_id: int,
    state_db_path: str | None = None,
) -> commands.Bot:
    """Construct (but do not run) the configured Discord bot.

    Splitting build from run keeps the wiring testable without a live gateway
    connection.
    """
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    # Imported lazily so importing this module never forces the whole agent stack.
    from src.hitl.channels.discord_channel import DiscordChannel
    from src.orchestrator.builder import build_orchestrator

    @bot.event
    async def on_ready() -> None:
        print(f"Logged in as {bot.user}")

    # Track threads we've already started a pipeline for (avoid duplicates).
    _started: set[int] = set()

    async def _start_pipeline(thread: discord.Thread, requirement: str) -> None:
        if thread.id in _started:
            return
        _started.add(thread.id)
        print(f"[bot] pipeline start thread={thread.id} req={requirement!r:.60}")
        channel = DiscordChannel(bot, forum_channel_id, owner_user_id)
        orchestrator = build_orchestrator(
            settings, agents_cfg, channel=channel, state_db_path=state_db_path
        )
        channel.attach_manager(orchestrator.hitl)
        asyncio.create_task(orchestrator.run(requirement, thread_id=str(thread.id)))

    @bot.event
    async def on_thread_create(thread: discord.Thread) -> None:
        """Start a pipeline for each new thread in our forum channel."""
        print(f"[bot] on_thread_create parent={thread.parent_id} expected={forum_channel_id}")
        if thread.parent_id != forum_channel_id:
            return
        # Wait briefly so the first message is available in history.
        await asyncio.sleep(1)
        requirement = thread.name or ""
        try:
            async for msg in thread.history(limit=1, oldest_first=True):
                if msg.content:
                    requirement = msg.content
                break
        except Exception as exc:  # noqa: BLE001
            print(f"[bot] history read failed: {exc}")
        await _start_pipeline(thread, requirement)

    @bot.event
    async def on_message(message: discord.Message) -> None:
        """Fallback: detect the first message posted to a new forum thread."""
        if message.author == bot.user:
            return
        thread = message.channel
        if not isinstance(thread, discord.Thread):
            return
        if thread.parent_id != forum_channel_id:
            return
        # Only trigger on the very first message (starter_message or position 0).
        if thread.id in _started:
            return
        # Check if this looks like the opening post of the thread.
        if message.id == thread.id or (hasattr(thread, "starter_message") and
                                        thread.starter_message and
                                        thread.starter_message.id == message.id):
            await _start_pipeline(thread, message.content or thread.name or "")
            return
        # Fallback: start pipeline on any first message in an unstarted thread.
        await _start_pipeline(thread, message.content or thread.name or "")

    return bot


def run_bot(
    settings: dict[str, Any],
    agents_cfg: dict[str, Any],
    *,
    bot_token: str,
    forum_channel_id: int,
    owner_user_id: int,
    state_db_path: str | None = None,
) -> None:
    """Build and block-run the bot (the production entry point)."""
    bot = build_bot(
        settings,
        agents_cfg,
        bot_token=bot_token,
        forum_channel_id=forum_channel_id,
        owner_user_id=owner_user_id,
        state_db_path=state_db_path,
    )
    bot.run(bot_token)
