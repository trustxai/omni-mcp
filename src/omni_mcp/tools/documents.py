"""Tools for the documents (v1) endpoints — list, read, drafts and lifecycle.

A *document* is the container behind a dashboard and its workbook. It is
addressed by its **identifier** (the string after `/dashboards/` in the URL, or
the **Identifier** field under *File > Document settings > Settings*), which
these tools accept anywhere a `document_id` is asked for.

Removed endpoints (not exposed here — they always answer `410 Gone`, sunset
`2026-07-31`; use the documents v2 tools instead):

- `PUT /v1/documents/{documentId}` (Update dashboard document) — successor tool
  `omni_patch_document_draft`, then `omni_publish_document_draft`.
- `PATCH /v1/documents/{documentId}` (Update document: name/description/
  identifier) — successor tools `omni_patch_document_draft` for name and
  description, `omni_rename_document_identifier` for the identifier.

Four endpoints here are deprecated but still served; each carries a
`Deprecated:` line in its docstring naming the tool that replaces it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import (
    ResponseFormat,
    cursor_paginated_response,
    iso_or_na,
    markdown_table,
    to_json,
    truncate_result,
)
from omni_mcp.server import mcp

#: Values accepted by the `include` query parameter of `GET /v1/documents`.
DocumentInclude = Literal["_count", "labels", "includeDeleted", "onlyFavorites", "onlySharedWithMe"]

#: Sharing scope of a document or of its destination folder.
DocumentScope = Literal["organization", "restricted"]


def _path(document_id: str) -> str:
    """Percent-encode one path segment (identifiers are free-form strings)."""
    return quote(document_id, safe="")


def _record(payload: Any) -> dict[str, Any]:
    """Narrow a decoded JSON body to a dict (the API may answer `null`)."""
    return payload if isinstance(payload, dict) else {}


def _sub_dict(item: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Read a nested object, tolerating a missing or `null` value."""
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def _sub_list(item: Mapping[str, Any], key: str) -> list[Any]:
    """Read a nested array, tolerating a missing or `null` value."""
    value = item.get(key)
    return value if isinstance(value, list) else []


def _labels(item: Mapping[str, Any]) -> str:
    raw = item.get("labels")
    if isinstance(raw, list) and raw:
        return ", ".join(str(label) for label in raw)
    return "none"


def _document_line(item: Mapping[str, Any]) -> str:
    """One markdown bullet per document record from `GET /v1/documents`."""
    folder = item.get("folder")
    folder_text = "root"
    if isinstance(folder, dict):
        folder_text = f"{folder.get('name', 'unnamed')} (`{folder.get('path', '?')}`)"
    owner = item.get("owner")
    owner_text = "unknown"
    if isinstance(owner, dict):
        owner_text = f"{owner.get('name', 'unnamed')} (`{owner.get('id', '?')}`)"
    counts = item.get("_count")
    count_text = ""
    if isinstance(counts, dict):
        count_text = f" · favorites: {counts.get('favorites', 0)} · views: {counts.get('views', 0)}"
    deleted = " · **deleted**" if item.get("deleted") else ""
    return (
        f"- **{item.get('name', 'unnamed')}** (`{item.get('identifier', 'unknown')}`) — "
        f"scope: {item.get('scope', 'unknown')} · folder: {folder_text} · owner: {owner_text} · "
        f"updated: {iso_or_na(item.get('updatedAt'))} · labels: {_labels(item)} · "
        f"dashboard: {'yes' if item.get('hasDashboard') else 'no'}{count_text}{deleted}\n"
        f"  {item.get('url', '')}"
    )


