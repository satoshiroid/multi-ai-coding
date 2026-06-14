"""Tests for the thin intake bot (no live Discord / no network)."""

from __future__ import annotations

import pytest

from src.interfaces.dispatch_bot import (
    build_dispatch_bot,
    build_dispatch_payload,
    resolve_project_type,
)

CHANNEL_MAP = {111: "hardware", 222: "app"}


def test_resolve_project_type_maps_channels():
    assert resolve_project_type(111, CHANNEL_MAP) == "hardware"
    assert resolve_project_type(222, CHANNEL_MAP) == "app"
    assert resolve_project_type(999, CHANNEL_MAP) is None


def test_build_dispatch_payload_shape():
    p = build_dispatch_payload("作りたい", "app", 42)
    assert p == {"requirement": "作りたい", "project_type": "app", "thread_id": "42"}
    # thread_id None → empty string (workflow tolerates a missing thread).
    assert build_dispatch_payload("x", "hardware", None)["thread_id"] == ""


def test_build_dispatch_bot_constructs_with_injected_dispatcher():
    class FakeDispatcher:
        async def dispatch(self, payload):  # pragma: no cover - not invoked here
            self.last = payload

    bot = build_dispatch_bot(
        bot_token="fake",
        channel_map=CHANNEL_MAP,
        dispatcher=FakeDispatcher(),
    )
    assert bot is not None


def test_build_dispatch_bot_requires_creds_without_dispatcher():
    with pytest.raises(ValueError):
        build_dispatch_bot(bot_token="fake", channel_map=CHANNEL_MAP)
