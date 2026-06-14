"""Abstract notification-channel contract used by :class:`HitlManager`.

A *channel* is the transport that connects the orchestrator to the human owner
(Discord in production, the terminal in tests). Decoupling the manager from any
concrete transport via this ABC lets the same approval-gate logic drive a
Discord bot, a CLI prompt, or a future Slack/web UI without modification — the
manager only ever speaks this four-method protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import HitlRequest


class BaseChannel(ABC):
    """Transport contract for presenting work to, and soliciting decisions from,
    the project owner.

    Implementations are responsible only for *rendering* and *delivery*; the
    asynchronous decision lifecycle (futures, timeouts) is owned by
    :class:`HitlManager`.
    """

    @abstractmethod
    async def create_project_thread(self, project_id: str, title: str) -> str:
        """Create a per-project conversation space; return its thread id.

        The returned id is opaque to the manager and is passed back to every
        subsequent ``push_*`` call so messages land in the right place.
        """

    @abstractmethod
    async def push_approval(self, thread_id: str, request: HitlRequest) -> None:
        """Present artifacts + approve/revise/reject controls to the owner.

        This only *displays* the gate; the owner's answer is delivered back to
        the manager out-of-band (e.g. via a Discord button callback) so the
        orchestrator can keep awaiting it for as long as the timeout allows.
        """

    @abstractmethod
    async def push_escalation(
        self, thread_id: str, title: str, body: str, options: list[str]
    ) -> None:
        """Present an escalation choice to the owner."""

    @abstractmethod
    async def push_progress(self, thread_id: str, message: str) -> None:
        """Post a progress update."""

    async def push_choice(
        self,
        thread_id: str,
        request: HitlRequest,
        options: list[str],
        image_paths: list[str] | None = None,
    ) -> None:
        """Present a proposal-style choice (one selectable option per proposal).

        ``image_paths`` lets callers show sketch proposals alongside the options
        (e.g. design selection). Default delegates to :meth:`push_escalation`;
        transports that can resolve the owner's selection override this.
        """
        await self.push_escalation(thread_id, request.title, request.body, options)
