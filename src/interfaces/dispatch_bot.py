"""Thin Discord intake bot — routes a new thread to GitHub Actions.

In the GitHub-centric model the heavy work (LLM + CAD/MCP) runs in Actions, not
in the bot. So this bot does almost nothing: it watches two forum channels,
maps the channel a request landed in onto a ``project_type`` (a deterministic
code lookup — no LLM, no phrasing heuristics), and fires a ``repository_dispatch``
carrying the requirement + type. The workflow (``.github/workflows/build.yml``)
then routes to the right runner.

    #hardware channel → project_type "hardware" → self-hosted macOS runner
    #app channel      → project_type "app"      → hosted ubuntu runner

The pure pieces (``resolve_project_type``, ``build_dispatch_payload``,
``GitHubDispatcher``) are split out so they can be unit-tested without a live
gateway.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

GITHUB_API = "https://api.github.com"
DEFAULT_EVENT_TYPE = "new-project"


# --------------------------------------------------------------------------- #
# Pure core (unit-testable)
# --------------------------------------------------------------------------- #
def resolve_project_type(parent_channel_id: int, channel_map: dict[int, str]) -> str | None:
    """Map a forum channel id onto a project type, or None if unmapped."""
    return channel_map.get(parent_channel_id)


# Leading slash-prefixes that declare the project type in a post (tolerant of
# the owner's common typos). Checked before the channel mapping.
_TYPE_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("app", ("/app", "/application", "/アプリ")),
    ("hardware", ("/hardware", "/hardwere", "/hw", "/ハード")),
)


def infer_type_from_text(text: str) -> tuple[str | None, str]:
    """Detect a leading /app or /hardware prefix; return (type|None, stripped).

    The prefix wins over the channel mapping so a single forum channel can carry
    both project types. Returns the original text unchanged when no prefix.
    """
    stripped = (text or "").lstrip()
    low = stripped.lower()
    for ptype, prefixes in _TYPE_PREFIXES:
        for p in prefixes:
            if low.startswith(p):
                return ptype, stripped[len(p):].lstrip()
    return None, text


def build_dispatch_payload(
    requirement: str, project_type: str, thread_id: str | None
) -> dict[str, Any]:
    """Build the ``client_payload`` for the repository_dispatch event."""
    return {
        "requirement": requirement,
        "project_type": project_type,
        "thread_id": str(thread_id) if thread_id is not None else "",
    }


class Dispatcher(Protocol):
    """Anything that can deliver a project to the execution backend."""

    async def dispatch(self, payload: dict[str, Any]) -> None: ...


class GitHubDispatcher:
    """Fires ``POST /repos/{repo}/dispatches`` to trigger the build workflow."""

    def __init__(self, repo: str, token: str, *, event_type: str = DEFAULT_EVENT_TYPE):
        self.repo = repo
        self.token = token
        self.event_type = event_type

    async def dispatch(self, payload: dict[str, Any]) -> None:
        url = f"{GITHUB_API}/repos/{self.repo}/dispatches"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        body = {"event_type": self.event_type, "client_payload": payload}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()  # 204 on success


# --------------------------------------------------------------------------- #
# Discord wiring
# --------------------------------------------------------------------------- #
def build_dispatch_bot(
    *,
    bot_token: str,
    channel_map: dict[int, str],
    github_repo: str | None = None,
    github_token: str | None = None,
    owner_user_id: int | None = None,
    event_type: str = DEFAULT_EVENT_TYPE,
    dispatcher: Dispatcher | None = None,
):
    """Construct (but do not run) the intake bot.

    ``dispatcher`` can be injected (tests / alternative backends); otherwise a
    :class:`GitHubDispatcher` is built from ``github_repo`` + ``github_token``.
    """
    import discord
    from discord.ext import commands

    if dispatcher is None:
        if not github_repo or not github_token:
            raise ValueError("github_repo and github_token are required without a dispatcher")
        dispatcher = GitHubDispatcher(github_repo, github_token, event_type=event_type)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    watched: set[int] = set(channel_map)
    _seen: set[int] = set()  # thread ids already dispatched

    async def _requirement_of(thread: "discord.Thread") -> str:
        requirement = thread.name or ""
        try:
            async for msg in thread.history(limit=1, oldest_first=True):
                if msg.content:
                    requirement = msg.content
                break
        except Exception as exc:  # noqa: BLE001
            print(f"[intake] history read failed: {exc}")
        return requirement

    async def _handle_thread(thread: "discord.Thread") -> None:
        if thread.id in _seen or thread.parent_id not in watched:
            return
        # Claim the thread *before* any await so concurrent events (on_thread_create
        # + on_message + poll) can't double-dispatch the same project.
        _seen.add(thread.id)
        requirement = await _requirement_of(thread)
        # A /app or /hardware prefix wins over the channel mapping; falls back to
        # the channel's mapped type, then "hardware".
        prefix_type, requirement = infer_type_from_text(requirement)
        project_type = (
            prefix_type
            or resolve_project_type(thread.parent_id, channel_map)
            or "hardware"
        )
        payload = build_dispatch_payload(requirement, project_type, str(thread.id))
        print(f"[intake] dispatch type={project_type} thread={thread.id} req={requirement!r:.60}")
        try:
            await dispatcher.dispatch(payload)
            await thread.send(
                f"🚀 受付: 種別 `{project_type}` でビルドを開始しました（GitHub Actions）。"
                "完了するとこのスレッドにPRリンクを通知します。"
            )
        except Exception as exc:  # noqa: BLE001
            _seen.discard(thread.id)  # allow retry on next poll
            print(f"[intake] dispatch failed thread={thread.id}: {type(exc).__name__}: {exc}")
            try:
                await thread.send(f"❌ ディスパッチ失敗: {type(exc).__name__}: {str(exc)[:200]}")
            except Exception:
                pass

    async def _poll() -> None:
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                for guild in bot.guilds:
                    for thread in await guild.active_threads():
                        if thread.parent_id in watched and thread.id not in _seen:
                            await _handle_thread(thread)
            except Exception as exc:  # noqa: BLE001
                print(f"[intake] poll error: {type(exc).__name__}: {exc}")
            await asyncio.sleep(15)

    @bot.event
    async def on_ready() -> None:
        print(f"Logged in as {bot.user}; watching channels {sorted(watched)}")
        # Pre-seed existing threads so a restart doesn't re-dispatch old projects.
        try:
            for guild in bot.guilds:
                for thread in await guild.active_threads():
                    if thread.parent_id in watched:
                        _seen.add(thread.id)
        except Exception as exc:  # noqa: BLE001
            print(f"[intake] pre-load error: {exc}")
        asyncio.create_task(_poll())

    @bot.event
    async def on_thread_create(thread: "discord.Thread") -> None:
        if thread.parent_id in watched:
            await asyncio.sleep(1)
            await _handle_thread(thread)

    @bot.event
    async def on_message(message: "discord.Message") -> None:
        if message.author == bot.user:
            return
        thread = message.channel
        if isinstance(thread, discord.Thread) and thread.parent_id in watched:
            await _handle_thread(thread)

    return bot
