"""Tools for the user group (SCIM) and user/group model role endpoints.

Groups live under SCIM `/scim/v2/groups` (create/list/get/replace/update/delete).
Those six endpoints require an **Organization API key** — Personal Access Tokens
are not accepted there (`orgApiKey` security scheme). Model role assignment
(`/v1/user-groups/{id}/model-roles`, `/v1/users/{id}/model-roles`) accepts either
credential type (`bearerAuth`).
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, iso_or_na, markdown_table, to_json, truncate_result
from omni_mcp.server import mcp

#: One line on what this module covers, for the generated README catalogue.
MODULE_SUMMARY = "User groups and user/group model roles"

#: SCIM schema URI required on every PATCH /scim/v2/groups/{id} request body.
PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

_ROLE_NAME_DESCRIPTION = (
    "The role to assign. Common roles: `VIEWER` (can view the model), `QUERIER` (can view and query), "
    "`QUERY_TOPICS` (can query specific topics; equivalent to Restricted Querier), `MODELER` (can edit and model "
    "the data), `CONNECTION_ADMIN` (full administrative access to the connection), `NO_ACCESS` (no access). "
    "Custom roles defined for your organization are also accepted, so this is not a closed enum."
)
_MODEL_ID_DESCRIPTION = (
    "UUID of the model to assign the role for. Optional when assigning `CONNECTION_ADMIN` (or a custom role based "
    "on it) — the role then applies at the connection level; required for other role types."
)
_CONNECTION_ID_DESCRIPTION = (
    "UUID of the connection the model belongs to. Required if model_id is not provided; optional (and inferred "
    "from the model) if model_id is provided."
)


def _quote(value: str) -> str:
    """URL-encode a path segment (ids may contain characters like `/`)."""
    return urllib.parse.quote(value, safe="")


def _group_rows(resources: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in resources:
        if not isinstance(group, dict):
            continue
        raw_meta = group.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        members = group.get("members")
        members = members if isinstance(members, list) else []
        rows.append(
            {
                "id": group.get("id", "unknown"),
                "displayName": group.get("displayName", "unknown"),
                "members": len(members),
                "created": iso_or_na(meta.get("created")),
                "lastModified": iso_or_na(meta.get("lastModified")),
            }
        )
    return rows


def _render_group_detail(group: dict[str, Any]) -> str:
    raw_meta = group.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    raw_members = group.get("members")
    members = [member for member in raw_members if isinstance(member, dict)] if isinstance(raw_members, list) else []
    lines = [
        f"# User Group: {group.get('displayName', 'unknown')}",
        "",
        f"- ID: `{group.get('id', 'unknown')}`",
        f"- Resource type: {meta.get('resourceType', 'Group')}",
        f"- Created: {iso_or_na(meta.get('created'))}",
        f"- Last modified: {iso_or_na(meta.get('lastModified'))}",
        "",
        f"## Members ({len(members)})",
    ]
    if members:
        rows = [{"value": member.get("value", "unknown"), "display": member.get("display", "")} for member in members]
        lines.append(markdown_table(rows))
    else:
        lines.append("_No members._")
    return "\n".join(lines)


def _model_role_rows(results: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "modelId": item.get("modelId", "unknown"),
            "connectionId": item.get("connectionId", "unknown"),
            "roleName": item.get("roleName", "unknown"),
            "baseRole": item.get("baseRole", "unknown"),
        }
        for item in results
        if isinstance(item, dict)
    ]


def _user_model_role_rows(results: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        raw_source = item.get("from")
        source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
        source_type = source.get("type", "unknown")
        if source_type == "Group Role" and source.get("name"):
            source_text = f"{source_type} ({source.get('name')})"
        else:
            source_text = source_type
        rows.append(
            {
                "modelId": item.get("modelId", "unknown"),
                "connectionId": item.get("connectionId", "unknown"),
                "roleName": item.get("roleName", "unknown"),
                "baseRole": item.get("baseRole", "unknown"),
                "priority": item.get("priority"),
                "resolved": item.get("resolved"),
                "from": source_text,
            }
        )
    return rows


class ListUserGroupsInput(BaseModel):
    """Input for `omni_list_user_groups`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    count: int = Field(default=100, ge=1, description="The number of groups to return. Defaults to 100.")
    start_index: int = Field(
        default=1,
        ge=1,
        description="An integer index (1-based) that determines the starting point of the sorted result list.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary table, `json` for the raw SCIM list response.",
    )


