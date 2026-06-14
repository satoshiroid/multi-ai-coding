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

    async def run_python(self, script: str) -> McpToolResult:
        """Execute arbitrary Blender Python code via the BlenderMCP addon."""
        return await self._client.call_tool("execute_blender_code", {"code": script})

    async def render(self, output_path: str = "render.png") -> McpToolResult:
        """Render the current Blender scene to ``output_path`` (absolute path)."""
        render_code = (
            "import bpy\n"
            f"bpy.context.scene.render.filepath = {repr(output_path)}\n"
            "bpy.context.scene.render.image_settings.file_format = 'PNG'\n"
            "bpy.ops.render.render(write_still=True)"
        )
        return await self._client.call_tool("execute_blender_code", {"code": render_code})
