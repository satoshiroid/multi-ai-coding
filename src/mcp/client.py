"""MCP client base — connects to existing CAD/EDA MCP servers over stdio.

The heavy lifting (actually driving FreeCAD/KiCAD/Blender) is delegated to
mature third-party MCP servers; this class is a thin async adapter that the
domain clients subclass. When a tool's MCP server is disabled in config, the
orchestrator substitutes :class:`~src.mcp.mock_transport.MockMcpClient`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpToolResult:
    """Normalized result of an MCP tool call."""

    tool: str
    ok: bool
    content: Any = None
    error: str | None = None
    raw: Any = None


@dataclass
class McpServerSpec:
    """How to launch / reach an MCP server (from config)."""

    name: str
    enabled: bool = False
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    host: str | None = None    # tcp transport (BlenderMCP addon socket)
    port: int | None = None


def client_for_spec(spec: McpServerSpec) -> Any:
    """Instantiate the right client for a spec's transport.

    ``tcp`` is the BlenderMCP addon's raw JSON socket (not MCP protocol), so it
    gets the dedicated :class:`~src.mcp.blender_tcp.BlenderTcpClient`; everything
    else goes through the official MCP SDK via :class:`McpClient`.
    """
    if spec.transport == "tcp":
        from src.mcp.blender_tcp import BlenderTcpClient  # local import: avoid cycle

        return BlenderTcpClient(host=spec.host or "localhost", port=spec.port or 9876)
    return McpClient(spec)


class McpClient:
    """Async adapter over a stdio MCP server using the official ``mcp`` SDK.

    Usage::

        async with McpClient(spec) as client:
            tools = await client.list_tools()
            result = await client.call_tool("create_sketch", {...})
    """

    def __init__(self, spec: McpServerSpec):
        self.spec = spec
        self._session = None
        self._stack = None

    async def __aenter__(self) -> "McpClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open a session to the configured MCP server (stdio or SSE)."""
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "mcp package not installed. Run `pip install mcp`."
            ) from exc

        self._stack = AsyncExitStack()

        if self.spec.transport == "stdio":
            if not self.spec.command:
                raise ValueError(f"MCP server {self.spec.name!r} has no launch command.")
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(command=self.spec.command, args=self.spec.args)
            read, write = await self._stack.enter_async_context(stdio_client(params))

        elif self.spec.transport == "sse":
            if not self.spec.url:
                raise ValueError(f"MCP server {self.spec.name!r} requires a URL for SSE transport.")
            from mcp.client.sse import sse_client

            read, write = await self._stack.enter_async_context(sse_client(self.spec.url))

        else:
            raise NotImplementedError(
                f"Unsupported MCP transport {self.spec.transport!r}. Use 'stdio' or "
                "'sse' here; the BlenderMCP addon socket uses transport 'tcp' via "
                "client_for_spec()."
            )

        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None

    async def list_tools(self) -> list[str]:
        if self._session is None:
            raise RuntimeError("MCP session not connected. Call connect() first.")
        resp = await self._session.list_tools()
        return [t.name for t in resp.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        if self._session is None:
            raise RuntimeError("MCP session not connected. Call connect() first.")
        try:
            resp = await self._session.call_tool(name, arguments)
            content = getattr(resp, "content", resp)
            is_error = bool(getattr(resp, "isError", False))
            return McpToolResult(
                tool=name,
                ok=not is_error,
                content=content,
                error=None if not is_error else str(content),
                raw=resp,
            )
        except Exception as exc:  # noqa: BLE001 - normalize transport errors
            return McpToolResult(tool=name, ok=False, error=str(exc))
