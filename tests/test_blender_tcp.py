"""Tests for the BlenderMCP addon TCP client (fake addon socket server)."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.mcp.blender_tcp import BlenderTcpClient
from src.mcp.client import McpServerSpec, client_for_spec


class FakeAddon:
    """Minimal stand-in for the BlenderMCP addon's JSON-over-TCP server."""

    def __init__(self) -> None:
        self.received: list[dict] = []
        self.server: asyncio.AbstractServer | None = None
        self.port: int = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            # Python 3.12's Server.wait_closed() waits for *all* connections and
            # can block indefinitely on a lingering one (a behaviour difference
            # vs 3.11/3.14 that hung CI). Bound it so fixture teardown can't hang.
            try:
                await asyncio.wait_for(self.server.wait_closed(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            self.server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = b""
        while True:
            chunk = await reader.read(8192)
            if not chunk:
                break
            buffer += chunk
            try:
                command = json.loads(buffer.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            buffer = b""
            self.received.append(command)
            response = json.dumps(self._respond(command)).encode("utf-8")
            if command["type"] == "get_scene_info":
                # Split the write to exercise the client's reassembly loop.
                mid = len(response) // 2
                writer.write(response[:mid])
                await writer.drain()
                await asyncio.sleep(0.01)
                writer.write(response[mid:])
            else:
                writer.write(response)
            await writer.drain()

    @staticmethod
    def _respond(command: dict) -> dict:
        kind = command["type"]
        if kind == "execute_code":
            return {"status": "success", "result": {"executed": True}}
        if kind == "get_scene_info":
            # Large + multibyte payload: must survive chunked reads.
            return {
                "status": "success",
                "result": {
                    "object_count": 3,
                    "objects": ["立方体オブジェクト"] * 200,
                },
            }
        if kind == "boom":
            return {"status": "error", "message": "kaboom"}
        return {"status": "success", "result": {}}


@pytest.fixture
async def fake_addon():
    addon = FakeAddon()
    await addon.start()
    yield addon
    await addon.stop()


async def test_execute_blender_code_maps_to_execute_code(fake_addon):
    async with BlenderTcpClient(host="127.0.0.1", port=fake_addon.port) as client:
        result = await client.call_tool("execute_blender_code", {"code": "import bpy"})
    assert result.ok
    assert result.content == {"executed": True}
    # MCP tool name translated to the addon command type.
    assert fake_addon.received[0] == {"type": "execute_code", "params": {"code": "import bpy"}}


async def test_error_status_surfaces_as_failed_result(fake_addon):
    async with BlenderTcpClient(host="127.0.0.1", port=fake_addon.port) as client:
        result = await client.call_tool("boom", {})
    assert not result.ok
    assert result.error == "kaboom"


async def test_chunked_multibyte_response_is_reassembled(fake_addon):
    async with BlenderTcpClient(host="127.0.0.1", port=fake_addon.port) as client:
        result = await client.call_tool("get_scene_info", {})
    assert result.ok
    assert result.content["object_count"] == 3
    assert len(result.content["objects"]) == 200


async def test_multiple_calls_reuse_one_connection(fake_addon):
    async with BlenderTcpClient(host="127.0.0.1", port=fake_addon.port) as client:
        first = await client.call_tool("execute_blender_code", {"code": "a = 1"})
        second = await client.call_tool("execute_blender_code", {"code": "a = 2"})
    assert first.ok and second.ok
    assert len(fake_addon.received) == 2


async def test_connection_refused_returns_failed_result():
    # Nothing listens on this port: call_tool must degrade, not raise.
    client = BlenderTcpClient(host="127.0.0.1", port=1, timeout=1.0)
    result = await client.call_tool("execute_blender_code", {"code": "x"})
    assert not result.ok
    assert result.error


def test_client_for_spec_selects_tcp_transport():
    spec = McpServerSpec(name="blender", enabled=True, transport="tcp", host="h", port=1234)
    client = client_for_spec(spec)
    assert isinstance(client, BlenderTcpClient)
    assert client.host == "h"
    assert client.port == 1234


def test_client_for_spec_defaults_to_mcp_sdk():
    from src.mcp.client import McpClient

    spec = McpServerSpec(name="freecad", transport="stdio", command="freecad-mcp")
    assert isinstance(client_for_spec(spec), McpClient)
