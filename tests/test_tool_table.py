"""Unit tests for `scripts/tool_table.py`, the generator behind the README catalogue."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from omni_mcp.server import mcp
from omni_mcp.tools import available_tool_modules

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
    # No unattributed-tools section — a tool description may legitimately say
    # "unknown", so this asserts on the heading the fallback module would print.
    assert "### `unknown`" not in captured.out


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


async def test_the_catalogue_covers_every_module_in_the_package() -> None:
    rows = await tool_table.catalogue()

    assert [module for module, _, _ in rows] == list(available_tool_modules())
    assert all(count > 0 for _, count, _ in rows), "a module with no tools would be a registration bug"
    assert all(summary for _, _, summary in rows)
    assert sum(count for _, count, _ in rows) == len(await mcp.list_tools())


async def test_a_module_without_a_summary_is_reported() -> None:
    summaries = tool_table.module_summaries()

    assert set(summaries) == set(available_tool_modules())

    import omni_mcp.tools.health as health

    original = health.MODULE_SUMMARY
    try:
        health.MODULE_SUMMARY = "   "
        with pytest.raises(tool_table.ModuleSummaryError) as excinfo:
            tool_table.module_summaries()
    finally:
        health.MODULE_SUMMARY = original

    assert excinfo.value.module_names == ["health"]
    assert "MODULE_SUMMARY" in str(excinfo.value)


async def test_readme_catalogue_block_is_in_sync() -> None:
    """The README block is generated; a hand edit (or a new tool) must fail here.

    This is the only check that runs in CI — the gate's `scripts/tool_table.py`
    invocation is a local step — so without it the catalogue would silently rot.
    """
    expected = tool_table.render_catalogue(await tool_table.catalogue())

    current = tool_table.extract_block(tool_table.README.read_text(encoding="utf-8"))

    assert current == expected, "stale README catalogue — run `uv run python scripts/tool_table.py --write`"


def test_replacing_the_block_needs_both_markers() -> None:
    with pytest.raises(ValueError, match="TOOL_MODULES_START"):
        tool_table.extract_block("# README\n\nno markers here\n")


def test_write_mode_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(f"# x\n\n{tool_table.CATALOGUE_START}\nstale\n{tool_table.CATALOGUE_END}\n\ntail\n")
    monkeypatch.setattr(tool_table, "README", readme)

    assert tool_table.main(["--write"]) == 0
    first = readme.read_text()
    assert tool_table.main(["--write"]) == 0

    assert readme.read_text() == first
    assert "stale" not in first
    assert first.startswith("# x\n")
    assert first.endswith("tail\n")
    assert "| `health` | 2 |" in first


def test_catalogue_mode_prints_the_block(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = tool_table.main(["--catalogue"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "| Module | Tools | Covers |" in captured.out
    assert "| `health` | 2 | Local diagnostics" in captured.out


def test_write_mode_reports_a_missing_marker_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--write` gets the marker message that was written for it.

    `extract_block` raises a sentence naming both markers, but `--write` went
    straight to `replace_block`, whose `str.index` raised a bare
    `ValueError: substring not found` — so the useful message was only ever
    seen by its own unit test.
    """
    readme = tmp_path / "README.md"
    readme.write_text("# README\n\nsomeone deleted the markers\n")
    monkeypatch.setattr(tool_table, "README", readme)

    exit_code = tool_table.main(["--write"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "README.md must contain" in captured.err
    assert tool_table.CATALOGUE_START in captured.err
    assert readme.read_text() == "# README\n\nsomeone deleted the markers\n", "README.md must be left untouched"
