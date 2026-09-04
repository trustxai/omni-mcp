"""Tools for the document permission, favorite, and label endpoints. Implemented by lane t11.

Lane workers: copy `src/omni_mcp/tools/health.py`'s shape (input model, decorator + annotations,
`params: <Input>` signature, docstring sections, try/except -> `handle_api_error`, `-> str` return).
See CONTRIBUTING.md for the full contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, cursor_paginated_response, markdown_table, to_json, truncate_result
from omni_mcp.server import mcp

#: The content role assigned when granting/updating direct document permissions.
DocumentRole = Literal["NO_ACCESS", "VIEWER", "EDITOR", "MANAGER", "OWNER"]

#: The default organization-wide role, one notch narrower than `DocumentRole` (no `OWNER`).
OrganizationRole = Literal["NO_ACCESS", "VIEWER", "EDITOR", "MANAGER"]

#: How access was granted, per the access-list and permits schemas.
AccessSource = Literal["direct", "folder"]

#: The kind of principal holding access.
PrincipalType = Literal["user", "userGroup"]

#: A label name, 2-25 characters (labels are matched case-insensitively by the API).
LabelName = Annotated[str, StringConstraints(min_length=2, max_length=25, strip_whitespace=True)]


def _document_path(document_id: str, suffix: str = "") -> str:
    """`/v1/documents/{documentId}` with `documentId` percent-encoded, plus an optional suffix."""
    return f"/v1/documents/{quote(document_id, safe='')}{suffix}"


def _label_path(document_id: str, label_name: str) -> str:
    """`/v1/documents/{documentId}/labels/{labelName}`, both segments percent-encoded."""
    return f"/v1/documents/{quote(document_id, safe='')}/labels/{quote(label_name, safe='')}"


def _as_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# Document permissions
# ---------------------------------------------------------------------------


class GetDocumentPermissionsInput(BaseModel):
    """Input for `omni_get_document_permissions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ...,
        min_length=1,
        description="The document identifier. Find it via File > Document settings > Settings > Identifier.",
    )
    user_id: str | None = Field(
        default=None,
        description="User membership ID to check resolved permits for (UUID). When omitted, only the "
        "document-level abilities are returned.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw abilities/permits payload.",
    )


def _format_abilities(abilities: Mapping[str, Any]) -> list[str]:
    lines = ["## Abilities", ""]
    if not abilities:
        lines.append("_No abilities reported._")
        return lines
    for key in sorted(abilities):
        lines.append(f"- `{key}`: **{abilities[key]}**")
    return lines


def _flatten_permit(permit: Mapping[str, Any]) -> dict[str, Any]:
    direct = permit.get("direct") if isinstance(permit.get("direct"), Mapping) else {}
    folder = permit.get("folder") if isinstance(permit.get("folder"), Mapping) else {}
    return {
        "id": permit.get("id"),
        "name": permit.get("name"),
        "type": permit.get("type"),
        "description": permit.get("description"),
        "directRole": direct.get("role") if isinstance(direct, Mapping) else None,
        "directAccessBoost": direct.get("accessBoost") if isinstance(direct, Mapping) else None,
        "directIsOwner": direct.get("isOwner") if isinstance(direct, Mapping) else None,
        "folderRole": folder.get("role") if isinstance(folder, Mapping) else None,
        "folderAccessBoost": folder.get("accessBoost") if isinstance(folder, Mapping) else None,
        "folderIsOwner": folder.get("isOwner") if isinstance(folder, Mapping) else None,
    }


@mcp.tool(
    name="omni_get_document_permissions",
    annotations=ToolAnnotations(
        title="Get Document Permissions",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_document_permissions(params: GetDocumentPermissionsInput) -> str:
    """Retrieve a document's ability toggles, plus one user's resolved permits.

    Calls `GET /v1/documents/{documentId}/permissions`. The `abilities` object always comes back
    (the document-level toggles such as `canDownload` or `canSchedule`); `permits` — the resolved
    direct and folder-inherited role for a specific user — is only present when `user_id` is given.

    When to Use:
    - Before changing sharing, to see the document's current ability settings.
    - To check what access level a specific user actually has (direct vs. folder-inherited).
    - To debug why a user cannot perform an action the document should allow.

    When NOT to Use:
    - To list every principal with access — use `omni_list_document_access` instead.
    - To change abilities — use `omni_update_document_permission_settings`.

    Returns:
    A markdown summary of the abilities (and, when `user_id` is given, a table of resolved
    permits), or the raw JSON payload when `response_format` is `json`. On failure, an `Error ...`
    string.

    Examples:
    - Abilities only: `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - Resolved permits for one user: `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "user_id": "df290ed4-b721-4efe-914b-95d30ce1c5f2"}}`

    Error Handling:
    400 means `user_id` is not a valid value; 403 means the caller needs **MANAGER** (or higher)
    permissions on the document; 404 means the document does not exist or is not visible to the
    caller.
    """
    try:
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id
        payload = await get_client().request_json(
            "GET", _document_path(params.document_id, "/permissions"), params=query or None
        )
        body = _as_dict(payload)

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        abilities_raw = body.get("abilities")
        abilities: dict[str, Any] = abilities_raw if isinstance(abilities_raw, dict) else {}
        lines = [f"# Document Permissions — `{params.document_id}`", ""]
        lines.extend(_format_abilities(abilities))

        permits_raw = body.get("permits")
        if isinstance(permits_raw, list) and permits_raw:
            lines.extend(["", f"## Permits for user `{params.user_id}`", ""])
            rows = [_flatten_permit(p) for p in permits_raw if isinstance(p, Mapping)]
            lines.append(markdown_table(rows))
        elif params.user_id:
            lines.extend(["", "_No permits reported for this user._"])
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


class ListDocumentAccessInput(BaseModel):
    """Input for `omni_list_document_access`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ...,
        min_length=1,
        description="The document identifier. Find it via File > Document settings > Settings > Identifier.",
    )
    page_size: int = Field(default=20, ge=1, le=100, description="Number of results per page (1-100).")
    cursor: str | None = Field(
        default=None, description="Pagination cursor from a previous response's `pageInfo.nextCursor`."
    )
    sort_field: Literal["name", "email", "role"] = Field(default="name", description="Field to sort results by.")
    sort_direction: Literal["asc", "desc"] = Field(default="asc", description="Sort order.")
    access_source: AccessSource | None = Field(
        default=None,
        description="Filter by how access was granted: `direct` (explicit document permissions) or "
        "`folder` (inherited folder permissions).",
    )
    principal_type: PrincipalType | None = Field(
        default=None,
        description="Filter by principal type: `user` (individual users only) or `userGroup` (user groups "
        "only). Sent to the API as the `type` query parameter.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable list, `json` for raw records."
    )


