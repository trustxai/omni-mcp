"""FastMCP server definition and entry points for omni_mcp."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("omni_mcp")


def main_stdio() -> None:
    """Entry point for local / Docker stdio transport."""
    mcp.run()


# Registration runs LAST, once this module is fully defined: tool modules do
# `from omni_mcp.server import mcp`, so any name declared below this import
# would be missing from the partially-initialised module during registration.
from omni_mcp.tools import register_all  # noqa: E402

register_all()


if __name__ == "__main__":
    main_stdio()
