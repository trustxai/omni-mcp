"""Tools for the users, embed users, and email-only user endpoints.

Standard and embed users live under the SCIM 2.0 surface (`/scim/v2/users`,
`/scim/v2/embed/users`); email-only users are a plain REST list
(`/v1/users/email-only`). SCIM list endpoints page with `startIndex`/`count`,
not the cursor shape used elsewhere in this server — see `_scim_list_response`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, cursor_paginated_response, iso_or_na, to_json, truncate_result
from omni_mcp.server import mcp

#: One line on what this module covers, for the generated README catalogue.
MODULE_SUMMARY = "Standard, embed and email-only users (SCIM)"

#: Omni's SCIM extension property carrying custom user attributes as key/value pairs.
USER_ATTRIBUTE_URN = "urn:omni:params:1.0:UserAttribute"
#: Omni's SCIM extension schema carrying additional metadata (currently just `lastLogin`).
USER_EXTENSION_URN = "urn:omni:params:scim:schemas:extension:user:2.0"
#: Required `schemas` value for a SCIM PATCH request body.
PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


# ---------------------------------------------------------------------------
# Shared rendering helpers
# ---------------------------------------------------------------------------


def _bool_text(value: Any) -> str:
    """Render a SCIM boolean the way the API spells it — `true`/`false`, not Python's `True`/`False`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "N/A" if value is None else str(value)


def _emails_summary(body: Mapping[str, Any]) -> str:
    emails = body.get("emails")
    if not isinstance(emails, list) or not emails:
        return "none"
    parts = []
    for entry in emails:
        if isinstance(entry, dict):
            value = entry.get("value", "unknown")
            suffix = " (primary)" if entry.get("primary") else ""
            parts.append(f"{value}{suffix}")
    return ", ".join(parts) if parts else "none"


def _groups_summary(body: Mapping[str, Any]) -> str:
    groups = body.get("groups")
    if not isinstance(groups, list) or not groups:
        return "none"
    parts = []
    for entry in groups:
        if isinstance(entry, dict):
            parts.append(f"{entry.get('display', 'unknown')} (`{entry.get('value', '?')}`)")
    return ", ".join(parts) if parts else "none"


def _user_attributes(body: Mapping[str, Any]) -> dict[str, Any]:
    attrs = body.get(USER_ATTRIBUTE_URN)
    return attrs if isinstance(attrs, dict) else {}


def _last_login(body: Mapping[str, Any]) -> str:
    extension = body.get(USER_EXTENSION_URN)
    if isinstance(extension, dict):
        return iso_or_na(extension.get("lastLogin"))
    return "N/A"


def _render_scim_detail(
    title: str, body: Mapping[str, Any], fmt: ResponseFormat, extra_lines: Sequence[str] = ()
) -> str:
    """Render one ScimUser/ScimEmbedUser resource — shared by get_user and get_embed_user."""
    if fmt is ResponseFormat.JSON:
        return truncate_result(to_json(body))
    raw_meta = body.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    lines = [
        f"# {title}",
        "",
        f"- ID: `{body.get('id', 'unknown')}`",
        f"- userName: `{body.get('userName', 'unknown')}`",
        f"- displayName: **{body.get('displayName', 'unknown')}**",
        f"- active: {_bool_text(body.get('active'))}",
        f"- emails: {_emails_summary(body)}",
        f"- groups: {_groups_summary(body)}",
        f"- created: {iso_or_na(meta.get('created'))}",
        f"- lastModified: {iso_or_na(meta.get('lastModified'))}",
    ]
    lines.extend(extra_lines)
    attributes = _user_attributes(body)
    lines.append(f"- user attributes: {to_json(attributes)}" if attributes else "- user attributes: none")
    last_login = _last_login(body)
    if last_login != "N/A":
        lines.append(f"- lastLogin: {last_login}")
    return truncate_result("\n".join(lines))


def _format_user_row(item: Mapping[str, Any]) -> str:
    return (
        f"- **{item.get('displayName', 'unnamed')}** (`{item.get('id')}`) — "
        f"userName `{item.get('userName', 'unknown')}`, active: {_bool_text(item.get('active'))}"
    )


