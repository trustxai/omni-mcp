"""Tool registration for omni_mcp.

ALL lane modules are listed here from day one (even while some are still empty
stubs) so that parallel worktrees implementing individual modules never need to
touch this file — eliminating merge conflicts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_all(mcp: FastMCP) -> None:
    """Import every tool module; @mcp.tool decorators self-register on import."""
    from omni_mcp.tools import (  # noqa: F401
        ai,
        ai_governance,
        ai_routines_evals,
        connections,
        content,
        dashboards,
        dbt,
        document_access,
        documents,
        documents_v2,
        folders,
        health,
        identity,
        model_git,
        models,
        queries,
        schedules,
        uploads,
        user_groups,
        users,
    )
