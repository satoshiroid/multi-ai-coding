"""KiCAD domain client for circuit / PCB design (CIRCUIT domain).

Thin high-level adapter over an injected MCP client (real
:class:`~src.mcp.client.McpClient` or :class:`~src.mcp.mock_transport.MockMcpClient`).
Targets lamaalrajih/kicad-mcp or oaslananka/kicad-mcp-pro servers.
"""

from __future__ import annotations

from typing import Any

from src.mcp.client import McpToolResult


class KiCADClient:
    """High-level KiCAD schematic / PCB operations over an MCP client."""

    def __init__(self, client: Any):
        self._client = client

    async def __aenter__(self) -> "KiCADClient":
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.close()

    async def create_schematic(self, name: str = "main") -> McpToolResult:
        """Create a new schematic ``name``."""
        arguments: dict[str, Any] = {"name": name}
        return await self._client.call_tool("create_schematic", arguments)

    async def place_components(self, components: list[dict]) -> McpToolResult:
        """Place the given ``components`` on the schematic / board."""
        arguments: dict[str, Any] = {"components": components}
        return await self._client.call_tool("place_components", arguments)

    async def autoroute(self) -> McpToolResult:
        """Autoroute the PCB."""
        arguments: dict[str, Any] = {}
        return await self._client.call_tool("autoroute", arguments)

    async def run_drc(self) -> McpToolResult:
        """Run the design rule check (DRC)."""
        arguments: dict[str, Any] = {}
        return await self._client.call_tool("run_drc", arguments)

    async def export_gerber(self, path: str = "gerber.zip") -> McpToolResult:
        """Export Gerber fabrication files to ``path``."""
        arguments: dict[str, Any] = {"path": path}
        return await self._client.call_tool("export_gerber", arguments)

    async def export_bom(self, path: str = "bom.csv") -> McpToolResult:
        """Export the bill of materials to ``path``."""
        arguments: dict[str, Any] = {"path": path}
        return await self._client.call_tool("export_bom", arguments)
