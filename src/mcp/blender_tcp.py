"""Direct TCP client for the BlenderMCP *addon* socket (ahujasid/blender-mcp).

The "Running on port 9876" server inside Blender is NOT an MCP endpoint — it is
the addon's own JSON-over-TCP socket. (The MCP server in that project is a
separate `uvx blender-mcp` bridge process that Claude Desktop spawns over
stdio.) Connecting to port 9876 with SSE or the MCP SDK therefore fails; this
client speaks the addon's wire protocol directly so no bridge process or `uv`
install is needed.

Wire protocol (one request per write, one JSON object per response):

    -> {"type": "execute_code", "params": {"code": "..."}}
    <- {"status": "success", "result": {...}}
    <- {"status": "error", "message": "..."}

Responses carry no length prefix or delimiter, so we accumulate chunks until
the buffer parses as JSON — the same strategy the official bridge uses.

The public surface (``connect``/``close``/``list_tools``/``call_tool``) is
duck-type compatible with :class:`~src.mcp.client.McpClient`, so
:class:`~src.mcp.blender_client.BlenderClient` and the orchestrator can use it
interchangeably.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.mcp.client import McpToolResult

# MCP-style tool name -> addon command type. Unlisted names pass through
# unchanged (the addon also accepts e.g. "get_scene_info" directly).
_TOOL_TO_COMMAND: dict[str, str] = {
    "execute_blender_code": "execute_code",
}

_KNOWN_TOOLS = [
    "execute_blender_code",
    "get_scene_info",
    "get_object_info",
    "get_viewport_screenshot",
]


class BlenderTcpClient:
    """Async client for the BlenderMCP addon's TCP socket.

    :param host: addon host (the panel's default is localhost).
    :param port: addon port (the panel's default is 9876).
    :param timeout: per-response timeout in seconds. Renders run inside
        ``execute_code`` on Blender's main thread, so this must cover a full
        render, not just a quick eval.
    """

    def __init__(self, host: str = "localhost", port: int = 9876, timeout: float = 180.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def __aenter__(self) -> "BlenderTcpClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open the TCP connection to the addon socket."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=10.0
        )

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001 - peer may already be gone
                pass
        self._reader = None
        self._writer = None

    async def list_tools(self) -> list[str]:
        return list(_KNOWN_TOOLS)

    async def send_command(self, command_type: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one raw addon command and return the decoded JSON response."""
        if self._writer is None or self._reader is None:
            await self.connect()
        assert self._writer is not None and self._reader is not None

        payload = json.dumps({"type": command_type, "params": params}).encode("utf-8")
        self._writer.write(payload)
        await self._writer.drain()

        # Accumulate until the buffer is one complete JSON document.
        buffer = b""
        while True:
            chunk = await asyncio.wait_for(self._reader.read(8192), timeout=self.timeout)
            if not chunk:
                raise ConnectionError(
                    "Blender addon closed the connection mid-response "
                    f"(received {len(buffer)} bytes)."
                )
            buffer += chunk
            try:
                return json.loads(buffer.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # partial JSON / split multibyte char — keep reading

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        """MCP-shaped tool call mapped onto the addon protocol."""
        command_type = _TOOL_TO_COMMAND.get(name, name)
        try:
            response = await self.send_command(command_type, arguments)
        except Exception as exc:  # noqa: BLE001 - normalize transport errors
            return McpToolResult(tool=name, ok=False, error=str(exc))

        if response.get("status") == "error":
            return McpToolResult(
                tool=name,
                ok=False,
                error=str(response.get("message", "unknown Blender addon error")),
                raw=response,
            )
        return McpToolResult(tool=name, ok=True, content=response.get("result"), raw=response)
