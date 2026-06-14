"""Discord bot entry: forum posts start pipelines; buttons resolve HITL gates.

The bot maps the two halves of the HITL lifecycle onto Discord events. A new
forum thread in the configured channel is treated as a product requirement and
kicks off a pipeline; the owner's later button clicks are handled entirely by the
view in :mod:`src.hitl.channels.discord_channel`, so the bot's only job is to
construct a :class:`DiscordChannel` + orchestrator per project and keep the two
wired together via ``attach_manager``. The pipeline runs as a background task so
a long-running gate never blocks the event loop.

Thread detection uses two complementary strategies:
1. ``on_thread_create`` / ``on_message`` gateway events (fast but unreliable for
   forum channels when the bot is not yet a thread member).
2. A background poll loop calling ``guild.active_threads()`` every 15 s (reliable
   HTTP-API fallback that catches any thread the gateway missed).
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

        async def _run_safe() -> None:
            try:
                await orchestrator.run(requirement, thread_id=str(thread.id))
            except Exception as exc:
                print(f"[pipeline] fatal error thread={thread.id}: {type(exc).__name__}: {exc}")
                try:
                    await channel.push_progress(
                        str(thread.id),
                        f"❌ パイプラインエラー: {type(exc).__name__}: {str(exc)[:300]}",
                    )
                except Exception:
                    pass

        asyncio.create_task(_run_safe())

    async def _get_requirement(thread: discord.Thread) -> str:
        requirement = thread.name or ""
        try:
            async for msg in thread.history(limit=1, oldest_first=True):
                if msg.content:
                    requirement = msg.content
                break
        except Exception as exc:  # noqa: BLE001
            print(f"[bot] history read failed: {exc}")
        return requirement

    async def _poll_forum() -> None:
        """Background loop: use HTTP API to detect new threads the gateway missed."""
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                for guild in bot.guilds:
                    threads = await guild.active_threads()
                    for thread in threads:
                        if thread.parent_id != forum_channel_id:
                            continue
                        if thread.id in _started:
                            continue
                        print(f"[bot] poll: new thread id={thread.id} name={thread.name!r}")
                        requirement = await _get_requirement(thread)
                        await _start_pipeline(thread, requirement)
            except Exception as exc:  # noqa: BLE001
                print(f"[bot] poll error: {type(exc).__name__}: {exc}")
            await asyncio.sleep(15)

    @bot.event
    async def on_ready() -> None:
        print(f"Logged in as {bot.user}")
        # Pre-populate _started so existing threads are not re-processed on restart.
        try:
            for guild in bot.guilds:
                existing = await guild.active_threads()
                for thread in existing:
                    if thread.parent_id == forum_channel_id:
                        _started.add(thread.id)
                        print(f"[bot] pre-loaded thread id={thread.id} name={thread.name!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[bot] pre-load error: {exc}")
        asyncio.create_task(_poll_forum())
        print("[bot] poll loop started (15s interval)")

    @bot.event
    async def on_thread_create(thread: discord.Thread) -> None:
        """Fast path: gateway event for thread creation (may not fire for forums)."""
        print(f"[bot] on_thread_create parent={thread.parent_id} expected={forum_channel_id}")
        if thread.parent_id != forum_channel_id:
            return
        await asyncio.sleep(1)
        requirement = await _get_requirement(thread)
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
        if thread.id in _started:
            return
        print(f"[bot] on_message trigger thread={thread.id}")
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
