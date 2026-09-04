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
docs/               README long-form companions (TOOLS.md, comparison.md)
scripts/tool_table.py generates docs/TOOLS.md and the README summary
```

`tools/__init__.py` already lists **every** module, so two people can implement different areas at
the same time without touching a shared file. `register_all()` takes no argument: every module
decorates against the single server instance it imports from `omni_mcp.server`. Do not add tools to an area that is not yours, and do
not edit another area's module.

## The tool contract

1. **Naming.** Tool names are `omni_<verb>_<noun>`: `omni_list_users`, `omni_get_model_yaml`,
   `omni_create_folder`, `omni_delete_schedule`. Lower snake case, always the `omni_` prefix (it is
   enforced by `tests/test_server.py`), and the same verb vocabulary throughout.

   **CRUD endpoints use CRUD verbs**: `list`, `get`, `search`, `create`, `update`, `replace`,
   `delete`. **Action endpoints use the API's own verb** — do not translate an action into a CRUD
   verb, because the API's word is what a model will be looking for:

   > `pause`, `resume`, `trigger`, `publish`, `archive`, `restore`, `grant`, `revoke`, `assign`,
   > `replace`, `sync`, `merge`, `migrate`, `validate`, `refresh`, `reset`, `upload`, `download`,
   > `export`, `import`, `generate`, `dismiss`, `enable`, `disable`, `wait`, `ask`

   So: `omni_pause_schedule`, not `omni_update_schedule_state`; `omni_merge_model_branch`, not
   `omni_update_model_branch`; `omni_revoke_folder_permissions`, not `omni_delete_folder_permissions`.
   `update` is a partial change (`PATCH`); `replace` is a whole-resource write (`PUT`).
2. **One pydantic input model per tool**, named `<ToolName>Input`, with
   `model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")` and a `Field(description=…)`
   on every field. Snake-case field names map to the API's camelCase query params in the tool body.

   **Credential and content fields opt out of stripping.** `str_strip_whitespace=True` is right for
   ids and names and wrong for anything whose bytes are the payload — passwords, deploy private
   keys (a PEM's trailing newline is load-bearing), CSV content, YAML content (its trailing newline
   is part of the checksum). Exempt those fields individually:

   ```python
   from typing import Annotated

   from pydantic import StringConstraints

   deploy_private_key: Annotated[str, StringConstraints(strip_whitespace=False)] | None = Field(
       default=None, description="PEM-encoded deploy key. Never echoed back."
   )
   ```

   Silently trimming one of these corrupts the request in a way the caller cannot see.
3. **Signature.** `async def omni_<verb>_<noun>(params: <ToolName>Input) -> str:` — always a single
   `params` argument, always a `str` return.
4. **Annotations.** Every tool passes `ToolAnnotations` with `title`, `readOnlyHint`,
   `destructiveHint`, `idempotentHint` and `openWorldHint`. Reads are
   `readOnlyHint=True, destructiveHint=False, idempotentHint=True`; deletes are
   `readOnlyHint=False, destructiveHint=True`; creates/updates are `readOnlyHint=False,
   destructiveHint=False`, with `idempotentHint=True` only for genuine PUT-style upserts.
   `openWorldHint=True` for anything that calls the API.
5. **Docstring sections**, in this order: a one-line summary (it becomes the tool's description in
   the generated `docs/TOOLS.md` reference), then `When to Use`, `When NOT to Use`, `Returns`, `Examples`, `Error Handling`.
   Write them for a model deciding which tool to call, not for a human reading source.
6. **Never raise.** Wrap the whole body in `try: … except Exception as exc: return handle_api_error(exc)`.
7. **`response_format`** on read tools: a `ResponseFormat` field defaulting to
   `ResponseFormat.MARKDOWN`, with `json` returning the structured payload via `to_json`.
8. **Mutating tools return a short confirmation string** — what changed and its id, e.g.
   `Created folder **Marketing** (id `abc123`).` — not a dump of the response body.
