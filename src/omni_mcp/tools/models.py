"""Tools for the model, model YAML, schema, cache, and topic endpoints.

These tools drive the **branch mode / promotion** workflow, which is the reason
this area exists. The loop an agent runs looks like this:

1. `omni_list_models` with `model_kind="SHARED"` finds the production model, and
   with `model_kind="BRANCH"` + `base_model_id=<shared model>` lists the branch
   models (each branch is itself a model, so a *branch id* is a model id).
2. `omni_get_model_yaml` (with `branch_id` when working on a branch) fetches the
   files. Checksums come back with them.
3. Edit the YAML locally.
4. `omni_update_model_yaml` sends each file back **with the checksum it was
   fetched with**. If someone else changed the file meanwhile the API rejects
   the write with `File has been modified since it was fetched`, instead of
   silently discarding their edit — so always fetch, edit, and send back the
   checksum rather than writing blind.
5. `omni_validate_model` (with the same `branch_id`) checks the branch before it
   is promoted, and `omni_get_topic` inspects a single topic's fields and joins.
6. Promotion itself — commit, push, pull request, merge — lives in the
   model-git tools, not here.

Schema models are a different lifecycle: `omni_create_model` (kind `SCHEMA`),
then `omni_refresh_model_schema` to load the database structure, then shared
models built on top of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Self
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

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

#: File suffixes the YAML write/delete endpoints accept for regular files.
YAML_FILE_SUFFIXES: tuple[str, ...] = (".topic", ".composite_topic", ".view")

#: Special YAML file names that carry no suffix (writes only, never deletes).
YAML_SPECIAL_FILES: frozenset[str] = frozenset({"model", "relationships"})

#: YAML file contents, exempt from the input models' whitespace stripping: a
#: stripped trailing newline or leading indentation would change the file that
#: is written (and its checksum) behind the caller's back.
YamlText = Annotated[str, StringConstraints(strip_whitespace=False)]

#: How many YAML files are rendered inline before the tool only lists them.
MAX_INLINE_YAML_FILES = 3


class ModelKind(StrEnum):
    """Model kinds that can be created."""

    SCHEMA = "SCHEMA"
    SHARED = "SHARED"
    SHARED_EXTENSION = "SHARED_EXTENSION"
    BRANCH = "BRANCH"


class ModelKindFilter(StrEnum):
    """Model kinds accepted by the list filter (a superset of `ModelKind`)."""

    SCHEMA = "SCHEMA"
    SHARED = "SHARED"
    SHARED_EXTENSION = "SHARED_EXTENSION"
    WORKBOOK = "WORKBOOK"
    BRANCH = "BRANCH"
    QUERY = "QUERY"
    TOPIC = "TOPIC"
    FIELD_PICKER_TOPIC = "FIELD_PICKER_TOPIC"


class ModelSortField(StrEnum):
    """Sortable fields on the model list."""

    NAME = "name"
    MODEL_KIND = "modelKind"
    CONNECTION_ID = "connectionId"
    BASE_MODEL_ID = "baseModelId"
    CREATED_AT = "createdAt"
    UPDATED_AT = "updatedAt"


class SortDirection(StrEnum):
    """Sort direction."""

    ASC = "asc"
    DESC = "desc"


class ModelInclude(StrEnum):
    """Optional extra payload on the model list."""

    ACTIVE_BRANCHES = "activeBranches"


class YamlReadMode(StrEnum):
    """Modes accepted when reading model YAML."""

    EXTENSION = "extension"
    STAGED = "staged"
    COMBINED = "combined"


class YamlWriteMode(StrEnum):
    """Modes accepted when writing or deleting model YAML."""

    COMBINED = "combined"
    EXTENSION = "extension"
    STAGED = "staged"
    MERGED = "merged"
    HISTORY = "history"


class ViewDeleteMode(StrEnum):
    """Modes accepted by the delete-view endpoint (upper case, unlike YAML modes)."""

    COMBINED = "COMBINED"
    MERGED = "MERGED"
    EXTENSION = "EXTENSION"


def _path(value: str) -> str:
    """Percent-encode one URL path segment.

    Branch names may contain `/` (the API's git-compatible regex is `[\\w\\-/]+`),
    so a nested name such as `team/project` is sent as `team%2Fproject`.
    """
    return quote(value, safe="")


def _validate_yaml_file_name(name: str, *, allow_special: bool) -> str:
    """Return `name` if the YAML endpoints accept it, else raise `ValueError`."""
    text = name.strip()
    if not text:
        raise ValueError("YAML file names cannot be empty.")
    if allow_special and text in YAML_SPECIAL_FILES:
        return text
    if text.endswith(YAML_FILE_SUFFIXES):
        return text
    allowed = "`model`, `relationships`, or " if allow_special else ""
    raise ValueError(
        f"Invalid YAML file name {text!r}. Allowed: {allowed}a file ending in .topic, .composite_topic, or .view."
    )


def _as_dict(payload: Any) -> dict[str, Any]:
    """Narrow a decoded JSON payload to a dict (empty when it is not one)."""
    return payload if isinstance(payload, dict) else {}


def _as_list(payload: Any) -> list[Any]:
    """Narrow a decoded JSON payload to a list (empty when it is not one)."""
    return payload if isinstance(payload, list) else []


def _format_model(item: Mapping[str, Any]) -> str:
    """One markdown bullet for a model record."""
    line = (
        f"- **{item.get('name') or 'unnamed'}** (`{item.get('id')}`) — kind `{item.get('modelKind') or 'unknown'}`, "
        f"connection `{item.get('connectionId') or 'n/a'}`, base model `{item.get('baseModelId') or 'n/a'}`, "
        f"updated {iso_or_na(item.get('updatedAt'))}"
    )
    if item.get("deletedAt"):
        line += f", deleted {iso_or_na(item.get('deletedAt'))}"
    branches = item.get("branches")
    if isinstance(branches, list) and branches:
        rendered = ", ".join(
            f"{branch.get('name') or 'unnamed'} (`{branch.get('id')}`)"
            for branch in branches
            if isinstance(branch, dict)
        )
        line += f"\n  - active branches: {rendered}"
    return line


class ListModelsInput(BaseModel):
    """Input for `omni_list_models`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    base_model_id: str | None = Field(
        default=None,
        description="Filter models by base model ID. Combine with `model_kind=BRANCH` to list one shared model's branches.",
    )
    connection_id: str | None = Field(default=None, description="Filter models by connection ID.")
    include_deleted: bool | None = Field(default=None, description="If `true`, include deleted (archived) models.")
    model_id: str | None = Field(default=None, description="Filter models by a specific model ID.")
    model_kind: ModelKindFilter | None = Field(
        default=None,
        description=(
            "Filter by model kind: `SCHEMA` (mirrors the database), `SHARED` (the governed model workbooks are "
            "built on), `SHARED_EXTENSION`, `WORKBOOK`, `BRANCH` (a development layer over a shared model), "
            "`QUERY`, `TOPIC`, `FIELD_PICKER_TOPIC`."
        ),
    )
    name: str | None = Field(default=None, description="Filter models by name.")
    explorable: bool | None = Field(
        default=None,
        description=(
            "When `true`, returns only `SHARED` and `SHARED_EXTENSION` models usable as a workbook base "
            "(`allowAsWorkbookBase: true`)."
        ),
    )
    sort_field: ModelSortField | None = Field(
        default=None,
        description="Field to sort by: `name`, `modelKind`, `connectionId`, `baseModelId`, `createdAt`, `updatedAt` (API default `updatedAt`).",
    )
    sort_direction: SortDirection | None = Field(
        default=None, description="Sort direction: `asc` or `desc` (API default `desc`)."
    )
    include: ModelInclude | None = Field(
        default=None,
        description="Extra fields to include. `activeBranches` adds each model's active branch models (with their IDs).",
    )
    page_size: int = Field(default=20, ge=1, le=100, description="Records per page (1-100). API default is 20.")
    cursor: str | None = Field(default=None, description="Pagination cursor returned by a previous page.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable list, `json` for the raw records and page info.",
    )


@mcp.tool(
    name="omni_list_models",
    annotations=ToolAnnotations(
        title="List Models",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_models(params: ListModelsInput) -> str:
    """List models with their metadata, optionally including active branches.

    Calls `GET /v1/models`. This is the entry point for every other tool in this
    area: model IDs, branch IDs, connection IDs and base model IDs all come from
    here. A branch is itself a model (`modelKind=BRANCH`), so the `id` of a
    branch record is exactly the `branch_id` the YAML, validate, refresh and
    schema tools expect.

    When to Use:
    - To find a model ID by name, kind or connection before any other call.
    - To list the branches of a shared model (`model_kind="BRANCH"` plus
      `base_model_id=<shared model id>`, or `include="activeBranches"`).
    - To find models a workbook can be built on (`explorable=true`).

    When NOT to Use:
    - To read a model's contents — use `omni_get_model_yaml` or `omni_get_topic`.
    - To list the schemas inside one model — use `omni_list_model_schemas`.

    Returns:
    A paginated markdown list (name, ID, kind, connection, base model, updated
    time, and active branches when requested) with the next cursor, or the raw
    records when `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - Shared models: `{"params": {"model_kind": "SHARED", "page_size": 50}}`
    - Branches of one model: `{"params": {"model_kind": "BRANCH", "base_model_id": "6a1b…"}}`
    - With branches inline: `{"params": {"include": "activeBranches"}}`
    - Next page: `{"params": {"cursor": "eyJpZCI6…"}}`

    Error Handling:
    400 means a filter value is invalid (check the `model_kind` / `sort_field`
    enums); 403 means the API key cannot see models on this instance. Any
    caller with model access may list models. Never loop pages inside one call —
    pass the returned cursor back instead.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size}
        if params.cursor:
            query["cursor"] = params.cursor
        if params.base_model_id:
            query["baseModelId"] = params.base_model_id
        if params.connection_id:
            query["connectionId"] = params.connection_id
        if params.include_deleted is not None:
            query["includeDeleted"] = params.include_deleted
        if params.model_id:
            query["modelId"] = params.model_id
        if params.model_kind is not None:
            query["modelKind"] = params.model_kind.value
        if params.name:
            query["name"] = params.name
        if params.explorable is not None:
            query["explorable"] = params.explorable
        if params.sort_field is not None:
            query["sortField"] = params.sort_field.value
        if params.sort_direction is not None:
            query["sortDirection"] = params.sort_direction.value
        if params.include is not None:
            query["include"] = params.include.value

        payload = await get_client().request_json("GET", "/v1/models", params=query)
        body = _as_dict(payload)
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_model,
            title="Models",
        )
    except Exception as exc:
        return handle_api_error(exc)


class CreateModelInput(BaseModel):
    """Input for `omni_create_model`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(description="ID of the connection the model is based on (UUID). Required.")
    model_kind: ModelKind | None = Field(
        default=None,
        description=(
            "Type of model (API default `SCHEMA`): `SCHEMA` mirrors the database structure; `SHARED` is the "
            "universal, governed model workbooks are based on; `SHARED_EXTENSION` extends a `SHARED` model "
            "(per user attribute or per department); `BRANCH` is a development layer over the shared model."
        ),
    )
    model_name: str | None = Field(default=None, description="Name of the model.")
    base_model_id: str | None = Field(
        default=None, description="**Applicable to branch and extension models.** ID of the base model."
    )
    access_grants: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Access grants for the model. Each item has the shape "
            '`{"name": "<grant name>", "accessBoostable": true|false}`. API default is an empty list.'
        ),
    )
    allow_as_workbook_base: bool | None = Field(
        default=None,
        description=(
            "**Only applicable to `SHARED_EXTENSION` models** (API default `false`). If `true`, the extension "
            "model appears in the model selector and users and AI can query it; if `false` it is hidden and "
            "exploration, query and AI chat access are disabled."
        ),
    )
    use_isolated_branches: bool | None = Field(
        default=None,
        description=(
            "**Only applicable to `SHARED_EXTENSION` models** (API default `false`). If `true`, branches are "
            "shown on the extension model page instead of the parent shared model."
        ),
    )


@mcp.tool(
    name="omni_create_model",
    annotations=ToolAnnotations(
        title="Create Model",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_model(params: CreateModelInput) -> str:
    """Create a model on a connection (schema, shared, extension or branch).

    Calls `POST /v1/models`. The documented workflow is: create the `SCHEMA`
    model for a connection, load the database structure into it with
    `omni_refresh_model_schema`, then create the `SHARED` model(s) on top of it.

    When to Use:
    - To stand up a new connection's schema model, once, before refreshing it.
    - To create a shared model, or to bulk-create `SHARED_EXTENSION` models
      (departmental or user-attribute driven variants of the shared model).

    When NOT to Use:
    - To create a branch for day-to-day modelling work — branches are normally
      created in the model IDE or by the git tools; this endpoint's `BRANCH`
      kind is for programmatic setups only.
    - To change an existing model's name — use `omni_rename_model`.

    Returns:
    A confirmation string with the new model's ID, kind and name, plus the next
    step. On failure, an `Error ...` string.

    Examples:
    - Schema model: `{"params": {"connection_id": "3f1c…", "model_kind": "SCHEMA", "model_name": "Warehouse"}}`
    - Extension model: `{"params": {"connection_id": "3f1c…", "model_kind": "SHARED_EXTENSION", "base_model_id": "6a1b…", "model_name": "Finance", "allow_as_workbook_base": true}}`
    - With an access grant: `{"params": {"connection_id": "3f1c…", "access_grants": [{"name": "region", "accessBoostable": true}]}}`

    Error Handling:
    400 with `Schema model already exists for the connection` means the
    connection already has its one schema model; `Schema model does not exist
    when creating a non-schema model` means the schema model must be created
    (and refreshed) first; `Shared model cannot be created when branch schema
    refresh is enabled` means the instance's branch-based schema refresh setting
    blocks this. 403 means the key lacks Connection Admin permission on the
    connection.
    """
    try:
        request_body: dict[str, Any] = {"connectionId": params.connection_id}
        if params.model_kind is not None:
            request_body["modelKind"] = params.model_kind.value
        if params.model_name:
            request_body["modelName"] = params.model_name
        if params.base_model_id:
            request_body["baseModelId"] = params.base_model_id
        if params.access_grants is not None:
            request_body["accessGrants"] = params.access_grants
        if params.allow_as_workbook_base is not None:
            request_body["allowAsWorkbookBase"] = params.allow_as_workbook_base
        if params.use_isolated_branches is not None:
            request_body["useIsolatedBranches"] = params.use_isolated_branches

        payload = await get_client().request_json("POST", "/v1/models", json_body=request_body)
        body = _as_dict(payload)
        if body.get("error"):
            return f"Error: the API reported `{body.get('error')}` while creating the model. {body.get('message') or ''}".strip()

        model = _as_dict(body.get("model"))
        kind = model.get("modelKind") or (params.model_kind.value if params.model_kind else "SCHEMA")
        next_step = (
            "Next: load the database structure with `omni_refresh_model_schema`."
            if kind == "SCHEMA"
            else "Next: add YAML with `omni_update_model_yaml`, then check it with `omni_validate_model`."
        )
        return (
            f"Created model **{model.get('name') or params.model_name or 'unnamed'}** "
            f"(id `{model.get('id')}`, kind `{kind}`) on connection `{params.connection_id}`. {next_step}"
        )
    except Exception as exc:
        return handle_api_error(exc)


class RenameModelInput(BaseModel):
    """Input for `omni_rename_model`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="ID of the model to rename (UUID).")
    name: str = Field(
        min_length=1,
        description=(
            "The new name for the model, e.g. `customer_metrics_v2`. Must be unique among active models with the "
            "same `modelKind` and `baseModelId`. For branch models it must also match the git-compatible regex "
            "`[\\w\\-/]+` and must not conflict with an existing git ref path."
        ),
    )


@mcp.tool(
    name="omni_rename_model",
    annotations=ToolAnnotations(
        title="Rename Model",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_rename_model(params: RenameModelInput) -> str:
    """Rename a shared, extension or branch model.

    Calls `PATCH /v1/models/{modelId}`. Useful for bulk maintenance of shared
    extension models created through the API, and for renaming a working branch
    before it is promoted.

    When to Use:
    - To rename a shared or shared extension model without opening the UI.
    - To give a branch a meaningful name before its pull request is opened.

    When NOT to Use:
    - On workbook models or query models — the endpoint rejects them.
    - On a branch that already has an open pull request; renaming would desync
      the model from the git remote and the API refuses it.

    Returns:
    A confirmation string with the model ID, the new name and the model kind.
    On failure, an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "6a1b…", "name": "customer_metrics_v2"}}`
    - Branch: `{"params": {"model_id": "0d2e…", "name": "team/pricing-fix"}}`

    Error Handling:
    400 messages are literal: `Cannot rename models of kind WORKBOOK`,
    `Cannot rename models of kind QUERY`, `Model name must be unique`,
    `Branch name must match git-compatible regex [\\w\\-/]+`, `Branch name would
    create git ref path conflict` (e.g. renaming to `team` when `team/project`
    exists), and `Cannot rename branch with open pull request`. 404 is `Model
    not found`. Permissions: **Connection Admin** for shared and shared
    extension models, **Modeler** or higher for branch models.
    """
    try:
        payload = await get_client().request_json(
            "PATCH", f"/v1/models/{_path(params.model_id)}", json_body={"name": params.name}
        )
        body = _as_dict(payload)
        kind = body.get("modelKind") or "unknown"
        return (
            f"Renamed model `{body.get('id') or params.model_id}` to **{body.get('name') or params.name}** "
            f"(kind `{kind}`)."
        )
    except Exception as exc:
        return handle_api_error(exc)


class ArchiveModelInput(BaseModel):
    """Input for `omni_archive_model`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(
        description="ID of the `SHARED` or `SHARED_EXTENSION` model to archive (UUID). Other kinds are rejected."
    )


@mcp.tool(
    name="omni_archive_model",
    annotations=ToolAnnotations(
        title="Archive Model",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_archive_model(params: ArchiveModelInput) -> str:
    """Archive (soft-delete) a shared or shared extension model and its content.

    Calls `DELETE /v1/models/{modelId}`. Only `SHARED` and `SHARED_EXTENSION`
    models can be archived here. Archiving takes the model, all of its content
    and every child extension model built on it to the trash; for shared models
    the git configuration is removed as well. The model is recoverable from the
    trash, but restoring it does **not** restore its content — that must be
    restored separately.

    When to Use:
    - To retire a shared or extension model that is no longer used, accepting
      that its workbooks and dashboards go to the trash with it.

    When NOT to Use:
    - To delete a branch — use `omni_delete_model_branch`.
    - To remove one topic or view — use `omni_delete_model_yaml_file` or
      `omni_delete_model_view`.
    - On schema, workbook or query models: the API rejects those kinds.

    Returns:
    A confirmation string naming the archived model ID and what went with it.
    On failure, an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "6a1b…"}}`

    Error Handling:
    400 `Cannot delete models of this kind` is returned for anything other than
    `SHARED` / `SHARED_EXTENSION` — use the delete-branch tool for branches.
    404 is `Model not found` or `Model already deleted`. Requires **Connection
    Admin** permissions.
    """
    try:
        payload = await get_client().request_json("DELETE", f"/v1/models/{_path(params.model_id)}")
        body = _as_dict(payload)
        if body.get("success") is False:
            return f"Error: the API did not archive model `{params.model_id}` (`success: false`)."
        return (
            f"Archived model `{params.model_id}` — the model, its content and any child extension models were "
            "moved to the trash (recoverable; restoring the model does not restore its content). For shared "
            "models the git configuration was removed."
        )
    except Exception as exc:
        return handle_api_error(exc)


class GetModelYamlInput(BaseModel):
    """Input for `omni_get_model_yaml`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The unique identifier of the model (UUID).")
    branch_id: str | None = Field(
        default=None,
        description=(
            "ID of the branch to read YAML from. Only valid for shared models in `combined` mode. Branch IDs come "
            "from `omni_list_models` with `model_kind=BRANCH`."
        ),
    )
    file_name: str | None = Field(
        default=None,
        description="Filter YAML files by name. Accepts a plain string or a regex pattern, e.g. `orders.view` or `.*\\.topic`.",
    )
    mode: YamlReadMode | None = Field(
        default=None,
        description=(
            "Mode used to retrieve YAML (API default `combined`): `extension` returns extension-only format, "
            "`staged` returns staged changes, `combined` returns the combined YAML."
        ),
    )
    include_checksums: bool = Field(
        default=True,
        description=(
            "If `true`, the response includes per-file checksums for concurrency control. Defaults to `true` here "
            "(the API default is `false`) because `omni_update_model_yaml` needs these checksums to avoid "
            "overwriting someone else's concurrent edit."
        ),
    )
    schema_name: str | None = Field(
        default=None,
        description=(
            "Restrict the returned YAML to views from this single schema (the API's `includeSchemas` parameter; "
            "one schema name only). Relationships are preserved even when they reference views outside it. "
            "Discover names with `omni_list_model_schemas`."
        ),
    )
    fully_resolved: bool | None = Field(
        default=None,
        description=(
            "If `true`, returns YAML with any `extends` usage resolved — matching what runs at query time. "
            "API default `false`, which returns the YAML as authored in the model files."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a file list plus inline YAML, `json` for the raw `files`/`checksums` payload.",
    )


@mcp.tool(
    name="omni_get_model_yaml",
    annotations=ToolAnnotations(
        title="Get Model YAML",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_model_yaml(params: GetModelYamlInput) -> str:
    """Read a model's YAML files, with the checksums needed to write them back.

    Calls `GET /v1/models/{modelId}/yaml`. This is step one of the edit loop:
    **fetch (with checksums) → edit → send back with the checksum** via
    `omni_update_model_yaml`. The checksum is what makes a write fail loudly
    with `File has been modified since it was fetched` instead of silently
    discarding a colleague's change.

    When to Use:
    - Before editing any model file, to get its current content and checksum.
    - To read a branch's YAML (`branch_id`) while working in branch mode.
    - To inspect one file (`file_name`) or one schema's views (`schema_name`)
      without pulling the whole model.

    When NOT to Use:
    - To get a topic's resolved fields and joins — `omni_get_topic` is far
      smaller and already structured.
    - To list schema names — use `omni_list_model_schemas`.

    Returns:
    A markdown table of the returned files (path, size in bytes, checksum),
    followed by the YAML itself in fenced blocks when a single file is returned
    or a `file_name` filter was given. Large results are truncated — narrow with
    `file_name` or `schema_name`. `json` returns the raw payload (`files`,
    `checksums`, `version`, `viewNames`).

    Examples:
    - Whole model: `{"params": {"model_id": "6a1b…"}}`
    - One file on a branch: `{"params": {"model_id": "6a1b…", "branch_id": "0d2e…", "file_name": "orders.topic"}}`
    - One schema's views: `{"params": {"model_id": "6a1b…", "schema_name": "ANALYTICS"}}`
    - Query-time YAML: `{"params": {"model_id": "6a1b…", "fully_resolved": true}}`

    Error Handling:
    400 `Schema models do not have branches` (drop `branch_id`) or `Branches are
    not valid for workbook models with mode=extension, staged`. 403 is
    `Permission denied` or `Feature not enabled`. 404 is `Model with id
    <modelId> does not exist` or `Branch does not exist`. 500 `Failed to get
    model YAML` is server-side — retry, and narrow the request.
    """
    try:
        query: dict[str, Any] = {"includeChecksums": params.include_checksums}
        if params.branch_id:
            query["branchId"] = params.branch_id
        if params.file_name:
            query["fileName"] = params.file_name
        if params.mode is not None:
            query["mode"] = params.mode.value
        if params.schema_name:
            query["includeSchemas"] = params.schema_name
        if params.fully_resolved is not None:
            query["fullyResolved"] = params.fully_resolved

        payload = await get_client().request_json("GET", f"/v1/models/{_path(params.model_id)}/yaml", params=query)
        body = _as_dict(payload)

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        files = _as_dict(body.get("files"))
        checksums = _as_dict(body.get("checksums"))
        header = [
            f"# Model YAML — `{params.model_id}`",
            "",
            f"- Branch: `{params.branch_id}`" if params.branch_id else "- Branch: none (model itself)",
            f"- Mode: `{params.mode.value if params.mode else 'combined'}`",
            f"- YAML version: `{body.get('version', 'n/a')}`",
            f"- Files returned: **{len(files):,}**",
            "",
        ]
        if not files:
            header.append("_No YAML files matched._")
            return truncate_result("\n".join(header))

        rows = [
            {
                "file": name,
                "bytes": len(content) if isinstance(content, str) else 0,
                "checksum": checksums.get(name, "not requested"),
            }
            for name, content in files.items()
        ]
        header.append(markdown_table(rows))
        if not checksums:
            header.append("")
            header.append(
                "_No checksums in this response — re-fetch with `include_checksums: true` before writing, so "
                "`omni_update_model_yaml` can detect concurrent edits._"
            )

        show_inline = params.file_name is not None or len(files) <= MAX_INLINE_YAML_FILES
        if show_inline:
            for name, content in list(files.items())[:MAX_INLINE_YAML_FILES]:
                header.extend(["", f"## `{name}`", "", "```yaml", str(content), "```"])
            hidden = len(files) - min(len(files), MAX_INLINE_YAML_FILES)
            if hidden > 0:
                header.append("")
                header.append(f"_… {hidden:,} more file(s) not shown inline; request them with `file_name`._")
        else:
            header.append("")
            header.append("_Contents omitted: pass `file_name` (a name or regex) to see the YAML of specific files._")
        return truncate_result("\n".join(header))
    except Exception as exc:
        return handle_api_error(exc)


class UpdateModelYamlInput(BaseModel):
    """Input for `omni_update_model_yaml`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The ID of the model to write to (UUID).")
    files: dict[str, YamlText] = Field(
        description=(
            "Map of file name → YAML content, sent verbatim (whitespace is never stripped). Valid names are "
            "`model`, `relationships`, `<topic_name>.topic`, `<composite_topic_name>.composite_topic` and "
            "`<view_name>.view`. Empty content removes the file when `mode` is `extension`, and makes the file "
            "ignored when `mode` is `combined`. The API writes one file per request, so this tool sends one "
            "request per entry, in order."
        ),
    )
    checksums: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional map of file name → the checksum that file had when it was fetched (the API's "
            "`previousChecksum`). Get them from `omni_get_model_yaml` with `include_checksums: true`. Any name "
            "here must also appear in `files`. Without a checksum the write overwrites whatever is there now."
        ),
    )
    branch_id: str | None = Field(
        default=None,
        description=(
            "**Required if git pull requests are required for the model.** ID of the branch to create or update "
            "on. Branch IDs come from `omni_list_models` with `model_kind=BRANCH`."
        ),
    )
    mode: YamlWriteMode = Field(
        default=YamlWriteMode.COMBINED,
        description=(
            "Mode used when writing (API default `combined`): `combined`, `extension`, `staged`, `merged`, "
            "`history`. Workbook models must use `combined` when a `branch_id` is given."
        ),
    )
    commit_message: str | None = Field(
        default=None, description="**Required for git-enabled models.** Commit message describing the change."
    )
    fully_resolved: bool | None = Field(
        default=None,
        description=(
            "If `true`, the YAML is accepted fully resolved: Omni resolves any `extends` usage and places the "
            "changes into the appropriate model files. API default `false` saves the YAML as-is."
        ),
    )

    @field_validator("files")
    @classmethod
    def _check_files(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("files must contain at least one file name → YAML content entry.")
        return {_validate_yaml_file_name(name, allow_special=True): content for name, content in value.items()}

    @model_validator(mode="after")
    def _check_checksums(self) -> Self:
        if self.checksums:
            unknown = sorted(set(self.checksums) - set(self.files))
            if unknown:
                raise ValueError(f"checksums names file(s) absent from `files`: {', '.join(unknown)}")
        return self


@mcp.tool(
    name="omni_update_model_yaml",
    annotations=ToolAnnotations(
        title="Create or Update Model YAML",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_update_model_yaml(params: UpdateModelYamlInput) -> str:
    """Create or overwrite model YAML files, with checksum conflict detection.

    Calls `POST /v1/models/{modelId}/yaml` once per entry in `files`. The safe
    loop is **fetch → edit → send back with the checksum**: read the files with
    `omni_get_model_yaml` (`include_checksums: true`), edit them, then pass each
    file's checksum in `checksums`. If anyone changed the file in between, the
    API answers `File has been modified since it was fetched` and nothing is
    overwritten — re-fetch, re-apply the edit and retry.

    In branch mode, pass the `branch_id` of the branch you are developing on
    (required when the model requires pull requests) and a `commit_message` for
    git-enabled models; the change lands on the branch and is promoted later
    through the git tools.

    When to Use:
    - To write a topic, composite topic, view, the `model` file or the
      `relationships` file, on a model or a branch.
    - To apply a reviewed set of YAML edits programmatically.

    When NOT to Use:
    - On schema models or models in git follower mode — the endpoint refuses
      both.
    - To remove a file — use `omni_delete_model_yaml_file`.
    - Without first fetching checksums, unless you deliberately intend to
      overwrite whatever is currently there.

    Returns:
    A confirmation string listing the files written, whether each was sent with
    a checksum, the branch, and the suggested follow-up (`omni_validate_model`).
    If one file fails, the files written before it are reported alongside the
    error. On failure, an `Error ...` string.

    Examples:
    - One topic with a checksum: `{"params": {"model_id": "6a1b…", "files": {"orders.topic": "label: Orders\\n"}, "checksums": {"orders.topic": "9f2c…"}, "branch_id": "0d2e…", "commit_message": "Relabel orders"}}`
    - Two files at once: `{"params": {"model_id": "6a1b…", "files": {"orders.view": "…", "relationships": "…"}, "mode": "combined"}}`
    - Remove from an extension: `{"params": {"model_id": "6a1b…", "files": {"legacy.view": ""}, "mode": "extension"}}`

    Error Handling:
    400 `File has been modified since it was fetched` means the checksum is
    stale: re-run `omni_get_model_yaml`, re-apply your edit and send the new
    checksum. Other 400s are `<parameter>: <parameter> is required`, `modelId:
    Invalid uuid`, `branchId: Invalid uuid` and `<parameter>: Invalid value
    <description>`. 403 is `Permission denied` or `Feature not enabled`; 404 is
    `Model with id <modelId> does not exist` or `Branch does not exist`.
    Requires **Modeler** permissions or higher on the model.
    """
    try:
        client = get_client()
        checksums = params.checksums or {}
        path = f"/v1/models/{_path(params.model_id)}/yaml"
        written: list[str] = []
        with_checksum: list[str] = []

        for file_name, content in params.files.items():
            request_body: dict[str, Any] = {
                "fileName": file_name,
                "yaml": content,
                "mode": params.mode.value,
            }
            if params.branch_id:
                request_body["branchId"] = params.branch_id
            if params.commit_message:
                request_body["commitMessage"] = params.commit_message
            checksum = checksums.get(file_name)
            if checksum:
                request_body["previousChecksum"] = checksum
            if params.fully_resolved is not None:
                request_body["fullyResolved"] = params.fully_resolved

            try:
                await client.request_json("POST", path, json_body=request_body)
            except Exception as exc:
                message = handle_api_error(exc)
                done = (
                    f"Wrote {len(written)} file(s) first: {', '.join(f'`{name}`' for name in written)}. "
                    if written
                    else ""
                )
                return f"{done}Failed on `{file_name}` — {message}"

            written.append(file_name)
            if checksum:
                with_checksum.append(file_name)

        target = (
            f"branch `{params.branch_id}` of model `{params.model_id}`"
            if params.branch_id
            else f"model `{params.model_id}`"
        )
        guarded = (
            f" Checksum-guarded: {', '.join(f'`{name}`' for name in with_checksum)}."
            if with_checksum
            else " No checksums were sent, so any concurrent edits were overwritten."
        )
        return (
            f"Updated **{len(written)}** YAML file(s) on {target} in `{params.mode.value}` mode: "
            f"{', '.join(f'`{name}`' for name in written)}.{guarded} "
            "Next: run `omni_validate_model` (with the same branch) before promoting."
        )
    except Exception as exc:
        return handle_api_error(exc)


class DeleteModelYamlFileInput(BaseModel):
    """Input for `omni_delete_model_yaml_file`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The ID of the model (UUID).")
    file_name: str = Field(
        description=(
            "The YAML file to delete. Must end in `.topic`, `.composite_topic` or `.view` — the `model` and "
            "`relationships` files cannot be deleted."
        ),
    )
    branch_id: str | None = Field(
        default=None,
        description=(
            "**Required if git pull requests are required for the model.** ID of the branch to delete from "
            "(from `omni_list_models` with `model_kind=BRANCH`)."
        ),
    )
    mode: YamlWriteMode | None = Field(
        default=None,
        description=(
            "Mode used when deleting (API default `combined`): `combined`, `extension`, `staged`, `merged`, "
            "`history`. Workbook models must use `combined` when a `branch_id` is given."
        ),
    )
    commit_message: str | None = Field(
        default=None, description="**Required for git-enabled models.** Commit message describing the deletion."
    )

    @field_validator("file_name")
    @classmethod
    def _check_file_name(cls, value: str) -> str:
        return _validate_yaml_file_name(value, allow_special=False)


@mcp.tool(
    name="omni_delete_model_yaml_file",
    annotations=ToolAnnotations(
        title="Delete Model YAML File",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_model_yaml_file(params: DeleteModelYamlFileInput) -> str:
    """Delete one topic, composite topic or view YAML file from a model.

    Calls `DELETE /v1/models/{modelId}/yaml`. Only files ending in `.topic`,
    `.composite_topic` or `.view` can be deleted — this tool rejects anything
    else (including the `model` and `relationships` files) before the request is
    sent. In branch mode, delete on the branch (`branch_id`) so the removal
    travels through the normal promotion path.

    When to Use:
    - To retire a topic, composite topic or view file from a model or branch.

    When NOT to Use:
    - On schema models or models in git follower mode — the endpoint refuses
      both.
    - To hide a view while keeping it in the parent model — use
      `omni_delete_model_view`, whose `mode` can mark it ignored instead of
      hard-deleting.
    - To empty a file rather than remove it — write empty YAML with
      `omni_update_model_yaml`.

    Returns:
    A confirmation string naming the deleted file, the model and the branch.
    On failure, an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "6a1b…", "file_name": "legacy_orders.topic"}}`
    - On a branch: `{"params": {"model_id": "6a1b…", "file_name": "old.view", "branch_id": "0d2e…", "commit_message": "Drop unused view"}}`

    Error Handling:
    400 covers `<parameter>: <parameter> is required`, `modelId: Invalid uuid`,
    `branchId: Invalid uuid` and `<parameter>: Invalid value <description>`.
    403 is `Permission denied` or `Feature not enabled`. 404 is `Model with id
    <modelId> does not exist`, `Branch does not exist` or `File does not exist`.
    Requires **Modeler** permissions or higher.
    """
    try:
        query: dict[str, Any] = {"fileName": params.file_name}
        if params.branch_id:
            query["branchId"] = params.branch_id
        if params.mode is not None:
            query["mode"] = params.mode.value
        if params.commit_message:
            query["commitMessage"] = params.commit_message

        await get_client().request_json("DELETE", f"/v1/models/{_path(params.model_id)}/yaml", params=query)
        target = (
            f"branch `{params.branch_id}` of model `{params.model_id}`"
            if params.branch_id
            else f"model `{params.model_id}`"
        )
        return f"Deleted YAML file `{params.file_name}` from {target}."
    except Exception as exc:
        return handle_api_error(exc)


class ValidateModelInput(BaseModel):
    """Input for `omni_validate_model`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The ID of the model to validate (UUID).")
    branch_id: str | None = Field(
        default=None,
        description=(
            "ID of the branch to validate; it must belong to this model. Omit to validate the model itself. "
            "Schema models do not support branches."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for issues grouped by file, `json` for the raw issue list.",
    )


@mcp.tool(
    name="omni_validate_model",
    annotations=ToolAnnotations(
        title="Validate Model",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_validate_model(params: ValidateModelInput) -> str:
    """List a model's (or branch's) validation errors and warnings, grouped by file.

    Calls `GET /v1/models/{modelId}/validate`. Run this after every YAML edit
    and before promoting a branch: it reports broken references, orphaned
    topics and similar problems, together with any auto-fix the model service
    can offer.

    When to Use:
    - Right after `omni_update_model_yaml` or `omni_delete_model_yaml_file`.
    - Before promoting a branch, as the pre-merge check.
    - To triage which file is responsible for a modelling failure.

    When NOT to Use:
    - To read the YAML itself — use `omni_get_model_yaml`.
    - To test that a query runs — validation is static and does not touch the
      warehouse.

    Returns:
    A markdown report: error/warning counts, then one section per YAML file with
    each issue and its suggested auto-fix. `json` returns the raw array. On
    failure, an `Error ...` string.

    Examples:
    - Model: `{"params": {"model_id": "6a1b…"}}`
    - Branch before promotion: `{"params": {"model_id": "6a1b…", "branch_id": "0d2e…"}}`
    - Raw issues: `{"params": {"model_id": "6a1b…", "response_format": "json"}}`

    Error Handling:
    400 is `modelId: Invalid uuid`, `Schema models do not have branches` (drop
    `branch_id`) or `Unrecognized key(s) in object: '<invalidParameter>'`. 404 is
    `Model with id <modelId> does not exist` or `Branch model with id <branchId>
    does not exist`. 500 `Model service error` is server-side — retry.
    """
    try:
        query: dict[str, Any] = {}
        if params.branch_id:
            query["branchId"] = params.branch_id

        payload = await get_client().request_json(
            "GET", f"/v1/models/{_path(params.model_id)}/validate", params=query or None
        )
        issues = _as_list(payload)

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(
                to_json(
                    {
                        "modelId": params.model_id,
                        "branchId": params.branch_id,
                        "issueCount": len(issues),
                        "issues": issues,
                    }
                )
            )

        errors = sum(1 for issue in issues if isinstance(issue, dict) and not issue.get("is_warning"))
        warnings = len(issues) - errors
        scope = f"branch `{params.branch_id}`" if params.branch_id else f"model `{params.model_id}`"
        lines = [
            f"# Validation — {scope}",
            "",
            f"**{errors:,}** error(s), **{warnings:,}** warning(s).",
            "",
        ]
        if not issues:
            lines.append("_No validation issues._")
            return truncate_result("\n".join(lines))

        grouped: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            grouped.setdefault(str(issue.get("yaml_path") or "(model level)"), []).append(issue)

        for yaml_path, file_issues in grouped.items():
            lines.append(f"## `{yaml_path}` ({len(file_issues):,})")
            lines.append("")
            for issue in file_issues:
                severity = "warning" if issue.get("is_warning") else "**error**"
                lines.append(f"- {severity}: {issue.get('message') or 'no message'}")
                auto_fix = _as_dict(issue.get("auto_fix"))
                fix = auto_fix.get("description_short") or auto_fix.get("description_unique")
                if fix:
                    lines.append(f"  - auto-fix available: {fix}")
            lines.append("")
        return truncate_result("\n".join(lines).rstrip())
    except Exception as exc:
        return handle_api_error(exc)


class ListModelSchemasInput(BaseModel):
    """Input for `omni_list_model_schemas`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The unique identifier of the model (UUID).")
    branch_id: str | None = Field(default=None, description="ID of the branch to retrieve schemas from.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable list, `json` for the raw array of schema names.",
    )


@mcp.tool(
    name="omni_list_model_schemas",
    annotations=ToolAnnotations(
        title="List Model Schemas",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_model_schemas(params: ListModelSchemasInput) -> str:
    """List every schema name available in a model (physical, virtual and dynamic).

    Calls `GET /v1/models/{modelId}/schemas`. The names it returns are the exact
    values `omni_get_model_yaml`'s `schema_name` and
    `omni_refresh_model_schema`'s `schemas` expect. The response is a plain
    sorted array, not a paginated one.

    When to Use:
    - To discover schema names before narrowing a YAML fetch or a selective
      schema refresh.
    - To confirm a schema exists in the model after a refresh.

    When NOT to Use:
    - To list tables or views — read the model YAML instead.
    - To list models — use `omni_list_models`.

    Returns:
    A markdown bullet list of schema names with a count, or the raw array when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "6a1b…"}}`
    - On a branch: `{"params": {"model_id": "6a1b…", "branch_id": "0d2e…"}}`

    Error Handling:
    401 means the key is missing or invalid; 403 is `Permission denied`; 404 is
    `Model with id <modelId> does not exist`.
    """
    try:
        query: dict[str, Any] = {}
        if params.branch_id:
            query["branchId"] = params.branch_id

        payload = await get_client().request_json(
            "GET", f"/v1/models/{_path(params.model_id)}/schemas", params=query or None
        )
        schemas = [str(item) for item in _as_list(payload)]

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(
                to_json({"modelId": params.model_id, "branchId": params.branch_id, "schemas": schemas})
            )

        scope = f"branch `{params.branch_id}`" if params.branch_id else f"model `{params.model_id}`"
        lines = [f"# Schemas — {scope}", "", f"**{len(schemas):,}** schema(s).", ""]
        if schemas:
            lines.extend(f"- `{name}`" for name in schemas)
        else:
            lines.append("_No schemas reported._")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


class RefreshModelSchemaInput(BaseModel):
    """Input for `omni_refresh_model_schema`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The ID of the model to refresh (UUID).")
    branch_id: str | None = Field(
        default=None,
        description=(
            "**Required if the instance's branch-based schema refresh setting is enabled**, and rejected when it "
            "is not. The branch ID is validated against the shared model."
        ),
    )
    hard_refresh: bool = Field(
        default=True,
        description=(
            "`true` (API default) performs a hard refresh, which removes objects dropped in the source; `false` "
            "performs an additive soft refresh. Selective `schemas`/`tables` filters require `false`."
        ),
    )
    schemas: str | None = Field(
        default=None,
        description="Comma-separated list of schemas to refresh selectively. Only allowed with `hard_refresh: false`.",
    )
    tables: str | None = Field(
        default=None,
        description="Comma-separated list of tables to refresh selectively. Only allowed with `hard_refresh: false`.",
    )

    @model_validator(mode="after")
    def _check_selective(self) -> Self:
        if (self.schemas or self.tables) and self.hard_refresh:
            raise ValueError("selective schemas/tables filters require hard_refresh=false")
        return self


@mcp.tool(
    name="omni_refresh_model_schema",
    annotations=ToolAnnotations(
        title="Refresh Model Schema",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_refresh_model_schema(params: RefreshModelSchemaInput) -> str:
    """Start a schema refresh so a model picks up the data source's latest structure.

    Calls `POST /v1/models/{modelId}/refresh` and returns immediately with a job
    ID — the refresh runs asynchronously. A hard refresh removes schemas, views
    and fields that no longer exist in the source (never anything users
    created); a soft refresh (`hard_refresh: false`) is additive, and is the
    only mode that accepts the selective `schemas` / `tables` filters.

    When to Use:
    - Right after `omni_create_model` for a `SCHEMA` model, to load the
      structure for the first time.
    - After the warehouse gains or drops tables and columns.
    - To pull in a handful of tables quickly (`hard_refresh: false` plus
      `tables`).

    When NOT to Use:
    - To reload cached query results — use `omni_reset_model_cache`.
    - To check the model is coherent afterwards — use `omni_validate_model`.

    Returns:
    A confirmation string with the job ID, model ID and reported status
    (`running`), plus what kind of refresh was started. On failure, an
    `Error ...` string.

    Examples:
    - Full refresh: `{"params": {"model_id": "6a1b…"}}`
    - Selective soft refresh: `{"params": {"model_id": "6a1b…", "hard_refresh": false, "tables": "orders,customers"}}`
    - Branch-based refresh: `{"params": {"model_id": "6a1b…", "branch_id": "0d2e…"}}`

    Error Handling:
    400 messages: `modelId: Invalid uuid`, `branch_id: Invalid uuid`, `branch_id
    is required when branch schema refresh is enabled`, `branch_id must not be
    provided when branch schema refresh is not enabled`, and `selective
    schemas/tables filters require hard_refresh=false` (this tool rejects that
    combination before sending). 404 means the model was not found, or the
    branch does not belong to the model's shared model. Requires **Connection
    Admin**, or **Modeler** on a connection that has exactly one shared model.
    """
    try:
        query: dict[str, Any] = {"hard_refresh": params.hard_refresh}
        if params.branch_id:
            query["branch_id"] = params.branch_id
        if params.schemas:
            query["schemas"] = params.schemas
        if params.tables:
            query["tables"] = params.tables

        payload = await get_client().request_json("POST", f"/v1/models/{_path(params.model_id)}/refresh", params=query)
        body = _as_dict(payload)
        kind = "hard (drops removed objects)" if params.hard_refresh else "soft (additive only)"
        filters: list[str] = []
        if params.schemas:
            filters.append(f"schemas: {params.schemas}")
        if params.tables:
            filters.append(f"tables: {params.tables}")
        selective = f" Selective filters — {'; '.join(filters)}." if filters else ""
        branch = f" on branch `{params.branch_id}`" if params.branch_id else ""
        return (
            f"Started a {kind} schema refresh for model `{body.get('modelId') or params.model_id}`{branch} — "
            f"job `{body.get('jobId')}`, status `{body.get('status') or 'running'}`.{selective} "
            "The refresh runs asynchronously; re-check the model afterwards with `omni_list_model_schemas` "
            "or `omni_validate_model`."
        )
    except Exception as exc:
        return handle_api_error(exc)


class ResetModelCacheInput(BaseModel):
    """Input for `omni_reset_model_cache`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The ID of the model the cache policy belongs to (UUID).")
    cache_policy_name: str = Field(
        min_length=1,
        description=(
            "The exact name of the cache policy to reset. Policy names are **not** validated against the model, "
            "so a typo silently resets nothing — copy the name from the model's YAML."
        ),
    )
    reset_at: str | None = Field(
        default=None,
        description=(
            "ISO-8601 date-time at which the cache is considered invalidated, e.g. `2026-01-31T12:00:00Z`. "
            "Cache entries created between this time and now stay valid. Cannot be in the future; defaults to now."
        ),
    )

    @field_validator("reset_at")
    @classmethod
    def _check_reset_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("reset_at must be an ISO-8601 date-time, for example 2026-01-31T12:00:00Z") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        if parsed > datetime.now(tz=UTC):
            raise ValueError("resetAt cannot be future dated")
        return text


@mcp.tool(
    name="omni_reset_model_cache",
    annotations=ToolAnnotations(
        title="Reset Model Cache",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_reset_model_cache(params: ResetModelCacheInput) -> str:
    """Invalidate the cached results of one cache policy on a model.

    Calls `POST /v1/models/{modelId}/cache_reset/{cachePolicyName}`. Every
    cached result older than `reset_at` (default: now) stops being served, so
    the next query hits the warehouse again.

    When to Use:
    - After a data load, when dashboards must stop serving stale results.
    - To invalidate everything cached before a known-bad load, by passing that
      load's timestamp as `reset_at`.

    When NOT to Use:
    - To pick up structural changes (new or dropped columns) — that is
      `omni_refresh_model_schema`.
    - Speculatively: resetting a busy policy sends every following query to the
      warehouse.

    Returns:
    A confirmation string with the cache-reset record ID, the policy name, the
    model and the effective reset time. On failure, an `Error ...` string.

    Examples:
    - Reset now: `{"params": {"model_id": "6a1b…", "cache_policy_name": "daily"}}`
    - Reset as of a past load: `{"params": {"model_id": "6a1b…", "cache_policy_name": "daily", "reset_at": "2026-01-31T12:00:00Z"}}`

    Error Handling:
    400 is `Model with id <modelId> does not exist` or `resetAt cannot be future
    dated` (this tool rejects future timestamps before sending); 404 is `Model
    with id <modelId> does not exist`. Note that the policy name itself is not
    validated by the API — a wrong name returns success while resetting nothing.
    """
    try:
        request_body: dict[str, Any] = {}
        if params.reset_at:
            request_body["resetAt"] = params.reset_at

        payload = await get_client().request_json(
            "POST",
            f"/v1/models/{_path(params.model_id)}/cache_reset/{_path(params.cache_policy_name)}",
            json_body=request_body or None,
        )
        body = _as_dict(payload)
        record = _as_dict(body.get("cache_reset"))
        effective = iso_or_na(record.get("reset_at") or params.reset_at)
        return (
            f"Reset cache policy `{params.cache_policy_name}` on model `{params.model_id}` "
            f"(record `{record.get('id') or 'unknown'}`, effective {effective}). Policy names are not validated "
            "by the API — confirm the name matches the model's YAML if nothing changes."
        )
    except Exception as exc:
        return handle_api_error(exc)


class MigrateModelInput(BaseModel):
    """Input for `omni_migrate_model`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The **source** shared model ID to read YAML from (UUID).")
    git_ref: str = Field(
        min_length=1,
        description="Git reference — branch name, tag or commit SHA — to read the source model's YAML from, e.g. `main`.",
    )
    target_model_id: str = Field(
        min_length=1, description="The **target** shared model ID to write the YAML to (UUID)."
    )
    branch_name: str | None = Field(
        default=None,
        description=(
            "**Required if the target model has git enabled.** Branch name on the target model; if the branch "
            "does not exist it is created, and if it does exist it is written to."
        ),
    )
    commit_message: str | None = Field(default=None, description="Git commit message for the migration commit.")
    delete_views_and_topics_missing_from_source: bool | None = Field(
        default=None,
        description=(
            "How target views/topics absent from the source are handled. `true` (API default) marks them "
            "`ignored: true`; `false` inherits them from the parent model instead."
        ),
    )


@mcp.tool(
    name="omni_migrate_model",
    annotations=ToolAnnotations(
        title="Migrate Model",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_migrate_model(params: MigrateModelInput) -> str:
    """Copy a model's YAML from one connection to another at a given git ref.

    Calls `POST /v1/models/{modelId}/migrate`. The API reads the source model's
    full YAML from its git repository at `git_ref` (merged with the default
    branch), then writes it to the target model — into `branch_name` when given,
    creating that branch if it does not exist — replacing the target's model
    definition. Same-organization and cross-organization migrations are both
    supported.

    When to Use:
    - To promote a model definition between environments (for example a
      development connection to production) at a known commit.
    - To seed a new connection's shared model from an existing one.

    When NOT to Use:
    - When the source model has no git configuration — the endpoint reads YAML
      from the repository and cannot work without it.
    - For ordinary edits to one model — use `omni_update_model_yaml`.
    - When the target's schema model does not match the source's at that commit;
      the migration will produce a broken model.

    Returns:
    A confirmation string naming the source model, the git ref, the target model
    and the branch written to. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "6a1b…", "git_ref": "main", "target_model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "branch_name": "migrate-from-prod", "commit_message": "Migrate model from production"}}`
    - Inherit missing content: `{"params": {"model_id": "6a1b…", "git_ref": "v2.1.0", "target_model_id": "a1b2…", "delete_views_and_topics_missing_from_source": false}}`

    Error Handling:
    400 messages: `Bad Request: Invalid JSON body`, `Bad Request: gitRef is
    empty`, `Bad Request: targetModelId is not a valid UUID`, and `Bad Request:
    Target model has git enabled but branchName was not provided`. 403 is
    `Forbidden: User does not have access to the source or target model`; 404 is
    `Not Found: Source model not found` / `Not Found: Target model not found`.
    This endpoint accepts an **Organization API key only** — a Personal Access
    Token is rejected. Permissions: Querier/Modeler/Connection Admin on the
    source, Modeler or Connection Admin on the target, and membership of both
    organizations for a cross-organization migration.
    """
    try:
        request_body: dict[str, Any] = {"gitRef": params.git_ref, "targetModelId": params.target_model_id}
        if params.branch_name:
            request_body["branchName"] = params.branch_name
        if params.commit_message:
            request_body["commitMessage"] = params.commit_message
        if params.delete_views_and_topics_missing_from_source is not None:
            request_body["deleteViewsAndTopicsMissingFromSource"] = params.delete_views_and_topics_missing_from_source

        payload = await get_client().request_json(
            "POST", f"/v1/models/{_path(params.model_id)}/migrate", json_body=request_body
        )
        body = _as_dict(payload)
        if body.get("success") is False:
            return f"Error: the API did not complete the migration of model `{params.model_id}` (`success: false`)."
        destination = f"branch `{params.branch_name}` of " if params.branch_name else ""
        return (
            f"Migrated model `{params.model_id}` at git ref `{params.git_ref}` into {destination}target model "
            f"`{params.target_model_id}`. Next: run `omni_validate_model` on the target before promoting it."
        )
    except Exception as exc:
        return handle_api_error(exc)


class DeleteModelBranchInput(BaseModel):
    """Input for `omni_delete_model_branch`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="ID of the **shared** model the branch belongs to (UUID).")
    branch_name: str = Field(
        min_length=1,
        description=(
            "Name of the branch to delete (not its ID). Names may contain `/` — a nested name such as "
            "`team/project` is percent-encoded into the URL path."
        ),
    )


@mcp.tool(
    name="omni_delete_model_branch",
    annotations=ToolAnnotations(
        title="Delete Model Branch",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_model_branch(params: DeleteModelBranchInput) -> str:
    """Delete a development branch from a shared model.

    Calls `DELETE /v1/models/{modelId}/branch/{branchName}`. This is the correct
    way to remove a branch model — `omni_archive_model` refuses branch kinds.
    Any un-promoted work on the branch goes with it.

    When to Use:
    - To clean up a branch after its changes were promoted or abandoned.

    When NOT to Use:
    - To archive a shared or extension model — use `omni_archive_model`.
    - Before the branch's changes are merged, unless you intend to discard them.
    - With a branch **ID**: this endpoint takes the branch **name**. Get names
      from `omni_list_models` with `model_kind="BRANCH"`.

    Returns:
    A confirmation string naming the branch and the shared model. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"model_id": "6a1b…", "branch_name": "pricing-fix"}}`
    - Nested name: `{"params": {"model_id": "6a1b…", "branch_name": "team/pricing-fix"}}`

    Error Handling:
    400 is `Bad Request: Invalid modelId format`; 404 is `Shared model or branch
    model does not exist` — check that the branch name (not ID) is right and
    that it belongs to this shared model. Requires **Modeler** permissions or
    higher.
    """
    try:
        payload = await get_client().request_json(
            "DELETE", f"/v1/models/{_path(params.model_id)}/branch/{_path(params.branch_name)}"
        )
        body = _as_dict(payload)
        if body.get("success") is False:
            return f"Error: the API did not delete branch `{params.branch_name}` (`success: false`)."
        return (
            f"Deleted branch `{params.branch_name}` from shared model `{params.model_id}`. "
            "Any un-promoted changes on that branch are gone."
        )
    except Exception as exc:
        return handle_api_error(exc)


class DeleteModelViewInput(BaseModel):
    """Input for `omni_delete_model_view`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The unique identifier of the model (UUID).")
    view_name: str = Field(min_length=1, description="The name of the view to delete or ignore.")
    branch_id: str | None = Field(default=None, description="Optional branch ID to apply the change on.")
    mode: ViewDeleteMode | None = Field(
        default=None,
        description=(
            "How the view is removed. `COMBINED` marks it `ignored: true` when it exists in the parent model and "
            "hard-deletes otherwise; `EXTENSION` hard-deletes it from the extension layer; `MERGED` marks it "
            "`shallowIgnored: true`, hiding it only from the immediate parent layer. The spec's schema default is "
            "`EXTENSION` while its prose calls `COMBINED` the default — pass `mode` explicitly to be sure."
        ),
    )


@mcp.tool(
    name="omni_delete_model_view",
    annotations=ToolAnnotations(
        title="Delete Model View",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_model_view(params: DeleteModelViewInput) -> str:
    """Delete or ignore a view in a model, choosing the layer it disappears from.

    Calls `DELETE /v1/models/{modelId}/view/{viewName}`. Unlike
    `omni_delete_model_yaml_file`, which always removes a file, this endpoint's
    `mode` decides whether the view is hard-deleted or merely marked ignored in
    the layer that overrides its parent — the usual way to hide an inherited
    view from an extension model without touching the shared model.

    When to Use:
    - To hide a view inherited from a parent model (`mode="COMBINED"` or
      `"MERGED"`).
    - To hard-delete a view that exists only in an extension layer
      (`mode="EXTENSION"`).

    When NOT to Use:
    - To delete the view's YAML file outright — use
      `omni_delete_model_yaml_file`.
    - To remove a topic — the topic file is deleted with
      `omni_delete_model_yaml_file`.

    Returns:
    A confirmation string naming the view, the model, the branch and the mode
    used. On failure, an `Error ...` string.

    Examples:
    - Hide an inherited view: `{"params": {"model_id": "6a1b…", "view_name": "main__orders", "mode": "COMBINED"}}`
    - Hard-delete from the extension: `{"params": {"model_id": "6a1b…", "view_name": "temp_view", "mode": "EXTENSION"}}`
    - On a branch: `{"params": {"model_id": "6a1b…", "view_name": "main__orders", "branch_id": "0d2e…"}}`

    Error Handling:
    400 is `Bad Request: Invalid modelId format` or `Bad Request: Invalid mode
    parameter` (modes are upper case here, unlike the lower-case YAML modes);
    401 means the key is missing or invalid; 404 is `Model not found` or `View
    not found`. Requires **Modeler** permissions or higher.
    """
    try:
        query: dict[str, Any] = {}
        if params.branch_id:
            query["branchId"] = params.branch_id
        if params.mode is not None:
            query["mode"] = params.mode.value

        payload = await get_client().request_json(
            "DELETE",
            f"/v1/models/{_path(params.model_id)}/view/{_path(params.view_name)}",
            params=query or None,
        )
        body = _as_dict(payload)
        if body.get("success") is False:
            return f"Error: the API did not delete view `{params.view_name}` (`success: false`)."
        mode = params.mode.value if params.mode else "the API default"
        branch = f" on branch `{params.branch_id}`" if params.branch_id else ""
        return f"Deleted or ignored view `{params.view_name}` in model `{params.model_id}`{branch} using mode `{mode}`."
    except Exception as exc:
        return handle_api_error(exc)


class GetModelAiAgentActionsInput(BaseModel):
    """Input for `omni_get_model_ai_agent_actions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The unique identifier of the model (UUID).")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable list of actions, `json` for the raw records.",
    )


@mcp.tool(
    name="omni_get_model_ai_agent_actions",
    annotations=ToolAnnotations(
        title="Get Model AI Agent Actions",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_model_ai_agent_actions(params: GetModelAiAgentActionsInput) -> str:
    """List a model's configured AI agent actions — sample queries and skills.

    Calls `GET /v1/models/{modelId}/ai-agent-actions`. Sample queries come from
    the model's `sample_queries` and from each topic's; skills come from the
    model's `skills` and each topic's, deduplicated by ID with topic skills
    overriding model skills. Each action carries a ready-made `prompt` that can
    be submitted to the AI job endpoint.

    When to Use:
    - To surface suggested prompts for a model in an AI interface.
    - To discover which curated questions and skills a model already ships.

    When NOT to Use:
    - To run one of the prompts — pass it to the AI tools instead.
    - To edit the actions — they are authored in the model's YAML
      (`sample_queries` / `skills`) and written with `omni_update_model_yaml`.

    Returns:
    A markdown list of actions (label, ID, type, description and the prompt to
    submit), or the raw records when `response_format` is `json`. An empty list
    simply means the model and its topics configure none. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"model_id": "6a1b…"}}`
    - Raw records: `{"params": {"model_id": "6a1b…", "response_format": "json"}}`

    Error Handling:
    400 is `modelId: Invalid UUID`; 403 is `Permission denied`; 404 is `Model
    with id <modelId> does not exist`.
    """
    try:
        payload = await get_client().request_json("GET", f"/v1/models/{_path(params.model_id)}/ai-agent-actions")
        body = _as_dict(payload)
        records = [item for item in _as_list(body.get("records")) if isinstance(item, dict)]

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"modelId": params.model_id, "records": records}))

        queries = sum(1 for item in records if item.get("type") == "sample_query")
        skills = sum(1 for item in records if item.get("type") == "skill")
        lines = [
            f"# AI agent actions — model `{params.model_id}`",
            "",
            f"**{len(records):,}** action(s): {queries:,} sample query/queries, {skills:,} skill(s).",
            "",
        ]
        if not records:
            lines.append("_No AI agent actions are configured on this model or its topics._")
            return truncate_result("\n".join(lines))
        for item in records:
            lines.append(
                f"- **{item.get('label') or 'unlabelled'}** (`{item.get('id')}`, {item.get('type') or 'unknown'}) — "
                f"{item.get('description') or 'no description'}"
            )
            prompt = item.get("prompt")
            if prompt:
                lines.append(f"  - prompt: `{str(prompt).replace(chr(10), ' ')}`")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


class GetTopicInput(BaseModel):
    """Input for `omni_get_topic`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The unique identifier of the model that contains the topic (UUID).")
    topic_name: str = Field(min_length=1, description="The name of the topic, e.g. `Customers` or `order_items`.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a views/joins summary, `json` for the full topic definition.",
    )


@mcp.tool(
    name="omni_get_topic",
    annotations=ToolAnnotations(
        title="Get Topic",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_topic(params: GetTopicInput) -> str:
    """Retrieve one topic's resolved views, fields and joins.

    Calls `GET /v1/models/{modelId}/topic/{topicName}`. The response mirrors the
    topic's IDE parameters: base view, default and always-where filters, the
    join path map, relationships, and every view with its dimensions and
    measures. It is the fastest way to learn what a topic can be queried on
    without reading the whole model's YAML.

    When to Use:
    - To find the field names available before building a query.
    - To understand how views are joined inside a topic (join type, direction,
      join path).
    - To check the effect of a YAML edit on the resolved topic.

    When NOT to Use:
    - To edit the topic — fetch and write its `.topic` file with
      `omni_get_model_yaml` / `omni_update_model_yaml`.
    - To list topics — list models with `model_kind="TOPIC"`, or read the YAML
      file list.

    Returns:
    A markdown summary — topic label, base view, IDE file, a table of views with
    their dimension and measure counts, and a table of relationships — always
    truncated to stay inside the result limit. `json` returns the full topic
    object, which can be large. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "6a1b…", "topic_name": "Customers"}}`
    - Full definition: `{"params": {"model_id": "6a1b…", "topic_name": "Customers", "response_format": "json"}}`

    Error Handling:
    400 means the model UUID is invalid; 404 means the model was not found, or
    the topic does not exist or is not permitted for this key. A topic hidden by
    access grants looks exactly like a missing topic.
    """
    try:
        payload = await get_client().request_json(
            "GET", f"/v1/models/{_path(params.model_id)}/topic/{_path(params.topic_name)}"
        )
        body = _as_dict(payload)
        topic = _as_dict(body.get("topic"))

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"modelId": params.model_id, "topic": topic}))

        if not topic:
            return f"_No topic named `{params.topic_name}` was returned for model `{params.model_id}`._"

        views = [item for item in _as_list(topic.get("views")) if isinstance(item, dict)]
        relationships = [item for item in _as_list(topic.get("relationships")) if isinstance(item, dict)]
        dimension_total = sum(len(_as_list(view.get("dimensions"))) for view in views)
        measure_total = sum(len(_as_list(view.get("measures"))) for view in views)

        lines = [
            f"# Topic `{topic.get('name') or params.topic_name}`",
            "",
            f"- Label: {topic.get('label') or 'n/a'}",
            f"- Base view: `{topic.get('base_view_name') or 'n/a'}`",
            f"- IDE file: `{topic.get('ide_file_name') or 'n/a'}`",
            f"- Extension model: `{topic.get('extension_model_id') or 'n/a'}`",
            f"- Views: **{len(views):,}** — {dimension_total:,} dimension(s), {measure_total:,} measure(s)",
            f"- Relationships: **{len(relationships):,}**",
            "",
            f"## Views ({len(views):,})",
            "",
            markdown_table(
                [
                    {
                        "view": view.get("name"),
                        "label": view.get("label"),
                        "schema": view.get("schema"),
                        "table": view.get("table_name"),
                        "dimensions": len(_as_list(view.get("dimensions"))),
                        "measures": len(_as_list(view.get("measures"))),
                    }
                    for view in views
                ]
            ),
            "",
            f"## Relationships ({len(relationships):,})",
            "",
            markdown_table(
                [
                    {
                        "left": relationship.get("left_view_name"),
                        "right": relationship.get("right_view_name"),
                        "join type": relationship.get("join_type"),
                        "type": relationship.get("type"),
                        "bidirectional": relationship.get("bidirectional"),
                        "ignored": relationship.get("ignored"),
                        "sql": relationship.get("sql"),
                    }
                    for relationship in relationships
                ]
            ),
        ]
        join_map = _as_dict(topic.get("join_via_map"))
        if join_map:
            lines.extend(["", f"## Join paths ({len(join_map):,})", ""])
            lines.extend(
                f"- `{view}` via {' → '.join(str(step) for step in path) if isinstance(path, list) and path else 'the base view'}"
                for view, path in join_map.items()
            )
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)
