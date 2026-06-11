"""MCP client adapters for CAD/EDA tools (Blender / FreeCAD / KiCAD)."""

from src.mcp.blender_tcp import BlenderTcpClient
from src.mcp.client import McpClient, McpServerSpec, McpToolResult, client_for_spec
from src.mcp.mock_transport import MockMcpClient

__all__ = [
    "BlenderTcpClient",
    "McpClient",
    "McpServerSpec",
    "McpToolResult",
    "MockMcpClient",
    "client_for_spec",
]
