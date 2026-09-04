"""`OMNI_TOOL_MODULES` — the registration allowlist, proven in a real process.

Registration happens once per interpreter, on `import omni_mcp.server`, and it
is irreversible: importing a tool module runs its `@mcp.tool` decorators
against the single global server. So "only these three modules are registered"
cannot be observed in this test process — it already has all twenty. Every
end-to-end case here therefore starts a fresh interpreter and reads back what
that process ended up with.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from omni_mcp.server import mcp
from omni_mcp.tools import available_tool_modules

#: Printed by the child: what registration actually produced, as JSON on stdout.
REPORT = """
import asyncio, json, sys
from omni_mcp.server import mcp
from omni_mcp.tools import registered_tool_modules

json.dump(
    {
        "modules": list(registered_tool_modules()),
        "tools": sorted(tool.name for tool in asyncio.run(mcp.list_tools())),
    },
    sys.stdout,
)
"""

SELECTION = ("model_git", "models", "queries")


def _run(snippet: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run `snippet` in a fresh interpreter with a controlled `OMNI_*` environment.

    `cwd` is a temp directory so the child cannot pick up the repository's
    `.env`, the same isolation `tests/conftest.py` gives in-process tests.
    """
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OMNI_")}
    environment.update(env)
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=environment,
    )


def _start_server(cwd: Path, **env: str) -> dict[str, list[str]]:
    result = _run(REPORT, cwd, **env)
    assert result.returncode == 0, result.stderr
    payload: dict[str, list[str]] = json.loads(result.stdout)
    return payload


def _tool_names(module_name: str) -> set[str]:
    """The tools implemented by one module, by the house name-matches-function rule."""
    module = importlib.import_module(f"omni_mcp.tools.{module_name}")
    return {
        name
        for name, value in vars(module).items()
        if name.startswith("omni_") and callable(value) and getattr(value, "__module__", "") == module.__name__
    }


async def _registered_tool_names() -> list[str]:
    return sorted(tool.name for tool in await mcp.list_tools())


async def test_unset_registers_every_module_and_every_tool(tmp_path: Path) -> None:
    """Backwards compatibility: no variable means exactly today's server."""
    payload = _start_server(tmp_path)

    assert tuple(payload["modules"]) == available_tool_modules()
    assert payload["tools"] == await _registered_tool_names()


async def test_empty_value_registers_everything_too(tmp_path: Path) -> None:
    payload = _start_server(tmp_path, OMNI_TOOL_MODULES="")

    assert tuple(payload["modules"]) == available_tool_modules()
    assert payload["tools"] == await _registered_tool_names()


async def test_whitespace_only_value_registers_everything_too(tmp_path: Path) -> None:
    payload = _start_server(tmp_path, OMNI_TOOL_MODULES="   ")

    assert tuple(payload["modules"]) == available_tool_modules()
    assert payload["tools"] == await _registered_tool_names()


def test_a_filter_registers_exactly_the_named_modules(tmp_path: Path) -> None:
    payload = _start_server(tmp_path, OMNI_TOOL_MODULES="models,model_git,queries")

    expected: set[str] = set()
    for module in SELECTION:
        expected |= _tool_names(module)

    assert sorted(payload["modules"]) == sorted(SELECTION)
    assert set(payload["tools"]) == expected
    # Nothing from an unselected module leaked in — including `health`, which
    # every other code path imports.
    assert "omni_health_check" not in payload["tools"]
    assert "omni_list_users" not in payload["tools"]


def test_a_filter_tolerates_whitespace_and_empty_entries(tmp_path: Path) -> None:
    payload = _start_server(tmp_path, OMNI_TOOL_MODULES=" models , ,model_git,  queries ,")

    assert sorted(payload["modules"]) == sorted(SELECTION)


def test_a_single_module_is_a_valid_selection(tmp_path: Path) -> None:
    payload = _start_server(tmp_path, OMNI_TOOL_MODULES="health")

    assert payload["modules"] == ["health"]
    assert payload["tools"] == ["omni_get_api_info", "omni_health_check"]


