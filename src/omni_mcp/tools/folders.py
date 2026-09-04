"""Tools for the folder, folder permission, folder label, and label endpoints.

Covers 14 operations across four tags: Folders (create/list/update/delete),
Folder permissions (grant/update/get/revoke), Folder labels (bulk update), and
Labels (create/list/get/update/delete). See CONTRIBUTING.md for the house
tool contract; `src/omni_mcp/tools/health.py` is the worked example.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, cursor_paginated_response, markdown_table, to_json, truncate_result
from omni_mcp.server import mcp

#: One line on what this module covers, for the generated README catalogue.
MODULE_SUMMARY = "Folders, folder permissions, folder labels, labels"

FolderScope = Literal["organization", "restricted"]
FolderSortField = Literal["favorites", "name", "path"]
SortDirection = Literal["asc", "desc"]
ContentRole = Literal["NO_ACCESS", "VIEWER", "EDITOR", "MANAGER", "OWNER"]
LabelName = Annotated[str, Field(min_length=2, max_length=25)]


def _non_none(**fields: Any) -> dict[str, Any]:
    """Build a JSON body/query dict, dropping keys whose value is `None`."""
    return {key: value for key, value in fields.items() if value is not None}


def _folder_path(folder_id: str, *suffix: str) -> str:
    path = f"/v1/folders/{quote(folder_id, safe='')}"
    for segment in suffix:
        path += f"/{segment}"
    return path


def _label_path(label_name: str) -> str:
    return f"/v1/labels/{quote(label_name, safe='')}"


def _format_folder_line(folder: Mapping[str, Any]) -> str:
    owner = folder.get("owner") if isinstance(folder.get("owner"), dict) else {}
    owner_name = owner.get("name") if isinstance(owner, dict) else None
    line = (
        f"- **{folder.get('name', 'unnamed')}** (`{folder.get('id')}`) — path `{folder.get('path', '')}`, "
        f"scope **{folder.get('scope', '?')}**"
    )
    if owner_name:
        line += f", owner {owner_name}"
    labels = folder.get("labels")
    if labels:
        line += f", labels: {', '.join(str(label) for label in labels)}"
    count = folder.get("_count")
    if isinstance(count, dict):
        line += f", docs: {count.get('documents', '?')}, favorites: {count.get('favorites', '?')}"
    url = folder.get("url")
    if url:
        line += f", url: {url}"
    return line


def _format_label_line(label: Mapping[str, Any]) -> str:
    flags = [
        flag for flag, present in (("verified", label.get("verified")), ("homepage", label.get("homepage"))) if present
    ]
    flag_text = f" [{', '.join(flags)}]" if flags else ""
    color = label.get("color")
    color_text = f", color {color}" if color else ""
    description = label.get("description")
    description_text = f" — {description}" if description else ""
    usage = label.get("usage_count", 0)
    return f"- **{label.get('name', 'unnamed')}**{flag_text} (usage: {usage}){color_text}{description_text}"


def _render_label(label: Mapping[str, Any]) -> str:
    lines = [
        f"# Label {label.get('name', 'unnamed')}",
        "",
        f"- Verified: **{bool(label.get('verified'))}**",
        f"- Homepage: **{bool(label.get('homepage'))}**",
        f"- Usage count: **{label.get('usage_count', 0)}**",
        f"- Color: `{label.get('color') or 'N/A'}`",
        f"- Description: {label.get('description') or 'N/A'}",
    ]
    return "\n".join(lines)


def _targets_description(user_ids: list[str] | None, user_group_ids: list[str] | None) -> str:
    parts = []
    if user_ids:
        parts.append(f"{len(user_ids)} user(s)")
    if user_group_ids:
        parts.append(f"{len(user_group_ids)} group(s)")
    return " and ".join(parts) if parts else "no targets"


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------


class ListFoldersInput(BaseModel):
    """Input for `omni_list_folders`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    include: str | None = Field(
        default=None,
        description="Comma-separated list of additional fields to include: `_count` (document/favorite counts), "
        "`labels` (folder labels), `onlySharedWithMe` (folders explicitly shared with `user_id`; cannot be "
        "combined with `owner_id` or `path`).",
    )
    path: str | None = Field(
        default=None,
        description="Filter folders by path. A single wildcard is allowed at the end: `*` for direct children "
        "only (e.g. `blob-sales/*`), `**` for all descendants recursively (e.g. `blob-sales/**`).",
    )
    labels: list[str] | None = Field(default=None, description="Labels to filter by (sent as a comma-separated list).")
    scope: FolderScope | None = Field(
        default=None,
        description="Scope of folders to retrieve. Defaults to `organization`. When `restricted`, an "
        "Organization API key requires `owner_id`; a Personal Access Token infers it from the caller.",
    )
    sort_field: FolderSortField | None = Field(
        default=None, description="Field to sort by: `favorites`, `name`, or `path`. Defaults to `name`."
    )
    sort_direction: SortDirection | None = Field(default=None, description="Sort direction. Defaults to `desc`.")
    cursor: str | None = Field(default=None, description="Cursor from a previous page, for pagination positioning.")
    page_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Records per page. The spec documents a minimum of 1; this server also caps it at 100.",
    )
    owner_id: str | None = Field(
        default=None,
        description="UUID of an organization membership to scope results to. Required when `scope=restricted` "
        "and using an Organization API key; optional (auto-inferred) for a Personal Access Token.",
    )
    user_id: str | None = Field(
        default=None,
        description="UUID of a standard or embed user; scopes results to folders that user can view. Required "
        "when `include` contains `onlyFavorites` and using an Organization API key.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for raw records."
    )


