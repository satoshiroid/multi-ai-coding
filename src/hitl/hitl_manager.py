"""Channel-agnostic coordinator for owner approval gates.

The orchestrator must be able to *await* a human decision that may not arrive
for hours or days — e.g. the owner clicking a Discord button long after the
request was posted. We model each outstanding gate as an :class:`asyncio.Future`
keyed by ``request_id``: the orchestrator coroutine awaits the future, while a
completely separate code path (the Discord bot's button callback) later calls
:meth:`HitlManager.resolve` to set its result. This decouples *posting* a
request from *receiving* the answer without polling, and a single timeout guards
against the owner never responding.

The CLI fallback is a special case: there is no external callback, so the
decision is collected inline via :meth:`CliChannel.prompt_decision`.
"""

from __future__ import annotations

import asyncio

from src.hitl.channels.base_channel import BaseChannel
from src.hitl.channels.cli_channel import CliChannel
from src.models import HitlDecision, HitlRequest, HitlResponse


class HitlManager:
    """Coordinates approval gates across an arbitrary :class:`BaseChannel`."""

    def __init__(self, channel: BaseChannel, timeout_hours: float = 72.0) -> None:
        self._channel = channel
        self._timeout = timeout_hours * 3600
        # Outstanding gates awaiting an out-of-band (e.g. Discord) decision.
        self._pending: dict[str, asyncio.Future[HitlResponse]] = {}

    @property
    def channel(self) -> BaseChannel:
        """The notification channel (so callers can post progress updates)."""
        return self._channel

    async def request(self, thread_id: str, request: HitlRequest) -> HitlResponse:
        """Push the request to the channel and await the owner's decision.

        For the CLI channel the decision is gathered synchronously. For any
        other (Discord) channel a future is registered under ``request_id`` and
        awaited until either :meth:`resolve` fulfils it or the timeout fires, in
        which case a ``TIMEOUT`` response is returned so the pipeline can fail
        the gate gracefully rather than hang forever.
        """
        # CLI path: no external callback exists, resolve inline.
        if isinstance(self._channel, CliChannel):
            await self._channel.push_approval(thread_id, request)
            return await self._channel.prompt_decision(request)

        # Generic (Discord) path: park a future and wait for an external resolve.
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[HitlResponse] = loop.create_future()
        self._pending[request.request_id] = fut
        try:
            await self._channel.push_approval(thread_id, request)
            return await asyncio.wait_for(fut, self._timeout)
        except asyncio.TimeoutError:
            return HitlResponse(
                request_id=request.request_id, decision=HitlDecision.TIMEOUT
            )
        finally:
            self._pending.pop(request.request_id, None)

    async def request_choice(
        self,
        thread_id: str,
        request: HitlRequest,
        options: list[str],
        image_paths: list[str] | None = None,
    ) -> HitlResponse:
        """Like :meth:`request` but the owner picks one of ``options``.

        Used for proposal-style escalations and image-based design selection: the
        chosen option text comes back in the response's ``feedback``. CLI resolves
        inline; Discord parks a future that a button callback fulfils via
        :meth:`resolve`.
        """
        if isinstance(self._channel, CliChannel):
            await self._channel.push_choice(thread_id, request, options, image_paths)
            return await self._channel.prompt_choice(request, options)

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[HitlResponse] = loop.create_future()
        self._pending[request.request_id] = fut
        try:
            await self._channel.push_choice(thread_id, request, options, image_paths)
            return await asyncio.wait_for(fut, self._timeout)
        except asyncio.TimeoutError:
            return HitlResponse(
                request_id=request.request_id, decision=HitlDecision.TIMEOUT
            )
        finally:
            self._pending.pop(request.request_id, None)

    def resolve(self, response: HitlResponse) -> bool:
        """Fulfil a pending gate, typically from a Discord button callback.

        Returns ``True`` if a matching, still-open request was found and
        completed; ``False`` if no such request is pending (e.g. it already
        timed out or was resolved).
        """
        fut = self._pending.get(response.request_id)
        if fut is not None and not fut.done():
            try:
                fut.set_result(response)
                return True
            except asyncio.InvalidStateError:
                return False
        return False

    def pending_ids(self) -> list[str]:
        """Return the ids of all gates currently awaiting an owner decision."""
        return list(self._pending.keys())
