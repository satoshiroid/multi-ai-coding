"""Mock MCP client for CAD-free environments (CI / no Blender/FreeCAD/KiCAD).

Mimics :class:`~src.mcp.client.McpClient` but returns canned tool results so the
full pipeline can be exercised end-to-end without any real CAD/EDA server.
"""

from __future__ import annotations

from typing import Any, Callable

from src.mcp.client import McpServerSpec, McpToolResult


# Per-tool canned responses, keyed by logical tool name.
_DEFAULT_TOOL_RESULTS: dict[str, Any] = {
    # Blender
    "render": {"image_path": "render.png"},
    "create_primitive": {"object": "Cube"},
    # FreeCAD
    "create_sketch": {"sketch": "Sketch001"},
    "pad_sketch": {"solid": "Pad001"},
    "pocket_sketch": {"solid": "Pocket001"},
    "recompute": {"status": "ok"},
    "export_step": {"step_file": "model.step"},
    # KiCAD
    "create_schematic": {"schematic": "main.kicad_sch"},
    "place_components": {"placed": 12},
    "autoroute": {"routed": True},
    "run_drc": {"violations": 0},
    "export_gerber": {"gerber": "gerber.zip"},
    "export_bom": {"items": 1},
}


class MockMcpClient:
    """Drop-in async replacement for :class:`McpClient`."""

    def __init__(
        self,
        spec: McpServerSpec | None = None,
        tool_results: dict[str, Any] | None = None,
        responder: Callable[[str, dict[str, Any]], Any] | None = None,
    ):
        self.spec = spec or McpServerSpec(name="mock")
        self._tool_results = {**_DEFAULT_TOOL_RESULTS, **(tool_results or {})}
        self._responder = responder
        self.connected = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "MockMcpClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def list_tools(self) -> list[str]:
        return list(self._tool_results.keys())

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        self.calls.append((name, arguments))
        if self._responder is not None:
            content = self._responder(name, arguments)
        else:
            content = self._tool_results.get(name, {"status": "ok", "tool": name})
        return McpToolResult(tool=name, ok=True, content=content)
