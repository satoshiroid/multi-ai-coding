"""Tests for the mock MCP transport and the domain clients on top of it."""

from __future__ import annotations

import pytest

from src.mcp.blender_client import BlenderClient
from src.mcp.freecad_client import FreeCADClient
from src.mcp.kicad_client import KiCADClient
from src.mcp.mock_transport import MockMcpClient


@pytest.mark.asyncio
async def test_mock_client_records_calls_and_returns_result():
    client = MockMcpClient()
    async with client:
        assert client.connected
        result = await client.call_tool("export_gerber", {"path": "g.zip"})
    assert result.ok
    assert result.content == {"gerber": "gerber.zip"}
    assert client.calls == [("export_gerber", {"path": "g.zip"})]


@pytest.mark.asyncio
async def test_mock_unknown_tool_falls_back():
    client = MockMcpClient()
    result = await client.call_tool("nonexistent_tool", {})
    assert result.ok
    assert result.content["tool"] == "nonexistent_tool"


@pytest.mark.asyncio
async def test_blender_client_render():
    client = MockMcpClient()
    blender = BlenderClient(client)
    result = await blender.render("out.png")
    assert result.ok
    # BlenderMCP addon uses execute_blender_code; render path must appear in code.
    assert len(client.calls) == 1
    tool, args = client.calls[0]
    assert tool == "execute_blender_code"
    assert "out.png" in args.get("code", "")


@pytest.mark.asyncio
async def test_blender_client_run_python():
    client = MockMcpClient()
    blender = BlenderClient(client)
    result = await blender.run_python("import bpy; print('hello')")
    assert result.ok
    assert client.calls == [("execute_blender_code", {"code": "import bpy; print('hello')"})]


@pytest.mark.asyncio
async def test_freecad_client_parametric_flow():
    client = MockMcpClient()
    cad = FreeCADClient(client)
    await cad.create_sketch("S1")
    await cad.pad_sketch("S1", 10.0)
    await cad.set_spreadsheet_cell("B1", "98")
    await cad.recompute()
    step = await cad.export_step("enclosure.step")
    assert step.ok
    called_tools = [c[0] for c in client.calls]
    assert "create_sketch" in called_tools
    assert "set_spreadsheet_cell" in called_tools
    assert "export_step" in called_tools


@pytest.mark.asyncio
async def test_kicad_client_pcb_flow():
    client = MockMcpClient()
    eda = KiCADClient(client)
    await eda.create_schematic("main")
    await eda.place_components([{"ref": "U1"}])
    await eda.autoroute()
    drc = await eda.run_drc()
    await eda.export_gerber()
    assert drc.ok
    called_tools = [c[0] for c in client.calls]
    assert "create_schematic" in called_tools
    assert "autoroute" in called_tools
    assert "export_gerber" in called_tools


@pytest.mark.asyncio
async def test_custom_responder():
    client = MockMcpClient(responder=lambda name, args: {"echo": name})
    result = await client.call_tool("anything", {})
    assert result.content == {"echo": "anything"}
