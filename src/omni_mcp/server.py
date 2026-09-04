"""FastMCP server definition and entry points for omni_mcp."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from omni_mcp.config import ToolModuleSettings

mcp = FastMCP("omni_mcp")


def main_stdio() -> None:
    """Entry point for local / Docker stdio transport."""
    mcp.run()


def _configured_tool_modules() -> tuple[str, ...]:
    """The `OMNI_TOOL_MODULES` allowlist; empty means "register everything".

    Read through `ToolModuleSettings`, which knows about *only* that variable,
    for two reasons that pull in opposite directions:

    - A bad **module name** must be fatal. The alternative is a server that
      silently exposes a tool set nobody asked for.
    - A malformed **other** `OMNI_*` must not be. The server still has to come
      up — `omni_get_api_info` is the tool that explains that misconfiguration,
      and it has to exist to be callable — *and* it must still honour the
      allowlist. Loading the full `Settings` here did neither: it raised, the
      fallback registered everything, and the report then claimed all 20
      modules were loaded because they were.
    """
    return ToolModuleSettings().tool_modules


# Registration runs LAST, once this module is fully defined: tool modules do
# `from omni_mcp.server import mcp`, so any name declared below this import
# would be missing from the partially-initialised module during registration.
from omni_mcp.tools import register_all  # noqa: E402

register_all(_configured_tool_modules())


if __name__ == "__main__":
    main_stdio()
