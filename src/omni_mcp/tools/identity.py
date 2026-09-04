"""Tools for the Who Am I, API token, and user-attribute endpoints. Implemented by lane t1."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

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

TokenType = Literal["organization", "personal", "mcp"]
SortField = Literal["createdAt", "name"]
SortDirection = Literal["asc", "desc"]


# --------------------------------------------------------------------------
# omni_whoami
# --------------------------------------------------------------------------


class WhoamiInput(BaseModel):
    """Input for `omni_whoami`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str | None = Field(
        default=None,
        description=(
            "Optional model filter. A single model ID or a comma-separated list. When provided, "
            "`rolesByModel` in the response contains only these models. When omitted, models the caller "
            "can access are returned up to a limit; see `rolesByModelTruncated`."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the full identity payload.",
    )


def _format_role_lines(roles_by_model: dict[str, Any], limit: int = 25) -> list[str]:
    lines: list[str] = []
    for model_id, role in list(roles_by_model.items())[:limit]:
        if not isinstance(role, dict):
            continue
        role_name = role.get("roleName", "unknown")
        base_role = role.get("baseRole", "unknown")
        connection_id = role.get("connectionId", "unknown")
        permissions = role.get("permissions") or []
        permission_text = ", ".join(str(item) for item in permissions) if permissions else "none reported"
        lines.append(
            f"- `{model_id}` — role **{role_name}** (base {base_role}, connection `{connection_id}`); "
            f"permissions: {permission_text}"
        )
    remaining = len(roles_by_model) - len(lines)
    if remaining > 0:
        lines.append(f"- _… {remaining:,} more model(s) not shown; pass `model_id` to scope the result._")
    return lines


@mcp.tool(
    name="omni_whoami",
    annotations=ToolAnnotations(
        title="Who Am I",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_whoami(params: WhoamiInput) -> str:
    """Get the caller's full identity, API key scope, org role, and per-model permissions.

    Calls `GET /v1/whoami`. Retrieves the authenticated caller's own identity, API key scope,
    organization role, and resolved per-model permissions — useful for determining whether the
    caller can execute a specific action before attempting it. Pass `model_id` to scope
    `rolesByModel` to specific models. This is the full identity + permissions report; for a
    connectivity/configuration summary use the health-check tool instead.

    When to Use:
    - To find the caller's own user ID and membership ID.
    - To check the caller's organization role (`MEMBER` vs `ORG_ADMIN`) before calling an
      admin-only endpoint such as the API token or user attribute tools.
    - To determine the caller's resolved, effective permissions on one or more models before
      attempting a query or edit.

    When NOT to Use:
    - To look up another user's identity (this only ever returns the caller's own).
    - This endpoint can be used by any user with a valid API key — it is not itself gated on
      Organization Admin, unlike most other tools in this module.

    Returns:
    A markdown summary — key scope, org role, user id, membership id, and per-model roles/
    permissions (truncated notice included when the API truncated the list) — or the raw
    identity payload when `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - Full identity: `{"params": {}}`
    - Scope roles to one model: `{"params": {"model_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - Scope roles to several models: `{"params": {"model_id": "model-1,model-2"}}`

    Error Handling:
    401 means authentication is required (missing/invalid API key). 404 means one or more
    requested `model_id`s do not exist or are not accessible to the caller. Any authenticated
    user may call this endpoint.
    """
    try:
        query: dict[str, Any] = {}
        if params.model_id:
            query["modelId"] = params.model_id
        payload = await get_client().request_json("GET", "/v1/whoami", params=query or None)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        raw_user = body.get("user")
        user: dict[str, Any] = raw_user if isinstance(raw_user, dict) else {}
        raw_roles = body.get("rolesByModel")
        roles: dict[str, Any] = raw_roles if isinstance(raw_roles, dict) else {}
        lines = [
            "# Who Am I",
            "",
            f"- User ID: `{user.get('id', 'unknown')}`",
            f"- Membership ID: `{user.get('membershipId', 'unknown')}`",
            f"- Key scope: **{body.get('keyScope', 'unknown')}**",
            f"- Organization role: **{body.get('orgRole', 'unknown')}**",
            "",
            f"## Model roles ({len(roles):,})",
        ]
        if body.get("rolesByModelTruncated"):
            lines.append("_The API truncated this list; pass `model_id` to scope it._")
        lines.extend(_format_role_lines(roles) or ["_No model roles reported._"])
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# omni_list_api_tokens
# --------------------------------------------------------------------------


class ListApiTokensInput(BaseModel):
    """Input for `omni_list_api_tokens`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: TokenType | None = Field(
        default=None,
        description=(
            "Filter by token type. Omit to return all types. `organization` - organization-level API key, "
            "not tied to a specific user. `personal` - personal access token, acts as a specific user. "
            "`mcp` - MCP OAuth grant issued during the OAuth authorization flow."
        ),
    )
    page_size: int = Field(default=20, ge=1, le=100, description="Number of items to return per page (1-100).")
    cursor: str | None = Field(default=None, description="Cursor for pagination, from a previous page's response.")
    sort_field: SortField = Field(default="createdAt", description="Field to sort by.")
    sort_direction: SortDirection = Field(default="desc", description="Sort direction.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary table, `json` for raw records."
    )


def _format_token_line(item: Mapping[str, Any]) -> str:
    name = item.get("name", "unnamed")
    token_id = item.get("id", "unknown")
    token_type = item.get("type", "unknown")
    enabled = "enabled" if item.get("enabled") else "disabled"
    created = iso_or_na(item.get("createdAt"))
    membership_id = item.get("membershipId")
    owner = f", owner `{membership_id}`" if membership_id else ""
    return f"- **{name}** (`{token_id}`) — type **{token_type}**, **{enabled}**, created {created}{owner}"


@mcp.tool(
    name="omni_list_api_tokens",
    annotations=ToolAnnotations(
        title="List API Tokens",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_api_tokens(params: ListApiTokensInput) -> str:
    """List API tokens in the organization, optionally filtered by type.

    Calls `GET /v1/api-keys`. Lists API tokens in the organization — including Organization API
    keys, Personal Access Tokens, and MCP OAuth grants — with optional filtering by `type`. Only
    metadata is returned; actual secret token values are never included in the API response or in
    this tool's output.

    When to Use:
    - To audit which API tokens exist, who owns them, and whether they are enabled.
    - To find a token's `id` before calling the get/enable/disable/delete tools.
    - To filter by `type` (`organization`, `personal`, `mcp`) to focus an audit.

    When NOT to Use:
    - To retrieve a token's secret value — the API never returns it; only metadata is available.
    - To look up a single token by id — use the get-token tool for that.

    Returns:
    A markdown list (name, id, type, enabled/disabled, created date, owning membership id) with
    pagination info, or the raw `records`/`pageInfo` payload when `response_format` is `json`.
    On failure, an `Error ...` string.

    Examples:
    - All tokens: `{"params": {}}`
    - Only personal tokens: `{"params": {"type": "personal"}}`
    - Next page: `{"params": {"cursor": "<nextCursor from previous call>"}}`

    Error Handling:
    Requires **Organization Admin** permissions — 403 means the caller's role is not
    `ORG_ADMIN` or the token is a PAT that lacks admin rights. 400 means an invalid `type`,
    malformed `cursor` (must be a UUID), or out-of-range `page_size`.
    """
    try:
        query: dict[str, Any] = {
            "pageSize": params.page_size,
            "sortField": params.sort_field,
            "sortDirection": params.sort_direction,
        }
        if params.type is not None:
            query["type"] = params.type
        if params.cursor:
            query["cursor"] = params.cursor
        payload = await get_client().request_json("GET", "/v1/api-keys", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_token_line,
            title="API Tokens",
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# omni_get_api_token
# --------------------------------------------------------------------------


class GetApiTokenInput(BaseModel):
    """Input for `omni_get_api_token`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(..., min_length=1, description="UUID of the API token to retrieve.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for the raw record."
    )


@mcp.tool(
    name="omni_get_api_token",
    annotations=ToolAnnotations(
        title="Get API Token",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_api_token(params: GetApiTokenInput) -> str:
    """Retrieve a single API token by its id.

    Calls `GET /v1/api-keys/{id}`. Returns only metadata — name, type, enabled state, creation
    timestamp, and owning membership id — never the token's secret value.

    When to Use:
    - To check whether a specific token is currently enabled before disabling/deleting it.
    - To confirm a token's type or owner after finding its id via the list tool.

    When NOT to Use:
    - To retrieve the token's secret value — the API never returns it.
    - To list many tokens at once — use the list tool instead.

    Returns:
    A markdown summary of the token's fields, or the raw record when `response_format` is
    `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    Requires **Organization Admin** permissions — 403 means the caller lacks that role or
    authentication failed. 404 means no token with that id exists in the caller's organization
    (the same 404 is returned whether the token belongs to another organization or does not
    exist at all — no tenant information is leaked). 400 means `id` is not a valid UUID.
    """
    try:
        path = f"/v1/api-keys/{quote(params.id, safe='')}"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        lines = [
            "# API Token",
            "",
            f"- ID: `{body.get('id', 'unknown')}`",
            f"- Name: **{body.get('name', 'unnamed')}**",
            f"- Type: **{body.get('type', 'unknown')}**",
            f"- Enabled: **{body.get('enabled')}**",
            f"- Created: {iso_or_na(body.get('createdAt'))}",
            f"- Owning membership ID: `{body.get('membershipId') or 'N/A (organization key)'}`",
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# omni_update_api_token
# --------------------------------------------------------------------------


class UpdateApiTokenInput(BaseModel):
    """Input for `omni_update_api_token`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(..., min_length=1, description="UUID of the API token to enable or disable.")
    enabled: bool = Field(..., description="`false` to disable the token, `true` to re-enable it.")


@mcp.tool(
    name="omni_update_api_token",
    annotations=ToolAnnotations(
        title="Update API Token",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_api_token(params: UpdateApiTokenInput) -> str:
    """Enable or disable an API token without permanently deleting it.

    Calls `PUT /v1/api-keys/{id}` with `{"enabled": <bool>}`. This is idempotent — calling it
    twice with the same body produces the same result, and the response reflects the token's
    current state (even if it was already in the requested state). To permanently revoke a
    token, use the delete tool instead.

    When to Use:
    - To temporarily suspend a token (e.g. suspected leak) without losing its history/id.
    - To re-enable a previously disabled token.

    When NOT to Use:
    - To permanently revoke a token — use the delete tool instead.
    - Note: PATs and MCP tokens are always enabled in practice; disabling mainly applies to
      Organization API keys.

    Returns:
    A short confirmation string with the token's id, name, and new enabled state. Never returns
    the token's secret value (the API does not return it). On failure, an `Error ...` string.

    Examples:
    - Disable a token: `{"params": {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "enabled": false}}`
    - Re-enable a token: `{"params": {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "enabled": true}}`

    Error Handling:
    Requires **Organization Admin** permissions — 403 means the caller lacks that role or
    authentication failed. 404 means the token does not exist or belongs to a different
    organization. 400 means `id` is not a valid UUID, `enabled` is not a boolean, or the request
    body contains an unrecognized key.
    """
    try:
        path = f"/v1/api-keys/{quote(params.id, safe='')}"
        payload = await get_client().request_json("PUT", path, json_body={"enabled": params.enabled})
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        name = body.get("name", "unnamed")
        token_id = body.get("id", params.id)
        state = "enabled" if body.get("enabled") else "disabled"
        return truncate_result(f"API token **{name}** (`{token_id}`) is now **{state}**.")
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# omni_delete_api_token
# --------------------------------------------------------------------------


class DeleteApiTokenInput(BaseModel):
    """Input for `omni_delete_api_token`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(..., min_length=1, description="UUID of the API token to permanently delete (revoke).")


@mcp.tool(
    name="omni_delete_api_token",
    annotations=ToolAnnotations(
        title="Delete API Token",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_delete_api_token(params: DeleteApiTokenInput) -> str:
    """Permanently delete (revoke) an API token.

    Calls `DELETE /v1/api-keys/{id}`. Works for all token types — Organization API keys,
    Personal Access Tokens, and MCP OAuth grants. This is the same as revoking a token in the
    Omni app and cannot be undone. Concurrent deletes are safe: the first caller receives `200`
    and any subsequent caller receives `404` consistently. An Organization Admin can delete the
    token they are currently authenticated with, which is useful for self-service rotation —
    but note that doing so will invalidate the very key this server is using.

    When to Use:
    - To permanently revoke a leaked or no-longer-needed token.
    - To rotate a token (delete the old one after creating/distributing a new one).

    When NOT to Use:
    - To temporarily suspend a token — use the enable/disable tool instead; this action cannot
      be undone.

    Returns:
    A short confirmation string with the deleted token's id. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    Requires **Organization Admin** permissions — 403 means the caller lacks that role. 404
    means no token with that id exists in the caller's organization, or another admin already
    revoked it. 400 means `id` is not a valid UUID or the Authorization header is malformed.
    """
    try:
        path = f"/v1/api-keys/{quote(params.id, safe='')}"
        payload = await get_client().request_json("DELETE", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        message = body.get("message", "API token revoked")
        return truncate_result(f"{message} (id `{params.id}`).")
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# omni_list_user_attributes
# --------------------------------------------------------------------------


class ListUserAttributesInput(BaseModel):
    """Input for `omni_list_user_attributes`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary table, `json` for raw records."
    )


def _format_attribute_row(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name", "unnamed"),
        "label": item.get("label", ""),
        "type": item.get("type", "unknown"),
        "multiple_values": item.get("multiple_values", False),
        "default_value": item.get("default_value"),
        "description": item.get("description") or "",
        "system": item.get("system", False),
        "id": item.get("id") or "",
    }


@mcp.tool(
    name="omni_list_user_attributes",
    annotations=ToolAnnotations(
        title="List User Attributes",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_user_attributes(params: ListUserAttributesInput) -> str:
    """List all user attribute definitions in the organization.

    Calls `GET /v1/user-attributes`. Retrieves all user attribute definitions, including both
    custom-defined and system-defined attributes (e.g. `omni_user_id`, `omni_user_email`,
    `omni_is_org_admin`). User attributes enable row-level security filtering, personalize
    dashboard content, and configure per-user database credentials. Useful for embed
    integrations to discover available attributes for setting values in SSO sessions. This
    endpoint does not paginate — all records are returned in one response.

    When to Use:
    - To discover which user attributes exist before setting them for an embed SSO session.
    - To distinguish system-defined attributes (`system: true`) from custom ones.
    - To check an attribute's `type` (`String`/`Number`) and whether it accepts multiple values.

    When NOT to Use:
    - To read or set a specific user's attribute *values* — this only lists attribute
      *definitions* (schema), not per-user values.

    Returns:
    A markdown table of all attribute definitions (name, label, type, multiple_values,
    default_value, description, system, id), or the raw `records` payload when
    `response_format` is `json`. This endpoint has no pagination — the response always
    contains the full list.

    Examples:
    - `{"params": {}}`
    - `{"params": {"response_format": "json"}}`

    Error Handling:
    Requires **Organization Admin** permissions — 403 means the caller lacks that role. 401
    means the API key is missing or invalid.
    """
    try:
        payload = await get_client().request_json("GET", "/v1/user-attributes")
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        raw_records = body.get("records")
        records: list[Any] = raw_records if isinstance(raw_records, list) else []

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"count": len(records), "records": records}))

        rows = [_format_attribute_row(item) for item in records]
        lines = [
            "# User Attributes",
            "",
            f"**{len(rows):,}** attribute definition(s). This endpoint does not paginate.",
            "",
        ]
        if rows:
            lines.append(markdown_table(rows))
        else:
            lines.append("_No user attributes defined._")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)
