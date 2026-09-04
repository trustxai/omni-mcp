"""FastMCP server definition and entry points for omni_mcp."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("omni_mcp")

# Register all tools (side-effect imports via decorators)
from omni_mcp.tools import register_all  # noqa: E402

register_all(mcp)


def main_stdio() -> None:
    """Entry point for local / Docker stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main_stdio()
