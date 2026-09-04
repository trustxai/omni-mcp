"""Tools for the content, content-validator, and content-migration endpoints.

Six operations:

- `GET  /v1/content`                             → `omni_get_content`
- `GET  /v1/content/search`                      → `omni_search_dashboards`
- `GET  /v1/models/{modelId}/content-validator`  → `omni_validate_content`
- `POST /v1/models/{modelId}/content-validator`  → `omni_find_and_replace_content`
- `GET  /unstable/documents/{dashboardId}/export` → `omni_export_dashboard`
- `POST /unstable/documents/import`               → `omni_import_dashboard`

The two migration endpoints live under `/unstable/` on purpose — that is the
literal path in the API, and the endpoints are in beta.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from omni_mcp.client import get_client
from omni_mcp.config import get_settings
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import (
    ResponseFormat,
    cursor_paginated_response,
    iso_or_na,
    to_json,
    truncate_result,
)
from omni_mcp.server import mcp

#: One line on what this module covers, for the generated README catalogue.
MODULE_SUMMARY = "Content search, the content validator, dashboard export/import"

#: Model-element kinds the content validator and find/replace understand.
ElementType = Literal["VIEW", "FIELD", "TOPIC"]

#: Keys the import endpoint requires from an export payload.
EXPORT_REQUIRED_KEYS: tuple[str, ...] = ("dashboard", "document", "workbookModel", "exportVersion")


class GetContentInput(BaseModel):
    """Input for `omni_get_content`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    labels: str | None = Field(
        default=None,
        description="Filter content by labels. Provide as a comma-separated list (e.g. `finance,marketing`).",
    )
    scope: Literal["restricted", "organization"] = Field(
        default="organization",
        description=(
            "Content scope filter. `organization` (default) returns org-wide content; `restricted` returns one "
            "creator's restricted content and then requires `creator_id`."
        ),
    )
    sort_field: Literal["favorites", "name"] = Field(
        default="name",
        description="Field to sort by: `name` (default) or `favorites`.",
    )
    sort_direction: Literal["asc", "desc"] | None = Field(
        default=None,
        description="Sort direction: `asc` (A-Z, 0-9) or `desc` (Z-A, 9-0).",
    )
    include: str | None = Field(
        default=None,
        description=(
            "Comma-separated extra fields to include: `_count` (folders: document and favorite counts; documents: "
            "favorite and view counts) and `labels` (associated content labels)."
        ),
    )
    folder_id: str | None = Field(
        default=None,
        description="UUID of a folder; returns all content in that folder. Cannot be combined with `path`.",
    )
    path: str | None = Field(
        default=None,
        description=(
            "Filter content by path. Cannot be combined with `folder_id`. `/folder/subfolder` returns the folder and "
            "its content, `/folder/*` the direct children only, `/folder/**` everything recursively, `/` all content "
            "in the organization."
        ),
    )
    creator_id: str | None = Field(
        default=None,
        description="UUID of an organization membership. Required when `scope` is `restricted`.",
    )
    page_size: int = Field(default=20, ge=1, le=100, description="Number of records per page (1-100).")
    cursor: str | None = Field(default=None, description="Pagination cursor from a previous response's `nextCursor`.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable list, `json` for the raw records plus page info.",
    )

    @model_validator(mode="after")
    def _check_filters(self) -> GetContentInput:
        if self.folder_id and self.path:
            raise ValueError("folderId and path cannot be used together")
        if self.scope == "restricted" and not self.creator_id:
            raise ValueError("creatorId required when scope is restricted")
        return self


