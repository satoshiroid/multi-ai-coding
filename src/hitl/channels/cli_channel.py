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

    async def push_choice(
        self,
        thread_id: str,
        request: HitlRequest,
        options: list[str],
        image_paths: list[str] | None = None,
    ) -> None:
        """Render a proposal-style escalation with numbered options."""
        _emit(f"\n[{thread_id}] 🔺 {request.title}")
        _emit(request.body)
        if image_paths:
            _emit("Images: " + ", ".join(image_paths))
        for i, opt in enumerate(options, start=1):
            _emit(f"  [{i}] {opt}")

    async def prompt_choice(
        self, request: HitlRequest, options: list[str]
    ) -> HitlResponse:
        """Collect the owner's proposal choice (auto-picks the first under auto-approve)."""
        if self.auto_approve or not options:
            return HitlResponse(
                request_id=request.request_id,
                decision=HitlDecision.APPROVE,
                feedback=(options[0] if options else None),
            )
        while True:
            raw = await asyncio.to_thread(input, f"対応案を選択 [1-{len(options)}]: ")
            try:
                idx = int(raw.strip()) - 1
            except ValueError:
                idx = -1
            if 0 <= idx < len(options):
                return HitlResponse(
                    request_id=request.request_id,
                    decision=HitlDecision.APPROVE,
                    feedback=options[idx],
                )
            _emit(f"1〜{len(options)} の番号で選択してください")

    async def prompt_decision(self, request: HitlRequest) -> HitlResponse:
        """Collect the owner's decision from the terminal.

        With ``auto_approve`` this returns immediately. Otherwise it reads a
        line from stdin in a worker thread (via :func:`asyncio.to_thread`) so
        the event loop is not blocked while waiting for the human, parsing:

        * ``a`` / Enter    -> APPROVE
        * ``r <feedback>`` -> REVISE (remainder is captured as feedback)
        * ``x``            -> REJECT
        * anything else    -> re-prompt (a typo must never silently approve)
        """
        if self.auto_approve:
            return HitlResponse(
                request_id=request.request_id, decision=HitlDecision.APPROVE
            )

        while True:
            raw = await asyncio.to_thread(
                input, "Decision [a/Enter=approve, r <feedback>=revise, x=reject]: "
            )
            response = self._parse_decision(request.request_id, raw)
            if response is not None:
                return response
            _emit("認識できない入力です: a=承認 / r 修正内容=修正 / x=却下")

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_decision(request_id: str, raw: str) -> HitlResponse | None:
        """Parse one input line; ``None`` means unrecognized (caller re-prompts).

        An approval gate guards irreversible progress, so a typo must never
        silently approve — only an explicit token (or bare Enter) does.
        """
        text = raw.strip()
        lowered = text.lower()
        if lowered in ("x", "reject", "却下"):
            return HitlResponse(request_id=request_id, decision=HitlDecision.REJECT)
        if lowered == "r" or lowered.startswith("r ") or lowered.startswith("修正"):
            feedback = text[1:].strip() if lowered.startswith("r") else text[2:].strip()
            return HitlResponse(
                request_id=request_id,
                decision=HitlDecision.REVISE,
                feedback=feedback or None,
            )
        if lowered in ("", "a", "approve", "承認"):
            return HitlResponse(request_id=request_id, decision=HitlDecision.APPROVE)
        return None

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
