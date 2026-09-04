"""Print a markdown table of every registered tool, grouped by tool module.

Used to fill the `<!-- TOOL_TABLE_START -->` / `<!-- TOOL_TABLE_END -->` block
in README.md:

    uv run python scripts/tool_table.py
"""

from __future__ import annotations

import sys
from typing import Any

from omni_mcp.server import mcp


def _one_line(description: str | None) -> str:
    """First non-empty line of the tool docstring, escaped for a table cell."""
    for line in (description or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.replace("|", "\\|")
    return ""


def _module_of(tool: Any) -> str:
    """Short tool-module name (`health`, `queries`, …)."""
    fn = getattr(tool, "fn", None)
    module = getattr(fn, "__module__", "") or ""
    return module.rsplit(".", 1)[-1] or "unknown"


def _read_only(tool: Any) -> str:
    annotations = getattr(tool, "annotations", None)
    read_only = getattr(annotations, "readOnlyHint", None) if annotations else None
    if read_only is None:
        return "?"
    return "read-only" if read_only else "writes"


def collect() -> dict[str, list[tuple[str, str, str]]]:
    """Group `(name, description, read-only flag)` rows by tool module."""
    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for tool in mcp._tool_manager.list_tools():
        grouped.setdefault(_module_of(tool), []).append((tool.name, _one_line(tool.description), _read_only(tool)))
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
    sys.stdout.write(render(collect()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