class CreateFolderInput(BaseModel):
    """Input for `omni_create_folder`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Name of the folder.")
    parent_folder_id: str | None = Field(
        default=None,
        description="UUID of the parent folder. Maximum nesting depth is 7 levels; a 400 is returned when the "
        "limit is reached. Use `omni_list_folders` to retrieve folder IDs.",
    )
    scope: FolderScope | None = Field(
        default=None,
        description="Scope of the folder. If omitted and no parent exists, defaults to `organization`; if "
        "omitted and a parent exists, inherits the parent's scope. A child folder's scope must match its "
        "parent's scope.",
    )
    user_id: str | None = Field(
        default=None, description="UUID of the folder owner. Use `omni_list_users` to retrieve user IDs."
    )
    breadcrumb_root: bool | None = Field(
        default=None,
        description="When `true`, breadcrumbs for this folder and its descendants start at this folder, hiding "
        "ancestor folders and the scope crumb. Useful to scope embed navigation to a subtree.",
    )


class UpdateFolderInput(BaseModel):
    """Input for `omni_update_folder`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="UUID of the folder to update.")
    name: str | None = Field(
        default=None, description="New display name for the folder. At least one of `name` or `path` is required."
    )
    path: str | None = Field(
        default=None,
        description="New URL path segment (lowercased automatically); updating it cascades to all descendant "
        "folder paths. Only alphanumeric characters and dashes are allowed; the reserved words `favorite`, "
        "`labels`, `move`, `share`, `transfer` cannot be used alone. At least one of `name` or `path` is required.",
    )
    resolve_path_conflict: bool | None = Field(
        default=None,
        description="When `true`, automatically resolves path conflicts by appending a numeric suffix. When "
        "`false` (default), a conflicting path returns `409 Conflict`.",
    )
    breadcrumb_root: bool | None = Field(
        default=None,
        description="When `true`, breadcrumbs start at this folder. Omit to leave unchanged; pass `false` to clear it.",
    )

    @model_validator(mode="after")
    def _require_name_or_path(self) -> UpdateFolderInput:
        if self.name is None and self.path is None:
            raise ValueError("At least one of 'name' or 'path' must be provided.")
        return self


