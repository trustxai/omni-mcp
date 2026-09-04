"""Generate the tool documentation from the live registry.

Two outputs, one source of truth:

    uv run python scripts/tool_table.py               # docs/TOOLS.md body
    uv run python scripts/tool_table.py --catalogue   # the README module catalogue
    uv run python scripts/tool_table.py --write       # write that catalogue into README.md

The catalogue fills the `<!-- TOOL_MODULES_START -->` / `<!-- TOOL_MODULES_END -->`
block in README.md; the full per-tool table fills docs/TOOLS.md.

Both cover **every module in the package**, never just the ones this machine
happens to have registered: `register_all()` is called with no filter before
anything is collected. A reader who narrowed their own server with
`OMNI_TOOL_MODULES` must still be able to see what else they could turn on.

Exits non-zero, printing nothing to stdout, when a registered tool cannot be
traced back to the module that implements it, or when a module is missing its
`MODULE_SUMMARY`.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from omni_mcp.server import mcp
from omni_mcp.tools import available_tool_modules, register_all

README = Path(__file__).resolve().parents[1] / "README.md"
CATALOGUE_START = "<!-- TOOL_MODULES_START -->"
CATALOGUE_END = "<!-- TOOL_MODULES_END -->"


class ToolAttributionError(RuntimeError):
    """Raised when a registered tool cannot be traced back to a tool module.

    Emitting an `unknown` group instead would hide the real problem: the tool's
    `name=` and the function implementing it have drifted apart, which also
    breaks anything else keyed on that house rule.
    """

    def __init__(self, tool_names: Iterable[str]) -> None:
        self.tool_names: list[str] = sorted(tool_names)
        super().__init__(
            f"cannot attribute {len(self.tool_names)} registered tool(s) to a module: "
            + ", ".join(self.tool_names)
            + ". Every tool's registered `name=` must match the function that implements it "
            "(see CONTRIBUTING.md) — rename the function or the tool so they agree."
        )


class ModuleSummaryError(RuntimeError):
    """Raised when a tool module does not declare a `MODULE_SUMMARY`.

    The catalogue's "Covers" column is generated from that constant, so a
    missing one would silently produce a blank cell in the README.
    """

    def __init__(self, module_names: Iterable[str]) -> None:
        self.module_names: list[str] = sorted(module_names)
        super().__init__(
            f"{len(self.module_names)} tool module(s) declare no `MODULE_SUMMARY`: "
            + ", ".join(self.module_names)
            + '. Add `MODULE_SUMMARY = "…"` below the imports — one line on what the module covers '
            "(see CONTRIBUTING.md); it is what the README catalogue prints."
        )


def _one_line(description: str | None) -> str:
    """First non-empty line of the tool docstring, escaped for a table cell."""
    for line in (description or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.replace("|", "\\|")
    return ""


def _register_every_module() -> None:
    """Make the whole package visible, whatever `OMNI_TOOL_MODULES` says here.

    Registration is additive, so this only ever adds the modules a filter left
    out — importing the server first is harmless.
    """
    register_all()


def module_index() -> dict[str, str]:
    """Map tool name -> tool module, using the house rule that a tool's
    registered name matches the function that implements it."""
    index: dict[str, str] = {}
    for module_name in available_tool_modules():
        module = importlib.import_module(f"omni_mcp.tools.{module_name}")
        for attr_name, attr in vars(module).items():
            if attr_name.startswith("omni_") and callable(attr) and getattr(attr, "__module__", "") == module.__name__:
                index[attr_name] = module_name
    return index


def module_summaries() -> dict[str, str]:
    """Map tool module -> its one-line `MODULE_SUMMARY`.

    Raises `ModuleSummaryError` when a module of the package declares none.
    """
    summaries: dict[str, str] = {}
    missing: list[str] = []
    for module_name in available_tool_modules():
        module = importlib.import_module(f"omni_mcp.tools.{module_name}")
        summary = getattr(module, "MODULE_SUMMARY", "")
        if not isinstance(summary, str) or not summary.strip():
            missing.append(module_name)
            continue
        summaries[module_name] = summary.strip().replace("|", "\\|")
    if missing:
        raise ModuleSummaryError(missing)
    return summaries


def _read_only(tool: object) -> str:
    annotations = getattr(tool, "annotations", None)
    read_only = getattr(annotations, "readOnlyHint", None) if annotations is not None else None
    if read_only is None:
        return "?"
    return "read-only" if read_only else "writes"


async def collect(index: dict[str, str] | None = None) -> dict[str, list[tuple[str, str, str]]]:
    """Group `(name, description, read-only flag)` rows by tool module.

    Raises `ToolAttributionError` when any registered tool falls outside
    `index` (by default `module_index()`).
    """
    _register_every_module()
    index = module_index() if index is None else index
    grouped: dict[str, list[tuple[str, str, str]]] = {}
    unattributed: list[str] = []
    for tool in await mcp.list_tools():
        module = index.get(tool.name)
        if module is None:
            unattributed.append(tool.name)
            continue
        grouped.setdefault(module, []).append((tool.name, _one_line(tool.description), _read_only(tool)))
    if unattributed:
        raise ToolAttributionError(unattributed)
    for rows in grouped.values():
        rows.sort(key=lambda row: row[0])
    return dict(sorted(grouped.items()))


async def catalogue() -> list[tuple[str, int, str]]:
    """`(module, tool count, one-line summary)` for **every** module in the package.

    A module with no tools still gets a row: the catalogue is the menu of what
    `OMNI_TOOL_MODULES` accepts, and an entry missing from it would look like an
    invalid name.
    """
    grouped = await collect()
    summaries = module_summaries()
    return [(module, len(grouped.get(module, [])), summaries[module]) for module in available_tool_modules()]


def render(grouped: dict[str, list[tuple[str, str, str]]]) -> str:
    total = sum(len(rows) for rows in grouped.values())
    lines = [f"**{total}** tools across **{len(grouped)}** modules.", ""]
    for module, rows in grouped.items():
        lines.append(f"### `{module}` ({len(rows)})")
        lines.append("")
        lines.append("| Tool | Access | Description |")
        lines.append("| --- | --- | --- |")
        for name, description, access in rows:
            lines.append(f"| `{name}` | {access} | {description} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_catalogue(rows: list[tuple[str, int, str]]) -> str:
    total = sum(count for _, count, _ in rows)
    lines = [
        f"**{total}** tools across **{len(rows)}** modules, and every one of them is registered unless",
        "[`OMNI_TOOL_MODULES`](#selecting-tool-modules) says otherwise. The full generated reference —",
        "every tool, its access mode and its one-line description — is in [docs/TOOLS.md](docs/TOOLS.md).",
        "",
        "| Module | Tools | Covers |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| `{module}` | {count} | {summary} |" for module, count, summary in rows)
    return "\n".join(lines) + "\n"


def extract_block(text: str) -> str:
    """The current catalogue body in `text`, between the two markers.

    Raises `ValueError` when the markers are missing or out of order — a
    silently skipped replacement is how a generated block goes stale.
    """
    start = text.find(CATALOGUE_START)
    end = text.find(CATALOGUE_END)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"README.md must contain {CATALOGUE_START} … {CATALOGUE_END}, in that order")
    return text[start + len(CATALOGUE_START) : end].lstrip("\n")


def replace_block(text: str, body: str) -> str:
    """`text` with the catalogue block replaced by `body`."""
    start = text.index(CATALOGUE_START)
    end = text.index(CATALOGUE_END)
    return text[: start + len(CATALOGUE_START)] + "\n" + body + text[end:]


def main(argv: Sequence[str] | None = None) -> int:
    """`argv` defaults to no flags — never `sys.argv`, so an in-process caller
    (the tests) is not handed pytest's own arguments."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--catalogue",
        action="store_true",
        help="print the per-module catalogue (the README block) instead of the full tool table",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the catalogue into README.md between its markers, and report whether it changed",
    )
    args = parser.parse_args(list(argv) if argv is not None else [])
    try:
        if args.catalogue or args.write:
            body = render_catalogue(asyncio.run(catalogue()))
        else:
            body = render(asyncio.run(collect()))
    except (ToolAttributionError, ModuleSummaryError) as exc:
        # Non-zero and nothing on stdout: broken output must never be pasted
        # into the README by a pipeline that ignores stderr.
        sys.stderr.write(f"tool_table.py: error: {exc}\n")
        return 1
    if args.write:
        current = README.read_text(encoding="utf-8")
        updated = replace_block(current, body)
        if updated == current:
            sys.stderr.write("tool_table.py: README.md catalogue already up to date\n")
        else:
            README.write_text(updated, encoding="utf-8")
            sys.stderr.write("tool_table.py: README.md catalogue updated\n")
        return 0
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
