"""Discord transport for :class:`HitlManager` approval gates.

Renders each phase gate as a rich Discord embed with interactive buttons inside
a per-project forum thread. The key design constraint is the *out-of-band*
decision lifecycle defined by :class:`HitlManager`: posting an approval only
*displays* the gate, while the owner's answer arrives much later through a button
callback. That callback lives on :class:`_ApprovalView`, which calls
``HitlManager.resolve`` to fulfil the parked future — so the view needs a
reference to the manager, injected after construction via :meth:`attach_manager`
(the channel is built before the manager that owns it).
"""

from __future__ import annotations

import os

import discord

from src.hitl.channels.base_channel import BaseChannel
from src.models import HitlDecision, HitlRequest, HitlResponse


class DiscordChannel(BaseChannel):
    """Discord implementation of :class:`BaseChannel`.

    :param bot: an already-connected ``discord.Client``/``commands.Bot``.
    :param forum_channel_id: forum channel whose threads represent projects.
    :param owner_user_id: only this user's button clicks are honoured.
    :param hitl_manager: usually set later via :meth:`attach_manager` because the
        manager is constructed *after* the channel it wraps.
    """

    def __init__(
        self,
        bot: discord.Client,
        forum_channel_id: int,
        owner_user_id: int,
        hitl_manager: object | None = None,
    ) -> None:
        self._bot = bot
        self._forum_channel_id = forum_channel_id
        self._owner_user_id = owner_user_id
        self._hitl = hitl_manager

    def attach_manager(self, hitl_manager: object) -> None:
        """Inject the :class:`HitlManager` whose futures the views resolve."""
        self._hitl = hitl_manager

    # ------------------------------------------------------------------ #
    # BaseChannel contract
    # ------------------------------------------------------------------ #
    async def create_project_thread(self, project_id: str, title: str) -> str:
        """Create a forum post (or text-channel thread) for one project.

        Returns the new thread id as a string so the manager can route every
        subsequent ``push_*`` call back to it.
        """
        channel = await self._resolve_channel(self._forum_channel_id)
        content = f"Project {project_id} started."

        if isinstance(channel, discord.ForumChannel):
            created = await channel.create_thread(name=title, content=content)
            # ForumChannel.create_thread returns a (Thread, Message) wrapper.
            thread = getattr(created, "thread", created)
        else:
            # Fall back to a thread on a regular text channel.
            thread = await channel.create_thread(name=title)  # type: ignore[union-attr]
        return str(thread.id)

    async def push_approval(self, thread_id: str, request: HitlRequest) -> None:
        """Render artifacts + approve/revise/reject controls for the owner."""
        thread = await self._fetch_thread(thread_id)

        embed = discord.Embed(title=request.title, description=request.body)
        if request.total_cost is not None:
            embed.add_field(name="Total cost", value=f"{request.total_cost}", inline=False)
        if request.bom:
            embed.add_field(name="BOM", value=self._format_bom(request), inline=False)

        files = self._collect_files(request.image_paths)
        view = _ApprovalView(request.request_id, self._owner_user_id, self._hitl)
        await thread.send(embed=embed, files=files, view=view)

    async def push_escalation(
        self, thread_id: str, title: str, body: str, options: list[str]
    ) -> None:
        """Present an escalation choice as one button per option string."""
        thread = await self._fetch_thread(thread_id)
        embed = discord.Embed(title=title, description=body)
        view = _EscalationView(options)
        await thread.send(embed=embed, view=view)

    async def push_progress(self, thread_id: str, message: str) -> None:
        """Post a plain-text progress update to the thread.

        Discord counts characters as UTF-16 code units (emoji = 2 units each),
        so we split by UTF-16 length rather than Python str length.
        """
        thread = await self._fetch_thread(thread_id)
        # 1800 UTF-16 units gives comfortable headroom under Discord's 2000-unit limit.
        limit_utf16 = 1800
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for char in message or " ":
            char_len = len(char.encode("utf-16-le")) // 2
            if current_len + char_len > limit_utf16:
                chunks.append("".join(current))
                current = [char]
                current_len = char_len
            else:
                current.append(char)
                current_len += char_len
        if current:
            chunks.append("".join(current))
        for chunk in chunks:
            await thread.send(chunk)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    async def _resolve_channel(self, channel_id: int) -> discord.abc.GuildChannel:
        """Return a channel from cache, falling back to an API fetch."""
        channel = self._bot.get_channel(channel_id)
        if channel is None:
            channel = await self._bot.fetch_channel(channel_id)
        return channel  # type: ignore[return-value]

    async def _fetch_thread(self, thread_id: str) -> discord.Thread:
        """Resolve a thread id (cache first, then API)."""
        tid = int(thread_id)
        thread = self._bot.get_channel(tid) or await self._bot.fetch_channel(tid)
        return thread  # type: ignore[return-value]

    @staticmethod
    def _collect_files(image_paths: list[str]) -> list[discord.File]:
        """Wrap on-disk images as :class:`discord.File`, skipping missing ones."""
        return [
            discord.File(path, filename=os.path.basename(path))
            for path in image_paths
            if os.path.exists(path)
        ]

    @staticmethod
    def _format_bom(request: HitlRequest) -> str:
        """Render the BOM as compact lines for an embed field."""
        lines = [
            f"- {item.domain.value} {item.part_number} x{item.quantity} "
            f"@ {item.unit_cost} = {item.line_cost} ({item.description})"
            for item in request.bom
        ]
        return "\n".join(lines) or "(empty)"


