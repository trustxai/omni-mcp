"""Server-wide invariants every lane module must keep satisfying."""

from __future__ import annotations

from typing import Any

from omni_mcp.server import mcp


def _tools() -> list[Any]:
    return list(mcp._tool_manager.list_tools())


def test_server_imports_and_registers_tools() -> None:
    assert mcp.name == "omni_mcp"
    assert len(_tools()) >= 2


def test_every_tool_name_is_prefixed() -> None:
    for tool in _tools():
        assert tool.name.startswith("omni_"), f"{tool.name} must be namespaced with the omni_ prefix"


def test_tool_names_are_unique() -> None:
    names = [tool.name for tool in _tools()]

    assert len(names) == len(set(names))


def test_every_tool_has_annotations_with_read_only_hint() -> None:
    for tool in _tools():
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name} is missing ToolAnnotations"
        assert annotations.readOnlyHint is not None, f"{tool.name} must set readOnlyHint"
        assert annotations.title, f"{tool.name} must set a human-readable title"


def test_every_tool_has_a_docstring_description() -> None:
    for tool in _tools():
        assert tool.description, f"{tool.name} must have a docstring"


def test_health_tools_are_registered() -> None:
    names = {tool.name for tool in _tools()}

    assert {"omni_health_check", "omni_get_api_info"} <= names