9. **List tools expose `page_size` and `cursor`** and render through `cursor_paginated_response`,
   which prints the next cursor so the caller can page. Never loop over pages inside a tool.

   **Not every list paginates the same way**, and the spec is the authority:
   - **SCIM endpoints** (`/scim/v2/Users`, `/scim/v2/Groups`) use **offset** pagination —
     `startIndex` (1-based) and `count`, answering with `totalResults`/`itemsPerPage`, not a
     cursor. Expose those parameter names as they are and render through the module-local SCIM
     list helper; do not invent a cursor on top.
   - **Some lists are bare arrays** with no `pageInfo` at all (eval prompt sets, eval runs, labels).
     Render them with `cursor_paginated_response(..., page_info=None)`, which prints the items and
     no pagination hint, or with a small module-local helper when the shape needs it. Never fake a
     `pageInfo`.
10. **Raw-body overrides are named `body`.** When an endpoint's schema is too open to model
    field-by-field, add one optional escape hatch called `body: dict[str, Any] | None`, sent to the
    API verbatim. Two rules keep it honest: it only wins when **no typed field is set**, and mixing
    it with typed fields is **rejected in a `model_validator`** rather than resolved silently —
    a caller who sets both has a bug, and a silent winner hides it. Say so in the field
    description, and keep the tool's own client-side guards documented as skipped on this path.
11. **Long-running operations poll against a monotonic deadline.** Convenience tools that wait
    (`omni_ask_ai`, `omni_export_dashboard_file`, `omni_wait_for_query_results`) must:
    - measure the budget with `time.monotonic()`, through **injectable module-level `_monotonic`
      and `_sleep`** so tests drive the clock instead of sleeping;
    - **clamp the per-request timeout to the remaining budget** — one slow request must not
      overshoot the wall-clock budget the caller asked for;
    - **cap the poll count** as well as the deadline, so a pathological fast-failing endpoint
      cannot spin;
    - on timeout, return the **job id** and whatever else resumes the wait, not an error — the
      caller continues with the status/result tools.
12. **Binary downloads are written to a file, never into the result.** Use
    `get_client().request(..., headers={"accept": "*/*"})` (the JSON default would make the API
    negotiate the wrong content type), write `response.content` to the caller's `output_path`,
    and return a confirmation with the path and byte count. Refuse to overwrite an existing file
    unless the caller passed `overwrite=True`, and reject `overwrite=True` without an
    `output_path`. A tool that writes a local file is not read-only: `readOnlyHint=False`,
    `destructiveHint=False`.
13. **Stay under the 1 MB tool-result limit**: every result goes through `truncate_result`, which
    budgets in UTF-8 **bytes** (`cursor_paginated_response` and `format_arrow_result` already do it
    for you).
14. **Never print to stdout** — stdout is the MCP channel. Use `logging` to stderr if you must.
15. **Never echo secrets.** Report whether a key is configured, never the key itself.

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
uv run ruff check src/ tests/ scripts/
uv run ruff format --check src/ tests/ scripts/
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
`omni-app-mcp` project → *Manage* → *Publishing*, register these two GitHub publishers:

| Owner | Repository | Workflow file | Environment |
| --- | --- | --- | --- |
| `trustxai` | [`omni-mcp`](https://github.com/trustxai/omni-mcp) | `release-please.yml` | Any *(registered)* |
| `trustxai` | [`omni-mcp`](https://github.com/trustxai/omni-mcp) | `publish.yml` | `pypi` |

The publisher registered today is the first row: workflow `release-please.yml` with the environment
left as **Any**, which the release job's own `environment: pypi` satisfies. Registering the second
one for `publish.yml` is what enables the manual re-run path — with only the first entry the normal
release path works and the manual fallback fails at `uv publish` with an OIDC/"not a trusted
publisher" error, at exactly the moment it is needed.
Add publishers that do not exist yet as *pending* ones (the project is not on PyPI until the first
successful upload).

## The tool reference

`docs/TOOLS.md` is generated — regenerate it whenever you add, rename or re-describe a tool:

```bash
uv run python scripts/tool_table.py
```

Paste the output verbatim under the header in `docs/TOOLS.md`, keeping the generator's format
byte-for-byte. Then update the compact per-module summary in the README between the
`<!-- TOOL_TABLE_START -->` and `<!-- TOOL_TABLE_END -->` markers — the totals and your module's
row need to match the numbers the generator just printed.

The script exits non-zero (printing nothing) when a tool's registered `name=` does not match the
function implementing it, since that tool could not be attributed to a module.