class ListDocumentsInput(BaseModel):
    """Input for `omni_list_documents`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    include: list[DocumentInclude] | None = Field(
        default=None,
        description=(
            "Additional fields/behaviours to request, sent as a comma-separated list: "
            "`_count` (favorite and view count metrics), `labels` (associated document labels), "
            "`includeDeleted` (include deleted documents), `onlyFavorites` (only documents the "
            "user in `user_id` has favorited — must be used with `user_id` when authenticating "
            "with an Organization API key), `onlySharedWithMe` (only documents explicitly shared "
            "with that user via document permissions, excluding documents they own)."
        ),
    )
    labels: list[str] | None = Field(
        default=None,
        description='Labels to filter by, sent comma-separated. For example `["finance", "marketing"]`.',
    )
    folder_id: str | None = Field(
        default=None,
        min_length=1,
        description="Folder UUID. Returns only documents inside that folder. Cannot be combined with `onlySharedWithMe`.",
    )
    page_size: int = Field(default=20, ge=1, le=100, description="Number of records per page (1-100).")
    sort_field: Literal["favorites", "name", "updatedAt", "visits"] | None = Field(
        default=None,
        description=(
            "Field to sort by: `favorites` (number of favorites), `name` (document name), "
            "`updatedAt` (last update time), `visits` (view count). The API defaults to `name`."
        ),
    )
    sort_direction: Literal["asc", "desc"] | None = Field(
        default=None,
        description="Sort direction, `asc` or `desc`. The API defaults to `desc`.",
    )
    cursor: str | None = Field(
        default=None,
        min_length=1,
        description="Cursor from a previous page's `nextCursor`. Pair it with the same `sort_field`/`sort_direction`.",
    )
    user_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "UUID of a standard or embed user; returns only documents that user may view. "
            "Required with `onlySharedWithMe`, and with `onlyFavorites` when using an Organization "
            "API key. A Personal Access Token infers the user from the token."
        ),
    )
    creator_id: str | None = Field(
        default=None,
        min_length=1,
        description="UUID of the user who created the documents; returns only documents that user created.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw records plus pagination info.",
    )

    @model_validator(mode="after")
    def _check_include_combinations(self) -> ListDocumentsInput:
        include = set(self.include or ())
        if "onlySharedWithMe" in include:
            if "onlyFavorites" in include:
                raise ValueError("onlySharedWithMe cannot be combined with onlyFavorites")
            if self.folder_id:
                raise ValueError("onlySharedWithMe cannot be combined with folderId")
        return self


class GetDocumentInput(BaseModel):
    """Input for `omni_get_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ...,
        min_length=1,
        description="Document identifier (for example `12db1a0a`, the string after `/dashboards/` in the URL).",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the full PUT-compatible document body.",
    )


class CreateDocumentInput(BaseModel):
    """Input for `omni_create_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the model to build the document on.")
    name: str = Field(..., min_length=1, description="Name of the document.")
    description: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="Description of the document (1-1024 characters).",
    )
    identifier: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Custom identifier used in the document's URL; auto-generated when omitted. Lowercase "
            "letters, numbers, hyphens and underscores only, and it may not start or end with a "
            "hyphen or underscore. Must be unique across the organization. Example: `sales-dashboard-2026`."
        ),
    )
    query_presentations: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Queries to seed the document's workbook with. Each item is "
            "`{'name': str, 'description': str, 'query': {...}, 'visConfig': {...}}`, where `query` "
            "is the query definition object and `visConfig` the visualization configuration."
        ),
    )


class DeleteDocumentInput(BaseModel):
    """Input for `omni_delete_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="Identifier of the document to move to Trash.")


class MoveDocumentInput(BaseModel):
    """Input for `omni_move_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="Identifier of the document to move.")
    folder_path: str | None = Field(
        default=None,
        description=(
            "Path of the destination folder, for example `/reports/2026/q1`. Leave unset (null) to "
            "move the document to the root level, outside any folder."
        ),
    )
    scope: DocumentScope | None = Field(
        default=None,
        description=(
            "Sharing scope for the document: `organization` (organization-wide access) or "
            "`restricted` (limited access). When omitted, the scope is computed from the document "
            "or the destination folder. When provided, it must match the destination folder's scope."
        ),
    )


class DuplicateDocumentInput(BaseModel):
    """Input for `omni_duplicate_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="Identifier of the published document to duplicate.")
    name: str = Field(..., min_length=1, max_length=255, description="Name for the duplicated document (1-255 chars).")
    folder_path: str | None = Field(
        default=None,
        description="Path of the destination folder. When omitted, the copy is created at the root level.",
    )
    scope: DocumentScope | None = Field(
        default=None,
        description=(
            "Visibility scope for the copy: `organization` or `restricted`. When omitted it is "
            "inherited from the destination folder, defaulting to `restricted`."
        ),
    )
    user_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Attribute the duplication to this user. **Requires an Organization API key** — "
            "Personal Access Tokens can only act as the authenticated user."
        ),
    )


class UpgradeDashboardLayoutInput(BaseModel):
    """Input for `omni_upgrade_dashboard_layout`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ...,
        min_length=1,
        description="Document identifier — either the document ID or the identifier slug (for example `abc123`).",
    )
    clear_existing_draft: bool | None = Field(
        default=None,
        description=(
            "When upgrading a published document, discard any existing draft instead of failing "
            "with a `409` conflict. Defaults to `false`."
        ),
    )


class TransferDocumentOwnershipInput(BaseModel):
    """Input for `omni_transfer_document_ownership`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="Identifier of the document whose ownership to transfer.")
    user_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Membership ID of the new owner. Must be an organization member who already has "
            "explicit permission on the document."
        ),
    )


class CreateDocumentDraftInput(BaseModel):
    """Input for `omni_create_document_draft`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="Identifier of the document to create a draft for.")
    branch_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "UUID of the branch to create the draft on; required when drafting on a branch. "
            "Retrieve branch IDs by listing models with `modelKind=BRANCH`."
        ),
    )


class ArchiveDocumentDraftInput(BaseModel):
    """Input for `omni_archive_document_draft`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="Identifier of the document whose draft to archive.")
    branch_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "UUID of the branch the draft is attached to. Omit to archive the draft that is not attached to a branch."
        ),
    )