class GetUserGroupInput(BaseModel):
    """Input for `omni_get_user_group`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_group_id: str = Field(..., min_length=1, description="The ID of the group to retrieve.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw SCIM group resource.",
    )


class CreateUserGroupInput(BaseModel):
    """Input for `omni_create_user_group`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="The name of the group. Names must be 64 characters or less. Required unless scim_body is given.",
    )
    member_ids: list[str] | None = Field(
        default=None,
        description="User IDs to add as initial group members. Each is sent as `{'value': <id>}`.",
    )
    scim_body: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw SCIM request body, sent verbatim instead of the body built from display_name/member_ids. "
            "Mutually exclusive with those fields."
        ),
    )

    @model_validator(mode="after")
    def _require_display_name_or_body(self) -> CreateUserGroupInput:
        if self.scim_body is not None:
            if self.display_name is not None or self.member_ids is not None:
                raise ValueError("scim_body is a raw override: send it on its own, without display_name or member_ids.")
            return self
        if not self.display_name:
            raise ValueError("display_name is required unless scim_body is provided.")
        return self


class ReplaceUserGroupInput(BaseModel):
    """Input for `omni_replace_user_group`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_group_id: str = Field(..., min_length=1, description="The ID of the group to replace.")
    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="The new name for the group. Names must be 64 characters or less. Required unless scim_body is given.",
    )
    member_ids: list[str] | None = Field(
        default=None,
        description=(
            "The full membership list; this OVERWRITES the existing membership entirely — include existing member "
            "ids to retain them, or pass an empty list to clear all members. Required unless scim_body is given."
        ),
    )
    scim_body: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw SCIM request body, sent verbatim instead of the body built from display_name/member_ids. "
            "Mutually exclusive with those fields."
        ),
    )

    @model_validator(mode="after")
    def _require_full_body(self) -> ReplaceUserGroupInput:
        if self.scim_body is not None:
            if self.display_name is not None or self.member_ids is not None:
                raise ValueError("scim_body is a raw override: send it on its own, without display_name or member_ids.")
            return self
        if self.display_name is None or self.member_ids is None:
            raise ValueError("display_name and member_ids are both required unless scim_body is provided.")
        return self


class UpdateUserGroupInput(BaseModel):
    """Input for `omni_update_user_group`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_group_id: str = Field(..., min_length=1, description="The ID of the group to update.")
    add_member_ids: list[str] | None = Field(
        default=None, description="User IDs to add to the group membership (SCIM `add` op on path `members`)."
    )
    remove_member_ids: list[str] | None = Field(
        default=None,
        description=(
            "User IDs to remove from the group membership. Each becomes its own SCIM `remove` op with path "
            '`members[value eq "{userId}"]`.'
        ),
    )
    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="New group name; sent as a `replace` op on path `displayName` (renames without touching members).",
    )
    operations: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Raw SCIM `Operations` array, sent verbatim instead of the operations built from "
            "add_member_ids/remove_member_ids/display_name. Mutually exclusive with those fields."
        ),
    )

    @field_validator("remove_member_ids")
    @classmethod
    def _reject_unquotable_member_ids(cls, value: list[str] | None) -> list[str] | None:
        """Ids are interpolated into a SCIM filter string, so they cannot carry its quoting."""
        for member_id in value or []:
            if '"' in member_id or "\\" in member_id:
                raise ValueError(
                    'remove_member_ids entries must not contain `"` or `\\`: each one is interpolated into the '
                    'SCIM filter `members[value eq "<id>"]`.'
                )
        return value

    @model_validator(mode="after")
    def _require_at_least_one_operation(self) -> UpdateUserGroupInput:
        if self.operations is not None:
            if self.add_member_ids is not None or self.remove_member_ids is not None or self.display_name is not None:
                raise ValueError(
                    "operations is a raw override: send it on its own, without add_member_ids, "
                    "remove_member_ids or display_name."
                )
            if len(self.operations) == 0:
                raise ValueError("operations must contain at least one entry when provided.")
            return self
        if not (self.add_member_ids or self.remove_member_ids or self.display_name):
            raise ValueError("Provide at least one of add_member_ids, remove_member_ids, display_name, or operations.")
        return self


