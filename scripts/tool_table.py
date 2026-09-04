"""Print a markdown table of every registered tool, grouped by tool module.

Used to fill the `<!-- TOOL_TABLE_START -->` / `<!-- TOOL_TABLE_END -->` block
in README.md:

    uv run python scripts/tool_table.py

Exits non-zero, printing nothing to stdout, when a registered tool cannot be
traced back to the module that implements it.
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
import sys
from collections.abc import Iterable

import omni_mcp.tools
from omni_mcp.server import mcp


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


def _one_line(description: str | None) -> str:
    """First non-empty line of the tool docstring, escaped for a table cell."""
    for line in (description or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.replace("|", "\\|")
    return ""


def module_index() -> dict[str, str]:
    """Map tool name -> tool module, using the house rule that a tool's
    registered name matches the function that implements it."""
    index: dict[str, str] = {}
    for _, module_name, _ in pkgutil.iter_modules(omni_mcp.tools.__path__):
        if module_name.startswith("_"):
            continue
        module = importlib.import_module(f"omni_mcp.tools.{module_name}")
        for attr_name, attr in vars(module).items():
            if attr_name.startswith("omni_") and callable(attr) and getattr(attr, "__module__", "") == module.__name__:
                index[attr_name] = module_name
    return index


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


def main() -> int:
    try:
        grouped = asyncio.run(collect())
    except ToolAttributionError as exc:
        # Non-zero and nothing on stdout: a broken table must never be pasted
        # into the README by a pipeline that ignores stderr.
        sys.stderr.write(f"tool_table.py: error: {exc}\n")
        return 1
    sys.stdout.write(render(grouped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
