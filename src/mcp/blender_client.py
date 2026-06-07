"""Blender domain client for industrial design (DESIGN domain).

Thin high-level adapter over an injected MCP client (real
:class:`~src.mcp.client.McpClient` or :class:`~src.mcp.mock_transport.MockMcpClient`).
Targets blenderlm-compatible MCP servers that expose Blender's Python API.
"""

from __future__ import annotations

from typing import Any

from src.mcp.client import McpToolResult


class BlenderClient:
    """High-level Blender operations over an MCP client (blenderlm-style)."""

    def __init__(self, client: Any):
        self._client = client

    async def __aenter__(self) -> "BlenderClient":
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.close()

    async def create_primitive(self, shape: str, **params: Any) -> McpToolResult:
        """Add a primitive mesh (``shape`` e.g. ``"cube"``) with extra params."""
        arguments: dict[str, Any] = {"shape": shape, **params}
        return await self._client.call_tool("create_primitive", arguments)

    async def render(self, output_path: str = "render.png") -> McpToolResult:
        """Render the current scene to ``output_path``."""
        arguments: dict[str, Any] = {"output_path": output_path}
        return await self._client.call_tool("render", arguments)

    async def run_python(self, script: str) -> McpToolResult:
        """Execute an arbitrary Blender Python API ``script`` (passthrough)."""
        arguments: dict[str, Any] = {"script": script}
        return await self._client.call_tool("run_python", arguments)
