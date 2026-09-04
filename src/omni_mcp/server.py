"""FastMCP server definition and entry points for omni_mcp."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from omni_mcp.config import get_settings

mcp = FastMCP("omni_mcp")


def main_stdio() -> None:
    """Entry point for local / Docker stdio transport."""
    mcp.run()


def _configured_tool_modules() -> tuple[str, ...]:
    """The `OMNI_TOOL_MODULES` allowlist; empty means "register everything".

    A bad *module name* is fatal on purpose — the alternative is a server that
    silently exposes a tool set nobody asked for. Any **other** malformed
    `OMNI_*` variable is not fatal here: the server still comes up with every
    tool, because `omni_get_api_info` is the tool that explains exactly that
    misconfiguration and it has to exist to be callable.
    """
    try:
        return get_settings().tool_modules
    except ValidationError as exc:
        if any(error.get("loc") == ("omni_tool_modules",) for error in exc.errors()):
            raise
        return ()


# Registration runs LAST, once this module is fully defined: tool modules do
# `from omni_mcp.server import mcp`, so any name declared below this import
# would be missing from the partially-initialised module during registration.
from omni_mcp.tools import register_all  # noqa: E402

register_all(_configured_tool_modules())


if __name__ == "__main__":
    main_stdio()