def _format_access_principal(principal: Mapping[str, Any]) -> str:
    name = principal.get("name", "unnamed")
    ptype = principal.get("type", "unknown")
    role = principal.get("role", "unknown")
    source = principal.get("accessSource", "unknown")
    text = f"- **{name}** (`{ptype}`) — role **{role}**, access via {source}"
    email = principal.get("email")
    if email:
        text += f", {email}"
    if principal.get("accessBoost"):
        text += ", AccessBoost enabled"
    if principal.get("isOwner"):
        text += ", owner"
    folder_info = principal.get("folderInfo")
    if isinstance(folder_info, Mapping) and folder_info:
        text += f" (via folder **{folder_info.get('name', 'unknown')}**, `{folder_info.get('path', '')}`)"
    return text


@mcp.tool(
    name="omni_list_document_access",
    annotations=ToolAnnotations(
        title="List Document Access",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_document_access(params: ListDocumentAccessInput) -> str:
    """List every user and group with access to a document.

    Calls `GET /v1/documents/{documentId}/access-list`. Each entry is a distinct access grant with
    its own role and settings; a principal may appear twice if it has both `direct` and
    `folder`-based access to the document.

    When to Use:
    - To audit who can see or edit a document, including access inherited from its folder.
    - Before revoking or changing access, to find the exact principal to target.
    - To filter down to only direct grants (`access_source="direct"`) or only groups
      (`principal_type="userGroup"`).

    When NOT to Use:
    - To check one user's resolved permission level in isolation — use
      `omni_get_document_permissions` with `user_id`, which is cheaper for that.

    Returns:
    A markdown list of principals (name, type, role, access source, email, AccessBoost, owner
    flag, and folder origin when applicable) with pagination info, or the raw JSON payload when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - First page: `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - Only direct grants to groups: `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "access_source": "direct", "principal_type": "userGroup"}}`
    - Next page: `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "cursor": "eyJuYW1lIjoiSm9obiJ9"}}`

    Error Handling:
    400 means an invalid `page_size`, `sort_field`, `sort_direction`, `access_source`,
    `principal_type`, or `cursor`; 403 means the caller needs **MANAGER** (or higher) permissions
    on the document; 404 means the document does not exist.
    """
    try:
        query: dict[str, Any] = {
            "pageSize": params.page_size,
            "sortField": params.sort_field,
            "sortDirection": params.sort_direction,
        }
        if params.cursor:
            query["cursor"] = params.cursor
        if params.access_source:
            query["accessSource"] = params.access_source
        if params.principal_type:
            query["type"] = params.principal_type
        payload = await get_client().request_json(
            "GET", _document_path(params.document_id, "/access-list"), params=query
        )
        body = _as_dict(payload)
        return cursor_paginated_response(
            items=body.get("principals") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_access_principal,
            title=f"Document Access — `{params.document_id}`",
        )
    except Exception as exc:
        return handle_api_error(exc)


class _PrincipalsMutationInput(BaseModel):
    """Shared shape for grant/update: a role plus at least one principal list."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ...,
        min_length=1,
        description="The document identifier. Find it via File > Document settings > Settings > Identifier.",
    )
    role: DocumentRole = Field(
        ...,
        description="The content role to assign: `NO_ACCESS` (no access; hidden from content system and "
        "search), `VIEWER` (view dashboard), `EDITOR` (edit dashboard and workbook), `MANAGER` (edit plus "
        "manage permissions), or `OWNER` (manage permissions and receive access requests; a document must "
        "always keep at least one owner).",
    )
    access_boost: bool | None = Field(
        default=None,
        description=(
            "If `true`, AccessBoost is enabled for the document for these principals. Omit to leave the "
            "current setting alone — it is only sent when set."
        ),
    )
    user_ids: list[str] = Field(
        default_factory=list,
        description="User IDs (UUIDs) to assign this role to. Either `user_ids` or `user_group_ids` is "
        "required. Retrieve IDs via the list-users / list-embed-users tools.",
    )
    user_group_ids: list[str] = Field(
        default_factory=list,
        description="User group IDs to assign this role to. Either `user_ids` or `user_group_ids` is "
        "required. Retrieve IDs via the list-user-groups tool.",
    )

    @model_validator(mode="after")
    def _require_principals(self) -> Self:
        if not self.user_ids and not self.user_group_ids:
            raise ValueError("Provide at least one of `user_ids` or `user_group_ids`.")
        return self


def _principals_body(params: _PrincipalsMutationInput) -> dict[str, Any]:
    body: dict[str, Any] = {"role": params.role}
    # Only sent when the caller said something: a PATCH that always carried
    # `accessBoost: false` silently turned an enabled boost off.
    if params.access_boost is not None:
        body["accessBoost"] = params.access_boost
    if params.user_ids:
        body["userIds"] = params.user_ids
    if params.user_group_ids:
        body["userGroupIds"] = params.user_group_ids
    return body


def _principals_confirmation(verb: str, params: _PrincipalsMutationInput, success: bool) -> str:
    outcome = "succeeded" if success else "returned without a `success` flag — verify with `omni_list_document_access`"
    return (
        f"{verb} role **{params.role}** on document `{params.document_id}` for "
        f"{len(params.user_ids)} user(s) and {len(params.user_group_ids)} group(s) — {outcome}."
    )


class GrantDocumentPermissionsInput(_PrincipalsMutationInput):
    """Input for `omni_grant_document_permissions`."""


@mcp.tool(
    name="omni_grant_document_permissions",
    annotations=ToolAnnotations(
        title="Grant Document Permissions",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_grant_document_permissions(params: GrantDocumentPermissionsInput) -> str:
    """Grant a content role on a document to users and/or user groups.

    Calls `POST /v1/documents/{documentId}/permissions`.

    When to Use:
    - To share a document with new users or groups for the first time.
    - To add additional principals at a given role alongside existing grants.

    When NOT to Use:
    - To change an existing grant's role — use `omni_update_document_permissions` (same shape,
      `PATCH` semantics).
    - To take access away — use `omni_revoke_document_permissions`.

    Returns:
    A short confirmation naming the role and the number of users/groups granted. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "role": "EDITOR", "user_ids": ["3c90c3cc-0d44-4b50-8888-8dd25736052a"]}}`
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "role": "VIEWER", "user_group_ids": ["mEhXj6ZI"], "access_boost": true}}`

    Error Handling:
    400 means `role` is missing/invalid, or neither `user_ids` nor `user_group_ids` was provided
    (also rejected client-side before the call); 403 means the caller needs **MANAGER** (or higher)
    permissions on the document; 404 means the document does not exist.
    """
    try:
        payload = await get_client().request_json(
            "POST", _document_path(params.document_id, "/permissions"), json_body=_principals_body(params)
        )
        success = bool(_as_dict(payload).get("success"))
        return _principals_confirmation("Granted", params, success)
    except Exception as exc:
        return handle_api_error(exc)


class UpdateDocumentPermissionsInput(_PrincipalsMutationInput):
    """Input for `omni_update_document_permissions`."""


@mcp.tool(
    name="omni_update_document_permissions",
    annotations=ToolAnnotations(
        title="Update Document Permissions",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_update_document_permissions(params: UpdateDocumentPermissionsInput) -> str:
    """Update the content role already granted to users and/or user groups on a document.

    Calls `PATCH /v1/documents/{documentId}/permissions`.

    When to Use:
    - To change an existing principal's role (e.g. VIEWER -> EDITOR).
    - To toggle AccessBoost for principals that already have access.

    When NOT to Use:
    - To share with a principal that has no existing grant — use
      `omni_grant_document_permissions` (identical request shape).
    - To remove access entirely — use `omni_revoke_document_permissions`.

    Returns:
    A short confirmation naming the role and the number of users/groups updated. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "role": "MANAGER", "user_ids": ["3c90c3cc-0d44-4b50-8888-8dd25736052a"]}}`

    Error Handling:
    400 means `role` is missing/invalid, or neither `user_ids` nor `user_group_ids` was provided
    (also rejected client-side before the call); 403 means the caller needs **MANAGER** (or higher)
    permissions on the document; 404 means the document does not exist.
    """
    try:
        payload = await get_client().request_json(
            "PATCH", _document_path(params.document_id, "/permissions"), json_body=_principals_body(params)
        )
        success = bool(_as_dict(payload).get("success"))
        return _principals_confirmation("Updated", params, success)
    except Exception as exc:
        return handle_api_error(exc)


class UpdateDocumentPermissionSettingsInput(BaseModel):
    """Input for `omni_update_document_permission_settings`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ...,
        min_length=1,
        description="The document identifier. Find it via File > Document settings > Settings > Identifier.",
    )
    organization_role: OrganizationRole | None = Field(
        default=None,
        description="The default content role for the whole organization: `NO_ACCESS`, `VIEWER`, `EDITOR`, "
        "or `MANAGER`.",
    )
    can_analyze: bool | None = Field(
        default=None, description="Allow exploring from this document by creating new workbooks."
    )
    can_download: bool | None = Field(default=None, description="Allow downloading query results and dashboards.")
    can_drill: bool | None = Field(default=None, description="Allow drilling into data points in the content.")
    can_duplicate: bool | None = Field(default=None, description="Allow duplicating the document.")
    can_request_access: bool | None = Field(default=None, description="Allow users without access to request it.")
    can_save_spreadsheets: bool | None = Field(
        default=None, description="Allow creating spreadsheets from query results."
    )
    can_schedule: bool | None = Field(
        default=None, description="Allow creating deliveries (schedules and alerts) on the document."
    )
    can_upload: bool | None = Field(
        default=None, description="Allow uploading data (e.g. CSVs) to create data input tables."
    )
    can_use_dashboard_ai: bool | None = Field(default=None, description="Allow using AI features within the dashboard.")
    can_use_timezone_override: bool | None = Field(
        default=None, description="Allow users to override the document's timezone setting."
    )
    can_view_workbook: bool | None = Field(
        default=None, description="Allow viewing a read-only version of the workbook."
    )
    require_pull_request_to_publish: bool | None = Field(
        default=None, description="Require a pull request through the git integration to publish changes."
    )

    @model_validator(mode="after")
    def _require_a_setting(self) -> Self:
        if all(getattr(self, name) is None for name, _ in _SETTINGS_FIELD_MAP):
            raise ValueError("Provide at least one setting to change; an empty PUT would send nothing.")
        return self


_SETTINGS_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("organization_role", "organizationRole"),
    ("can_analyze", "canAnalyze"),
    ("can_download", "canDownload"),
    ("can_drill", "canDrill"),
    ("can_duplicate", "canDuplicate"),
    ("can_request_access", "canRequestAccess"),
    ("can_save_spreadsheets", "canSaveSpreadsheets"),
    ("can_schedule", "canSchedule"),
    ("can_upload", "canUpload"),
    ("can_use_dashboard_ai", "canUseDashboardAi"),
    ("can_use_timezone_override", "canUseTimezoneOverride"),
    ("can_view_workbook", "canViewWorkbook"),
    ("require_pull_request_to_publish", "requirePullRequestToPublish"),
)


@mcp.tool(
    name="omni_update_document_permission_settings",
    annotations=ToolAnnotations(
        title="Update Document Permission Settings",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_document_permission_settings(params: UpdateDocumentPermissionSettingsInput) -> str:
    """Update a document's permission/interactivity ability settings and default org role.

    Calls `PUT /v1/documents/{documentId}/permissions`. Only the fields you set are sent, and only
    those fields are updated — everything else on the document is left as-is.

    When to Use:
    - To toggle abilities such as download, scheduling, drill, or upload for the document.
    - To change the default organization-wide role (`organization_role`).

    When NOT to Use:
    - To grant/update/revoke a specific user's or group's role — use the permissions grant/update/
      revoke tools instead.

    Returns:
    A short confirmation listing which fields were changed. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "can_save_spreadsheets": false, "can_request_access": false, "can_download": true}}`
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "organization_role": "VIEWER"}}`

    Error Handling:
    400 means an invalid parameter value; 403 means the caller needs **MANAGER** (or higher)
    permissions on the document; 404 means the document does not exist.
    """
    try:
        body: dict[str, Any] = {}
        changed: list[str] = []
        for field_name, api_field in _SETTINGS_FIELD_MAP:
            value = getattr(params, field_name)
            if value is not None:
                body[api_field] = value
                changed.append(api_field)
        payload = await get_client().request_json(
            "PUT", _document_path(params.document_id, "/permissions"), json_body=body
        )
        success = bool(_as_dict(payload).get("success"))
        outcome = "succeeded" if success else "returned without a `success` flag"
        fields_text = ", ".join(changed)
        return (
            f"Updated permission settings on document `{params.document_id}` — {outcome}. "
            f"Fields changed: {fields_text}."
        )
    except Exception as exc:
        return handle_api_error(exc)


class RevokeDocumentPermissionsInput(BaseModel):
    """Input for `omni_revoke_document_permissions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        ...,
        min_length=1,
        description="The document identifier. Find it via File > Document settings > Settings > Identifier.",
    )
    user_ids: list[str] = Field(
        default_factory=list,
        description="User IDs (UUIDs) to revoke access from. Either `user_ids` or `user_group_ids` is required.",
    )
    user_group_ids: list[str] = Field(
        default_factory=list,
        description="User group IDs to revoke access from. Either `user_ids` or `user_group_ids` is required.",
    )

    @model_validator(mode="after")
    def _require_principals(self) -> Self:
        if not self.user_ids and not self.user_group_ids:
            raise ValueError("Provide at least one of `user_ids` or `user_group_ids`.")
        return self