class SearchDashboardsInput(BaseModel):
    """Input for `omni_search_dashboards`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    q: str = Field(
        ...,
        min_length=1,
        description="Free-text keywords matched against dashboard names, descriptions, and tile titles.",
    )
    limit: int = Field(default=10, ge=1, le=25, description="Max results to return (1-25).")
    user_id: str | None = Field(
        default=None,
        description=(
            "UUID of the user to search as; results are scoped to what that user can access (content permits and "
            "model role). Requires an Organization API key — a Personal Access Token may only pass its own "
            "membership ID."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable list, `json` for the raw search results.",
    )


class ValidateContentInput(BaseModel):
    """Input for `omni_validate_content`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the model to validate content against.")
    branch_id: str | None = Field(
        default=None,
        description="UUID of the branch to validate against. Omit to validate against the main model.",
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "UUID of the user to act on behalf of. Only valid with an Organization API key — user-scoped keys get "
            "a 403 when this is provided."
        ),
    )
    include_personal_folders: bool | None = Field(
        default=None,
        description="When true, include personal folders in the scan.",
    )
    find: str | None = Field(
        default=None,
        description=(
            "Name of the model element to find content references for; must be used together with `find_type`. For "
            "`FIELD` the value must be fully qualified with the view name (e.g. `orders.status`)."
        ),
    )
    find_type: ElementType | None = Field(
        default=None,
        description=(
            "Type of model element to search for (`VIEW`, `FIELD`, `TOPIC`); must be used together with `find`. When "
            "both are provided, only content referencing that element is validated."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for issues grouped by document, `json` for the raw validation payload.",
    )

    @model_validator(mode="after")
    def _check_find(self) -> ValidateContentInput:
        if bool(self.find) != bool(self.find_type):
            raise ValueError("Both 'find' and 'find_type' parameters must be provided together")
        if self.find_type == "FIELD" and self.find and "." not in self.find:
            raise ValueError(
                "When find_type is FIELD, the find parameter must be scoped by view name (e.g. view_name.field_name)"
            )
        return self


class FindAndReplaceContentInput(BaseModel):
    """Input for `omni_find_and_replace_content`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the model to perform the find/replace against.")
    find: str = Field(
        ...,
        min_length=1,
        description=(
            "Value to find, scoped by `find_or_replace_type`: `VIEW` — the view name; `FIELD` — the fully qualified "
            "`view_name.field_name`; `TOPIC` — the topic name."
        ),
    )
    replacement: str = Field(
        ...,
        min_length=1,
        description=(
            "Replacement value: `VIEW` — the replacement view name; `FIELD` — the fully qualified "
            "`view_name.field_name`; `TOPIC` — the replacement topic name."
        ),
    )
    find_or_replace_type: ElementType = Field(
        ...,
        description=(
            "Type of find/replace operation: `VIEW` replaces view references, `FIELD` replaces field references "
            "(both values fully qualified), `TOPIC` replaces topic references."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Safety switch. This rewrites every matching document on the model and cannot be undone through the API, "
            "so nothing is sent unless this is explicitly true. Validate first with `omni_validate_content`."
        ),
    )
    branch_id: str | None = Field(
        default=None,
        description="UUID of the branch to operate on. Omit to operate on the main model.",
    )
    include_personal_folders: bool | None = Field(
        default=None,
        description="When true, include personal folders in the replacement.",
    )
    only_in_workbook_id: str | None = Field(
        default=None,
        description=(
            "Scope the replacement to one workbook. Must be the workbook model's internal UUID (visible in the model "
            "IDE URL as `/model/{uuid}`), NOT the document's URL identifier/slug — passing a slug silently returns "
            "0 replacements instead of an error."
        ),
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "UUID of the user to act on behalf of. Only valid with an Organization API key — user-scoped keys get "
            "a 403 when this is provided."
        ),
    )

    @model_validator(mode="after")
    def _check_field_scoping(self) -> FindAndReplaceContentInput:
        # Only the `find` side is a documented 400 ("Find field must be scoped by
        # view name"); the API publishes no such rule for `replacement`, so that
        # value is passed through rather than rejected locally.
        if self.find_or_replace_type == "FIELD" and "." not in self.find:
            raise ValueError("Find field must be scoped by view name (e.g. view_name.field_name)")
        return self


class ExportDashboardInput(BaseModel):
    """Input for `omni_export_dashboard`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dashboard_id: str = Field(..., min_length=1, description="Dashboard identifier to export.")
    output_path: str | None = Field(
        default=None,
        description=(
            "Local file path to write the export JSON to. Parent directories are created. When set, the tool returns "
            "the path and byte size instead of the payload — use it for large dashboards, then feed the file to "
            "`omni_import_dashboard` via `input_path`."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description=(
            "Allow `output_path` to replace an existing file. Without it an existing file is left untouched. "
            "Only meaningful together with `output_path`."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a summary plus the export JSON, `json` for the raw export payload only.",
    )

    @model_validator(mode="after")
    def _check_overwrite(self) -> ExportDashboardInput:
        if self.overwrite and not self.output_path:
            raise ValueError("overwrite only applies together with output_path; nothing is written without it")
        return self


class ImportDashboardInput(BaseModel):
    """Input for `omni_import_dashboard`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    base_model_id: str = Field(..., min_length=1, description="UUID of the target model to import the dashboard into.")
    export_payload: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The full export payload from `omni_export_dashboard`, shaped "
            '`{"dashboard": {...}, "document": {...}, "workbookModel": {...}, "exportVersion": "0.1", '
            '"fileUploads": [{"fileUploadId", "fileName", "contentType", "data"}]}`. Provide exactly one of '
            "`export_payload` or `input_path`."
        ),
    )
    input_path: str | None = Field(
        default=None,
        description=(
            "Local path of a JSON file holding the export payload (as written by `omni_export_dashboard`'s "
            "`output_path`). Provide exactly one of `export_payload` or `input_path`."
        ),
    )
    identifier: str | None = Field(default=None, description="Custom identifier for the imported dashboard.")
    folder_path: str | None = Field(default=None, description="Destination folder path (e.g. `/Finance/Reports`).")

    @model_validator(mode="after")
    def _check_source(self) -> ImportDashboardInput:
        if bool(self.export_payload) == bool(self.input_path):
            raise ValueError("Provide exactly one of 'export_payload' or 'input_path'")
        return self


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _format_content_record(record: Any) -> str:
    """One markdown bullet for a `/v1/content` record (folder or document)."""
    item = _as_dict(record)
    name = item.get("name") or item.get("title") or "unnamed"
    ident = item.get("identifier") or item.get("id") or "no-id"
    kind = item.get("type") or ("folder" if item.get("isFolder") else "content")
    parts = [f"- **{name}** (`{ident}`) — {kind}"]
    folder = _as_dict(item.get("folder"))
    location = item.get("path") or folder.get("path") or folder.get("name")
    if location:
        parts.append(f"path `{location}`")
    labels = item.get("labels")
    if isinstance(labels, list) and labels:
        rendered = ", ".join(str(_as_dict(label).get("name", label)) for label in labels)
        parts.append(f"labels: {rendered}")
    counts = _as_dict(item.get("_count"))
    if counts:
        parts.append("counts: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    updated = item.get("updatedAt") or item.get("updated_at")
    if updated:
        parts.append(f"updated {iso_or_na(updated)}")
    return " · ".join(parts)


def _format_search_result(result: Any) -> list[str]:
    """Markdown lines for one dashboard search hit."""
    item = _as_dict(result)
    verified = " ✅ verified" if item.get("verified") else ""
    lines = [f"### {item.get('name', 'unnamed')} (`{item.get('identifier', 'no-id')}`){verified}"]
    description = item.get("description")
    if description:
        lines.append(f"{description}")
    lines.append(f"- Folder: `{item.get('folderPath') or 'N/A'}`")
    tiles = item.get("tileNames")
    if isinstance(tiles, list) and tiles:
        lines.append("- Tiles: " + ", ".join(str(tile) for tile in tiles))
    if item.get("url"):
        lines.append(f"- URL: {item['url']}")
    lines.append("")
    return lines


def _document_issue_lines(document: dict[str, Any]) -> list[str]:
    """Issue bullets for one validated document (empty when it is clean)."""
    lines: list[str] = []
    for entry in document.get("queries_and_issues") or []:
        query = _as_dict(entry)
        issues = [str(issue) for issue in (query.get("issues") or [])]
        if not issues:
            continue
        name = query.get("query_name") or "unnamed query"
        presentation = query.get("query_presentation_id") or query.get("query_id_map_key") or "n/a"
        lines.append(f"- Query **{name}** (`{presentation}`):")
        lines.extend(f"  - {issue}" for issue in issues)
    filter_issues = [str(issue) for issue in (document.get("dashboard_filter_issues") or [])]
    if filter_issues:
        lines.append("- Dashboard filter issues:")
        lines.extend(f"  - {issue}" for issue in filter_issues)
    return lines


def _format_validation(body: dict[str, Any]) -> str:
    """Render the validator payload grouped by document, broken references first."""
    documents = [_as_dict(item) for item in (body.get("content") or [])]
    branch = _as_dict(body.get("branch"))
    branch_label = f"`{branch.get('name') or branch.get('id')}`" if branch else "main model"

    broken: list[tuple[dict[str, Any], list[str]]] = []
    clean: list[dict[str, Any]] = []
    for document in documents:
        issue_lines = _document_issue_lines(document)
        if issue_lines:
            broken.append((document, issue_lines))
        else:
            clean.append(document)

    # Issue bullets are the indented children; the parent bullets name the query.
    total_issues = sum(len([line for line in issue_lines if line.startswith("  - ")]) for _, issue_lines in broken)

    lines = [
        f"# Content validation — model `{body.get('model_id', 'unknown')}`",
        "",
        f"Validated against: {branch_label}.",
        f"Scanned **{len(documents):,}** document(s); **{len(broken):,}** with broken references "
        f"(**{total_issues:,}** issue(s)).",
        "",
    ]

    if broken:
        lines.append(f"## Documents with issues ({len(broken):,})")
        lines.append("")
        for document, issue_lines in broken:
            folder = _as_dict(document.get("folder"))
            owner = _as_dict(document.get("owner"))
            lines.append(f"### {document.get('name', 'unnamed')} (`{document.get('identifier', 'no-id')}`)")
            lines.append(
                f"- Type: {document.get('type', 'unknown')} · Folder: `{folder.get('path') or 'N/A'}` · "
                f"Owner: {owner.get('name') or 'unknown'} ({owner.get('email') or 'no email'})"
            )
            lines.append(
                f"- Document id: `{document.get('document_id', 'unknown')}` · Updated: "
                f"{iso_or_na(document.get('updated_at'))} · Requires PR to publish: "
                f"{bool(document.get('require_pull_request_to_publish'))}"
            )
            lines.extend(issue_lines)
            lines.append("")
    else:
        lines.append("**No broken references found.**")
        lines.append("")

    if clean:
        lines.append(f"## Clean documents ({len(clean):,})")
        lines.append("")
        lines.extend(f"- **{item.get('name', 'unnamed')}** (`{item.get('identifier', 'no-id')}`)" for item in clean)

    return truncate_result("\n".join(lines))


def _write_export_file(output_path: str, text: str, overwrite: bool) -> tuple[Path | None, str | None]:
    """Write `text` to `output_path`; return `(path, None)` or `(None, error)`."""
    target = Path(output_path).expanduser()
    if target.exists() and not overwrite:
        return None, (
            f"Error: `{target}` already exists. Pass `overwrite: true` to replace it, or choose another "
            "`output_path`. Nothing was written."
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        return None, f"Error: could not write `{target}` – {type(exc).__name__}: {exc}."
    return target, None


def _read_export_file(input_path: str) -> tuple[dict[str, Any] | None, str | None]:
    """Load an export payload from a local JSON file; return `(payload, None)` or `(None, error)`."""
    source = Path(input_path).expanduser()
    if not source.is_file():
        return None, f"Error: no such file `{source}`. Export a dashboard first with `omni_export_dashboard`."
    try:
        loaded = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"Error: could not read `{source}` – {type(exc).__name__}: {exc}."
    except json.JSONDecodeError as exc:
        return None, f"Error: `{source}` is not valid JSON – {exc}."
    if not isinstance(loaded, dict):
        return (
            None,
            f"Error: `{source}` must contain a JSON object with the exported dashboard, not a {type(loaded).__name__}.",
        )
    return loaded, None


@mcp.tool(
    name="omni_get_content",
    annotations=ToolAnnotations(
        title="Retrieve Content",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_content(params: GetContentInput) -> str:
    """Retrieve a paginated list of documents and folders.

    Calls `GET /v1/content`. Browse the whole organization, one folder
    (`folder_id`), or a path pattern (`path`), optionally filtered by labels and
    by the creator of restricted content.

    When to Use:
    - To browse the content tree or find what lives in a folder or path.
    - To resolve a dashboard/folder name into an identifier for other tools.
    - To list content carrying particular labels.

    When NOT to Use:
    - For free-text relevance search over dashboards — use `omni_search_dashboards`.
    - To read a document's contents or queries — use the document tools.
    - To check whether content still matches the model — use `omni_validate_content`.

    Returns:
    A markdown list of records (name, identifier, type, path, labels, counts,
    last update) with the next cursor, or the raw records plus page info when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - Everything, first page: `{"params": {}}`
    - One folder: `{"params": {"folder_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - A path, recursively, with labels: `{"params": {"path": "/Finance/**", "include": "labels,_count"}}`
    - Restricted content for one creator: `{"params": {"scope": "restricted", "creator_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`
    - Next page: `{"params": {"cursor": "eyJpZCI6..."}}`

    Error Handling:
    400 covers `Page size must be at least 1` / `cannot exceed 100`, `Invalid sort
    field`, `creatorId required when scope is restricted`, `folderId and path
    cannot be used together` and `Unrecognized query parameters` (the last three
    are also caught client-side). 404 `User with id <uuid> does not exist` means a
    bad `creator_id`. 429 is the 60 requests/minute limit. Any bearer token works;
    results are scoped to what the key's user may see.
    """
    try:
        query: dict[str, Any] = {
            "scope": params.scope,
            "sortField": params.sort_field,
            "pageSize": params.page_size,
        }
        if params.labels:
            query["labels"] = params.labels
        if params.sort_direction:
            query["sortDirection"] = params.sort_direction
        if params.include:
            query["include"] = params.include
        if params.folder_id:
            query["folderId"] = params.folder_id
        if params.path:
            query["path"] = params.path
        if params.creator_id:
            query["creatorId"] = params.creator_id
        if params.cursor:
            query["cursor"] = params.cursor

        payload = await get_client().request_json("GET", "/v1/content", params=query)
        body = _as_dict(payload)
        return cursor_paginated_response(
            items=[_as_dict(record) for record in (body.get("records") or [])],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_content_record,
            title="Omni content",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_search_dashboards",
    annotations=ToolAnnotations(
        title="Search Dashboards",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_search_dashboards(params: SearchDashboardsInput) -> str:
    """Free-text search over the organization's dashboards.

    Calls `GET /v1/content/search`. Keywords are matched against dashboard names,
    descriptions, and tile titles, and the most relevant dashboards come back
    first with their identifier, folder path, tile names, URL, and verified flag.

    When to Use:
    - To find a dashboard when you only know roughly what it is about.
    - To turn a human description ("revenue by region") into a dashboard identifier.
    - To check whether a dashboard on a topic already exists before building one.

    When NOT to Use:
    - To enumerate a folder or the whole content tree — use `omni_get_content`.
    - To search folders, workbooks, or non-dashboard content — this searches dashboards only.

    Returns:
    A markdown list of matches (name, identifier, description, folder path, tile
    names, URL, verified badge), or the raw `results` array when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"q": "revenue by region"}}`
    - Top 25 hits: `{"params": {"q": "churn", "limit": 25}}`
    - As another user (Organization API key only): `{"params": {"q": "pipeline", "user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`

    Error Handling:
    400 means `q is required` or `limit must be between 1 and 25` (both are also
    enforced client-side). 401 means the API key is missing or invalid. 403 comes
    back when a Personal Access Token passes a `user_id` other than its own
    membership. 429 is the 60 requests/minute limit. There is no paging: raise
    `limit` (max 25) or narrow the keywords.
    """
    try:
        query: dict[str, Any] = {"q": params.q, "limit": params.limit}
        if params.user_id:
            query["userId"] = params.user_id

        payload = await get_client().request_json("GET", "/v1/content/search", params=query)
        body = _as_dict(payload)
        results = [_as_dict(item) for item in (body.get("results") or [])]

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"query": params.q, "count": len(results), "results": results}))

        lines = [
            f'# Dashboard search — "{params.q}"',
            "",
            f"**{len(results):,}** match(es), most relevant first.",
            "",
        ]
        if results:
            for result in results:
                lines.extend(_format_search_result(result))
        else:
            lines.append("_No dashboards matched._")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_validate_content",
    annotations=ToolAnnotations(
        title="Validate Content",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_validate_content(params: ValidateContentInput) -> str:
    """Validate every document on a model and report broken view/field references.

    Calls `GET /v1/models/{modelId}/content-validator`. It scans the documents
    associated with the model and reports fields, views, and topics referenced by
    saved queries or dashboard filters that no longer exist. Pass `find` +
    `find_type` to scope the scan to content that references one element, which
    avoids validating every document.

    When to Use:
    - After renaming or removing a view, field, or topic, to find the content it broke.
    - Before a model change, to see which dashboards reference an element (`find` + `find_type`).
    - As the dry run before `omni_find_and_replace_content`.

    When NOT to Use:
    - To fix the references — that is `omni_find_and_replace_content`.
    - To validate the model itself rather than the content built on it.

    Returns:
    A markdown report grouped by document — the documents with broken references
    first (each query and its issue messages, plus dashboard-filter issues, with
    folder, owner, last update, and whether publishing needs a pull request),
    then the clean documents. `response_format: json` returns the raw payload.
    On failure, an `Error ...` string.

    Examples:
    - Whole model: `{"params": {"model_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - Only content using one field: `{"params": {"model_id": "550e8400-e29b-41d4-a716-446655440000", "find": "orders.status", "find_type": "FIELD"}}`
    - On a branch, including personal folders: `{"params": {"model_id": "550e8400-...", "branch_id": "550e8400-...", "include_personal_folders": true}}`

    Error Handling:
    400 means `modelId: Invalid UUID`, `Both 'find' and 'find_type' parameters
    must be provided together`, or a `FIELD` search that is not scoped by view
    name (the last two are also enforced client-side). 403 means
    `User-scoped API keys cannot act on behalf of other users` — `user_id`
    requires an Organization API key. 404 means `Shared model with id <modelId>
    does not exist`. 429 is the 60 requests/minute limit. The caller needs access
    to the model; scanning a large model can be slow, so scope it with `find`.
    """
    try:
        query: dict[str, Any] = {}
        if params.branch_id:
            query["branch_id"] = params.branch_id
        if params.user_id:
            query["userId"] = params.user_id
        if params.include_personal_folders is not None:
            query["include_personal_folders"] = params.include_personal_folders
        if params.find:
            query["find"] = params.find
        if params.find_type:
            query["find_type"] = params.find_type

        path = f"/v1/models/{quote(params.model_id, safe='')}/content-validator"
        payload = await get_client().request_json("GET", path, params=query or None)
        body = _as_dict(payload)

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        return _format_validation(body)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_find_and_replace_content",
    annotations=ToolAnnotations(
        title="Find and Replace Content",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_find_and_replace_content(params: FindAndReplaceContentInput) -> str:
    """Replace a view, field, or topic reference across every document on a model.

    Calls `POST /v1/models/{modelId}/content-validator`. This rewrites saved
    queries, workbook models, and dashboard filters in place across all documents
    on the model (optionally scoped to one workbook), so it is destructive and
    cannot be undone through the API. Nothing is sent unless `confirm` is true.

    When to Use:
    - After renaming a view, field, or topic in the model, to repoint all content at the new name.
    - To clean up references reported by `omni_validate_content`.
    - To migrate content off a deprecated topic onto its replacement.

    When NOT to Use:
    - Before running `omni_validate_content` to see exactly what will change.
    - To edit a single document — use the document tools instead.
    - To rename the model element itself; this only rewrites references to it.

    Returns:
    A short confirmation with the model id and the counts the API reports
    (queries, documents, workbook models, dashboard filters replaced, and
    documents skipped because they require a pull request to publish). With
    `confirm` false, a refusal describing what would happen — no request is sent.
    On failure, an `Error ...` string.

    Examples:
    - Preview the refusal: `{"params": {"model_id": "550e8400-...", "find": "old_view", "replacement": "new_view", "find_or_replace_type": "VIEW"}}`
    - Rename a view: `{"params": {"model_id": "550e8400-...", "find": "old_view", "replacement": "new_view", "find_or_replace_type": "VIEW", "confirm": true}}`
    - Rename a field: `{"params": {"model_id": "550e8400-...", "find": "orders.old_field", "replacement": "orders.new_field", "find_or_replace_type": "FIELD", "confirm": true}}`
    - Scope to one workbook on a branch: `{"params": {"model_id": "550e8400-...", "find": "old_topic", "replacement": "new_topic", "find_or_replace_type": "TOPIC", "branch_id": "550e8400-...", "only_in_workbook_id": "550e8400-...", "confirm": true}}`

    Error Handling:
    400 means `modelId: Invalid UUID` or `Find field must be scoped by view
    name.` (also enforced client-side). 403 means `User-scoped API keys cannot
    act on behalf of other users` — `user_id` requires an Organization API key.
    404 means `Shared model with id <modelId> does not exist`. 429 is the 60
    requests/minute limit. The caller needs edit access to the model's content; a
    zero-count result usually means `only_in_workbook_id` was given a document
    slug instead of the workbook model UUID.
    """
    try:
        if not params.confirm:
            scope = f" in workbook `{params.only_in_workbook_id}`" if params.only_in_workbook_id else ""
            branch = f" on branch `{params.branch_id}`" if params.branch_id else " on the main model"
            return (
                f"Refused: no request was sent. Replacing {params.find_or_replace_type} `{params.find}` with "
                f"`{params.replacement}`{branch}{scope} rewrites every matching document on model "
                f"`{params.model_id}` and cannot be undone through the API. Run `omni_validate_content` with "
                f'`find="{params.find}"` and `find_type="{params.find_or_replace_type}"` to see the affected '
                "content, then re-run this tool with `confirm: true`."
            )

        body: dict[str, Any] = {
            "find": params.find,
            "replacement": params.replacement,
            "find_or_replace_type": params.find_or_replace_type,
        }
        if params.branch_id:
            body["branch_id"] = params.branch_id
        if params.include_personal_folders is not None:
            body["include_personal_folders"] = params.include_personal_folders
        if params.only_in_workbook_id:
            body["only_in_workbook_id"] = params.only_in_workbook_id

        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id

        path = f"/v1/models/{quote(params.model_id, safe='')}/content-validator"
        payload = await get_client().request_json("POST", path, params=query or None, json_body=body)
        result = _as_dict(payload)
        skipped = result.get("skipped_pr_required_count") or 0
        note = f" {skipped:,} document(s) skipped (pull request required to publish)." if skipped else ""
        return truncate_result(
            f"Replaced {params.find_or_replace_type} `{params.find}` with `{params.replacement}` on model "
            f"`{params.model_id}`: {result.get('replaced_queries_count', 0):,} query(ies), "
            f"{result.get('replaced_documents_count', 0):,} document(s), "
            f"{result.get('replaced_workbook_models_count', 0):,} workbook model(s), "
            f"{result.get('replaced_dashboard_filters_count', 0):,} dashboard filter(s).{note}"
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_export_dashboard",
    annotations=ToolAnnotations(
        # Not read-only: with `output_path` set the tool writes a local file.
        # Still non-destructive — replacing an existing file needs `overwrite`.
        title="Export Dashboard",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_export_dashboard(params: ExportDashboardInput) -> str:
    """Export a dashboard as a migration payload for another instance.

    Calls `GET /unstable/documents/{dashboardId}/export` — the path is literally
    under `/unstable/`, and this endpoint is in beta and may change. The payload
    contains `dashboard`, `document`, `workbookModel`, `exportVersion`, and any
    `fileUploads` (base64 spreadsheet data backing spreadsheet tiles). Requires
    an Organization API key; Personal Access Tokens are not accepted.

    Export payloads are large, so prefer `output_path`: the raw JSON is written
    to that local file (parent directories are created) and the tool returns the
    path and byte size instead of the body. `omni_import_dashboard` can read that
    same file back with `input_path`.

    When to Use:
    - To move a dashboard between instances (dev → prod, or into another org).
    - To snapshot a dashboard's definition to disk before a risky change.
    - To stage a payload for `omni_import_dashboard`.

    When NOT to Use:
    - To read or edit a dashboard in place — use the dashboard/document tools.
    - To copy a dashboard within the same instance — duplicate it instead.
    - To fetch data or query results; this exports the definition only.

    Returns:
    With `output_path`: a confirmation with the file path and byte size. Without
    it: a markdown summary (export version, top-level sections, file uploads)
    followed by the export JSON, truncated to the result budget — or the raw
    payload when `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - To a file (recommended): `{"params": {"dashboard_id": "12db1a0a", "output_path": "/tmp/revenue-dashboard.json"}}`
    - Replace an existing file: `{"params": {"dashboard_id": "12db1a0a", "output_path": "/tmp/revenue-dashboard.json", "overwrite": true}}`
    - Inline JSON: `{"params": {"dashboard_id": "12db1a0a", "response_format": "json"}}`

    Error Handling:
    404 means the dashboard identifier does not exist on this instance. 403
    usually means the key is a Personal Access Token rather than an Organization
    API key, or the caller cannot see the dashboard. 429 is the 60
    requests/minute limit. Local write failures (an existing file without
    `overwrite`, a bad directory) are reported without any data loss.
    """
    try:
        path = f"/unstable/documents/{quote(params.dashboard_id, safe='')}/export"
        payload = await get_client().request_json("GET", path)
        body = _as_dict(payload)
        serialized = to_json(payload)

        if params.output_path:
            target, error = _write_export_file(params.output_path, serialized, params.overwrite)
            if error is not None:
                return error
            size = len(serialized.encode("utf-8"))
            return (
                f"Exported dashboard `{params.dashboard_id}` to `{target}` ({size:,} bytes, export version "
                f"`{body.get('exportVersion', 'unknown')}`). Import it with `omni_import_dashboard` using "
                f'`input_path: "{target}"`.'
            )

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(serialized)

        uploads = body.get("fileUploads")
        upload_count = len(uploads) if isinstance(uploads, list) else 0
        lines = [
            f"# Dashboard export — `{params.dashboard_id}`",
            "",
            f"- Export version: `{body.get('exportVersion', 'unknown')}`",
            f"- Sections: {', '.join(f'`{key}`' for key in body) or 'none'}",
            f"- File uploads: {upload_count:,}",
            "",
            "Pass `output_path` to write this payload to a file instead of returning it.",
            "",
            "```json",
            "",
        ]
        header = "\n".join(lines)
        closing = "\n```"
        # The JSON is truncated on its own so the fence still closes — truncating
        # the assembled string would cut the trailing ``` off a large export.
        budget = get_settings().omni_max_result_chars - len(header.encode("utf-8")) - len(closing.encode("utf-8"))
        return header + truncate_result(serialized, max(budget, 1)) + closing
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_import_dashboard",
    annotations=ToolAnnotations(
        title="Import Dashboard",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_import_dashboard(params: ImportDashboardInput) -> str:
    """Import an exported dashboard into a model on this instance.

    Calls `POST /unstable/documents/import` — the path is literally under
    `/unstable/`, and this endpoint is in beta and may change. It creates content
    on the target instance (and can overwrite content at the same `identifier`),
    so it is treated as destructive. Requires an Organization API key; Personal
    Access Tokens are not accepted.

    Supply the export payload either inline as `export_payload` or as `input_path`
    pointing at the file `omni_export_dashboard` wrote — exactly one of the two.
    The payload must carry `dashboard`, `document`, `workbookModel`, and
    `exportVersion` (`"0.1"`); its `fileUploads` array is forwarded so
    spreadsheet tiles are restored.

    When to Use:
    - To land a dashboard exported from another instance (dev → prod).
    - To restore a dashboard from an export snapshot.
    - As the second half of a migration started with `omni_export_dashboard`.

    When NOT to Use:
    - To create a dashboard from scratch — use the document tools.
    - To update an existing dashboard's tiles or filters in place.
    - Without knowing the target `base_model_id`; the import binds the content to that model.

    Returns:
    A short confirmation naming the new dashboard id (plus folder path and
    identifier when given). On failure, an `Error ...` string.

    Examples:
    - From a file: `{"params": {"base_model_id": "550e8400-e29b-41d4-a716-446655440000", "input_path": "/tmp/revenue-dashboard.json"}}`
    - Into a folder with a custom identifier: `{"params": {"base_model_id": "550e8400-...", "input_path": "/tmp/revenue-dashboard.json", "folder_path": "/Finance/Reports", "identifier": "revenue-2026"}}`
    - Inline payload: `{"params": {"base_model_id": "550e8400-...", "export_payload": {"dashboard": {}, "document": {}, "workbookModel": {}, "exportVersion": "0.1"}}}`

    Error Handling:
    400 means the payload is malformed or `exportVersion` is not `"0.1"` — always
    import a payload produced by `omni_export_dashboard`. 403 means
    `User-scoped API keys cannot act on behalf of other users`, or that the
    target folder is personal/restricted and the caller lacks the
    `omni_allows_personal_content` system attribute. 429 is the 60
    requests/minute limit. A missing or non-JSON `input_path` is reported locally
    before any request is sent.
    """
    try:
        export: dict[str, Any] = params.export_payload or {}
        if params.input_path:
            loaded, error = _read_export_file(params.input_path)
            if error is not None:
                return error
            export = loaded or {}

        # A key present but null is as unusable as a missing one, and the API
        # answers that with an opaque 400.
        missing = [key for key in EXPORT_REQUIRED_KEYS if export.get(key) is None]
        if missing:
            return (
                f"Error: the export payload is missing required key(s), or has them set to null: "
                f"{', '.join(missing)}. It must be the full "
                "response from `omni_export_dashboard` (`dashboard`, `document`, `workbookModel`, `exportVersion`). "
                "No request was sent."
            )

        body: dict[str, Any] = {
            "baseModelId": params.base_model_id,
            "dashboard": export["dashboard"],
            "document": export["document"],
            "workbookModel": export["workbookModel"],
            "exportVersion": export["exportVersion"],
        }
        if export.get("fileUploads") is not None:
            body["fileUploads"] = export["fileUploads"]
        if params.identifier:
            body["identifier"] = params.identifier
        if params.folder_path:
            body["folderPath"] = params.folder_path

        payload = await get_client().request_json("POST", "/unstable/documents/import", json_body=body)
        result = _as_dict(payload)
        dashboard_id = _as_dict(result.get("dashboard")).get("dashboardId", "unknown")
        extras = []
        if params.folder_path:
            extras.append(f"folder `{params.folder_path}`")
        if params.identifier:
            extras.append(f"identifier `{params.identifier}`")
        suffix = f" ({', '.join(extras)})" if extras else ""
        return truncate_result(f"Imported dashboard `{dashboard_id}` into model `{params.base_model_id}`{suffix}.")
    except Exception as exc:
        return handle_api_error(exc)
