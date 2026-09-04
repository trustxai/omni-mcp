"""Allow `python -m omni_mcp` to start the stdio server."""

from __future__ import annotations

from omni_mcp.server import main_stdio

if __name__ == "__main__":
    main_stdio()