@mcp.tool(
    name="omni_revoke_document_permissions",
    annotations=ToolAnnotations(
        title="Revoke Document Permissions",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_revoke_document_permissions(params: RevokeDocumentPermissionsInput) -> str:
    """Revoke document access from users and/or user groups.

    Calls `DELETE /v1/documents/{documentId}/permissions`. This removes the direct grant entirely
    (there is no partial role left behind); a principal with folder-inherited access may still be
    able to see the document through that path.

    When to Use:
    - To remove a user's or group's direct access to a document.
    - As cleanup after off-boarding a user or retiring a group's need for a document.

    When NOT to Use:
    - To lower (not remove) a role — use `omni_update_document_permissions` with a smaller role
      instead.
    - To remove folder-inherited access — that is controlled by folder permissions, not this
      endpoint.

    Returns:
    A short confirmation naming the number of users/groups revoked. On failure, an `Error ...`
    string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "user_ids": ["3c90c3cc-0d44-4b50-8888-8dd25736052a"]}}`
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "user_group_ids": ["mEhXj6ZI"]}}`

    Error Handling:
    400 means neither `user_ids` nor `user_group_ids` was provided (also rejected client-side
    before the call) or an id is invalid; 403 means the caller needs **MANAGER** (or higher)
    permissions on the document; 404 means the document does not exist.
    """
    try:
        body: dict[str, Any] = {}
        if params.user_ids:
            body["userIds"] = params.user_ids
        if params.user_group_ids:
            body["userGroupIds"] = params.user_group_ids
        payload = await get_client().request_json(
            "DELETE", _document_path(params.document_id, "/permissions"), json_body=body
        )
        success = bool(_as_dict(payload).get("success"))
        outcome = "succeeded" if success else "returned without a `success` flag"
        return (
            f"Revoked permissions on document `{params.document_id}` for {len(params.user_ids)} user(s) "
            f"and {len(params.user_group_ids)} group(s) — {outcome}."
        )
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Document favorites
# ---------------------------------------------------------------------------


class FavoriteDocumentInput(BaseModel):
    """Input for `omni_favorite_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="The document identifier.")
    user_id: str | None = Field(
        default=None,
        description="**Requires an Organization API key.** Membership ID of the user to favorite the "
        "document on behalf of. Personal Access Tokens (PATs) cannot use this parameter to act on behalf "
        "of other users.",
    )


@mcp.tool(
    name="omni_favorite_document",
    annotations=ToolAnnotations(
        title="Favorite Document",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_favorite_document(params: FavoriteDocumentInput) -> str:
    """Add a published document to a user's favorites.

    Calls `PUT /v1/documents/{documentId}/favorite`. Only published documents can be favorited.
    The API returns `204` whether the document was newly favorited or was already a favorite, so
    this call is safe to repeat.

    When to Use:
    - To favorite a document for the calling user.
    - With an Organization API key, to favorite a document on behalf of another user (e.g. during a
      migration that preserves favorites).

    When NOT to Use:
    - On an unpublished document — the API returns 404.
    - To act on behalf of another user with a Personal Access Token — that always returns 403; use
      an Organization API key instead.

    Returns:
    A short confirmation that the document is now favorited. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "user_id": "df290ed4-b721-4efe-914b-95d30ce1c5f2"}}`

    Error Handling:
    403 means a Personal Access Token tried to use `user_id` (that parameter is Organization-API-
    key-only); 404 means the document does not exist, is not published, or `user_id` was not found.
    """
    try:
        query = {"userId": params.user_id} if params.user_id else None
        await get_client().request_json("PUT", _document_path(params.document_id, "/favorite"), params=query)
        on_behalf_of = f" on behalf of user `{params.user_id}`" if params.user_id else ""
        return f"Favorited document `{params.document_id}`{on_behalf_of}."
    except Exception as exc:
        return handle_api_error(exc)


class UnfavoriteDocumentInput(BaseModel):
    """Input for `omni_unfavorite_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="The document identifier.")
    user_id: str | None = Field(
        default=None,
        description="**Requires an Organization API key.** Membership ID of the user to unfavorite the "
        "document on behalf of. Personal Access Tokens (PATs) cannot use this parameter to act on behalf "
        "of other users.",
    )


