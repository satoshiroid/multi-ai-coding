"""FreeCAD domain client for parametric mechanical design (MECHA domain).

Thin high-level adapter over an injected MCP client (real
:class:`~src.mcp.client.McpClient` or :class:`~src.mcp.mock_transport.MockMcpClient`).
Targets neka-nat/freecad-mcp or proximile/FreeCAD-MCP servers.
"""

from __future__ import annotations

from typing import Any

from src.mcp.client import McpToolResult


class FreeCADClient:
    """High-level parametric FreeCAD operations over an MCP client."""

    def __init__(self, client: Any):
        self._client = client

    async def __aenter__(self) -> "FreeCADClient":
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.close()

    async def create_sketch(self, name: str, plane: str = "XY") -> McpToolResult:
        """Create a new sketch ``name`` on the given ``plane``."""
        arguments: dict[str, Any] = {"name": name, "plane": plane}
        return await self._client.call_tool("create_sketch", arguments)

    async def pad_sketch(self, sketch: str, length_mm: float) -> McpToolResult:
        """Pad (extrude) ``sketch`` by ``length_mm``."""
        arguments: dict[str, Any] = {"sketch": sketch, "length_mm": length_mm}
        return await self._client.call_tool("pad_sketch", arguments)

    async def pocket_sketch(self, sketch: str, depth_mm: float) -> McpToolResult:
        """Pocket (cut) ``sketch`` to ``depth_mm``."""
        arguments: dict[str, Any] = {"sketch": sketch, "depth_mm": depth_mm}
        return await self._client.call_tool("pocket_sketch", arguments)

    async def set_spreadsheet_cell(self, cell: str, value: str) -> McpToolResult:
        """Set spreadsheet ``cell`` (e.g. ``"B2"``) for spreadsheet-driven params."""
        arguments: dict[str, Any] = {"cell": cell, "value": value}
        return await self._client.call_tool("set_spreadsheet_cell", arguments)

    async def recompute(self) -> McpToolResult:
        """Recompute the active document."""
        arguments: dict[str, Any] = {}
        return await self._client.call_tool("recompute", arguments)

    async def export_step(self, path: str = "model.step") -> McpToolResult:
        """Export the model to a STEP file at ``path``."""
        arguments: dict[str, Any] = {"path": path}
        return await self._client.call_tool("export_step", arguments)

    async def run_macro(self, code: str) -> McpToolResult:
        """Execute an arbitrary FreeCAD Python macro ``code`` (passthrough)."""
        arguments: dict[str, Any] = {"code": code}
        return await self._client.call_tool("run_macro", arguments)
