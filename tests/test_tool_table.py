"""Unit tests for `scripts/tool_table.py`, the generator behind the README table."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from omni_mcp.server import mcp

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tool_table.py"


def _load_script() -> ModuleType:
    """Import the script by path — `scripts/` is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("tool_table", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool_table = _load_script()


async def test_every_registered_tool_is_attributed_to_a_module() -> None:
    grouped = await tool_table.collect()

    assert grouped, "no tools registered"
    assert "unknown" not in grouped
    assert sum(len(rows) for rows in grouped.values()) == len(await mcp.list_tools())


async def test_a_tool_that_does_not_match_its_function_is_reported() -> None:
    # A synthetic mismatch: the tool is registered under a name no function owns.
    index = tool_table.module_index()
    index.pop("omni_health_check")

    with pytest.raises(tool_table.ToolAttributionError) as excinfo:
        await tool_table.collect(index=index)

    assert excinfo.value.tool_names == ["omni_health_check"]
    assert "omni_health_check" in str(excinfo.value)


def test_main_prints_the_table_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = tool_table.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "tools across" in captured.out
    assert "| Tool | Access | Description |" in captured.out
    assert "### `health`" in captured.out
    assert "unknown" not in captured.out


def test_main_exits_non_zero_and_prints_nothing_on_a_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(tool_table, "module_index", lambda: {})

    exit_code = tool_table.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "cannot attribute" in captured.err
    assert "omni_health_check" in captured.err