def _format_embed_user_row(item: Mapping[str, Any]) -> str:
    return (
        f"- **{item.get('displayName', 'unnamed')}** (`{item.get('id')}`) — "
        f"embedExternalId `{item.get('embedExternalId', 'unknown')}`, "
        f"entity `{item.get('embedEntity', 'unknown')}`, active: {_bool_text(item.get('active'))}"
    )


def _format_email_only_user_row(item: Mapping[str, Any]) -> str:
    attrs = item.get("user_attributes")
    count = len(attrs) if isinstance(attrs, dict) else 0
    return f"- **{item.get('email', 'unknown')}** (user_id `{item.get('user_id')}`) — {count} attribute(s)"


def _scim_list_response(
    *,
    title: str,
    body: Mapping[str, Any],
    fmt: ResponseFormat,
    item_formatter: Callable[[Mapping[str, Any]], str],
) -> str:
    """Render a SCIM `ListResponse` envelope (`Resources`/`totalResults`/`startIndex`/`itemsPerPage`).

    Unlike `cursor_paginated_response`, SCIM list endpoints page with
    `startIndex`/`count` rather than an opaque cursor, so the "next page" hint
    is the arithmetic `startIndex + itemsPerPage`.
    """
    raw_resources = body.get("Resources")
    resources = [item for item in raw_resources if isinstance(item, Mapping)] if isinstance(raw_resources, list) else []
    total = body.get("totalResults")
    per_page = body.get("itemsPerPage")
    start_index = body.get("startIndex")

    if fmt is ResponseFormat.JSON:
        # The SCIM envelope is returned verbatim — `Resources`, `schemas` and
        # all — so a caller can hand it straight to a SCIM-aware consumer.
        return truncate_result(to_json(body))

    lines = [f"# {title}", ""]
    if isinstance(total, int):
        lines.append(
            f"Showing **{len(resources):,}** of **{total:,}** record(s) "
            f"(startIndex **{start_index}**, itemsPerPage **{per_page}**)."
        )
    else:
        lines.append(f"Showing **{len(resources):,}** record(s).")
    if isinstance(total, int) and isinstance(start_index, int) and isinstance(per_page, int) and per_page > 0:
        next_index = start_index + per_page
        if next_index <= total:
            lines.append(f"More available — pass `start_index={next_index}` to fetch the next page.")
        else:
            lines.append("End of results.")
    lines.append("")
    if resources:
        lines.extend(item_formatter(item) for item in resources)
    else:
        lines.append("_No records._")
    return truncate_result("\n".join(lines))


# ---------------------------------------------------------------------------
# List email-only users — GET /v1/users/email-only
# ---------------------------------------------------------------------------


class ListEmailOnlyUsersInput(BaseModel):
    """Input for `omni_list_email_only_users`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str | None = Field(default=None, description="Filter users by email (partial match, case-insensitive).")
    cursor: str | None = Field(default=None, description="Pagination cursor from a previous response.")
    page_size: int = Field(default=20, ge=1, le=20, description="Results per page (1-20).")
    sort_direction: Literal["asc", "desc"] = Field(
        default="desc",
        description="Sort order: `asc` for ascending (A-Z), `desc` for descending (Z-A).",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for the raw records."
    )


@mcp.tool(
    name="omni_list_email_only_users",
    annotations=ToolAnnotations(
        title="List Email-Only Users",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_email_only_users(params: ListEmailOnlyUsersInput) -> str:
    """List email-only users and their user attributes.

    Email-only users are lightweight identities (e.g. schedule/routine
    recipients) that never log in to Omni and have no SCIM membership record —
    this is a different population from the SCIM users listed by
    `omni_list_users`.

    When to Use:
    - To find the membership ID behind an email-only recipient's address.
    - To audit which custom user attributes are set on email-only identities.

    When NOT to Use:
    - To list users who can log in — use `omni_list_users`.
    - To page through more than 20 records at once — this endpoint caps
      `page_size` at 20; call again with the returned `cursor`.

    Returns:
    A markdown summary (record count, next-cursor hint, one line per user with
    their `user_id` and attribute count) or, with `response_format="json"`, the
    raw `pageInfo`/`records` payload.

    Examples:
    - `{"params": {}}`
    - `{"params": {"email": "blob", "page_size": 10}}`
    - `{"params": {"cursor": "bob@example.com", "sort_direction": "asc"}}`

    Error Handling:
    401 means `OMNI_API_KEY` is missing or invalid. Accepts either an
    Organization API key or a Personal Access Token.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size, "sortDirection": params.sort_direction}
        if params.email:
            query["email"] = params.email
        if params.cursor:
            query["cursor"] = params.cursor
        payload = await get_client().request_json("GET", "/v1/users/email-only", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_email_only_user_row,
            title="Email-only users",
        )
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Create user — POST /scim/v2/users
# ---------------------------------------------------------------------------


