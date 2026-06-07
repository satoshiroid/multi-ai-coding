"""Interactive terminal channel — the test / mock-E2E fallback for Discord.

When no Discord bot is wired up (CI, local mock runs) the owner role is played
by stdin/stdout. Unlike the Discord transport, a CLI decision is collected
*synchronously* by reading a line of input, so :class:`CliChannel` exposes an
extra :meth:`prompt_decision` that :class:`HitlManager` calls directly instead
of parking an external future.

Rendering uses ``rich`` when present for readable tables/panels, but degrades to
plain :func:`print` so the channel works in a bare environment.
"""

from __future__ import annotations

import asyncio

from src.hitl.channels.base_channel import BaseChannel
from src.models import HitlDecision, HitlRequest, HitlResponse

# ``rich`` is an optional nicety; fall back to plain printing if unavailable so
# the test fallback never adds a hard dependency.
try:  # pragma: no cover - import-guard, behaviour identical either way
    from rich.console import Console
    from rich.table import Table

    _console: Console | None = Console()
except Exception:  # noqa: BLE001 - any import failure means "no rich"
    _console = None
    Console = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]


def _emit(text: str) -> None:
    """Print via rich if available, else plain stdout."""
    if _console is not None:
        _console.print(text)
    else:
        print(text)


class CliChannel(BaseChannel):
    """Terminal implementation of :class:`BaseChannel`.

    :param auto_approve: when True the channel never blocks on input and every
        gate resolves to ``APPROVE``. Used by ``--mock`` end-to-end runs where a
        human is not present.
    """

    def __init__(self, auto_approve: bool = False) -> None:
        self.auto_approve = auto_approve

    async def create_project_thread(self, project_id: str, title: str) -> str:
        """Print a header and return a synthetic, deterministic thread id."""
        _emit(f"\n=== PROJECT {project_id}: {title} ===")
        return f"cli-{project_id}"

    async def push_approval(self, thread_id: str, request: HitlRequest) -> None:
        """Render the request, its cost and BOM for the owner to inspect."""
        _emit(f"\n[{thread_id}] APPROVAL GATE: {request.gate}")
        _emit(f"Title: {request.title}")
        _emit(request.body)
        if request.image_paths:
            _emit("Images: " + ", ".join(request.image_paths))
        if request.bom:
            self._render_bom(request)
        if request.total_cost is not None:
            _emit(f"Total cost: {request.total_cost}")
        opts = ", ".join(o.value for o in request.options)
        _emit(f"Options: {opts}")

    async def push_escalation(
        self, thread_id: str, title: str, body: str, options: list[str]
    ) -> None:
        """Render an escalation choice to stdout."""
        _emit(f"\n[{thread_id}] ESCALATION: {title}")
        _emit(body)
        _emit("Options: " + ", ".join(options))

    async def push_progress(self, thread_id: str, message: str) -> None:
        """Render a progress update to stdout."""
        _emit(f"[{thread_id}] {message}")

    async def prompt_decision(self, request: HitlRequest) -> HitlResponse:
        """Collect the owner's decision from the terminal.

        With ``auto_approve`` this returns immediately. Otherwise it reads a
        line from stdin in a worker thread (via :func:`asyncio.to_thread`) so
        the event loop is not blocked while waiting for the human, parsing:

        * ``a``            -> APPROVE
        * ``r <feedback>`` -> REVISE (remainder is captured as feedback)
        * ``x``            -> REJECT
        """
        if self.auto_approve:
            return HitlResponse(
                request_id=request.request_id, decision=HitlDecision.APPROVE
            )

        raw = await asyncio.to_thread(
            input, "Decision [a=approve, r <feedback>=revise, x=reject]: "
        )
        return self._parse_decision(request.request_id, raw)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_decision(request_id: str, raw: str) -> HitlResponse:
        text = raw.strip()
        lowered = text.lower()
        if lowered == "x":
            return HitlResponse(request_id=request_id, decision=HitlDecision.REJECT)
        if lowered == "r" or lowered.startswith("r "):
            feedback = text[1:].strip() or None
            return HitlResponse(
                request_id=request_id,
                decision=HitlDecision.REVISE,
                feedback=feedback,
            )
        # Default (including "a" or empty) to approve.
        return HitlResponse(request_id=request_id, decision=HitlDecision.APPROVE)

    @staticmethod
    def _render_bom(request: HitlRequest) -> None:
        if _console is not None and Table is not None:
            table = Table(title="Bill of Materials")
            for col in ("Domain", "Part", "Description", "Qty", "Unit", "Line"):
                table.add_column(col)
            for item in request.bom:
                table.add_row(
                    item.domain.value,
                    item.part_number,
                    item.description,
                    str(item.quantity),
                    f"{item.unit_cost}",
                    f"{item.line_cost}",
                )
            _console.print(table)
        else:
            _emit("BOM:")
            for item in request.bom:
                _emit(
                    f"  - {item.domain.value} {item.part_number} "
                    f"x{item.quantity} @ {item.unit_cost} = {item.line_cost} "
                    f"({item.description})"
                )