class DeleteFolderInput(BaseModel):
    """Input for `omni_delete_folder`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="UUID of the folder to delete.")
    force: bool = Field(
        default=False,
        description="When `true`, recursively archives documents (to trash) and deletes sub-folders. Limited "
        "to 100 total items across the folder tree; SSO embed users cannot force-delete entity folders.",
    )


@mcp.tool(
    name="omni_list_folders",
    annotations=ToolAnnotations(
        title="List Folders", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def omni_list_folders(params: ListFoldersInput) -> str:
    """List folders within the organization, with filtering, sorting, and cursor pagination.

    Behavior depends on the API key type: Organization-scoped keys get full access (and must pass
    `owner_id` for `scope=restricted`); Personal Access Tokens get permission-filtered access matching
    the UI, auto-inferring `owner_id` for `scope=restricted` when omitted.

    When to Use:
    - To browse folders before creating, updating, or deleting one — folder IDs are required elsewhere.
    - To find a folder by name/path prefix, or to list a user's own restricted folders.
    - To audit which folders carry a given label (via the `labels` filter).

    When NOT to Use:
    - To read a folder's permissions — use `omni_get_folder_permissions`.
    - To list labels themselves — use `omni_list_labels`.

    Returns:
    A markdown summary of matching folders (name, id, path, scope, owner, labels, counts) with a
    pagination footer, or the raw `records`/`pageInfo` payload when `response_format` is `json`. On
    failure, an `Error ...` string.

    Examples:
    - List organization folders: `{"params": {}}`
    - Children of a path: `{"params": {"path": "blob-sales/*"}}`
    - A user's restricted folders: `{"params": {"scope": "restricted", "owner_id": "<uuid>"}}`
    - Next page: `{"params": {"cursor": "eyJpZCI6ImZvbGRlcjEyMyJ9"}}`

    Error Handling:
    400 covers an invalid `page_size`/`sort_field`/`include`/path pattern, or an `onlySharedWithMe`
    combined with `owner_id`/`path`. 403 means a Personal Access Token tried to read another user's
    restricted folders, or personal content access is disabled for the caller. 404 means the given
    `path` does not exist.
    """
    try:
        query: dict[str, Any] = _non_none(
            include=params.include,
            path=params.path,
            labels=",".join(params.labels) if params.labels else None,
            scope=params.scope,
            sortField=params.sort_field,
            sortDirection=params.sort_direction,
            cursor=params.cursor,
            ownerId=params.owner_id,
            userId=params.user_id,
        )
        query["pageSize"] = params.page_size
        payload = await get_client().request_json("GET", "/v1/folders", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_folder_line,
            title="Folders",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_folder",
    annotations=ToolAnnotations(
        title="Create Folder", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
)
async def omni_create_folder(params: CreateFolderInput) -> str:
    """Create a new folder, optionally nested under a parent.

    Folders can be nested up to 7 levels. Folder paths are automatically generated based on
    hierarchy, so a child folder's path includes its parents' paths.

    When to Use:
    - To set up a new place to organize documents or sub-folders.
    - To create a personal (`restricted`) folder scoped to a specific owner.

    When NOT to Use:
    - To rename or move an existing folder — use `omni_update_folder`.
    - To grant access to a folder — use `omni_grant_folder_permissions`.

    Returns:
    A confirmation string naming the created folder, its id, path, and scope. On failure, an
    `Error ...` string.

    Examples:
    - Top-level folder: `{"params": {"name": "Blob Sales"}}`
    - Nested, restricted, owned folder:
      `{"params": {"name": "My Reports", "parent_folder_id": "<uuid>", "scope": "restricted",
      "user_id": "<uuid>"}}`

    Error Handling:
    400 means nesting depth exceeded 7 levels, `name` was missing, or a child folder's scope did not
    match its parent's. 403 means a user-scoped API key tried to act on behalf of another user, or the
    target user's personal content access is disabled. 404 means `parent_folder_id` or `user_id` does
    not exist.
    """
    try:
        body: dict[str, Any] = {"name": params.name}
        body.update(
            _non_none(
                parentFolderId=params.parent_folder_id,
                scope=params.scope,
                userId=params.user_id,
                breadcrumbRoot=params.breadcrumb_root,
            )
        )
        payload = await get_client().request_json("POST", "/v1/folders", json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return (
            f"Created folder **{result.get('name', params.name)}** (id `{result.get('id')}`) "
            f"at path `{result.get('path')}` — scope **{result.get('scope') or 'unknown'}**."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_update_folder",
    annotations=ToolAnnotations(
        title="Update Folder", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
)
async def omni_update_folder(params: UpdateFolderInput) -> str:
    """Update a folder's name and/or path segment. Requires Editor permissions or higher on the folder.

    Either or both of `name`/`path` can be updated in one request. Name-only updates do not touch
    descendant folders; path updates automatically update every descendant folder's path.

    When to Use:
    - To rename a folder.
    - To move/rename a folder's URL path segment (cascades to descendants).

    When NOT to Use:
    - To change folder labels — use `omni_bulk_update_folder_labels`.
    - To change who can access the folder — use the folder permission tools.

    Returns:
    A confirmation string with the folder's id, new name, and new path. On failure, an `Error ...`
    string.

    Examples:
    - Rename only: `{"params": {"folder_id": "<uuid>", "name": "Blob Sales Reports"}}`
    - Change path and auto-resolve conflicts:
      `{"params": {"folder_id": "<uuid>", "path": "sales-reports", "resolve_path_conflict": true}}`

    Error Handling:
    400 means neither `name` nor `path` was provided, the path used a reserved word alone (`favorite`,
    `labels`, `move`, `share`, `transfer`) or non-alphanumeric/dash characters. 403 means the caller
    lacks Editor permissions on the folder. 404 means the folder does not exist. 409 means the
    requested path already exists at this location and `resolve_path_conflict` was not set.
    """
    try:
        body = _non_none(
            name=params.name,
            path=params.path,
            resolvePathConflict=params.resolve_path_conflict,
            breadcrumbRoot=params.breadcrumb_root,
        )
        payload = await get_client().request_json("PATCH", _folder_path(params.folder_id), json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return f"Updated folder `{params.folder_id}` — now **{result.get('name')}** at path `{result.get('path')}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_delete_folder",
    annotations=ToolAnnotations(
        title="Delete Folder", readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
    ),
)
async def omni_delete_folder(params: DeleteFolderInput) -> str:
    """Delete a folder. By default, only empty folders can be deleted.

    With `force=true`: all documents in the folder tree are archived (soft-deleted to trash),
    sub-folders are deleted recursively (deepest first), up to 100 total items, all in a single
    transaction — a partial failure rolls back completely. SSO embed users cannot force-delete
    entity folders.

    When to Use:
    - To remove an empty folder.
    - To remove a folder tree and archive its documents, with `force=true`.

    When NOT to Use:
    - To permanently purge documents — this only archives them to trash.

    Returns:
    A confirmation string that the folder was deleted. On failure, an `Error ...` string.

    Examples:
    - Delete an empty folder: `{"params": {"folder_id": "<uuid>"}}`
    - Force-delete a tree: `{"params": {"folder_id": "<uuid>", "force": true}}`

    Error Handling:
    400 means the folder is non-empty and `force` was not set, or (with `force=true`) the tree exceeds
    the 100-item limit. 403 means the caller lacks delete permission, or an SSO embed user attempted a
    force-delete on an entity folder. 404 means the folder does not exist.
    """
    try:
        query = {"force": params.force} if params.force else None
        payload = await get_client().request_json("DELETE", _folder_path(params.folder_id), params=query)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        suffix = " (force)" if params.force else ""
        if result.get("success") is False:
            return f"Delete folder `{params.folder_id}` reported no success in the response body."
        return f"Deleted folder `{params.folder_id}`{suffix}."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# Folder labels
# --------------------------------------------------------------------------


class BulkUpdateFolderLabelsInput(BaseModel):
    """Input for `omni_bulk_update_folder_labels`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="UUID of the folder whose labels are being changed.")
    add: list[LabelName] | None = Field(
        default=None, description="Label names to add to the folder. Each must already exist (2-25 characters)."
    )
    remove: list[LabelName] | None = Field(
        default=None, description="Label names to remove from the folder (2-25 characters)."
    )
    user_id: str | None = Field(
        default=None,
        description="Optional user ID that attributes the action to the specified user. Requires an "
        "Organization API key.",
    )

    @model_validator(mode="after")
    def _validate_add_remove(self) -> BulkUpdateFolderLabelsInput:
        if not self.add and not self.remove:
            raise ValueError("At least one of 'add' or 'remove' must contain at least one label.")
        if self.add and self.remove:
            overlap = {label.lower() for label in self.add} & {label.lower() for label in self.remove}
            if overlap:
                raise ValueError(f"Labels cannot be included in both 'add' and 'remove': {', '.join(sorted(overlap))}")
        return self