class CreateUserInput(BaseModel):
    """Input for `omni_create_user`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_name: str | None = Field(
        default=None,
        description="The user's email address (SCIM `userName`). Required unless `scim_body` is provided.",
    )
    display_name: str | None = Field(
        default=None, description="The user's display name. Required unless `scim_body` is provided."
    )
    user_attributes: dict[str, Any] | None = Field(
        default=None,
        description=(
            f"User attributes as key/value pairs (sent as `{USER_ATTRIBUTE_URN}`), keyed by the "
            "attribute's Reference ID from the User attributes admin page."
        ),
    )
    scim_body: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw SCIM request body override, sent verbatim. Mutually exclusive with "
            "`user_name`/`display_name`/`user_attributes`."
        ),
    )

    @model_validator(mode="after")
    def _reject_a_mixed_override(self) -> CreateUserInput:
        if self.scim_body is not None and (
            self.user_name is not None or self.display_name is not None or self.user_attributes is not None
        ):
            raise ValueError(
                "scim_body is a raw override: send it on its own, without user_name, display_name or user_attributes."
            )
        return self


@mcp.tool(
    name="omni_create_user",
    annotations=ToolAnnotations(
        title="Create User",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_user(params: CreateUserInput) -> str:
    """Create a standard SCIM user.

    Builds the SCIM 2.0 request body (`userName`, `displayName`, and the
    `urn:omni:params:1.0:UserAttribute` extension) from the first-class fields,
    or sends `scim_body` verbatim when provided. To manage model/connection
    role assignments for the new user, use the user model role tools instead.

    When to Use:
    - To provision a new user account ahead of an SSO or manual invite.
    - To set initial custom user attributes at creation time.

    When NOT to Use:
    - To create an embed user — embed users are provisioned through embed
      SSO/JWT, not this endpoint.
    - To change an existing user — use `omni_update_user` or `omni_replace_user`.

    Returns:
    A confirmation string with the new user's `displayName` and `id`, or an
    `Error ...` string on failure.

    Examples:
    - `{"params": {"user_name": "blob.ross@blobsrus.com", "display_name": "Blob Ross"}}`
    - `{"params": {"user_name": "blob.ross@blobsrus.com", "display_name": "Blob Ross", "user_attributes": {"good_blob": "true"}}}`
    - `{"params": {"scim_body": {"userName": "x@y.com", "displayName": "X Y"}}}`

    Error Handling:
    429 means the 60 requests/minute rate limit was hit. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        if params.scim_body is not None:
            body: dict[str, Any] = params.scim_body
        else:
            if not params.user_name or not params.display_name:
                return "Error: user_name and display_name are required unless scim_body is provided."
            body = {"userName": params.user_name, "displayName": params.display_name}
            if params.user_attributes:
                body[USER_ATTRIBUTE_URN] = params.user_attributes
        payload = await get_client().request_json("POST", "/scim/v2/users", json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        display_name = result.get("displayName", params.display_name or "unknown")
        user_id = result.get("id", "unknown")
        return truncate_result(f"Created user **{display_name}** (id `{user_id}`).")
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# List users — GET /scim/v2/users
# ---------------------------------------------------------------------------


class ListUsersInput(BaseModel):
    """Input for `omni_list_users`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    filter: str | None = Field(
        default=None,
        description=(
            'SCIM filter on `userName`, e.g. `userName eq "blob.ross@blobsrus.co"` or '
            '`userName co "smith"`. Attribute names, operator names, and email comparisons are '
            "case-insensitive."
        ),
    )
    count: int = Field(default=100, ge=1, description="The number of users to return per page.")
    start_index: int = Field(
        default=1, ge=1, description="1-based index of the starting point of the sorted result list."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for the raw SCIM resources."
    )


@mcp.tool(
    name="omni_list_users",
    annotations=ToolAnnotations(
        title="List Users",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_users(params: ListUsersInput) -> str:
    """List standard users in the organization, sorted by creation time.

    Uses SCIM `startIndex`/`count` pagination, not the cursor pagination used
    elsewhere in this server. Use `omni_list_embed_users` to retrieve embed
    users instead.

    When to Use:
    - To find a user's `id` before calling `omni_get_user`/`omni_update_user`.
    - To search for a user by exact or partial `userName` with `filter`.

    When NOT to Use:
    - To list embed users — use `omni_list_embed_users`.
    - To list users who have never logged in and have no SCIM record — use
      `omni_list_email_only_users`.

    Returns:
    A markdown summary (record count, next `start_index` hint, one line per
    user with `id`/`userName`/`active`) or, with `response_format="json"`, the
    raw SCIM `Resources` array plus paging fields.

    Examples:
    - `{"params": {}}`
    - `{"params": {"filter": "userName co \\"smith\\"", "count": 50}}`
    - `{"params": {"start_index": 51, "count": 50}}`

    Error Handling:
    400 means the `filter` expression is malformed. 429 means the 60
    requests/minute rate limit was hit. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        query: dict[str, Any] = {"count": params.count, "startIndex": params.start_index}
        if params.filter:
            query["filter"] = params.filter
        payload = await get_client().request_json("GET", "/scim/v2/users", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return _scim_list_response(
            title="Users", body=body, fmt=params.response_format, item_formatter=_format_user_row
        )
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Retrieve user — GET /scim/v2/users/{userId}
# ---------------------------------------------------------------------------


class GetUserInput(BaseModel):
    """Input for `omni_get_user`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(..., description="The ID of the user to retrieve.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for the raw SCIM resource."
    )


@mcp.tool(
    name="omni_get_user",
    annotations=ToolAnnotations(
        title="Retrieve User",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_user(params: GetUserInput) -> str:
    """Retrieve a standard user using their unique ID.

    When to Use:
    - To inspect a single user's `active` state, emails, groups, and custom
      user attributes before updating or deleting them.

    When NOT to Use:
    - To retrieve an embed user — use `omni_get_embed_user`.
    - To look up many users at once — use `omni_list_users` with `filter`.

    Returns:
    A markdown detail view (id, userName, displayName, active, emails,
    groups, timestamps, user attributes, last login) or, with
    `response_format="json"`, the raw SCIM resource.

    Examples:
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`

    Error Handling:
    404 means no user exists with that ID on this instance. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        path = f"/scim/v2/users/{quote(params.user_id, safe='')}"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return _render_scim_detail("User", body, params.response_format)
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Replace user — PUT /scim/v2/users/{userId}
# ---------------------------------------------------------------------------


class ReplaceUserInput(BaseModel):
    """Input for `omni_replace_user`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(..., description="The ID of the user to replace.")
    user_name: str | None = Field(
        default=None,
        description="The user's email address. Required unless `scim_body` is provided.",
    )
    display_name: str | None = Field(
        default=None,
        description=(
            "The user's display name. Per SCIM PUT semantics, omitting this resets it to its "
            "default unless `scim_body` is provided."
        ),
    )
    active: bool | None = Field(
        default=None,
        description=(
            "Whether the user is active; defaults to `true` when omitted. Set to `false` to "
            "deactivate the user — this is NOT reversible (see `omni_update_user`)."
        ),
    )
    user_attributes: dict[str, Any] | None = Field(
        default=None,
        description=(
            f"User attributes (`{USER_ATTRIBUTE_URN}`). Per SCIM PUT semantics, omitting this "
            "clears any existing attributes unless `scim_body` is provided."
        ),
    )
    scim_body: dict[str, Any] | None = Field(
        default=None,
        description=("Raw SCIM request body override, sent verbatim. Mutually exclusive with the fields above."),
    )

    @model_validator(mode="after")
    def _reject_a_mixed_override(self) -> ReplaceUserInput:
        if self.scim_body is not None and (
            self.user_name is not None
            or self.display_name is not None
            or self.active is not None
            or self.user_attributes is not None
        ):
            raise ValueError(
                "scim_body is a raw override: send it on its own, without user_name, display_name, "
                "active or user_attributes."
            )
        return self


@mcp.tool(
    name="omni_replace_user",
    annotations=ToolAnnotations(
        title="Replace User",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_replace_user(params: ReplaceUserInput) -> str:
    """Replace the specified user's entire SCIM resource.

    Per the SCIM 2.0 specification, `PUT` replaces the whole resource: any
    optional attribute omitted from the request (`display_name`, `active`,
    `user_attributes`) is reset to its default rather than left unchanged. To
    update individual attributes without affecting others, use
    `omni_update_user` instead.

    When to Use:
    - To fully overwrite a user's profile in one call, when you intend to set
      every attribute explicitly.

    When NOT to Use:
    - To change just one or two fields — use `omni_update_user`, or this call
      will silently clear the fields you did not pass.

    Returns:
    A confirmation string with the user's `displayName`, `id`, and resulting
    `active` state, or an `Error ...` string on failure.

    Examples:
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "user_name": "blob.ross@blobsrus.co", "display_name": "Blob Ross"}}`
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "user_name": "blob.ross@blobsrus.co", "active": false}}`

    Error Handling:
    400 means the request body is invalid (e.g. missing `userName`). 404 means
    no user exists with that ID. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        if params.scim_body is not None:
            body: dict[str, Any] = params.scim_body
        else:
            if not params.user_name:
                return "Error: user_name is required unless scim_body is provided."
            body = {"userName": params.user_name}
            if params.display_name is not None:
                body["displayName"] = params.display_name
            if params.active is not None:
                body["active"] = params.active
            if params.user_attributes is not None:
                body[USER_ATTRIBUTE_URN] = params.user_attributes
        path = f"/scim/v2/users/{quote(params.user_id, safe='')}"
        payload = await get_client().request_json("PUT", path, json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        display_name = result.get("displayName", params.display_name or "unknown")
        user_id = result.get("id", params.user_id)
        active = result.get("active")
        return truncate_result(f"Replaced user **{display_name}** (id `{user_id}`) — active: {_bool_text(active)}.")
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Update user — PATCH /scim/v2/users/{userId}
# ---------------------------------------------------------------------------


class UpdateUserInput(BaseModel):
    """Input for `omni_update_user`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(..., description="The ID of the user to update.")
    active: bool | None = Field(
        default=None,
        description=(
            "Set to `false` to revoke the user's membership — this is NOT reversible: their "
            "schedules are deleted (unless transferred first) and their Personal Access Tokens "
            "are disabled. Set to `true` to (re)activate."
        ),
    )
    display_name: str | None = Field(default=None, description="Replace the user's display name.")
    user_name: str | None = Field(
        default=None,
        min_length=1,
        description="Replace the user's `userName` (their email address); sent as a `replace` op on path `userName`.",
    )
    user_attributes: dict[str, Any] | None = Field(
        default=None,
        description=f"Replace the user's attributes (`{USER_ATTRIBUTE_URN}`), keyed by attribute Reference ID.",
    )
    operations: list[dict[str, Any]] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Raw SCIM `Operations` list override (each `{op, path?, value}`, per RFC 7644 "
            "§3.5.2). Mutually exclusive with the fields above; must hold at least one operation."
        ),
    )

    @model_validator(mode="after")
    def _reject_a_mixed_override(self) -> UpdateUserInput:
        if self.operations is not None and (
            self.active is not None
            or self.display_name is not None
            or self.user_name is not None
            or self.user_attributes is not None
        ):
            raise ValueError(
                "operations is a raw override: send it on its own, without active, display_name, "
                "user_name or user_attributes."
            )
        return self


@mcp.tool(
    name="omni_update_user",
    annotations=ToolAnnotations(
        title="Update User",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_update_user(params: UpdateUserInput) -> str:
    """Partially update the specified user via SCIM PATCH operations.

    Unlike `omni_replace_user`, attributes not referenced by an operation are
    left unchanged. Builds the SCIM `Operations` list from `active`,
    `display_name`, `user_name`, and `user_attributes` (as `replace`
    operations), or sends `operations` verbatim when provided — `operations` is
    a raw override and cannot be combined with those fields.

    Omni does not have a reversible "deactivated" state: setting `active` to
    `false` revokes the user's membership permanently — their schedules are
    deleted (unless transferred first) and their Personal Access Tokens are
    disabled.

    When to Use:
    - To change one or two fields (e.g. rename a user, or revoke membership)
      without resetting anything else.

    When NOT to Use:
    - To overwrite the entire resource in one call — use `omni_replace_user`.
    - To revoke membership casually — this is not reversible; read the warning
      above first.

    Returns:
    A confirmation string with the user's `displayName`, `id`, and resulting
    `active` state, or an `Error ...` string on failure.

    Examples:
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "active": false}}`
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "display_name": "Blob Ross"}}`
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "user_name": "blob.ross@blobsrus.co"}}`
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c", "operations": [{"op": "replace", "path": "active", "value": false}]}}`

    Error Handling:
    400 means the patch operations are invalid (bad `op`, missing `value`, or
    an unrecognised `path`). 404 means no user exists with that ID. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        if params.operations is not None:
            operations = params.operations
        else:
            operations = []
            if params.active is not None:
                operations.append({"op": "replace", "path": "active", "value": params.active})
            if params.display_name is not None:
                operations.append({"op": "replace", "path": "displayName", "value": params.display_name})
            if params.user_name is not None:
                operations.append({"op": "replace", "path": "userName", "value": params.user_name})
            if params.user_attributes is not None:
                operations.append({"op": "replace", "path": USER_ATTRIBUTE_URN, "value": params.user_attributes})
            if not operations:
                return "Error: provide at least one of active, display_name, user_name, user_attributes, or operations."
        body = {"schemas": [PATCH_OP_SCHEMA], "Operations": operations}
        path = f"/scim/v2/users/{quote(params.user_id, safe='')}"
        payload = await get_client().request_json("PATCH", path, json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        display_name = result.get("displayName", params.display_name or "unknown")
        user_id = result.get("id", params.user_id)
        active = result.get("active")
        return truncate_result(f"Updated user **{display_name}** (id `{user_id}`) — active: {_bool_text(active)}.")
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Delete user — DELETE /scim/v2/users/{userId}
# ---------------------------------------------------------------------------


class DeleteUserInput(BaseModel):
    """Input for `omni_delete_user`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(..., description="The ID of the user to delete.")


@mcp.tool(
    name="omni_delete_user",
    annotations=ToolAnnotations(
        title="Delete User",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_user(params: DeleteUserInput) -> str:
    """Delete the specified standard user.

    Deleting a user revokes their membership, which is **not reversible**:
    their schedules are deleted (unless transferred first) and their Personal
    Access Tokens are disabled. Use `omni_delete_embed_user` to delete embed
    users instead.

    When to Use:
    - To permanently remove a user's membership from the organization, after
      confirming any owned schedules have been transferred.

    When NOT to Use:
    - To temporarily suspend a user — Omni has no reversible deactivated
      state; think carefully before calling this or `omni_update_user` with
      `active=false`.
    - To delete an embed user — use `omni_delete_embed_user`.

    Returns:
    A confirmation string with the deleted user's `id`, or an `Error ...`
    string on failure.

    Examples:
    - `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`

    Error Handling:
    404 means no user exists with that ID. 429 means the 60 requests/minute
    rate limit was hit. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        path = f"/scim/v2/users/{quote(params.user_id, safe='')}"
        await get_client().request_json("DELETE", path)
        return truncate_result(f"Deleted user (id `{params.user_id}`).")
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# List embed users — GET /scim/v2/embed/users
# ---------------------------------------------------------------------------


class ListEmbedUsersInput(BaseModel):
    """Input for `omni_list_embed_users`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    filter: str | None = Field(
        default=None,
        description=(
            'SCIM filter on `userName` or `embedExternalId`, e.g. `userName eq "blob.ross@blobsrus.co"` '
            'or `embedExternalId co "sales"`. Attribute and operator names are case-insensitive; '
            "`embedExternalId` comparisons are case-sensitive, per the SCIM spec."
        ),
    )
    count: int = Field(default=100, ge=1, description="The number of embed users to return per page.")
    start_index: int = Field(
        default=1, ge=1, description="1-based index of the starting point of the sorted result list."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for the raw SCIM resources."
    )


@mcp.tool(
    name="omni_list_embed_users",
    annotations=ToolAnnotations(
        title="List Embed Users",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_embed_users(params: ListEmbedUsersInput) -> str:
    """List embed users in the organization, sorted by creation time.

    Uses SCIM `startIndex`/`count` pagination. Use `omni_list_users` to
    retrieve standard users instead.

    When to Use:
    - To find an embed user's `id` by their `embedExternalId` before calling
      `omni_get_embed_user`/`omni_delete_embed_user`.

    When NOT to Use:
    - To list standard (non-embed) users — use `omni_list_users`.

    Returns:
    A markdown summary (record count, next `start_index` hint, one line per
    embed user with `id`/`embedExternalId`/`embedEntity`/`active`) or, with
    `response_format="json"`, the raw SCIM `Resources` array plus paging
    fields.

    Examples:
    - `{"params": {}}`
    - `{"params": {"filter": "embedExternalId co \\"sales\\"", "count": 50}}`

    Error Handling:
    400 means the `filter` expression is malformed. 429 means the 60
    requests/minute rate limit was hit. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        query: dict[str, Any] = {"count": params.count, "startIndex": params.start_index}
        if params.filter:
            query["filter"] = params.filter
        payload = await get_client().request_json("GET", "/scim/v2/embed/users", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return _scim_list_response(
            title="Embed users", body=body, fmt=params.response_format, item_formatter=_format_embed_user_row
        )
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Retrieve embed user — GET /scim/v2/embed/users/{userId}
# ---------------------------------------------------------------------------


class GetEmbedUserInput(BaseModel):
    """Input for `omni_get_embed_user`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(..., description="The ID of the embed user to retrieve.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for the raw SCIM resource."
    )


@mcp.tool(
    name="omni_get_embed_user",
    annotations=ToolAnnotations(
        title="Retrieve Embed User",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_embed_user(params: GetEmbedUserInput) -> str:
    """Retrieve an embed user using their unique ID.

    Use `omni_get_user` to retrieve standard (non-embed) users instead.

    When to Use:
    - To inspect a single embed user's `embedExternalId`, `embedEntity`, and
      group memberships before deleting them.

    When NOT to Use:
    - To retrieve a standard user — use `omni_get_user`.

    Returns:
    A markdown detail view (id, userName, displayName, active, emails,
    groups, timestamps, plus `embedEntity`/`embedExternalId`/`embedEmail`) or,
    with `response_format="json"`, the raw SCIM resource.

    Examples:
    - `{"params": {"user_id": "2212aecf-a2ba-4d99-b23b-f615bc4c6522"}}`

    Error Handling:
    404 means no embed user exists with that ID on this instance. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        path = f"/scim/v2/embed/users/{quote(params.user_id, safe='')}"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        extra_lines = [
            f"- embedEntity: `{body.get('embedEntity', 'unknown')}`",
            f"- embedExternalId: `{body.get('embedExternalId', 'unknown')}`",
            f"- embedEmail: {body.get('embedEmail') or 'none'}",
        ]
        return _render_scim_detail("Embed user", body, params.response_format, extra_lines)
    except Exception as exc:
        return handle_api_error(exc)


# ---------------------------------------------------------------------------
# Delete embed user — DELETE /scim/v2/embed/users/{userId}
# ---------------------------------------------------------------------------


class DeleteEmbedUserInput(BaseModel):
    """Input for `omni_delete_embed_user`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(..., description="The ID of the embed user to delete.")


@mcp.tool(
    name="omni_delete_embed_user",
    annotations=ToolAnnotations(
        title="Delete Embed User",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_embed_user(params: DeleteEmbedUserInput) -> str:
    """Delete the specified embed user.

    Use `omni_delete_user` to delete standard (non-embed) users instead.

    When to Use:
    - To permanently remove an embed user's identity, e.g. after the external
      end-user it represents is deprovisioned.

    When NOT to Use:
    - To delete a standard user — use `omni_delete_user`.

    Returns:
    A confirmation string with the deleted embed user's `id`, or an
    `Error ...` string on failure.

    Examples:
    - `{"params": {"user_id": "2212aecf-a2ba-4d99-b23b-f615bc4c6522"}}`

    Error Handling:
    429 means the 60 requests/minute rate limit was hit. Requires an Organization API key; Personal Access Tokens are not accepted for this endpoint and get a 403.
    """
    try:
        path = f"/scim/v2/embed/users/{quote(params.user_id, safe='')}"
        await get_client().request_json("DELETE", path)
        return truncate_result(f"Deleted embed user (id `{params.user_id}`).")
    except Exception as exc:
        return handle_api_error(exc)