class _ApprovalView(discord.ui.View):
    """Approve / Revise / Reject buttons gated to the project owner.

    Each callback fulfils the manager's parked future via ``resolve`` so the
    awaiting orchestrator coroutine can proceed, then stops the view so the
    buttons stop accepting input.
    """

    def __init__(
        self,
        request_id: str,
        owner_user_id: int,
        hitl_manager: object | None,
    ) -> None:
        super().__init__(timeout=None)
        self._request_id = request_id
        self._owner_user_id = owner_user_id
        self._hitl = hitl_manager

    async def _guard(self, interaction: discord.Interaction) -> bool:
        """Reject clicks from anyone but the owner; True if allowed to proceed."""
        if interaction.user.id != self._owner_user_id:
            await interaction.response.send_message(
                "オーナーのみ操作できます", ephemeral=True
            )
            return False
        return True

    def _resolve(self, decision: HitlDecision, feedback: str | None = None) -> None:
        if self._hitl is not None:
            self._hitl.resolve(
                HitlResponse(
                    request_id=self._request_id, decision=decision, feedback=feedback
                )
            )

    @discord.ui.button(label="✅承認", style=discord.ButtonStyle.green)
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        self._resolve(HitlDecision.APPROVE)
        await interaction.response.send_message("承認しました")
        self.stop()

    @discord.ui.button(label="✏️修正", style=discord.ButtonStyle.blurple)
    async def revise(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        # Feedback is collected through a modal, which resolves on submit.
        await interaction.response.send_modal(
            _ReviseModal(self._request_id, self._hitl, self)
        )

    @discord.ui.button(label="❌却下", style=discord.ButtonStyle.red)
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._guard(interaction):
            return
        self._resolve(HitlDecision.REJECT)
        await interaction.response.send_message("却下しました")
        self.stop()


class _ReviseModal(discord.ui.Modal, title="修正内容"):
    """Collect free-text revision feedback before resolving the gate."""

    feedback: discord.ui.TextInput = discord.ui.TextInput(
        label="フィードバック",
        style=discord.TextStyle.paragraph,
        required=False,
    )

    def __init__(
        self,
        request_id: str,
        hitl_manager: object | None,
        view: _ApprovalView,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._hitl = hitl_manager
        self._view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self._hitl is not None:
            self._hitl.resolve(
                HitlResponse(
                    request_id=self._request_id,
                    decision=HitlDecision.REVISE,
                    feedback=str(self.feedback.value) or None,
                )
            )
        await interaction.response.send_message("修正を受け付けました")
        self._view.stop()


class _EscalationView(discord.ui.View):
    """Render one button per escalation option string.

    Escalation answers are informational here (no manager future to fulfil); the
    view simply acknowledges the chosen option to the owner.
    """

    def __init__(self, options: list[str]) -> None:
        super().__init__(timeout=None)
        for option in options:
            self.add_item(_EscalationButton(option))


class _EscalationButton(discord.ui.Button):
    """A single escalation option that echoes the owner's choice."""

    def __init__(self, option: str) -> None:
        super().__init__(label=option, style=discord.ButtonStyle.secondary)
        self._option = option

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"選択: {self._option}", ephemeral=True
        )
