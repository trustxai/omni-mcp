"""Server-wide invariants every lane module must keep satisfying."""

from __future__ import annotations

import inspect
import subprocess
import sys

import mcp.types as mcp_types

import omni_mcp.server
from omni_mcp.server import mcp
from omni_mcp.tools.health import TOOL_MODULES


async def _tools() -> list[mcp_types.Tool]:
    return list(await mcp.list_tools())


async def test_server_imports_and_registers_tools() -> None:
    assert mcp.name == "omni_mcp"
    assert len(await _tools()) >= 2


async def test_every_tool_name_is_prefixed() -> None:
    for tool in await _tools():
        assert tool.name.startswith("omni_"), f"{tool.name} must be namespaced with the omni_ prefix"


async def test_tool_names_are_unique() -> None:
    names = [tool.name for tool in await _tools()]

    assert len(names) == len(set(names))


async def test_every_tool_has_annotations_with_read_only_hint() -> None:
    for tool in await _tools():
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name} is missing ToolAnnotations"
        assert annotations.readOnlyHint is not None, f"{tool.name} must set readOnlyHint"
        assert annotations.title, f"{tool.name} must set a human-readable title"


async def test_every_tool_has_a_docstring_description() -> None:
    for tool in await _tools():
        assert tool.description, f"{tool.name} must have a docstring"


async def test_health_tools_are_registered() -> None:
    names = {tool.name for tool in await _tools()}

    assert {"omni_health_check", "omni_get_api_info"} <= names


def test_server_module_is_fully_initialised_after_tool_import() -> None:
    """Guards the import order: registration must run after `main_stdio`.

    A tool module importing anything defined below `register_all()` would blow
    up with a partially-initialised module, taking every tool down with it.
    """
    import omni_mcp.tools.health  # noqa: F401
    from omni_mcp.server import main_stdio
    from omni_mcp.server import mcp as server_mcp

    assert callable(main_stdio)
    assert server_mcp is mcp


def test_registration_runs_after_the_module_is_fully_defined() -> None:
    """Encodes the import-order rule: every name must exist before registration.

    If `register_all()` moves above a definition, a tool module importing that
    name gets a partially-initialised `omni_mcp.server` and every tool fails.
    """
    source = inspect.getsource(omni_mcp.server)

    assert source.index("def main_stdio") < source.index("register_all()")


def test_tool_modules_match_what_register_all_imports() -> None:
    """`TOOL_MODULES` is discovered; `register_all` must import all of it."""
    for module in TOOL_MODULES:
        assert f"omni_mcp.tools.{module}" in sys.modules, f"register_all() does not import tools/{module}.py"
    assert "health" in TOOL_MODULES
    assert not any(module.startswith("_") for module in TOOL_MODULES)


def test_importing_the_server_writes_nothing_to_stdout() -> None:
    """stdout is the MCP channel: a stray print would corrupt the protocol."""
    result = subprocess.run(
        [sys.executable, "-c", "import omni_mcp.server"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == "", f"something wrote to stdout on import: {result.stdout!r}"