@mcp.tool(
    name="omni_bulk_update_folder_labels",
    annotations=ToolAnnotations(
        title="Bulk Update Folder Labels",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_bulk_update_folder_labels(params: BulkUpdateFolderLabelsInput) -> str:
    """Add and/or remove multiple labels from a folder in a single all-or-nothing request.

    Label matching is case-insensitive. Labels must already exist (create them first with
    `omni_create_label`) and cannot appear in both `add` and `remove` in the same request. Adding or
    removing **Verified** or **Homepage** labels requires Organization Admin permissions.

    When to Use:
    - To tag or untag a folder with existing labels in one atomic call.

    When NOT to Use:
    - To create a brand-new label — use `omni_create_label` first.
    - To change a folder's name or path — use `omni_update_folder`.

    Returns:
    A confirmation string listing the folder's resulting labels. On failure, an `Error ...` string.

    Examples:
    - Add labels: `{"params": {"folder_id": "<uuid>", "add": ["production", "reviewed"]}}`
    - Remove labels: `{"params": {"folder_id": "<uuid>", "remove": ["draft"]}}`
    - Add and remove together:
      `{"params": {"folder_id": "<uuid>", "add": ["approved"], "remove": ["pending-review"]}}`

    Error Handling:
    400 means the request was empty, a label appeared in both `add` and `remove`, or a label name was
    outside the 2-25 character range. 403 means the caller lacks Manager/Owner permissions on the
    folder, or lacks Organization Admin permissions to modify Verified/Homepage labels. 404 means the
    folder does not exist, or a referenced label does not exist (create it first).
    """
    try:
        query = _non_none(userId=params.user_id)
        body = _non_none(add=params.add, remove=params.remove)
        payload = await get_client().request_json(
            "PATCH", _folder_path(params.folder_id, "labels"), params=query or None, json_body=body
        )
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        labels = result.get("labels") or []
        labels_text = ", ".join(str(label) for label in labels) if labels else "none"
        return f"Updated labels on folder `{params.folder_id}`: {labels_text}."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# Folder permissions
# --------------------------------------------------------------------------


class GrantFolderPermissionsInput(BaseModel):
    """Input for `omni_grant_folder_permissions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="UUID of the folder to grant permissions on.")
    role: ContentRole = Field(
        ...,
        description="Content role to assign: `NO_ACCESS`, `VIEWER` (view contents), `EDITOR` (edit/add content), "
        "`MANAGER` (edit, add content, manage permissions), or `OWNER` (Manager permissions plus access "
        "requests; the folder must always have at least one owner).",
    )
    access_boost: bool | None = Field(
        default=None, description="When `true`, AccessBoost is enabled for the folder for these principals."
    )
    user_ids: list[str] | None = Field(
        default=None,
        description="User IDs (UUIDs) to grant the role to. Either `user_ids` or `user_group_ids` is required.",
    )
    user_group_ids: list[str] | None = Field(
        default=None,
        description="User group IDs to grant the role to. Either `user_ids` or `user_group_ids` is required.",
    )

    @model_validator(mode="after")
    def _require_targets(self) -> GrantFolderPermissionsInput:
        if not self.user_ids and not self.user_group_ids:
            raise ValueError("Either 'user_ids' or 'user_group_ids' must be provided.")
        return self


class UpdateFolderPermissionsInput(BaseModel):
    """Input for `omni_update_folder_permissions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="UUID of the folder whose permissions are being updated.")
    role: ContentRole = Field(
        ...,
        description="Content role to assign: `NO_ACCESS`, `VIEWER`, `EDITOR`, `MANAGER`, or `OWNER` (the folder "
        "must always have at least one owner).",
    )
    access_boost: bool | None = Field(
        default=None, description="When `true`, AccessBoost is enabled for the folder for these principals."
    )
    user_ids: list[str] | None = Field(
        default=None,
        description="User IDs (UUIDs) whose role is being updated. Either `user_ids` or `user_group_ids` is required.",
    )
    user_group_ids: list[str] | None = Field(
        default=None,
        description="User group IDs whose role is being updated. Either `user_ids` or `user_group_ids` is required.",
    )

    @model_validator(mode="after")
    def _require_targets(self) -> UpdateFolderPermissionsInput:
        if not self.user_ids and not self.user_group_ids:
            raise ValueError("Either 'user_ids' or 'user_group_ids' must be provided.")
        return self


class GetFolderPermissionsInput(BaseModel):
    """Input for `omni_get_folder_permissions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="UUID of the folder to read permissions for.")
    user_id: str = Field(..., description="UUID of the user to retrieve folder permissions for.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable table, `json` for raw permits."
    )


class RevokeFolderPermissionsInput(BaseModel):
    """Input for `omni_revoke_folder_permissions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    folder_id: str = Field(..., description="UUID of the folder to revoke permissions on.")
    user_ids: list[str] | None = Field(
        default=None,
        description="User IDs (UUIDs) to revoke permissions from. Either `user_ids` or `user_group_ids` is required.",
    )
    user_group_ids: list[str] | None = Field(
        default=None,
        description="User group IDs to revoke permissions from. Either `user_ids` or `user_group_ids` is required.",
    )

    @model_validator(mode="after")
    def _require_targets(self) -> RevokeFolderPermissionsInput:
        if not self.user_ids and not self.user_group_ids:
            raise ValueError("Either 'user_ids' or 'user_group_ids' must be provided.")
        return self


@mcp.tool(
    name="omni_grant_folder_permissions",
    annotations=ToolAnnotations(
        title="Grant Folder Permissions",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_grant_folder_permissions(params: GrantFolderPermissionsInput) -> str:
    """Grant a content role on a folder to one or more users or user groups.

    When to Use:
    - To give new users or groups access to a folder's contents.

    When NOT to Use:
    - To change an existing grant's role — use `omni_update_folder_permissions` (this tool also works,
      but the update tool communicates intent more precisely).
    - To read current permissions — use `omni_get_folder_permissions`.
    - To remove access entirely — use `omni_revoke_folder_permissions`.

    Returns:
    A confirmation string naming the role and how many users/groups it was granted to. On failure, an
    `Error ...` string.

    Examples:
    - Grant Editor to two users: `{"params": {"folder_id": "<uuid>", "role": "EDITOR",
      "user_ids": ["<uuid1>", "<uuid2>"]}}`
    - Grant Viewer to a group with AccessBoost: `{"params": {"folder_id": "<uuid>", "role": "VIEWER",
      "user_group_ids": ["<group-id>"], "access_boost": true}}`

    Error Handling:
    400 means neither `user_ids` nor `user_group_ids` was provided, or a UUID was invalid. 403 means
    the caller lacks Manager permissions on the folder. 404 means the folder does not exist.
    """
    try:
        body: dict[str, Any] = {"role": params.role}
        body.update(
            _non_none(accessBoost=params.access_boost, userIds=params.user_ids, userGroupIds=params.user_group_ids)
        )
        payload = await get_client().request_json("POST", _folder_path(params.folder_id, "permissions"), json_body=body)
        targets = _targets_description(params.user_ids, params.user_group_ids)
        if isinstance(payload, dict) and payload.get("success") is False:
            return (
                f"Grant **{params.role}** on folder `{params.folder_id}` for {targets} reported no success "
                "in the response body."
            )
        return f"Granted **{params.role}** on folder `{params.folder_id}` to {targets}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_update_folder_permissions",
    annotations=ToolAnnotations(
        title="Update Folder Permissions",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_update_folder_permissions(params: UpdateFolderPermissionsInput) -> str:
    """Update the content role already granted to users or groups on a folder.

    When to Use:
    - To change the role of users/groups that already have some access to the folder.

    When NOT to Use:
    - To grant access to principals with none yet — use `omni_grant_folder_permissions`.
    - To remove access entirely — use `omni_revoke_folder_permissions`.

    Returns:
    A confirmation string naming the new role and how many users/groups it applies to. On failure, an
    `Error ...` string.

    Examples:
    - Downgrade a user to Viewer: `{"params": {"folder_id": "<uuid>", "role": "VIEWER",
      "user_ids": ["<uuid>"]}}`
    - Promote a group to Manager: `{"params": {"folder_id": "<uuid>", "role": "MANAGER",
      "user_group_ids": ["<group-id>"]}}`

    Error Handling:
    400 means neither `user_ids` nor `user_group_ids` was provided, or a UUID was invalid. 403 means
    the caller lacks Manager permissions on the folder. 404 means the folder does not exist.
    """
    try:
        body: dict[str, Any] = {"role": params.role}
        body.update(
            _non_none(accessBoost=params.access_boost, userIds=params.user_ids, userGroupIds=params.user_group_ids)
        )
        payload = await get_client().request_json(
            "PATCH", _folder_path(params.folder_id, "permissions"), json_body=body
        )
        targets = _targets_description(params.user_ids, params.user_group_ids)
        if isinstance(payload, dict) and payload.get("success") is False:
            return (
                f"Update of permissions on folder `{params.folder_id}` for {targets} reported no success "
                "in the response body."
            )
        return f"Updated permissions on folder `{params.folder_id}` to **{params.role}** for {targets}."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_folder_permissions",
    annotations=ToolAnnotations(
        title="Get Folder Permissions",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_folder_permissions(params: GetFolderPermissionsInput) -> str:
    """Retrieve the folder permissions that apply to a specific user.

    When to Use:
    - To inspect what access (direct role, AccessBoost, ownership) a user has on a folder before
      granting, updating, or revoking permissions.

    When NOT to Use:
    - To list every folder a user can see — use `omni_list_folders` with `user_id`.

    Returns:
    A markdown table of permission holders (id, name, type, role, ownership, AccessBoost), or the raw
    `permits` payload when `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"folder_id": "<uuid>", "user_id": "<uuid>"}}`
    - JSON: `{"params": {"folder_id": "<uuid>", "user_id": "<uuid>", "response_format": "json"}}`

    Error Handling:
    400 means `user_id` was missing or invalid. 403 means the caller lacks Manager permissions on the
    folder. 404 means the folder does not exist.
    """
    try:
        query = {"userId": params.user_id}
        payload = await get_client().request_json("GET", _folder_path(params.folder_id, "permissions"), params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        permits = body.get("permits") or []

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(
                to_json({"folderId": params.folder_id, "userId": params.user_id, "permits": permits})
            )

        rows: list[dict[str, Any]] = []
        for permit in permits:
            direct = permit.get("direct") if isinstance(permit.get("direct"), dict) else {}
            rows.append(
                {
                    "id": permit.get("id"),
                    "name": permit.get("name"),
                    "type": permit.get("type"),
                    "role": direct.get("role"),
                    "isOwner": direct.get("isOwner"),
                    "accessBoost": direct.get("accessBoost"),
                    "description": permit.get("description"),
                }
            )
        lines = [
            f"# Folder Permissions — folder `{params.folder_id}`, user `{params.user_id}`",
            "",
            markdown_table(rows),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_revoke_folder_permissions",
    annotations=ToolAnnotations(
        title="Revoke Folder Permissions",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_revoke_folder_permissions(params: RevokeFolderPermissionsInput) -> str:
    """Revoke folder permissions from one or more users or user groups.

    When to Use:
    - To remove a user's or group's access to a folder entirely.

    When NOT to Use:
    - To merely downgrade a role — use `omni_update_folder_permissions` with `NO_ACCESS` or a lower
      role instead, if the principal should still resolve in permission listings.

    Returns:
    A confirmation string naming how many users/groups had access revoked. On failure, an `Error ...`
    string.

    Examples:
    - `{"params": {"folder_id": "<uuid>", "user_ids": ["<uuid>"]}}`
    - `{"params": {"folder_id": "<uuid>", "user_group_ids": ["<group-id>"]}}`

    Error Handling:
    400 means neither `user_ids` nor `user_group_ids` was provided, or a UUID was invalid. 403 means
    the caller lacks Manager permissions on the folder. 404 means the folder does not exist.
    """
    try:
        body = _non_none(userIds=params.user_ids, userGroupIds=params.user_group_ids)
        payload = await get_client().request_json(
            "DELETE", _folder_path(params.folder_id, "permissions"), json_body=body
        )
        targets = _targets_description(params.user_ids, params.user_group_ids)
        if isinstance(payload, dict) and payload.get("success") is False:
            return (
                f"Revocation of permissions on folder `{params.folder_id}` for {targets} reported no success "
                "in the response body."
            )
        return f"Revoked permissions on folder `{params.folder_id}` from {targets}."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


class CreateLabelInput(BaseModel):
    """Input for `omni_create_label`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(
        ...,
        min_length=2,
        max_length=25,
        description="Label name (2-25 characters). Case-insensitive: 'Production' and 'production' are the same label.",
    )
    color: str | None = Field(default=None, max_length=9, description="Hex color for the label, e.g. `#0366d6`.")
    description: str | None = Field(default=None, max_length=500, description="Description of the label.")
    verified: bool | None = Field(
        default=None,
        description="If `true`, documents with this label are marked verified/curated. Requires Organization "
        "Admin permissions.",
    )
    homepage: bool | None = Field(
        default=None,
        description="If `true`, documents with this label display on the instance's Homepage. Requires "
        "Organization Admin permissions.",
    )
    user_id: str | None = Field(
        default=None,
        description="Optional user ID that attributes the action to the specified user. Requires an "
        "Organization API key.",
    )


