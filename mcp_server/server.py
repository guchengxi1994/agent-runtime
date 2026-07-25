from __future__ import annotations

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("agent-runtime-test-tools")


@mcp.tool()
def add(a: float, b: float) -> dict[str, float]:
    """Return the exact sum of two numbers."""
    return {"a": a, "b": b, "sum": a + b}


@mcp.tool()
def echo(text: str, uppercase: bool = False) -> dict[str, str]:
    """Return text unchanged, or uppercased when requested."""
    return {"text": text.upper() if uppercase else text}


@mcp.tool()
def reverse_text(text: str) -> dict[str, str]:
    """Reverse a string. Useful for verifying a non-numeric MCP call."""
    return {"text": text[::-1]}


@mcp.tool()
def server_info() -> dict[str, object]:
    """Return static metadata for this local test MCP server."""
    return {"name": "agent-runtime-test-tools", "version": "1.0", "tools": ["add", "echo", "reverse_text", "server_info"]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
