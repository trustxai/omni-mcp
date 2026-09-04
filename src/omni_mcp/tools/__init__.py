"""Tool registration for omni_mcp.

Modules are **discovered** from this package rather than hand-listed, so a new
lane module registers itself by existing — parallel worktrees never touch this
file, and the discovered set is the same one `OMNI_TOOL_MODULES` selects from
and `omni_get_api_info` reports.

Registration is process-global and irreversible: importing a tool module runs
its `@mcp.tool` decorators against the single server instance,
`omni_mcp.server.mcp`, which the modules import directly. There is no
unregister, so the allowlist has to be applied *before* the import — which is
what `register_all(modules=...)` does.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from collections.abc import Iterable, Sequence


class UnknownToolModuleError(ValueError):
    """Raised when the allowlist names a module this package does not contain.

    Skipping the entry would be worse than failing: the server would come up
    with a tool set nobody asked for, and a typo would surface much later as a
    tool that is mysteriously missing.
    """

    def __init__(self, unknown: Iterable[str], available: Iterable[str]) -> None:
        self.unknown: tuple[str, ...] = tuple(unknown)
        self.available: tuple[str, ...] = tuple(available)
        super().__init__(
            "OMNI_TOOL_MODULES names unknown tool module(s): "
            + ", ".join(f"'{name}'" for name in self.unknown)
            + ". Valid modules are: "
            + ", ".join(self.available)
            + f". Leave OMNI_TOOL_MODULES unset or empty to register all {len(self.available)}."
        )


def available_tool_modules() -> tuple[str, ...]:
    """Every tool module present in the package, registered or not.

    Discovered rather than hand-listed so it never drifts as modules land, and
    deliberately independent of any active filter: this is the menu, not the
    order.
    """
    return tuple(sorted(name for _, name, _ in pkgutil.iter_modules(__path__) if not name.startswith("_")))


def registered_tool_modules() -> tuple[str, ...]:
    """The tool modules this process has imported — what the server exposes.

    This, not `available_tool_modules()`, is what any "what is loaded here?"
    report must print. Derived from `sys.modules` rather than from bookkeeping
    inside `register_all`, because *importing* is what registers the tools: a
    module pulled in by any other route (a test, or one day a sibling module
    importing a shared helper) is registered whether or not the allowlist named
    it, and a report that missed it would be wrong in the same way the old
    package-inventory report was.
    """
    return tuple(name for name in available_tool_modules() if f"omni_mcp.tools.{name}" in sys.modules)


def parse_tool_modules(value: str) -> tuple[str, ...]:
    """Parse a comma-separated allowlist into validated module names.

    Entries are trimmed and empty ones dropped, so `"models, ,queries,"` is the
    same as `"models,queries"`; a repeat is not an error, it is deduplicated.
    The caller's order is preserved. An empty result means "no filter" — every
    module — which is what an unset or empty `OMNI_TOOL_MODULES` produces.

    Raises `UnknownToolModuleError` when an entry does not name a module of this
    package.
    """
    names: list[str] = []
    for entry in value.split(","):
        name = entry.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        # The overwhelmingly common case (no filter): nothing to validate, and
        # no reason to scan the package on every `Settings()`.
        return ()
    available = available_tool_modules()
    unknown = [name for name in names if name not in available]
    if unknown:
        raise UnknownToolModuleError(unknown, available)
    return tuple(names)


def register_all(modules: Sequence[str] | None = None) -> tuple[str, ...]:
    """Import the selected tool modules; `@mcp.tool` decorators self-register.

    `modules` is an allowlist of module names. `None` — or an empty sequence,
    which is exactly what an unset or empty `OMNI_TOOL_MODULES` yields —
    registers every module in the package, the historical behaviour and the
    default.

    Returns the modules this call selected. Calling it again is additive, never
    a reset: a second call cannot unregister what the first one imported, so
    `registered_tool_modules()` reports the union.
    """
    available = available_tool_modules()
    if modules:
        selected = tuple(modules)
        unknown = [name for name in selected if name not in available]
        if unknown:
            raise UnknownToolModuleError(unknown, available)
    else:
        selected = available
    for name in selected:
        importlib.import_module(f"omni_mcp.tools.{name}")
    return selected
