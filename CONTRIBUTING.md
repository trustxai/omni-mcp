# Contributing

Thanks for helping out. This document is the **contract every tool module follows** — read it once
before adding tools, and the review will be short.

## Getting set up

```bash
uv sync --group dev
uv run pre-commit install
cp .env.example .env   # optional; only needed for live tests
```

Python 3.13, [uv](https://docs.astral.sh/uv/), hatchling, `src/` layout.

## Project layout

```
src/omni_mcp/
  config.py          Settings (OMNI_* env vars) + get_settings()
  client.py          OmniClient + get_client(): auth, retries, URL joining
  errors.py          handle_api_error(exc) -> str
  formatters.py      ResponseFormat, to_json, truncate_result, pagination, Arrow decoding
  server.py          the FastMCP instance; imports register_all at import time
  tools/__init__.py  register_all(): imports every tool module (no arguments)
  tools/<area>.py    one module per API area — where your tools go
tests/test_<area>.py one test file per tool module
scripts/tool_table.py generates the README tool table
```

`tools/__init__.py` already lists **every** module, so two people can implement different areas at
the same time without touching a shared file. `register_all()` takes no argument: every module
decorates against the single server instance it imports from `omni_mcp.server`. Do not add tools to an area that is not yours, and do
not edit another area's module.

## The tool contract

1. **Naming.** Tool names are `omni_<verb>_<noun>`: `omni_list_users`, `omni_get_model`,
   `omni_create_folder`, `omni_delete_schedule`. Lower snake case, always the `omni_` prefix (it is
   enforced by `tests/test_server.py`), and the same verb vocabulary throughout:
   `list`, `get`, `search`, `create`, `update`, `delete`, `run`, `validate`.
2. **One pydantic input model per tool**, named `<ToolName>Input`, with
   `model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")` and a `Field(description=…)`
   on every field. Snake-case field names map to the API's camelCase query params in the tool body.
3. **Signature.** `async def omni_<verb>_<noun>(params: <ToolName>Input) -> str:` — always a single
   `params` argument, always a `str` return.
4. **Annotations.** Every tool passes `ToolAnnotations` with `title`, `readOnlyHint`,
   `destructiveHint`, `idempotentHint` and `openWorldHint`. Reads are
   `readOnlyHint=True, destructiveHint=False, idempotentHint=True`; deletes are
   `readOnlyHint=False, destructiveHint=True`; creates/updates are `readOnlyHint=False,
   destructiveHint=False`, with `idempotentHint=True` only for genuine PUT-style upserts.
   `openWorldHint=True` for anything that calls the API.
5. **Docstring sections**, in this order: a one-line summary (it becomes the tool's description in
   the README table), then `When to Use`, `When NOT to Use`, `Returns`, `Examples`, `Error Handling`.
   Write them for a model deciding which tool to call, not for a human reading source.
6. **Never raise.** Wrap the whole body in `try: … except Exception as exc: return handle_api_error(exc)`.
7. **`response_format`** on read tools: a `ResponseFormat` field defaulting to
   `ResponseFormat.MARKDOWN`, with `json` returning the structured payload via `to_json`.
8. **Mutating tools return a short confirmation string** — what changed and its id, e.g.
   `Created folder **Marketing** (id `abc123`).` — not a dump of the response body.
9. **List tools expose `page_size` and `cursor`** and render through `cursor_paginated_response`,
   which prints the next cursor so the caller can page. Never loop over pages inside a tool.
10. **Stay under the 1 MB tool-result limit**: every result goes through `truncate_result`, which
    budgets in UTF-8 **bytes** (`cursor_paginated_response` and `format_arrow_result` already do it
    for you).
11. **Never print to stdout** — stdout is the MCP channel. Use `logging` to stderr if you must.
12. **Never echo secrets.** Report whether a key is configured, never the key itself.

### Template

```python
"""Tools for the <area> endpoints."""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, cursor_paginated_response, truncate_result
from omni_mcp.server import mcp


class ListThingsInput(BaseModel):
    """Input for `omni_list_things`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    page_size: int = Field(default=20, ge=1, le=100, description="Records per page (1-100).")
    cursor: str | None = Field(default=None, description="Cursor from a previous page.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for raw records."
    )


@mcp.tool(
    name="omni_list_things",
    annotations=ToolAnnotations(
        title="List Things",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_things(params: ListThingsInput) -> str:
    """List things.

    When to Use:
    - …

    When NOT to Use:
    - …

    Returns:
    …

    Examples:
    - `{"params": {"page_size": 50}}`

    Error Handling:
    …
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size}
        if params.cursor:
            query["cursor"] = params.cursor
        payload = await get_client().request_json("GET", "/v1/things", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=lambda item: f"- **{item.get('name', 'unnamed')}** (`{item.get('id')}`)",
            title="Things",
        )
    except Exception as exc:
        return handle_api_error(exc)
```

`src/omni_mcp/tools/health.py` is the in-repo worked example — copy its shape.

## Calling the API

`OmniClient.request()` / `request_json()` take a path **relative to the instance's API root**:
`/v1/users`, `/v1/query/run`, `/scim/v2/Users`. Never build a full URL, and never create your own
`httpx.AsyncClient`. The client adds the bearer token, honours `Retry-After` on `429`, backs off on
`502/503/504`, and raises `httpx.HTTPStatusError` for `handle_api_error` to translate.

Query results come back as base64-encoded Apache Arrow: decode with
`formatters.decode_arrow_base64()` or render directly with `formatters.format_arrow_result()`.
A `408` carries `remaining_job_ids` — `handle_api_error` already tells the caller to poll them.

## Tests

One test file per tool module: `tests/test_<area>.py`. Unit tests never touch the network — they
monkeypatch `get_client` in the module under test with a fake that records calls:

```python
class _FakeClient:
    """Records `(method, path, kwargs)` calls; returns a canned payload."""

    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {}
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return self._payload


async def test_list_things(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"records": [{"id": "1", "name": "one"}], "pageInfo": {}})
    monkeypatch.setattr("omni_mcp.tools.things.get_client", lambda: fake)

    result = await omni_list_things(ListThingsInput())

    assert "one" in result
    assert fake.calls == [("GET", "/v1/things", {"params": {"pageSize": 20}})]
```

Cover, per tool: the happy path (markdown), the JSON format, the exact request the tool issues
(method, path, params/body), and at least one error path built from an `httpx.HTTPStatusError`.
`tests/conftest.py` strips ambient `OMNI_*` variables and chdirs away from the repo, so unit tests
never see your real credentials.

**Live tests** carry `@pytest.mark.live` and run against a real instance; they are skipped unless
`OMNI_API_KEY` is set (in the environment or `.env`). Mutating live tests also carry
`@pytest.mark.destructive` and additionally require `OMNI_TEST_ALLOW_DESTRUCTIVE=1`. Run them with
`uv run pytest -m live`.

## The gate

Before pushing, everything below must pass:

```bash
uv run pytest -m "not live"
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run python -c "from omni_mcp.server import mcp; print('import ok')"
uv run python scripts/tool_table.py
```

CI runs the same checks on every pull request — lint, format, typecheck, the non-live tests and
the import smoke check. `mypy` is strict: annotate everything.

## Commits and releases

Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`,
`docs:`, `test:`, `chore:`, `ci:`, `refactor:`. The type drives the version bump, so use `feat:` for
new tools and `fix:` for behaviour fixes.

[release-please](https://github.com/googleapis/release-please) watches `main` and keeps a release
pull request open with the generated `CHANGELOG.md` and the bumped version in `pyproject.toml`.
Merging it creates the GitHub release, and the `publish` job **inside the same workflow** builds and
uploads to PyPI with trusted publishing (no tokens). That job lives there deliberately: GitHub does
not trigger `on: release` workflows from a release created with `GITHUB_TOKEN`.
`.github/workflows/publish.yml` is a manual `workflow_dispatch` fallback that takes a tag. Do not
bump the version or edit `CHANGELOG.md` by hand.

### PyPI trusted publishers (two of them)

PyPI matches a trusted publisher on the **workflow filename plus the environment**, not on the
repository alone — so both workflows that can upload need their own entry. On pypi.org, under the
`omni-mcp` project → *Manage* → *Publishing*, register these two GitHub publishers:

| Owner | Repository | Workflow file | Environment |
| --- | --- | --- | --- |
| `trustxai` | `omni-mcp` | `release-please.yml` | `pypi` |
| `trustxai` | `omni-mcp` | `publish.yml` | `pypi` |

With only the first entry the normal release path works and the manual fallback fails at
`uv publish` with an OIDC/"not a trusted publisher" error — at exactly the moment it is needed.
Before the first release, add both as *pending* publishers (the project does not exist on PyPI yet).

## README tool table

The `Tools` section of the README is generated:

```bash
uv run python scripts/tool_table.py
```

Paste the output between the `<!-- TOOL_TABLE_START -->` and `<!-- TOOL_TABLE_END -->` markers. The
script exits non-zero (printing nothing) when a tool's registered `name=` does not match the
function implementing it, since that tool could not be attributed to a module.
