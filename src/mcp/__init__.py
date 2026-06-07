"""MCP client adapters for CAD/EDA tools (Blender / FreeCAD / KiCAD)."""

from src.mcp.client import McpClient, McpToolResult
from src.mcp.mock_transport import MockMcpClient

__all__ = ["McpClient", "McpToolResult", "MockMcpClient"]