class ListDocumentDraftsInput(BaseModel):
    """Input for `omni_list_document_drafts`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="Identifier of the published document.")
    include: Literal["archived"] | None = Field(
        default=None,
        description="Pass `archived` to include soft-deleted drafts. By default only active drafts are returned.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable table, `json` for the raw draft objects.",
    )


class GetDocumentQueriesInput(BaseModel):
    """Input for `omni_get_document_queries`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ...,
        min_length=1,
        description="Document identifier — the string after `/dashboards/` in the document URL, e.g. `12db1a0a`.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a summary, `json` for the full query JSON ready for the query-run tool.",
    )


class RemoveDashboardFromDocumentInput(BaseModel):
    """Input for `omni_remove_dashboard_from_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ..., min_length=1, description="Identifier of the published document the draft belongs to (e.g. `abc123`)."
    )
    draft_id: str = Field(
        ...,
        min_length=1,
        description="Identifier of the existing draft, as returned when the draft was created (e.g. `def456`).",
    )


@mcp.tool(
    name="omni_list_documents",
    annotations=ToolAnnotations(
        title="List Documents",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_documents(params: ListDocumentsInput) -> str:
    """List documents (dashboards and workbooks) with filtering and cursor pagination.

    Calls `GET /v1/documents`. Filters by folder, labels, creator, and by what a
    given user can see; optionally adds favorite/view counts, labels, deleted
    documents, only-favorites and only-shared-with-me views.

    When to Use:
    - To find a document's identifier before calling any other document tool.
    - To inventory the documents in a folder, or everything carrying a label.
    - To see what a specific user can view, has favorited, or has had shared with them.

    When NOT to Use:
    - To full-text search names and contents — use the content search tool.
    - To page through everything at once: pass one page, then reuse `cursor`.
    - To list drafts of a document — use `omni_list_document_drafts`.

    Returns:
    A markdown list — name, identifier, scope, folder, owner, `updatedAt`,
    labels, whether it has a dashboard, and the URL — with the record count and
    the next cursor. `json` returns the raw records plus pagination info.

    Examples:
    - First page by recency: `{"params": {"sort_field": "updatedAt", "sort_direction": "desc", "page_size": 50}}`
    - In one folder with counts: `{"params": {"folder_id": "3f1c...", "include": ["_count", "labels"]}}`
    - Shared with a user: `{"params": {"include": ["onlySharedWithMe"], "user_id": "9e87..."}}`
    - Next page: `{"params": {"cursor": "eyJpZCI6...", "sort_field": "updatedAt", "sort_direction": "desc"}}`

    Error Handling:
    400 covers `pageSize: Page size must be at least 1`, `pageSize: Page size
    cannot exceed 100`, `sortField: Invalid enum value. Expected 'favorites' |
    'name' | 'updatedAt' | 'visits', received '<invalidField>'`, `creatorId:
    Invalid uuid`, `userId: Invalid uuid`, `formErrors: Unrecognized key(s) in
    object: '<unknownParameter>'`, `onlySharedWithMe requires userId`,
    `onlySharedWithMe cannot be combined with onlyFavorites` and
    `onlySharedWithMe cannot be combined with folderId` — the last two are
    rejected locally before any request is sent. 404 is `User with id <uuid>
    does not exist` (a malformed UUID is a 400 instead). With an Organization
    API key, `onlyFavorites` and `onlySharedWithMe` require `user_id`; a
    Personal Access Token infers it from the token.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size}
        if params.include:
            query["include"] = ",".join(params.include)
        if params.labels:
            query["labels"] = ",".join(params.labels)
        if params.folder_id:
            query["folderId"] = params.folder_id
        if params.sort_field:
            query["sortField"] = params.sort_field
        if params.sort_direction:
            query["sortDirection"] = params.sort_direction
        if params.cursor:
            query["cursor"] = params.cursor
        if params.user_id:
            query["userId"] = params.user_id
        if params.creator_id:
            query["creatorId"] = params.creator_id

        payload = await get_client().request_json("GET", "/v1/documents", params=query)
        body = _record(payload)
        return cursor_paginated_response(
            items=_sub_list(body, "records"),
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_document_line,
            title="Documents",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_document",
    annotations=ToolAnnotations(
        title="Get Dashboard Document",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_document(params: GetDocumentInput) -> str:
    """Retrieve a dashboard document's configuration (name, filters, query presentations).

    Calls `GET /v1/documents/{documentId}`. The body is the round-trip shape the
    old `PUT` accepted, so it is still the quickest way to read a dashboard's
    filter configuration and query presentations in one call.

    Deprecated: superseded by `omni_get_document_state` (documents v2), which
    also reports draft state. Prefer it for new work.

    When to Use:
    - To inspect a dashboard's filters, refresh interval and query presentations.
    - To capture a dashboard's current configuration before editing it via a draft.

    When NOT to Use:
    - On workbook-only or analysis documents — the API answers `400`.
    - To list documents (use `omni_list_documents`) or to fetch the runnable
      query JSON (use `omni_get_document_queries`).

    Returns:
    A markdown summary — name, identifier, model ID, description, facet filters,
    refresh interval, filter order and one row per query presentation — or the
    complete configuration body when `response_format` is `json`.

    Examples:
    - `{"params": {"document_id": "12db1a0a"}}`
    - Full body: `{"params": {"document_id": "12db1a0a", "response_format": "json"}}`

    Error Handling:
    400 is `Analysis documents are not supported` (the document has no
    dashboard). 403 means the caller may not view the document. 404 is
    `Document with identifier "<documentId>" not found`.
    """
    try:
        payload = await get_client().request_json("GET", f"/v1/documents/{_path(params.document_id)}")
        body = _record(payload)

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        presentations = body.get("queryPresentations")
        presentation_rows: list[dict[str, Any]] = []
        if isinstance(presentations, list):
            for item in presentations:
                if not isinstance(item, dict):
                    continue
                query = _sub_dict(item, "query")
                fields = _sub_list(query, "fields")
                presentation_rows.append(
                    {
                        "Name": item.get("name", "unnamed"),
                        "ID": item.get("id", "N/A"),
                        "Chart": item.get("chartType", "N/A"),
                        "Table": query.get("table", "N/A"),
                        "Fields": len(fields),
                    }
                )
        filter_order = body.get("filterOrder")
        filter_text = ", ".join(str(name) for name in filter_order) if isinstance(filter_order, list) else "none"
        lines = [
            f"# {body.get('name', 'unnamed')}",
            "",
            f"- Identifier: `{params.document_id}`",
            f"- Model ID: `{body.get('modelId', 'unknown')}`",
            f"- Description: {body.get('description') or 'none'}",
            f"- Facet filters: {'enabled' if body.get('facetFilters') else 'disabled'}",
            f"- Refresh interval: {body.get('refreshInterval') if body.get('refreshInterval') else 'disabled'}",
            f"- Filter order: {filter_text or 'none'}",
            "",
            f"## Query presentations ({len(presentation_rows):,})",
            "",
            markdown_table(presentation_rows),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_document",
    annotations=ToolAnnotations(
        title="Create Document",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_document(params: CreateDocumentInput) -> str:
    """Create a new document on a model, optionally seeded with query presentations.

    Calls `POST /v1/documents`. Returns the new workbook and dashboard IDs plus
    the document identifier used in its URL.

    Deprecated: superseded by `omni_create_document_v2` (documents v2). Prefer
    it for new work.

    When to Use:
    - To create a dashboard/workbook programmatically on a known model.
    - To pin a specific URL identifier when promoting content between environments.

    When NOT to Use:
    - To copy an existing document — use `omni_duplicate_document`, which keeps
      the tiles and layout.
    - To edit an existing document — create a draft and patch it with the
      documents v2 tools.

    Returns:
    A confirmation string with the document name, identifier, workbook ID and
    dashboard ID.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "name": "Sales Dashboard"}}`
    - Pinned identifier: `{"params": {"model_id": "a1b2...", "name": "Sales Dashboard",
      "identifier": "sales-dashboard-2026", "description": "Q1 2026 sales performance metrics"}}`

    Error Handling:
    400 covers `modelId: Required`, `name: Required`, an invalid identifier
    format (lowercase letters, numbers, hyphens and underscores only; it may not
    start or end with a hyphen or underscore) and an identifier already in use by
    another document. The caller needs permission to create content on the
    model's connection.
    """
    try:
        body: dict[str, Any] = {"modelId": params.model_id, "name": params.name}
        if params.description is not None:
            body["description"] = params.description
        if params.identifier is not None:
            body["identifier"] = params.identifier
        if params.query_presentations is not None:
            body["queryPresentations"] = params.query_presentations

        payload = await get_client().request_json("POST", "/v1/documents", json_body=body)
        result = _record(payload)
        workbook = _sub_dict(result, "workbook")
        dashboard = _sub_dict(result, "dashboard")
        identifier = workbook.get("identifier", params.identifier or "unknown")
        return (
            f"Created document **{workbook.get('name', params.name)}** (identifier `{identifier}`) — "
            f"workbook `{workbook.get('id', 'unknown')}`, dashboard `{dashboard.get('id', 'none')}`."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_delete_document",
    annotations=ToolAnnotations(
        title="Delete Document",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_document(params: DeleteDocumentInput) -> str:
    """Delete a document, moving it to Trash.

    Calls `DELETE /v1/documents/{documentId}`. The document leaves every listing
    (unless `include: ["includeDeleted"]` is passed to `omni_list_documents`) and
    its schedules stop running.

    When to Use:
    - To retire a document the user has explicitly asked to delete.
    - To clean up documents created by a failed automation run.

    When NOT to Use:
    - To hide a document from most people — move it to a restricted folder with
      `omni_move_document` instead.
    - To discard in-progress edits — archive the draft with
      `omni_archive_document_draft` and keep the published document.

    Returns:
    A confirmation string naming the deleted document identifier.

    Examples:
    - `{"params": {"document_id": "12db1a0a"}}`

    Error Handling:
    404 is `Document with identifier "<documentId>" not found` — which is also
    what a second delete of the same document returns. 403 means the caller's
    permission level on the document is too low.
    """
    try:
        await get_client().request_json("DELETE", f"/v1/documents/{_path(params.document_id)}")
        return f"Deleted document `{params.document_id}` (moved to Trash)."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_move_document",
    annotations=ToolAnnotations(
        title="Move Document",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_move_document(params: MoveDocumentInput) -> str:
    """Move a document to another folder, or change its sharing scope.

    Calls `PUT /v1/documents/{documentId}/move`. Leaving `folder_path` unset
    moves the document to the root level, outside any folder.

    When to Use:
    - To file a document into a folder, or pull it back out to the root.
    - To flip a document between `organization` and `restricted` access along
      with its destination folder.

    When NOT to Use:
    - To grant one person access — use the document permission tools.
    - To copy rather than move — use `omni_duplicate_document`.

    Returns:
    A confirmation string naming the document and its destination.

    Examples:
    - Into a folder: `{"params": {"document_id": "12db1a0a", "folder_path": "/reports/2026/q1"}}`
    - To the root: `{"params": {"document_id": "12db1a0a"}}`
    - With a scope: `{"params": {"document_id": "12db1a0a", "folder_path": "/shared", "scope": "organization"}}`

    Error Handling:
    400 is `Scope "<scope>" and folder scope "<folderScope>" do not match` or
    `Invalid method`. 403 is `User-scoped API keys cannot act on behalf of other
    users`; moving into personal/restricted scope also requires the user's
    **Personal content access** setting (`omni_allows_personal_content`) to be
    enabled, otherwise the API answers `403 Forbidden`. 404 is `Document with
    identifier "<documentId>" not found` or `Folder with path <path> does not
    exist`.
    """
    try:
        body: dict[str, Any] = {"folderPath": params.folder_path}
        if params.scope is not None:
            body["scope"] = params.scope
        await get_client().request_json("PUT", f"/v1/documents/{_path(params.document_id)}/move", json_body=body)
        destination = f"`{params.folder_path}`" if params.folder_path else "the root level"
        scope_text = f" with scope **{params.scope}**" if params.scope else ""
        return f"Moved document `{params.document_id}` to {destination}{scope_text}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_duplicate_document",
    annotations=ToolAnnotations(
        title="Duplicate Document",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_duplicate_document(params: DuplicateDocumentInput) -> str:
    """Duplicate a published document into a new document.

    Calls `POST /v1/documents/{documentId}/duplicate`. Only published documents
    can be duplicated — duplicating a draft returns `404`.

    When to Use:
    - To branch off a copy of a dashboard before reworking it.
    - To template a report per team, region or customer.
    - To promote a document into another folder while keeping the original.

    When NOT to Use:
    - To move the original — use `omni_move_document`.
    - On a draft: publish it first, or duplicate the published version.

    Returns:
    A confirmation string with the new document's name, identifier, workbook ID
    and dashboard ID.

    Examples:
    - `{"params": {"document_id": "12db1a0a", "name": "My Dashboard Copy"}}`
    - Into a folder: `{"params": {"document_id": "12db1a0a", "name": "My Dashboard Copy",
      "folder_path": "/reports/2024/q4", "scope": "organization"}}`
    - On behalf of a user: `{"params": {"document_id": "12db1a0a", "name": "Copy", "user_id": "9e87..."}}`

    Error Handling:
    400 covers `name: Name cannot be empty`, `name: Name must be 255 characters
    or less` and `Scope "organization" and folder scope "restricted" do not
    match`. 403 covers `User does not have access to create content on the
    specified connection`, `User-scoped API keys can only be used to act on
    behalf of the authenticated user.` (so `user_id` requires an Organization API
    key) and `Document duplication is disabled` when the document's duplication
    permission is off; personal/restricted targets also require the user's
    **Personal content access** setting (`omni_allows_personal_content`). 404
    covers `Document with identifier "<documentId>" not found`, `Published
    document with id "<documentId>" does not exist` (raised when the target is a
    draft) and `Folder with path "<folderPath>" does not exist`.
    """
    try:
        body: dict[str, Any] = {"name": params.name}
        if params.folder_path is not None:
            body["folderPath"] = params.folder_path
        if params.scope is not None:
            body["scope"] = params.scope
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id

        payload = await get_client().request_json(
            "POST",
            f"/v1/documents/{_path(params.document_id)}/duplicate",
            params=query or None,
            json_body=body,
        )
        result = _record(payload)
        return (
            f"Duplicated document `{params.document_id}` into **{result.get('name', params.name)}** "
            f"(identifier `{result.get('identifier', 'unknown')}`) — workbook "
            f"`{result.get('workbookId', 'unknown')}`, dashboard `{result.get('dashboardId', 'none')}`."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_upgrade_dashboard_layout",
    annotations=ToolAnnotations(
        title="Upgrade Dashboard Layout",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_upgrade_dashboard_layout(params: UpgradeDashboardLayoutInput) -> str:
    """Upgrade a document to the advanced dashboard layout.

    Calls `POST /v1/documents/{documentId}/upgrade` — the same action as
    *File > Upgrade layout* in the UI. The operation is idempotent: a document
    already on the advanced layout is a no-op and reports `upgraded: false`. For
    published documents the upgrade runs through a draft-and-publish workflow
    automatically.

    When to Use:
    - To move a legacy dashboard onto the advanced layout before editing tiles.
    - To bulk-upgrade dashboards; re-running on an already-upgraded document is safe.

    When NOT to Use:
    - On workbook-only documents — with no dashboard the API answers `400`.
    - When a draft holds work you want to keep: publish or archive it first,
      because `clear_existing_draft` discards it.

    Returns:
    A confirmation string saying whether the layout was upgraded or was already
    advanced, with the document identifier.

    Examples:
    - `{"params": {"document_id": "abc123"}}`
    - Discard a blocking draft: `{"params": {"document_id": "abc123", "clear_existing_draft": true}}`

    Error Handling:
    400 means the document has no dashboard to upgrade. 401 means authentication
    is required; 403 means permission denied; 404 means the document was not
    found. 409 means a draft already exists for the published document — set
    `clear_existing_draft` to `true` to discard it and proceed.
    """
    try:
        body: dict[str, Any] = {}
        if params.clear_existing_draft is not None:
            body["clearExistingDraft"] = params.clear_existing_draft
        payload = await get_client().request_json(
            "POST", f"/v1/documents/{_path(params.document_id)}/upgrade", json_body=body
        )
        result = _record(payload)
        identifier = result.get("identifier", params.document_id)
        if result.get("upgraded"):
            return f"Upgraded document `{identifier}` to the advanced dashboard layout."
        return f"No change: document `{identifier}` already uses the advanced dashboard layout."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_transfer_document_ownership",
    annotations=ToolAnnotations(
        title="Transfer Document Ownership",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_transfer_document_ownership(params: TransferDocumentOwnershipInput) -> str:
    """Transfer ownership of a document to another organization member.

    Calls `PUT /v1/documents/{documentId}/transfer-ownership`. The named user is
    granted the `OWNER` role, every other `OWNER` is demoted to `MANAGER`, the
    new owner's explicit permits are revoked (owner access supersedes them), and
    the content search index is updated. The document's creator is unchanged and
    a restricted document stays in its folder. Transferring to the current owner
    is a no-op that still returns success.

    Deprecated: superseded by the document permission tools —
    `omni_grant_document_permissions` and `omni_update_document_permissions`.
    Prefer them for new work.

    When to Use:
    - To reassign a document when its owner leaves the team.
    - To hand a dashboard over to the team that maintains it.

    When NOT to Use:
    - To give someone edit access without ownership — grant a permission instead.
    - Before the target has explicit access: grant them a document permission
      first, or the API answers `New owner must have explicit document permission`.

    Returns:
    A confirmation string naming the document and the new owner's membership ID.

    Examples:
    - `{"params": {"document_id": "12db1a0a", "user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`

    Error Handling:
    400 covers `Invalid JSON`, `userId is required` and `New owner must have
    explicit document permission`. 403 is `Insufficient permissions` — the acting
    user needs `MANAGER` permissions or higher on the document. 404 is `Document
    with identifier "<documentId>" not found` or `User not found`.
    """
    try:
        await get_client().request_json(
            "PUT",
            f"/v1/documents/{_path(params.document_id)}/transfer-ownership",
            json_body={"userId": params.user_id},
        )
        return f"Transferred ownership of document `{params.document_id}` to membership `{params.user_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_document_draft",
    annotations=ToolAnnotations(
        title="Create Document Draft",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_create_document_draft(params: CreateDocumentDraftInput) -> str:
    """Create a draft of a document (or return the existing one).

    Calls `POST /v1/documents/{documentId}/draft`. A draft is an isolated copy of
    the document — workbook and dashboard — that can be modified without touching
    the published version. Each branch can hold one draft per document; if a
    draft already exists for the document (or branch), that draft's identifier is
    returned instead of a second one being created. Requires **Editor**
    permissions or higher on the document.

    Deprecated: superseded by `omni_patch_document_draft` (documents v2), which
    creates the draft and applies changes in one call. Prefer it for new work.

    When to Use:
    - To start editing a published document safely.
    - To open a draft on a branch before making model-linked changes.

    When NOT to Use:
    - To read the current draft state — use the documents v2 draft tools.
    - To discard a draft — use `omni_archive_document_draft`.

    Returns:
    A confirmation string with the draft identifier (which may be a pre-existing
    draft).

    Examples:
    - `{"params": {"document_id": "12db1a0a"}}`
    - On a branch: `{"params": {"document_id": "12db1a0a", "branch_id": "a9bc51d2-1234-5678-9abc-def012345678"}}`

    Error Handling:
    400 means the document is not eligible for the publishing workflow. 401 means
    the API key is invalid or missing. 403 is `Permission denied - EDITOR role
    required`. 404 is `Document with identifier <documentId> not found`.
    """
    try:
        body: dict[str, Any] = {}
        if params.branch_id:
            body["branchId"] = params.branch_id
        payload = await get_client().request_json(
            "POST", f"/v1/documents/{_path(params.document_id)}/draft", json_body=body
        )
        result = _record(payload)
        branch_text = f" on branch `{params.branch_id}`" if params.branch_id else ""
        return (
            f"Draft `{result.get('identifier', 'unknown')}` is ready for document "
            f"`{params.document_id}`{branch_text} (an existing draft is returned unchanged)."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_archive_document_draft",
    annotations=ToolAnnotations(
        title="Archive Document Draft",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_archive_document_draft(params: ArchiveDocumentDraftInput) -> str:
    """Archive a document's current draft, discarding its unpublished changes.

    Calls `DELETE /v1/documents/{documentId}/draft`. Archived drafts move to the
    **Archived** section of the document's **Drafts** drawer and are retained for
    30 days. Passing `branch_id` archives that branch's draft instead of the one
    not attached to a branch.

    When to Use:
    - To throw away unpublished edits and return to the published version.
    - To clear a blocking draft before an operation that refuses to overwrite one.

    When NOT to Use:
    - To keep the changes — publish the draft instead.
    - To delete the whole document — use `omni_delete_document`.

    Returns:
    A confirmation string naming the document (and branch) whose draft was archived.

    Examples:
    - `{"params": {"document_id": "12db1a0a"}}`
    - On a branch: `{"params": {"document_id": "12db1a0a", "branch_id": "a9bc51d2-1234-5678-9abc-def012345678"}}`

    Error Handling:
    404 is `Document with identifier <documentId> not found` or `Draft with id
    <documentId> does not exist` — the latter is also what archiving twice
    returns. The caller needs edit permission on the document.
    """
    try:
        body: dict[str, Any] = {}
        if params.branch_id:
            body["branchId"] = params.branch_id
        await get_client().request_json("DELETE", f"/v1/documents/{_path(params.document_id)}/draft", json_body=body)
        branch_text = f" on branch `{params.branch_id}`" if params.branch_id else ""
        return f"Archived the draft of document `{params.document_id}`{branch_text} (retained for 30 days)."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_list_document_drafts",
    annotations=ToolAnnotations(
        title="List Document Drafts",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_document_drafts(params: ListDocumentDraftsInput) -> str:
    """List the drafts attached to a published document.

    Calls `GET /v1/documents/{documentId}/drafts`. A document can have one draft
    per branch plus one draft not attached to a branch. Results are sorted by the
    draft's `updatedAt`, descending. Requires **Viewer** permissions or higher.

    When to Use:
    - To check whether a draft is blocking an upgrade or a rename.
    - To find a `draft_id` for `omni_remove_dashboard_from_document`.
    - To see which drafts are out of date against the published document.

    When NOT to Use:
    - To read a draft's contents — use the documents v2 draft tools.
    - On an unpublished document: the API answers `404`.

    Returns:
    A markdown table — draft identifier, branch, status, out-of-date flag,
    created/updated timestamps via `iso_or_na`, creator and last editor — or the
    raw draft objects when `response_format` is `json`.

    Examples:
    - `{"params": {"document_id": "ecd01fe5"}}`
    - Include archived: `{"params": {"document_id": "ecd01fe5", "include": "archived"}}`

    Error Handling:
    400 is `Invalid include parameter value`. 401 means the API key is invalid or
    missing. 403 is `You do not have permission to view this document.` —
    **Viewer** permissions or higher are required. 404 is `Document with
    identifier <identifier> not found` or `Published document with id
    <identifier> does not exist` when the document exists but was never published.
    """
    try:
        query: dict[str, Any] = {}
        if params.include:
            query["include"] = params.include
        payload = await get_client().request_json(
            "GET", f"/v1/documents/{_path(params.document_id)}/drafts", params=query or None
        )
        drafts = payload if isinstance(payload, list) else []

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"documentId": params.document_id, "count": len(drafts), "drafts": drafts}))

        rows: list[dict[str, Any]] = []
        for draft in drafts:
            if not isinstance(draft, dict):
                continue
            branch = draft.get("branch")
            branch_text = "none"
            if isinstance(branch, dict):
                branch_text = f"{branch.get('name') or 'unnamed'} ({branch.get('id', '?')})"
            created_by = draft.get("createdBy")
            edited_by = draft.get("lastEditedBy")
            rows.append(
                {
                    "Draft": draft.get("identifier", "unknown"),
                    "Published": draft.get("publishedIdentifier", "unknown"),
                    "Branch": branch_text,
                    "Status": draft.get("status", "unknown"),
                    "Out of date": "yes" if draft.get("draftOutOfDate") else "no",
                    "Created": iso_or_na(draft.get("createdAt")),
                    "Updated": iso_or_na(draft.get("updatedAt")),
                    "Created by": created_by.get("name", "unknown") if isinstance(created_by, dict) else "unknown",
                    "Last edited by": edited_by.get("name", "unknown") if isinstance(edited_by, dict) else "unknown",
                }
            )
        scope_note = "including archived" if params.include == "archived" else "active only"
        lines = [
            f"# Drafts of document `{params.document_id}`",
            "",
            f"Showing **{len(rows):,}** draft(s) ({scope_note}).",
            "",
            markdown_table(rows),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_document_queries",
    annotations=ToolAnnotations(
        title="Get Document Queries",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_document_queries(params: GetDocumentQueriesInput) -> str:
    """Retrieve the queries behind a document, ready to re-run through the query API.

    Calls `GET /v1/documents/{documentId}/queries`. Each entry carries the query
    UUID, its name, a URL to the sheet in the workbook, and the query JSON that
    the run-query tool accepts as-is.

    When to Use:
    - To re-run a dashboard's queries programmatically and get the rows back.
    - To inspect which fields, filters and tables a dashboard depends on.
    - To copy a query definition as the starting point for a new one.

    When NOT to Use:
    - To read the dashboard's layout or filter bar — use `omni_get_document`.
    - To execute the queries: this only returns their definitions.

    Returns:
    A markdown summary — one row per query with name, ID and URL — or the full
    query JSON for each query when `response_format` is `json` (pass that JSON
    straight to the run-query tool).

    Examples:
    - `{"params": {"document_id": "12db1a0a"}}`
    - Runnable JSON: `{"params": {"document_id": "12db1a0a", "response_format": "json"}}`

    Error Handling:
    403 is `User cannot view document`. 404 is `Document with id <identifier>
    does not exist`.
    """
    try:
        payload = await get_client().request_json("GET", f"/v1/documents/{_path(params.document_id)}/queries")
        body = _record(payload)
        queries = _sub_list(body, "queries")

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(
                to_json({"documentId": params.document_id, "count": len(queries), "queries": queries})
            )

        rows: list[dict[str, Any]] = []
        for item in queries:
            if not isinstance(item, dict):
                continue
            query = _sub_dict(item, "query")
            fields = _sub_list(query, "fields")
            rows.append(
                {
                    "Name": item.get("name", "unnamed"),
                    "ID": item.get("id", "unknown"),
                    "Table": query.get("table", "N/A"),
                    "Fields": len(fields),
                    "URL": item.get("url", ""),
                }
            )
        lines = [
            f"# Queries in document `{params.document_id}`",
            "",
            f"Showing **{len(rows):,}** query(ies). Re-request with `response_format: json` to get the "
            "query JSON for the run-query tool.",
            "",
            markdown_table(rows),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_remove_dashboard_from_document",
    annotations=ToolAnnotations(
        title="Remove Dashboard From Document",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_remove_dashboard_from_document(params: RemoveDashboardFromDocumentInput) -> str:
    """Remove the dashboard from a draft, leaving a workbook-only document.

    Calls `DELETE /v2/documents/{documentId}/draft/{draftId}/dashboard`. The
    change lands on the draft only; publish the draft to apply it to the
    published document. A document that is already workbook-only is unchanged.
    **Note:** the document's schedules are removed when the draft is published.

    When to Use:
    - To turn a dashboard document back into a plain workbook.
    - To drop a dashboard that duplicates another before publishing.

    When NOT to Use:
    - To delete individual tiles — patch the draft with the documents v2 tools.
    - To delete the document — use `omni_delete_document`.
    - Without a draft: create one first (`omni_create_document_draft` or
      `omni_patch_document_draft`) and pass its identifier as `draft_id`.

    Returns:
    A confirmation string with the document name, the published identifier and
    the draft identifier the change was applied to.

    Examples:
    - `{"params": {"document_id": "abc123", "draft_id": "def456"}}`

    Error Handling:
    401 means authentication is required. 403 means insufficient permissions to
    update the document. 404 means the document or the draft was not found. 405
    is method not allowed. 409 means the specified document is not a published
    document. 422 means the document is an app, not a dashboard.
    """
    try:
        payload = await get_client().request_json(
            "DELETE",
            f"/v2/documents/{_path(params.document_id)}/draft/{_path(params.draft_id)}/dashboard",
        )
        result = _record(payload)
        return (
            f"Removed the dashboard from draft `{result.get('draftIdentifier', params.draft_id)}` of document "
            f"**{result.get('name', 'unnamed')}** (`{result.get('identifier', params.document_id)}`). "
            "Publish the draft to apply it — schedules are removed on publish."
        )
    except Exception as exc:
        return handle_api_error(exc)