class ListLabelsInput(BaseModel):
    """Input for `omni_list_labels` (no filters — returns every label in the organization)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for raw records."
    )


class GetLabelInput(BaseModel):
    """Input for `omni_get_label`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    label_name: str = Field(..., description="The label name. Lookup is case-insensitive.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for the raw label."
    )


class UpdateLabelInput(BaseModel):
    """Input for `omni_update_label`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    label_name: str = Field(..., description="Current name of the label to update. Lookup is case-insensitive.")
    name: str | None = Field(default=None, min_length=2, max_length=25, description="New name for the label.")
    verified: bool | None = Field(
        default=None,
        description="If `true`, documents with the label are marked Verified. Requires Organization Admin permissions.",
    )
    homepage: bool | None = Field(
        default=None,
        description="If `true`, documents with the label are visible on the Homepage. Requires Organization "
        "Admin permissions.",
    )
    color: str | None = Field(default=None, max_length=9, description="Hex color for the label, e.g. `#0366d6`.")
    description: str | None = Field(default=None, max_length=500, description="Description of the label.")
    user_id: str | None = Field(
        default=None,
        description="Optional user ID that attributes the action to the specified user. Requires an "
        "Organization API key.",
    )


class DeleteLabelInput(BaseModel):
    """Input for `omni_delete_label`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    label_name: str = Field(..., description="Name of the label to delete. Lookup is case-insensitive.")
    user_id: str | None = Field(
        default=None,
        description="Optional user ID that attributes the action to the specified user. Requires an "
        "Organization API key.",
    )


