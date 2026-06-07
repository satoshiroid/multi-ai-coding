"""Tests for the Discord channel building blocks (no live Discord connection).

We can't open a real gateway in CI, so these verify import-time correctness,
that DiscordChannel satisfies the BaseChannel contract, and that the bot
builder constructs without touching the network.
"""

from __future__ import annotations

import inspect

from src.hitl.channels.base_channel import BaseChannel


def test_discord_channel_implements_base_channel():
    from src.hitl.channels.discord_channel import DiscordChannel

    assert issubclass(DiscordChannel, BaseChannel)
    # All abstract methods are implemented (otherwise instantiation would fail).
    assert not getattr(DiscordChannel, "__abstractmethods__", set())


def test_discord_channel_methods_are_coroutines():
    from src.hitl.channels.discord_channel import DiscordChannel

    for name in ("create_project_thread", "push_approval", "push_escalation", "push_progress"):
        assert inspect.iscoroutinefunction(getattr(DiscordChannel, name))


def test_discord_channel_attach_manager():
    from src.hitl.channels.discord_channel import DiscordChannel

    ch = DiscordChannel(bot=object(), forum_channel_id=1, owner_user_id=2)
    sentinel = object()
    ch.attach_manager(sentinel)
    # stored privately; just ensure it doesn't raise and is referenced
    assert any(v is sentinel for v in vars(ch).values())


def test_bot_builder_constructs():
    """build_bot should construct a bot object without connecting."""
    from src.config import load_agents, load_settings
    from src.interfaces.discord_bot import build_bot

    settings, agents = load_settings(), load_agents()
    bot = build_bot(
        settings,
        agents,
        bot_token="fake-token",
        forum_channel_id=123,
        owner_user_id=456,
    )
    assert bot is not None