class DeleteUserGroupInput(BaseModel):
    """Input for `omni_delete_user_group`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_group_id: str = Field(..., min_length=1, description="The ID of the group to delete.")


class GetUserGroupModelRolesInput(BaseModel):
    """Input for `omni_get_user_group_model_roles`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_group_id: str = Field(..., min_length=1, description="The ID of the user group to retrieve model roles for.")
    model_id: str | None = Field(
        default=None,
        min_length=1,
        description="Filter to a specific model ID (UUID). If omitted, returns roles for all models the group can access.",
    )
    connection_id: str | None = Field(
        default=None,
        min_length=1,
        description="Filter to models from a specific connection ID (UUID). If omitted, returns roles for all connections.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary table, `json` for the raw response.",
    )


class AssignUserGroupModelRoleInput(BaseModel):
    """Input for `omni_assign_user_group_model_role`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_group_id: str = Field(
        ..., min_length=1, description="The ID of the user group to assign or update a model role for."
    )
    model_id: str | None = Field(default=None, min_length=1, description=_MODEL_ID_DESCRIPTION)
    connection_id: str | None = Field(default=None, min_length=1, description=_CONNECTION_ID_DESCRIPTION)
    role_name: str = Field(..., min_length=1, description=_ROLE_NAME_DESCRIPTION)

    @model_validator(mode="after")
    def _require_a_scope(self) -> AssignUserGroupModelRoleInput:
        if not self.model_id and not self.connection_id:
            raise ValueError(
                "Provide model_id, connection_id, or both — connectionId is required when modelId is absent."
            )
        return self


class GetUserModelRolesInput(BaseModel):
    """Input for `omni_get_user_model_roles`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(..., min_length=1, description="The ID (UUID) of the user to retrieve model roles for.")
    model_id: str | None = Field(
        default=None,
        min_length=1,
        description="Filter to a specific model ID (UUID). If omitted, returns roles for all models the user can access.",
    )
    connection_id: str | None = Field(
        default=None,
        min_length=1,
        description="Filter to models from a specific connection ID (UUID). If omitted, returns roles for all connections.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary table, `json` for the raw response.",
    )