@mcp.tool(
    name="omni_create_label",
    annotations=ToolAnnotations(
        title="Create Label", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
)
async def omni_create_label(params: CreateLabelInput) -> str:
    """Create a new label for organizing and categorizing folders and documents.

    Any user can create basic labels; Organization Admin permissions are required to create
    **Verified** (`verified: true`) or **Homepage** (`homepage: true`) labels.

    When to Use:
    - To define a new label before applying it to folders (`omni_bulk_update_folder_labels`) or
      documents.

    When NOT to Use:
    - To apply an existing label to a folder — use `omni_bulk_update_folder_labels`.

    Returns:
    A confirmation string naming the created label. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"name": "Dev", "color": "#0366d6", "description": "Documents based on dev schemas"}}`
    - Verified label (Organization Admin only): `{"params": {"name": "Production", "verified": true}}`

    Error Handling:
    400 means the name was shorter than 2 or longer than 25 characters. 403 means the caller lacks
    Organization Admin permissions (required for `verified`/`homepage`) or lacks permission to manage
    labels at all. 409 means a label with this name already exists (case-insensitive).
    """
    try:
        query = _non_none(userId=params.user_id)
        body: dict[str, Any] = {"name": params.name}
        body.update(
            _non_none(
                color=params.color, description=params.description, verified=params.verified, homepage=params.homepage
            )
        )
        payload = await get_client().request_json("POST", "/v1/labels", params=query or None, json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return f"Created label **{result.get('name', params.name)}** (usage: {result.get('usage_count', 0)})."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_list_labels",
    annotations=ToolAnnotations(
        title="List Labels", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def omni_list_labels(params: ListLabelsInput) -> str:
    """List every label defined in the organization. Takes no filters — the API returns all of them.

    When to Use:
    - To see what labels exist before applying one to a folder, or before creating a new one (label
      names must be unique, case-insensitively).

    When NOT to Use:
    - To look up a single label's details — use `omni_get_label` (avoids scanning the full list).

    Returns:
    A markdown summary of every label (name, verified/homepage flags, usage count, color,
    description), or the raw `labels` array when `response_format` is `json`. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {}}`
    - `{"params": {"response_format": "json"}}`

    Error Handling:
    This endpoint has no documented error responses beyond the standard auth failures.
    """
    try:
        payload = await get_client().request_json("GET", "/v1/labels")
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("labels") or [],
            page_info=None,
            fmt=params.response_format,
            item_formatter=_format_label_line,
            title="Labels",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_label",
    annotations=ToolAnnotations(
        title="Get Label", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def omni_get_label(params: GetLabelInput) -> str:
    """Retrieve a single label by name.

    When to Use:
    - To check a label's current verified/homepage status, usage count, color, or description.

    When NOT to Use:
    - To browse all labels — use `omni_list_labels`.

    Returns:
    A markdown summary of the label's details, or the raw label payload when `response_format` is
    `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"label_name": "Production"}}`
    - `{"params": {"label_name": "In Review", "response_format": "json"}}`

    Error Handling:
    404 means no label with this name exists (lookup is case-insensitive).
    """
    try:
        payload = await get_client().request_json("GET", _label_path(params.label_name))
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        return truncate_result(_render_label(body))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_update_label",
    annotations=ToolAnnotations(
        title="Update Label", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def omni_update_label(params: UpdateLabelInput) -> str:
    """Update an existing label — rename it, or change its Verified/Homepage/color/description.

    Only include the fields being changed. Users can update basic labels they created; Organization
    Admin permissions are required to modify **Verified** or **Homepage** status.

    When to Use:
    - To rename a label, recolor it, or promote/demote its Verified or Homepage status.

    When NOT to Use:
    - To apply or remove a label from a folder — use `omni_bulk_update_folder_labels`.

    Returns:
    A confirmation string naming the label's (possibly new) name. On failure, an `Error ...` string.

    Examples:
    - Rename: `{"params": {"label_name": "Draft", "name": "In Review"}}`
    - Recolor and describe:
      `{"params": {"label_name": "Production", "color": "#DDDDDD", "description": "Ready for review"}}`

    Error Handling:
    400 means the new name was shorter than 2 or longer than 25 characters. 403 means the caller lacks
    permission to manage labels, or lacks Organization Admin permissions to modify Verified/Homepage.
    404 means no label with this name exists. 409 means the new name already exists (case-insensitive).
    """
    try:
        query = _non_none(userId=params.user_id)
        body = _non_none(
            name=params.name,
            verified=params.verified,
            homepage=params.homepage,
            color=params.color,
            description=params.description,
        )
        payload = await get_client().request_json(
            "PUT", _label_path(params.label_name), params=query or None, json_body=body
        )
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return f"Updated label `{params.label_name}` — now **{result.get('name', params.label_name)}**."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_delete_label",
    annotations=ToolAnnotations(
        title="Delete Label", readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
    ),
)
async def omni_delete_label(params: DeleteLabelInput) -> str:
    """Delete a label from the organization.

    Any user can delete basic labels; Organization Admin permissions are required to delete
    **Verified** or **Homepage** labels. Labels currently applied to any document cannot be deleted —
    remove the label from every document first.

    When to Use:
    - To permanently remove a label that is no longer needed and applied to nothing.

    When NOT to Use:
    - To remove a label from one folder only — use `omni_bulk_update_folder_labels` with `remove`.

    Returns:
    A confirmation string that the label was deleted. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"label_name": "Draft"}}`

    Error Handling:
    403 means the caller lacks permission to manage labels, or lacks Organization Admin permissions
    for a Verified/Homepage label. 404 means no label with this name exists. 409 means the label is
    still applied to documents and must be removed from all of them first.
    """
    try:
        query = _non_none(userId=params.user_id)
        await get_client().request_json("DELETE", _label_path(params.label_name), params=query or None)
        return f"Deleted label `{params.label_name}`."
    except Exception as exc:
        return handle_api_error(exc)