def test_an_unknown_module_fails_at_startup(tmp_path: Path) -> None:
    """Not a silent skip: the server refuses to start and says what to fix."""
    result = _run("import omni_mcp.server", tmp_path, OMNI_TOOL_MODULES="models,modles,queries")

    assert result.returncode != 0
    assert result.stdout == "", "stdout is the MCP channel and must stay clean"
    # Names the offending entry…
    assert "modles" in result.stderr
    # …the variable to fix…
    assert "OMNI_TOOL_MODULES" in result.stderr
    # …and every valid module name.
    for module in available_tool_modules():
        assert module in result.stderr, f"the error must list `{module}` as a valid module"
    # The entries that were fine are not blamed.
    assert "'models'" not in result.stderr


def test_an_unrelated_bad_setting_still_starts_with_every_tool(tmp_path: Path) -> None:
    """A malformed `OMNI_BASE_URL` must not become a startup crash.

    `omni_get_api_info` exists to explain exactly that misconfiguration, so the
    server has to come up for it to be callable.
    """
    payload = _start_server(tmp_path, OMNI_BASE_URL="acme.omniapp.co")

    assert tuple(payload["modules"]) == available_tool_modules()


API_INFO = """
import asyncio, sys
from omni_mcp.tools.health import ApiInfoInput, omni_get_api_info

sys.stderr.write(asyncio.run(omni_get_api_info(ApiInfoInput())))
"""


def test_api_info_reports_the_registered_set_not_the_package(tmp_path: Path) -> None:
    """The regression guard: with a filter active, health must not claim 20.

    `omni_get_api_info` is the one tool whose job is saying what is loaded, so
    reporting every module in the package would make it lie precisely when
    someone is debugging a filter.
    """
    result = _run(API_INFO, tmp_path, OMNI_TOOL_MODULES="health,queries")
    report = result.stderr

    assert result.returncode == 0, report
    assert f"## Tool modules (2 of {len(available_tool_modules())} registered)" in report
    assert "`health`" in report and "`queries`" in report
    assert "OMNI_TOOL_MODULES" in report
    # The modules that were filtered out are named as *not* registered, never
    # presented as available tools.
    assert "Not registered" in report
    assert report.index("Not registered") < report.index("`models`")


def test_a_module_imported_past_the_allowlist_is_reported_as_registered(tmp_path: Path) -> None:
    """Importing *is* registering, and the report follows reality.

    Nothing in the package does this today, but a module that reached
    `sys.modules` by another route has run its `@mcp.tool` decorators, so its
    tools are live. Reporting the allowlist instead would be the same class of
    lie as reporting the package inventory.
    """
    payload = json.loads(_run("import omni_mcp.tools.users\n" + REPORT, tmp_path, OMNI_TOOL_MODULES="health").stdout)

    assert payload["modules"] == ["health", "users"]
    assert "omni_list_users" in payload["tools"]


@pytest.mark.parametrize("value", ["", "health"])
def test_registering_twice_is_additive_not_a_reset(value: str) -> None:
    """`register_all` may be called again (the docs generator does exactly that)."""
    from omni_mcp.tools import register_all, registered_tool_modules

    register_all([value] if value else None)

    assert set(registered_tool_modules()) == set(available_tool_modules())


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tool_table.py"


async def test_the_generated_catalogue_ignores_an_active_filter(tmp_path: Path) -> None:
    """The docs describe the package, never the machine that generated them.

    A reader who has already narrowed their own server has to keep seeing what
    else they could switch on — a catalogue that shrank with the filter would
    remove exactly the information the filter creates a need for.
    """
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OMNI_")}
    environment["OMNI_TOOL_MODULES"] = "health"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--catalogue"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    modules = available_tool_modules()
    assert f"**{len(await mcp.list_tools())}** tools across **{len(modules)}** modules" in result.stdout
    for module in modules:
        assert f"| `{module}` |" in result.stdout, f"the catalogue dropped `{module}` under a filter"