class AssignUserModelRoleInput(BaseModel):
    """Input for `omni_assign_user_model_role`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(
        ..., min_length=1, description="The ID (UUID) of the user to assign or update a model role for."
    )
    model_id: str | None = Field(default=None, min_length=1, description=_MODEL_ID_DESCRIPTION)
    connection_id: str | None = Field(default=None, min_length=1, description=_CONNECTION_ID_DESCRIPTION)
    role_name: str = Field(..., min_length=1, description=_ROLE_NAME_DESCRIPTION)

    @model_validator(mode="after")
    def _require_a_scope(self) -> AssignUserModelRoleInput:
        if not self.model_id and not self.connection_id:
            raise ValueError(
                "Provide model_id, connection_id, or both — connectionId is required when modelId is absent."
            )
        return self


@mcp.tool(
    name="omni_list_user_groups",
    annotations=ToolAnnotations(
        title="List User Groups",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_user_groups(params: ListUserGroupsInput) -> str:
    """List user groups, sorted by creation time.

    Calls `GET /scim/v2/groups`. Pagination is SCIM-style (`startIndex`/`count`),
    not cursor-based: the response reports `totalResults` and `itemsPerPage`, and
    this tool prints the next `start_index` to pass when more groups remain.

    When to Use:
    - To browse or search for a group by paging through the whole list.
    - To get the total count of user groups in the organization.

    When NOT to Use:
    - To fetch one group you already have the id for (use `omni_get_user_group`).

    Returns:
    A markdown table of `id`, `displayName`, member count, `created`, and
    `lastModified` per group, plus a "next start_index" hint — or the raw SCIM
    list response when `response_format` is `json`. On failure, an `Error ...`
    string.

    Examples:
    - `{"params": {}}`
    - `{"params": {"count": 50, "start_index": 51}}`

    Error Handling:
    Requires an Organization API key — Personal Access Tokens are not accepted
    on this SCIM endpoint and return 403. Exceeding 60 requests/minute returns
    429 (the client already retries once, honouring `Retry-After`).
    """
    try:
        query: dict[str, Any] = {"count": params.count, "startIndex": params.start_index}
        payload = await get_client().request_json("GET", "/scim/v2/groups", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        raw_resources = body.get("Resources")
        resources: list[Any] = raw_resources if isinstance(raw_resources, list) else []
        total = body.get("totalResults")
        items_per_page = body.get("itemsPerPage")
        start_index = body.get("startIndex", params.start_index)

        lines = ["# User Groups", ""]
        if isinstance(total, int):
            lines.append(f"Showing **{len(resources):,}** of **{total:,}** group(s) (startIndex {start_index}).")
        else:
            lines.append(f"Showing **{len(resources):,}** group(s) (startIndex {start_index}).")
        # `itemsPerPage == 0` would make the "next page" hint point at the page
        # just fetched, so callers would loop on it forever.
        if (
            isinstance(total, int)
            and isinstance(items_per_page, int)
            and items_per_page > 0
            and (start_index - 1) + items_per_page < total
        ):
            next_index = start_index + items_per_page
            lines.append(f"More available — pass `start_index={next_index}` to fetch the next page.")
        else:
            lines.append("End of results.")
        lines.append("")
        lines.append(markdown_table(_group_rows(resources)) if resources else "_No user groups found._")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_user_group",
    annotations=ToolAnnotations(
        title="Get User Group",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_user_group(params: GetUserGroupInput) -> str:
    """Retrieve a user group using its unique ID.

    Calls `GET /scim/v2/groups/{userGroupId}`.

    When to Use:
    - To look up a group's current name and membership before editing it.

    When NOT to Use:
    - To browse all groups (use `omni_list_user_groups`).

    Returns:
    A markdown summary of the group (id, resource type, created/last-modified
    timestamps, and a member table), or the raw SCIM group resource when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"user_group_id": "mEhXj6ZI"}}`

    Error Handling:
    Requires an Organization API key — Personal Access Tokens are not accepted
    on this SCIM endpoint and return 403. An unknown `user_group_id` returns 404.
    Exceeding 60 requests/minute returns 429.
    """
    try:
        path = f"/scim/v2/groups/{_quote(params.user_group_id)}"
        payload = await get_client().request_json("GET", path)
        group: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(group))
        return truncate_result(_render_group_detail(group))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_user_group",
    annotations=ToolAnnotations(
        title="Create User Group",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_user_group(params: CreateUserGroupInput) -> str:
    """Create a user group.

    Calls `POST /scim/v2/groups`. To manage model and connection role
    assignments for the new group, use `omni_assign_user_group_model_role`.

    When to Use:
    - To create a new group of users that should share model/connection roles.

    When NOT to Use:
    - To add or remove members on an existing group (use `omni_update_user_group`).
    - To assign model roles (use `omni_assign_user_group_model_role`).

    Returns:
    A short confirmation string with the new group's name, id, and member count.
    On failure, an `Error ...` string.

    Examples:
    - `{"params": {"display_name": "Blob Sales"}}`
    - `{"params": {"display_name": "Blob Sales", "member_ids": ["9e8719d9-276a-4964-9395-a493189a247c"]}}`

    Error Handling:
    Requires an Organization API key — Personal Access Tokens are not accepted
    on this SCIM endpoint and return 403. Exceeding 60 requests/minute returns
    429 (the client already retries once, honouring `Retry-After`).
    """
    try:
        if params.scim_body is not None:
            body: dict[str, Any] = params.scim_body
        else:
            body = {"displayName": params.display_name}
            if params.member_ids:
                body["members"] = [{"value": member_id} for member_id in params.member_ids]
        payload = await get_client().request_json("POST", "/scim/v2/groups", json_body=body)
        group: dict[str, Any] = payload if isinstance(payload, dict) else {}
        name = group.get("displayName", params.display_name or "unknown")
        group_id = group.get("id", "unknown")
        member_count = len(group.get("members") or [])
        return truncate_result(f"Created user group **{name}** (id `{group_id}`) with {member_count} member(s).")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_replace_user_group",
    annotations=ToolAnnotations(
        title="Replace User Group",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_replace_user_group(params: ReplaceUserGroupInput) -> str:
    """Replace the specified user group, including its membership.

    Calls `PUT /scim/v2/groups/{userGroupId}`. Per the SCIM 2.0 specification,
    `PUT` replaces the entire group resource — `member_ids` overwrites the
    existing membership list entirely. To add or remove individual members
    without replacing the full list, use `omni_update_user_group` instead.

    When to Use:
    - To rename a group and/or set its complete membership list in one call.

    When NOT to Use:
    - To add/remove a handful of members without touching the rest (use
      `omni_update_user_group`'s `add_member_ids`/`remove_member_ids`).

    Returns:
    A short confirmation string with the group's name, id, and new member count.
    On failure, an `Error ...` string.

    Examples:
    - `{"params": {"user_group_id": "mEhXj6ZI", "display_name": "Blob SEs", "member_ids": ["9e8719d9-276a-4964-9395-a493189a247c"]}}`

    Error Handling:
    Requires an Organization API key — Personal Access Tokens are not accepted
    on this SCIM endpoint and return 403. `400`: "Invalid request body". An
    unknown `user_group_id` returns 404. Exceeding 60 requests/minute returns 429.
    """
    try:
        if params.scim_body is not None:
            body: dict[str, Any] = params.scim_body
        else:
            body = {
                "displayName": params.display_name,
                "members": [{"value": member_id} for member_id in (params.member_ids or [])],
            }
        path = f"/scim/v2/groups/{_quote(params.user_group_id)}"
        payload = await get_client().request_json("PUT", path, json_body=body)
        group: dict[str, Any] = payload if isinstance(payload, dict) else {}
        name = group.get("displayName", params.display_name or "unknown")
        group_id = group.get("id", params.user_group_id)
        member_count = len(group.get("members") or [])
        return truncate_result(f"Replaced user group **{name}** (id `{group_id}`); now has {member_count} member(s).")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_update_user_group",
    annotations=ToolAnnotations(
        title="Update User Group",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_update_user_group(params: UpdateUserGroupInput) -> str:
    """Update a user group by applying SCIM 2.0 patch operations.

    Calls `PATCH /scim/v2/groups/{userGroupId}`. Use this to add or remove
    individual members, or to rename the group, without affecting other
    attributes. To replace the entire group resource (including the full
    membership list) in one request, use `omni_replace_user_group` instead.

    When to Use:
    - To add or remove a few members from an existing group.
    - To rename a group without touching its membership.

    When NOT to Use:
    - To set the complete membership list in one call (use `omni_replace_user_group`).

    Returns:
    A short confirmation string with the group's name, id, and current member
    count after the patch is applied. On failure, an `Error ...` string.

    Examples:
    - Add members: `{"params": {"user_group_id": "mEhXj6ZI", "add_member_ids": ["9e8719d9-276a-4964-9395-a493189a247c"]}}`
    - Remove a member: `{"params": {"user_group_id": "mEhXj6ZI", "remove_member_ids": ["9e8719d9-276a-4964-9395-a493189a247c"]}}`
    - Rename: `{"params": {"user_group_id": "mEhXj6ZI", "display_name": "Blob SEs"}}`

    Error Handling:
    Requires an Organization API key — Personal Access Tokens are not accepted
    on this SCIM endpoint and return 403. `400`: "Invalid patch operations". An
    unknown `user_group_id` returns 404. Exceeding 60 requests/minute returns 429.
    """
    try:
        if params.operations is not None:
            operations = params.operations
        else:
            operations = []
            if params.add_member_ids:
                operations.append(
                    {"op": "add", "path": "members", "value": [{"value": m} for m in params.add_member_ids]}
                )
            if params.remove_member_ids:
                for member_id in params.remove_member_ids:
                    operations.append({"op": "remove", "path": f'members[value eq "{member_id}"]'})
            if params.display_name:
                operations.append({"op": "replace", "path": "displayName", "value": params.display_name})
        body = {"schemas": [PATCH_OP_SCHEMA], "Operations": operations}
        path = f"/scim/v2/groups/{_quote(params.user_group_id)}"
        payload = await get_client().request_json("PATCH", path, json_body=body)
        group: dict[str, Any] = payload if isinstance(payload, dict) else {}
        name = group.get("displayName", "unknown")
        group_id = group.get("id", params.user_group_id)
        member_count = len(group.get("members") or [])
        return truncate_result(f"Updated user group **{name}** (id `{group_id}`); now has {member_count} member(s).")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_delete_user_group",
    annotations=ToolAnnotations(
        title="Delete User Group",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_user_group(params: DeleteUserGroupInput) -> str:
    """Delete the specified user group.

    Calls `DELETE /scim/v2/groups/{userGroupId}`. This does not delete the
    group's member users, only the group itself and its role assignments.

    When to Use:
    - To permanently remove a group that is no longer needed.

    When NOT to Use:
    - To remove members while keeping the group (use `omni_update_user_group`).

    Returns:
    A short confirmation string with the deleted group's id. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"user_group_id": "mEhXj6ZI"}}`

    Error Handling:
    Requires an Organization API key — Personal Access Tokens are not accepted
    on this SCIM endpoint and return 403. An unknown `user_group_id` returns 404.
    Exceeding 60 requests/minute returns 429. This is destructive and cannot be
    undone from the API.
    """
    try:
        path = f"/scim/v2/groups/{_quote(params.user_group_id)}"
        await get_client().request_json("DELETE", path)
        return truncate_result(f"Deleted user group (id `{params.user_group_id}`).")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_user_group_model_roles",
    annotations=ToolAnnotations(
        title="Get User Group Model Roles",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_user_group_model_roles(params: GetUserGroupModelRolesInput) -> str:
    """Retrieve the model role assignments for a user group.

    Calls `GET /v1/user-groups/{userGroupId}/model-roles`. All members of the
    group inherit these roles. Only `shared` and `shared_extension` models can
    have model roles assigned.

    When to Use:
    - To audit or confirm what a group's members can do on a given model or
      connection before granting or changing access.

    When NOT to Use:
    - To check a specific user's *effective* (resolved) roles, including ones
      inherited from other groups (use `omni_get_user_model_roles`).

    Returns:
    A markdown table of `modelId`, `connectionId`, `roleName`, and `baseRole`
    per assignment, or the raw response when `response_format` is `json`. On
    failure, an `Error ...` string.

    Examples:
    - `{"params": {"user_group_id": "mEhXj6ZI"}}`
    - `{"params": {"user_group_id": "mEhXj6ZI", "model_id": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a"}}`

    Error Handling:
    404: "User group not found in organization." Exceeding 60 requests/minute
    returns 429. Both Organization API keys and Personal Access Tokens may call
    this endpoint.
    """
    try:
        query: dict[str, Any] = {}
        if params.model_id:
            query["modelId"] = params.model_id
        if params.connection_id:
            query["connectionId"] = params.connection_id
        path = f"/v1/user-groups/{_quote(params.user_group_id)}/model-roles"
        payload = await get_client().request_json("GET", path, params=query or None)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        raw_results = body.get("results")
        results: list[Any] = raw_results if isinstance(raw_results, list) else []
        lines = [f"# Model Roles — User Group `{body.get('userGroupId', params.user_group_id)}`", ""]
        lines.append(markdown_table(_model_role_rows(results)) if results else "_No model role assignments found._")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_assign_user_group_model_role",
    annotations=ToolAnnotations(
        title="Assign User Group Model Role",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_assign_user_group_model_role(params: AssignUserGroupModelRoleInput) -> str:
    """Assign or update a model role for a user group.

    Calls `POST /v1/user-groups/{userGroupId}/model-roles`. If the group
    already has a role for the specified model, this updates it to the new
    role. All members of the group inherit this role. Only `shared` and
    `shared_extension` models can be assigned model roles.

    When to Use:
    - To grant (or change) a group's access level to a model or connection.

    When NOT to Use:
    - To assign a role to a single user directly (use `omni_assign_user_model_role`).
    - To manage group membership itself (use `omni_update_user_group`).

    Returns:
    A short confirmation string with the group id, model/connection id, and
    assigned role name. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"user_group_id": "mEhXj6ZI", "connection_id": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed", "model_id": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a", "role_name": "QUERIER"}}`
    - `{"params": {"user_group_id": "mEhXj6ZI", "connection_id": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed", "role_name": "CONNECTION_ADMIN"}}`

    Error Handling:
    400: "Invalid JSON", "Invalid model ID", "Invalid connection ID", "Method
    not allowed". 404: "User group not found in organization", "Model does not
    exist", "Connection does not exist". 422: "Invalid role", "Model does not
    belong to connection", "Only shared and shared_extension models can be
    assigned model roles". Exceeding 60 requests/minute returns 429. Both
    Organization API keys and Personal Access Tokens may call this endpoint,
    but the caller's own permissions still gate which roles they may grant.
    """
    try:
        body: dict[str, Any] = {"roleName": params.role_name}
        if params.connection_id:
            body["connectionId"] = params.connection_id
        if params.model_id:
            body["modelId"] = params.model_id
        path = f"/v1/user-groups/{_quote(params.user_group_id)}/model-roles"
        payload = await get_client().request_json("POST", path, json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        group_id = result.get("userGroupId", params.user_group_id)
        role_name = result.get("roleName", params.role_name)
        scope = []
        if result.get("modelId") or params.model_id:
            scope.append(f"model `{result.get('modelId', params.model_id)}`")
        if result.get("connectionId") or params.connection_id:
            scope.append(f"connection `{result.get('connectionId', params.connection_id)}`")
        scope_text = f" for {' / '.join(scope)}" if scope else ""
        return truncate_result(f"Assigned role **{role_name}** to user group `{group_id}`{scope_text}.")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_user_model_roles",
    annotations=ToolAnnotations(
        title="Get User Model Roles",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_user_model_roles(params: GetUserModelRolesInput) -> str:
    """Retrieve the model role assignments for a user.

    Calls `GET /v1/users/{userId}/model-roles`. This includes both direct
    role assignments and roles inherited from user group memberships, so a
    user can have several rows for the same model — the row with
    `resolved: true` is the one that determines their effective permissions.

    When to Use:
    - To debug why a user does or doesn't have access to a model, including
      whether the role came from a direct grant, a group, or a connection's
      base role.

    When NOT to Use:
    - To see only a group's own assignments, not what an individual user
      resolves to (use `omni_get_user_group_model_roles`).

    Returns:
    A markdown table of `modelId`, `connectionId`, `roleName`, `baseRole`,
    `priority`, `resolved`, and where the role comes from (`from`), or the raw
    response when `response_format` is `json`. On failure, an `Error ...`
    string.

    Examples:
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "connection_id": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed"}}`

    Error Handling:
    404: "User not found in organization." Exceeding 60 requests/minute returns
    429. Both Organization API keys and Personal Access Tokens may call this
    endpoint.
    """
    try:
        query: dict[str, Any] = {}
        if params.model_id:
            query["modelId"] = params.model_id
        if params.connection_id:
            query["connectionId"] = params.connection_id
        path = f"/v1/users/{_quote(params.user_id)}/model-roles"
        payload = await get_client().request_json("GET", path, params=query or None)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        raw_results = body.get("results")
        results: list[Any] = raw_results if isinstance(raw_results, list) else []
        membership_id = body.get("membershipId", "unknown")
        lines = [f"# Model Roles — User `{params.user_id}` (membership `{membership_id}`)", ""]
        lines.append(
            markdown_table(_user_model_role_rows(results)) if results else "_No model role assignments found._"
        )
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_assign_user_model_role",
    annotations=ToolAnnotations(
        title="Assign User Model Role",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_assign_user_model_role(params: AssignUserModelRoleInput) -> str:
    """Assign or update a model role for a user.

    Calls `POST /v1/users/{userId}/model-roles`. If the user already has a
    role for the specified model, this updates it to the new role. Only
    `shared` and `shared_extension` models can be assigned model roles.

    When to Use:
    - To grant (or change) one user's direct access level to a model or
      connection, independent of their group memberships.

    When NOT to Use:
    - To grant access to everyone in a group at once (use
      `omni_assign_user_group_model_role`).

    Returns:
    A short confirmation string with the user id, model/connection id, and
    assigned role name. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "connection_id": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed", "model_id": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a", "role_name": "QUERIER"}}`
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "connection_id": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed", "role_name": "CONNECTION_ADMIN"}}`

    Error Handling:
    400: "Invalid JSON", "Invalid model ID", "Invalid connection ID", "Method
    not allowed". 404: "User not found in organization", "Model does not
    exist", "Connection does not exist". 422: "Invalid role", "Model does not
    belong to connection", "Only shared and shared_extension models can be
    assigned model roles". Exceeding 60 requests/minute returns 429. Both
    Organization API keys and Personal Access Tokens may call this endpoint,
    but the caller's own permissions still gate which roles they may grant.
    """
    try:
        body: dict[str, Any] = {"roleName": params.role_name}
        if params.connection_id:
            body["connectionId"] = params.connection_id
        if params.model_id:
            body["modelId"] = params.model_id
        path = f"/v1/users/{_quote(params.user_id)}/model-roles"
        payload = await get_client().request_json("POST", path, json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        user_id = result.get("userId", params.user_id)
        role_name = result.get("roleName", params.role_name)
        scope = []
        if result.get("modelId") or params.model_id:
            scope.append(f"model `{result.get('modelId', params.model_id)}`")
        if result.get("connectionId") or params.connection_id:
            scope.append(f"connection `{result.get('connectionId', params.connection_id)}`")
        scope_text = f" for {' / '.join(scope)}" if scope else ""
        return truncate_result(f"Assigned role **{role_name}** to user `{user_id}`{scope_text}.")
    except Exception as exc:
        return handle_api_error(exc)