@mcp.tool(
    name="omni_unfavorite_document",
    annotations=ToolAnnotations(
        title="Unfavorite Document",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_unfavorite_document(params: UnfavoriteDocumentInput) -> str:
    """Remove a published document from a user's favorites.

    Calls `DELETE /v1/documents/{documentId}/favorite`. Only published documents can be
    unfavorited. The API returns `204` whether the document was previously favorited or not, so
    this call is safe to repeat.

    When to Use:
    - To unfavorite a document for the calling user.
    - With an Organization API key, to unfavorite a document on behalf of another user.

    When NOT to Use:
    - To act on behalf of another user with a Personal Access Token — that always returns 403; use
      an Organization API key instead.

    Returns:
    A short confirmation that the document is no longer favorited. On failure, an `Error ...`
    string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "user_id": "df290ed4-b721-4efe-914b-95d30ce1c5f2"}}`

    Error Handling:
    400 means an invalid HTTP method reached the API (should not occur through this tool); 403
    means a Personal Access Token tried to use `user_id`; 404 means the document does not exist, is
    not published, or `user_id` was not found.
    """
    try:
        query = {"userId": params.user_id} if params.user_id else None
        await get_client().request_json("DELETE", _document_path(params.document_id, "/favorite"), params=query)
        on_behalf_of = f" on behalf of user `{params.user_id}`" if params.user_id else ""
        return f"Unfavorited document `{params.document_id}`{on_behalf_of}."
    except Exception as exc:
        return handle_api_error(exc)


class ListDocumentFavoritersInput(BaseModel):
    """Input for `omni_list_document_favoriters`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    identifier: str = Field(..., min_length=1, description="Document identifier (either document ID or slug).")
    page_size: int = Field(default=20, ge=1, le=100, description="Number of items per page (min 1, max 100).")
    cursor: str | None = Field(
        default=None,
        description="Page cursor from a previous response's `nextCursor`. Omit for the first page.",
    )
    sort_direction: Literal["asc", "desc"] = Field(
        default="asc",
        description="Sort direction by `favoritedAt`. `asc` returns oldest favorites first; `desc` "
        "returns newest first.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable list, `json` for raw records."
    )


def _format_favoriter(record: Mapping[str, Any]) -> str:
    name = record.get("name", "unnamed")
    user_id = record.get("userId", "unknown")
    email = record.get("email", "no email")
    favorited_at = record.get("favoritedAt", "unknown time")
    return f"- **{name}** (`{user_id}`) — {email}, favorited at {favorited_at}"


@mcp.tool(
    name="omni_list_document_favoriters",
    annotations=ToolAnnotations(
        title="List Document Favoriters",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_document_favoriters(params: ListDocumentFavoritersInput) -> str:
    """List the users who have favorited a published document.

    Calls `GET /v1/documents/{identifier}/favorites`, paginated and sorted by `favoritedAt`.
    **Requires an Organization API key** — Personal Access Tokens cannot call this endpoint.

    When to Use:
    - To answer "who favorited document X" — for example, a content-migration script that
      preserves favorites when replacing a document should call this once per document rather than
      iterating every user in the organization.

    When NOT to Use:
    - With a Personal Access Token — this endpoint always returns 403 for PATs; use an Organization
      API key instead.

    Returns:
    A markdown list of favoriters (name, user id, email, favorited-at timestamp) with pagination
    info, or the raw JSON payload when `response_format` is `json`. On failure, an `Error ...`
    string.

    Examples:
    - `{"params": {"identifier": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - Newest first: `{"params": {"identifier": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "sort_direction": "desc"}}`

    Error Handling:
    400 means an invalid `cursor`, out-of-range `page_size`, invalid `sort_direction`, or an
    unknown query parameter; 401 means authentication is missing; 403 means the caller lacks
    **MANAGER** (or **OWNER**) permission on the document, **or** the caller used a Personal Access
    Token instead of an Organization API key; 404 means the document does not exist or is
    unpublished.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size, "sortDirection": params.sort_direction}
        if params.cursor:
            query["cursor"] = params.cursor
        payload = await get_client().request_json("GET", _document_path(params.identifier, "/favorites"), params=query)
        body = _as_dict(payload)
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_favoriter,
            title=f"Document Favoriters — `{params.identifier}`",
        )
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Document labels
# ---------------------------------------------------------------------------


class BulkUpdateDocumentLabelsInput(BaseModel):
    """Input for `omni_bulk_update_document_labels`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="The document identifier (UUID).")
    add: list[LabelName] = Field(
        default_factory=list,
        description="Label names to add to the document (2-25 characters each). The label must already "
        "exist (create it first via the create-label tool).",
    )
    remove: list[LabelName] = Field(
        default_factory=list,
        description="Label names to remove from the document (2-25 characters each).",
    )
    user_id: str | None = Field(
        default=None,
        description="**Requires an Organization API key.** Optional user ID that attributes the action to "
        "the specified user.",
    )

    @model_validator(mode="after")
    def _validate_add_remove(self) -> Self:
        if not self.add and not self.remove:
            raise ValueError("Provide at least one label in `add` or `remove`.")
        add_lower = {label.lower() for label in self.add}
        remove_lower = {label.lower() for label in self.remove}
        overlap = add_lower & remove_lower
        if overlap:
            raise ValueError(f"Labels cannot appear in both `add` and `remove`: {', '.join(sorted(overlap))}")
        return self


@mcp.tool(
    name="omni_bulk_update_document_labels",
    annotations=ToolAnnotations(
        title="Bulk Update Document Labels",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_bulk_update_document_labels(params: BulkUpdateDocumentLabelsInput) -> str:
    """Add and/or remove multiple labels from a document in one atomic operation.

    Calls `PATCH /v1/documents/{documentId}/labels`. All changes succeed or fail together — there
    are no partial updates. Label matching is case-insensitive, and a label cannot appear in both
    `add` and `remove` in the same request (rejected client-side before the call). Labels must
    already exist (create them first via the create-label tool) before they can be added to or
    removed from a document.

    When to Use:
    - To apply and retire several labels on a document in a single call.
    - When you need an all-or-nothing guarantee across multiple label changes.

    When NOT to Use:
    - For a single label add or remove — `omni_apply_document_label` /
      `omni_remove_document_label` are simpler for that case.
    - To add or remove **Verified** or **Homepage** labels without Organization Admin permissions —
      the API rejects those with 403.

    Returns:
    A short confirmation listing the document's current labels after the update. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "add": ["production", "reviewed"]}}`
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "remove": ["draft", "needs-review"]}}`
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "add": ["approved"], "remove": ["pending-review"]}}`

    Error Handling:
    400 means the request is empty, a label appears in both `add` and `remove` (also rejected
    client-side), a label name is out of the 2-25 character range, or the body has an unrecognized
    field; 403 means the caller lacks the `canLabel` permission on the document, or lacks
    Organization Admin permissions needed to touch **Verified**/**Homepage** labels; 404 means the
    document does not exist, a label in `add` was never created globally, or a label in `remove`
    is not currently on the document.
    """
    try:
        body: dict[str, Any] = {}
        if params.add:
            body["add"] = list(params.add)
        if params.remove:
            body["remove"] = list(params.remove)
        query = {"userId": params.user_id} if params.user_id else None
        payload = await get_client().request_json(
            "PATCH", _document_path(params.document_id, "/labels"), params=query, json_body=body
        )
        labels = _as_dict(payload).get("labels")
        labels_text = ", ".join(labels) if isinstance(labels, list) and labels else "none reported"
        return truncate_result(f"Updated labels on document `{params.document_id}`. Current labels: {labels_text}.")
    except Exception as exc:
        return handle_api_error(exc)


class ApplyDocumentLabelInput(BaseModel):
    """Input for `omni_apply_document_label`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="The document identifier.")
    label_name: LabelName = Field(
        ...,
        description="The label name to apply (2-25 characters). Labels are matched case-insensitively "
        "(e.g. adding `BlobSales` when `blobsales` exists is treated as a duplicate). Special characters "
        "are percent-encoded automatically. Applying a **Verified** or **Homepage** label requires "
        "Organization Admin permissions. The label must already exist (create it first via the "
        "create-label tool).",
    )
    user_id: str | None = Field(
        default=None,
        description="**Requires an Organization API key.** Optional user ID that attributes the action to "
        "the specified user.",
    )


@mcp.tool(
    name="omni_apply_document_label",
    annotations=ToolAnnotations(
        title="Apply Document Label",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_apply_document_label(params: ApplyDocumentLabelInput) -> str:
    """Apply an existing label to a document, adding it alongside any labels already there.

    Calls `PUT /v1/documents/{documentId}/labels/{labelName}`. Documents can have multiple labels;
    this endpoint adds one without replacing the others. The API returns `204` whether the label
    was newly applied or already present, so this call is safe to repeat.

    When to Use:
    - To tag a document with one additional label.
    - To apply a **Verified** or **Homepage** label as an Organization Admin.

    When NOT to Use:
    - To add and remove several labels atomically — use `omni_bulk_update_document_labels`.
    - When the label does not exist yet — create it first via the create-label tool, or this
      returns 404.

    Returns:
    A short confirmation that the label was applied. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "label_name": "production"}}`
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "label_name": "Q1 2024"}}`

    Error Handling:
    400 means `label_name` is out of the 2-25 character range (also rejected client-side); 403
    means the caller lacks the Manager role on the document, or lacks Organization Admin
    permissions needed for **Verified**/**Homepage** labels; 404 means the document does not exist,
    or the label was never created (create it first via the create-label tool).
    """
    try:
        query = {"userId": params.user_id} if params.user_id else None
        await get_client().request_json("PUT", _label_path(params.document_id, params.label_name), params=query)
        return f"Applied label **{params.label_name}** to document `{params.document_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class RemoveDocumentLabelInput(BaseModel):
    """Input for `omni_remove_document_label`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="The document identifier.")
    label_name: LabelName = Field(
        ...,
        description="The label name to remove (2-25 characters). Labels are matched case-insensitively "
        "(e.g. removing `BlobSales` removes `blobsales` if present). Special characters are "
        "percent-encoded automatically.",
    )
    user_id: str | None = Field(
        default=None,
        description="**Requires an Organization API key.** Optional user ID that attributes the action to "
        "the specified user.",
    )


@mcp.tool(
    name="omni_remove_document_label",
    annotations=ToolAnnotations(
        title="Remove Document Label",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_remove_document_label(params: RemoveDocumentLabelInput) -> str:
    """Remove one label from a document.

    Calls `DELETE /v1/documents/{documentId}/labels/{labelName}`. **This endpoint is not
    idempotent**: if the label is not currently on the document, the API returns `404` — repeating
    a successful call will fail the second time.

    When to Use:
    - To untag a document from one specific label.

    When NOT to Use:
    - To add and remove several labels atomically — use `omni_bulk_update_document_labels`.
    - To repeat a call you already know succeeded — the second call returns 404 because the label
      is no longer present.

    Returns:
    A short confirmation that the label was removed. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "label_name": "draft"}}`

    Error Handling:
    403 means the caller lacks permission to modify labels on this document; 404 means the
    document does not exist, or the label is not currently on the document (this call is not
    idempotent, so a repeat after success also returns 404).
    """
    try:
        query = {"userId": params.user_id} if params.user_id else None
        await get_client().request_json("DELETE", _label_path(params.document_id, params.label_name), params=query)
        return f"Removed label **{params.label_name}** from document `{params.document_id}`."
    except Exception as exc:
        return handle_api_error(exc)
