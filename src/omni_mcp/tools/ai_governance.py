"""Tools for the AI credit control, credit usage, and model suggestion endpoints.

Covers three API tags: AI Credit Controls, AI Credit Usage, and AI Model
Suggestions. Every endpoint here requires **Organization Admin** permissions
(some also require AI / per-user / per-entity-group credit limits to be
enabled for the organization) — see each tool's `Error Handling` section.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
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

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _ms_to_iso(value: Any) -> str:
    """Render a Unix-ms timestamp as an ISO-8601 UTC string, or `N/A` when absent."""
    if value is None:
        return "N/A"
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)


def _credit_controls_rows(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Key/value rows summarizing an `AiCreditControlsResponse` payload."""
    downgrade = body.get("downgradeCredits")
    shutoff = body.get("shutoffCredits")
    user_default = body.get("userDefaultCredits")
    entity_group_default = body.get("entityGroupDefaultCredits")
    return [
        {"Field": "Account credit limit", "Value": body.get("accountCreditLimit", "unknown")},
        {"Field": "Credits used (current period)", "Value": body.get("creditsUsed", "unknown")},
        {"Field": "Downgrade threshold", "Value": "off" if downgrade is None else downgrade},
        {"Field": "Shutoff threshold", "Value": "off" if shutoff is None else shutoff},
        {"Field": "Default per-user credit limit", "Value": "unlimited" if user_default is None else user_default},
        {
            "Field": "Default per-entity-group credit limit",
            "Value": "unlimited" if entity_group_default is None else entity_group_default,
        },
        {"Field": "Billing period start", "Value": _ms_to_iso(body.get("periodStart"))},
        {"Field": "Billing period end", "Value": _ms_to_iso(body.get("periodEnd"))},
    ]


def _render_credit_controls(body: Mapping[str, Any], *, heading: str) -> str:
    return truncate_result(f"# {heading}\n\n{markdown_table(_credit_controls_rows(body))}")


def _run_markdown(run: Mapping[str, Any] | None) -> str:
    """Render a `SuggestionRun` object (or `None`) as markdown."""
    if run is None:
        return "_No generation runs yet for this model._"
    triggered_by = run.get("triggeredBy") or {}
    trigger_line = f"- Trigger: **{run.get('triggerSource', 'unknown')}**"
    if triggered_by:
        who = triggered_by.get("name") or triggered_by.get("userId") or "unknown"
        trigger_line += f" (by {who})"
    lines = [
        f"- Run `{run.get('id', 'unknown')}` — status: **{run.get('status', 'unknown')}**",
        trigger_line,
        f"- Created: {iso_or_na(run.get('createdAt'))}",
        f"- Execution started: {iso_or_na(run.get('executionStartedAt'))}",
        f"- Completed: {iso_or_na(run.get('completedAt'))}",
    ]
    error = run.get("error")
    if error:
        lines.append(f"- Error: {error}")
    return "\n".join(lines)


def _suggestion_summary(item: Mapping[str, Any]) -> str:
    """One suggestion rendered as title, status, evidence count, proposed-changes summary."""
    proposed = item.get("proposedChanges") or {}
    edits = proposed.get("edits")
    edit_count = len(edits) if isinstance(edits, list) else 0
    evidence = item.get("evidence")
    evidence_count = len(evidence) if isinstance(evidence, list) else 0
    status = "ignored" if item.get("ignoredAt") else "active"
    lines = [
        f"- **{item.get('title', 'untitled')}** (`{item.get('id', 'unknown')}`) — status: {status}, "
        f"priority {item.get('priority', '?')}, category `{item.get('category', 'unknown')}`",
        f"  - {edit_count} proposed change(s), {evidence_count} evidence item(s)",
    ]
    if item.get("ignoreReason"):
        lines.append(f"  - Dismiss reason: {item.get('ignoreReason')}")
    return "\n".join(lines)


def _success_confirmation(body: Any, *, action: str, subject: str) -> str:
    """Short confirmation string for the `SuccessResponse`-shaped mutations."""
    # Only an explicit `success: false` contradicts the 2xx — the same rule the
    # folder, model-git and document tools apply. An absent flag, an empty body
    # and a non-JSON body all leave the mutation confirmed.
    ok = body.get("success") is not False if isinstance(body, dict) else True
    if ok:
        return f"{action} {subject}."
    return f"{action.rstrip('.')} request for {subject} completed, but the API did not confirm success."


# --------------------------------------------------------------------------
# AI Credit Controls: get / update org-wide thresholds and defaults
# --------------------------------------------------------------------------


class GetAiCreditControlsInput(BaseModel):
    """Input for `omni_get_ai_credit_controls` (no parameters beyond output format)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary table, `json` for the raw `AiCreditControlsResponse`.",
    )


@mcp.tool(
    name="omni_get_ai_credit_controls",
    annotations=ToolAnnotations(
        title="Get AI Credit Controls",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_ai_credit_controls(params: GetAiCreditControlsInput) -> str:
    """Get the organization's AI credit controls, thresholds, and current usage.

    Calls `GET /v1/ai/credit-controls`. Returns the downgrade and shutoff
    thresholds, the default per-user and per-entity-group AI credit limits,
    plus read-only context: the account-wide credit limit, this
    organization's usage for the current billing period, and the period
    bounds. This endpoint can be used for embedded instances.

    When to Use:
    - To see the current downgrade/shutoff thresholds before changing them.
    - To check how much of the account's AI credit budget has been used this period.
    - To find the default per-user / per-entity-group limits before overriding one.

    When NOT to Use:
    - To read one specific user's or entity group's limit — use
      `omni_list_user_ai_credit_limits` / `omni_list_entity_group_ai_credit_limits`.
    - To read credit *usage* for specific users or entity groups — use
      `omni_get_user_ai_credit_usage` / `omni_get_entity_group_ai_credit_usage`.

    Returns:
    A markdown table of the thresholds, defaults, usage, and billing-period
    bounds, or the raw `AiCreditControlsResponse` JSON when `response_format`
    is `json`. Thresholds/defaults show as `off` / `unlimited` when `null`.

    Examples:
    - `{"params": {}}`
    - `{"params": {"response_format": "json"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 401 means the API key is
    missing or invalid. 403 means either the caller lacks Organization Admin
    permissions or AI credit controls are not enabled for the organization.
    """
    try:
        payload = await get_client().request_json("GET", "/v1/ai/credit-controls")
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        return _render_credit_controls(body, heading="AI Credit Controls")
    except Exception as exc:
        return handle_api_error(exc)


class UpdateAiCreditControlsInput(BaseModel):
    """Input for `omni_update_ai_credit_controls`.

    Every field is optional, but at least one must be provided. Omit a field
    to leave it unchanged; pass it explicitly as `null` to turn that control
    off (or, for the two default-limit fields, to make it unlimited by
    default); pass a non-negative number to set it.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    downgrade_credits: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Credit usage at which AI downgrades to a cheaper model. Omit to leave unchanged, `null` to "
            "turn off, or a non-negative number to set. Must be less than or equal to `shutoff_credits`."
        ),
    )
    shutoff_credits: float | None = Field(
        default=None,
        ge=0,
        description="Credit usage at which AI shuts off entirely. Omit to leave unchanged, `null` to turn off, "
        "or a non-negative number to set.",
    )
    user_default_credits: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Default per-user AI credit limit for the billing period — what every user without an "
            "individual limit gets. Omit to leave unchanged, `null` for unlimited by default, or a "
            "non-negative number to set."
        ),
    )
    entity_group_default_credits: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Default per-entity-group AI credit limit for the billing period — what every embed entity "
            "group without an individual limit gets. Omit to leave unchanged, `null` for unlimited by "
            "default, or a non-negative number to set."
        ),
    )


@mcp.tool(
    name="omni_update_ai_credit_controls",
    annotations=ToolAnnotations(
        title="Update AI Credit Controls",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_ai_credit_controls(params: UpdateAiCreditControlsInput) -> str:
    """Update the organization's AI credit downgrade/shutoff thresholds and defaults.

    Calls `PATCH /v1/ai/credit-controls`. Only the fields you explicitly pass
    are changed — omitted fields are left untouched, and a field passed as
    `null` turns that control off (or makes that default unlimited). This
    endpoint can be used for embedded instances. Calling it again with the
    same values is a no-op, so it is idempotent.

    When to Use:
    - To set or clear the downgrade/shutoff credit thresholds.
    - To change the default per-user or per-entity-group AI credit limit.

    When NOT to Use:
    - To set an individual user's or entity group's limit — use
      `omni_set_user_ai_credit_limits` / `omni_set_entity_group_ai_credit_limits`.

    Returns:
    A short confirmation with a markdown table of the resulting full state
    (thresholds, defaults, usage, billing period).

    Examples:
    - Turn off the downgrade threshold: `{"params": {"downgrade_credits": null}}`
    - Set both thresholds: `{"params": {"downgrade_credits": 800, "shutoff_credits": 1200}}`
    - Make per-user limits unlimited by default: `{"params": {"user_default_credits": null}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an empty body, a
    negative value, an unknown field, or `downgrade_credits` greater than
    `shutoff_credits`. 401 means a missing/invalid API key. 403 means the
    caller lacks Organization Admin permissions or AI credit controls are not
    enabled for the organization.
    """
    try:
        fields_set = params.model_fields_set
        payload: dict[str, Any] = {}
        if "downgrade_credits" in fields_set:
            payload["downgradeCredits"] = params.downgrade_credits
        if "shutoff_credits" in fields_set:
            payload["shutoffCredits"] = params.shutoff_credits
        if "user_default_credits" in fields_set:
            payload["userDefaultCredits"] = params.user_default_credits
        if "entity_group_default_credits" in fields_set:
            payload["entityGroupDefaultCredits"] = params.entity_group_default_credits
        if not payload:
            return (
                "Error: provide at least one field to update — downgrade_credits, shutoff_credits, "
                "user_default_credits, or entity_group_default_credits."
            )
        result = await get_client().request_json("PATCH", "/v1/ai/credit-controls", json_body=payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        return _render_credit_controls(body, heading="AI Credit Controls Updated")
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# AI Credit Controls: individual user limits
# --------------------------------------------------------------------------


class ListUserAiCreditLimitsInput(BaseModel):
    """Input for `omni_list_user_ai_credit_limits`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    page_size: int = Field(default=20, ge=1, le=100, description="Number of results per page (1-100). Defaults to 20.")
    cursor: str | None = Field(
        default=None, description="Cursor for pagination from a previous response's `nextCursor`."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a bullet summary, `json` for raw records."
    )


@mcp.tool(
    name="omni_list_user_ai_credit_limits",
    annotations=ToolAnnotations(
        title="List User AI Credit Limits",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_user_ai_credit_limits(params: ListUserAiCreditLimitsInput) -> str:
    """List the organization's individual (per-user) AI credit limits.

    Calls `GET /v1/ai/credit-controls/users`. Only users with an individual
    limit are returned, ordered by `userId` ascending; users following the
    organization default are omitted.

    When to Use:
    - To audit which users have a custom AI credit limit.
    - Before calling `omni_set_user_ai_credit_limits`, to see current overrides.

    When NOT to Use:
    - To read the organization-wide default limit — use `omni_get_ai_credit_controls`.
    - To read how many credits a user has *used* — use `omni_get_user_ai_credit_usage`.

    Returns:
    A markdown list of `userId` → limit (`unlimited` when `null`), paginated
    via `pageInfo`, or the raw records as JSON when `response_format` is
    `json`.

    Examples:
    - `{"params": {}}`
    - `{"params": {"page_size": 50, "cursor": "eyJpZCI6IjEyMzQ1In0"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an invalid cursor
    or `page_size`. 403 means the caller lacks Organization Admin permissions,
    or per-user AI credit limits are not enabled for the organization.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size}
        if params.cursor:
            query["cursor"] = params.cursor
        payload = await get_client().request_json("GET", "/v1/ai/credit-controls/users", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=lambda item: (
                f"- User `{item.get('userId', 'unknown')}`: "
                f"{'unlimited' if item.get('creditLimit') is None else item.get('creditLimit')} credits"
            ),
            title="User AI Credit Limits",
        )
    except Exception as exc:
        return handle_api_error(exc)


class UserCreditLimitEntry(BaseModel):
    """One entry in a bulk user AI credit limit update.

    Exactly one of `credit_limit` (a number or explicit `null`) or
    `use_default_limit: true` must be provided.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(..., min_length=1, description="The user's ID within this organization.")
    credit_limit: float | None = Field(
        default=None,
        ge=0,
        description=(
            "The user's individual AI credit limit for the billing period, or `null` for unlimited. "
            "Overrides the organization default. Mutually exclusive with `use_default_limit`."
        ),
    )
    use_default_limit: Literal[True] | None = Field(
        default=None,
        description=(
            "Set to `true` to remove the user's individual limit so they follow the organization "
            "default. Mutually exclusive with `credit_limit`."
        ),
    )

    @model_validator(mode="after")
    def _check_mutually_exclusive(self) -> UserCreditLimitEntry:
        limit_given = "credit_limit" in self.model_fields_set
        default_given = self.use_default_limit is True
        if limit_given and default_given:
            raise ValueError("provide exactly one of `credit_limit` or `use_default_limit: true`, not both")
        if not limit_given and not default_given:
            raise ValueError("provide `credit_limit` (a number or null) or `use_default_limit: true`")
        return self

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"userId": self.user_id}
        if "credit_limit" in self.model_fields_set:
            payload["creditLimit"] = self.credit_limit
        else:
            payload["useDefaultLimit"] = True
        return payload


class SetUserAiCreditLimitsInput(BaseModel):
    """Input for `omni_set_user_ai_credit_limits`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    users: list[UserCreditLimitEntry] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description=(
            "Users to update, at most 1000 per request. Each entry has a `user_id` plus exactly one of "
            "`credit_limit` (a number or `null`) or `use_default_limit: true`."
        ),
    )


@mcp.tool(
    name="omni_set_user_ai_credit_limits",
    annotations=ToolAnnotations(
        title="Set User AI Credit Limits",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_set_user_ai_credit_limits(params: SetUserAiCreditLimitsInput) -> str:
    """Set individual users' AI credit limits in bulk.

    Calls `PATCH /v1/ai/credit-controls/users`. Each entry names a user and
    either sets an individual limit or removes one so the user follows the
    organization default. All entries are applied in a single transaction —
    either every entry is applied, or none are.

    When to Use:
    - To grant a small group of power users a higher AI credit limit.
    - To reset one or more users back to the organization default.

    When NOT to Use:
    - To change the organization-wide default — use `omni_update_ai_credit_controls`.
    - To update entity groups instead of users — use `omni_set_entity_group_ai_credit_limits`.

    Returns:
    A short confirmation listing each user's resulting effective limit
    (`unlimited`, a number, or "uses organization default").

    Examples:
    - Set two users' limits: `{"params": {"users": [{"user_id": "u1", "credit_limit": 50}, {"user_id": "u2", "credit_limit": null}]}}`
    - Reset a user to default: `{"params": {"users": [{"user_id": "u1", "use_default_limit": true}]}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an empty `users`
    array, more than 1000 entries, an entry with both `credit_limit` and
    `use_default_limit` (or neither), a negative `credit_limit`, or a
    duplicated `user_id`. 403 means the caller lacks Organization Admin
    permissions, per-user AI credit limits are not enabled, or credit
    controls editing is disabled for the organization. 404 means a `user_id`
    is not a member of the organization (the response names the first
    invalid ID); no limits are changed in that case.
    """
    try:
        payload = {"users": [entry.to_payload() for entry in params.users]}
        result = await get_client().request_json("PATCH", "/v1/ai/credit-controls/users", json_body=payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        users = body.get("users") or []
        lines = [f"Updated **{len(users)}** user AI credit limit(s):"]
        for user in users:
            if user.get("usesDefaultLimit"):
                desc = "uses organization default"
            elif user.get("creditLimit") is None:
                desc = "unlimited"
            else:
                desc = f"{user.get('creditLimit')} credits"
            lines.append(f"- `{user.get('userId', 'unknown')}` → {desc}")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# AI Credit Controls: individual entity group (embed) limits
# --------------------------------------------------------------------------


class ListEntityGroupAiCreditLimitsInput(BaseModel):
    """Input for `omni_list_entity_group_ai_credit_limits`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    page_size: int = Field(default=20, ge=1, le=100, description="Number of results per page (1-100). Defaults to 20.")
    cursor: str | None = Field(
        default=None, description="Cursor for pagination from a previous response's `nextCursor`."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a bullet summary, `json` for raw records."
    )


@mcp.tool(
    name="omni_list_entity_group_ai_credit_limits",
    annotations=ToolAnnotations(
        title="List Entity Group AI Credit Limits",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_entity_group_ai_credit_limits(params: ListEntityGroupAiCreditLimitsInput) -> str:
    """List the organization's individual (per-embed-entity-group) AI credit limits.

    Calls `GET /v1/ai/credit-controls/entity-groups`. Only entity groups with
    an individual limit are returned, ordered by entity group ID ascending;
    entity groups following the organization default are omitted.

    When to Use:
    - To audit which embed entity groups have a custom AI credit limit.
    - Before calling `omni_set_entity_group_ai_credit_limits`, to see current overrides.

    When NOT to Use:
    - To read the organization-wide default limit — use `omni_get_ai_credit_controls`.
    - To read individual users' limits — use `omni_list_user_ai_credit_limits`.

    Returns:
    A markdown list of `entity` → limit (`unlimited` when `null`), paginated
    via `pageInfo`, or the raw records as JSON when `response_format` is
    `json`.

    Examples:
    - `{"params": {}}`
    - `{"params": {"page_size": 50}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an invalid cursor
    or `page_size`. 403 means the caller lacks Organization Admin
    permissions, or per-entity-group AI credit limits are not enabled for
    the organization.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size}
        if params.cursor:
            query["cursor"] = params.cursor
        payload = await get_client().request_json("GET", "/v1/ai/credit-controls/entity-groups", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=lambda item: (
                f"- Entity group `{item.get('entity', 'unknown')}`: "
                f"{'unlimited' if item.get('creditLimit') is None else item.get('creditLimit')} credits"
            ),
            title="Entity Group AI Credit Limits",
        )
    except Exception as exc:
        return handle_api_error(exc)


class EntityGroupCreditLimitEntry(BaseModel):
    """One entry in a bulk entity group AI credit limit update.

    Exactly one of `credit_limit` (a number or explicit `null`) or
    `use_default_limit: true` must be provided.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity: str = Field(..., min_length=1, description="The embed entity's identifier (the SSO `entity` value).")
    credit_limit: float | None = Field(
        default=None,
        ge=0,
        description=(
            "The entity group's individual AI credit limit for the billing period, or `null` for "
            "unlimited (distinct from following the organization default). Mutually exclusive with "
            "`use_default_limit`."
        ),
    )
    use_default_limit: Literal[True] | None = Field(
        default=None,
        description=(
            "Set to `true` to remove the entity group's individual limit so it follows the organization "
            "default. Mutually exclusive with `credit_limit`."
        ),
    )

    @model_validator(mode="after")
    def _check_mutually_exclusive(self) -> EntityGroupCreditLimitEntry:
        limit_given = "credit_limit" in self.model_fields_set
        default_given = self.use_default_limit is True
        if limit_given and default_given:
            raise ValueError("provide exactly one of `credit_limit` or `use_default_limit: true`, not both")
        if not limit_given and not default_given:
            raise ValueError("provide `credit_limit` (a number or null) or `use_default_limit: true`")
        return self

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"entity": self.entity}
        if "credit_limit" in self.model_fields_set:
            payload["creditLimit"] = self.credit_limit
        else:
            payload["useDefaultLimit"] = True
        return payload


class SetEntityGroupAiCreditLimitsInput(BaseModel):
    """Input for `omni_set_entity_group_ai_credit_limits`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity_groups: list[EntityGroupCreditLimitEntry] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description=(
            "Entity groups to update, at most 1000 per request. Each entry has an `entity` plus exactly "
            "one of `credit_limit` (a number or `null`) or `use_default_limit: true`."
        ),
    )


@mcp.tool(
    name="omni_set_entity_group_ai_credit_limits",
    annotations=ToolAnnotations(
        title="Set Entity Group AI Credit Limits",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_set_entity_group_ai_credit_limits(params: SetEntityGroupAiCreditLimitsInput) -> str:
    """Set individual embed entity groups' AI credit limits in bulk.

    Calls `PATCH /v1/ai/credit-controls/entity-groups`. Each entry names an
    entity group and either sets an individual limit or removes one so the
    entity group follows the organization default. All entries are applied
    in a single transaction — either every entry is applied, or none are.
    To set an unlimited override (distinct from following the organization
    default), pass `credit_limit: null`.

    When to Use:
    - To grant one embedded customer's entity group a higher AI credit budget.
    - To reset one or more entity groups back to the organization default.

    When NOT to Use:
    - To change the organization-wide default — use `omni_update_ai_credit_controls`.
    - To update users instead of entity groups — use `omni_set_user_ai_credit_limits`.

    Returns:
    A short confirmation listing each entity group's resulting effective
    limit (`unlimited`, a number, or "uses organization default").

    Examples:
    - Set two entity groups' limits: `{"params": {"entity_groups": [{"entity": "blobsrus", "credit_limit": 50}, {"entity": "blobsrus-eu", "credit_limit": null}]}}`
    - Reset an entity group to default: `{"params": {"entity_groups": [{"entity": "blobsrus", "use_default_limit": true}]}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an empty
    `entity_groups` array, more than 1000 entries, an entry with both
    `credit_limit` and `use_default_limit` (or neither), a negative
    `credit_limit`, or a duplicated `entity`. 403 means the caller lacks
    Organization Admin permissions, per-entity-group AI credit limits are
    not enabled, or credit controls editing is disabled for the
    organization. 404 means an entity has no entity group in the
    organization (the response names the first invalid entity); no limits
    are changed in that case.
    """
    try:
        payload = {"entityGroups": [entry.to_payload() for entry in params.entity_groups]}
        result = await get_client().request_json("PATCH", "/v1/ai/credit-controls/entity-groups", json_body=payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        entity_groups = body.get("entityGroups") or []
        lines = [f"Updated **{len(entity_groups)}** entity group AI credit limit(s):"]
        for group in entity_groups:
            if group.get("usesDefaultLimit"):
                desc = "uses organization default"
            elif group.get("creditLimit") is None:
                desc = "unlimited"
            else:
                desc = f"{group.get('creditLimit')} credits"
            lines.append(f"- `{group.get('entity', 'unknown')}` → {desc}")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# AI Credit Usage: read usage for specific users / entity groups
# --------------------------------------------------------------------------


class GetUserAiCreditUsageInput(BaseModel):
    """Input for `omni_get_user_ai_credit_usage`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description=(
            "Users to read usage for, at most 1000 per request. Each entry is a user's UUID within this "
            "organization; each ID must appear only once."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a usage table, `json` for the raw payload."
    )

    @model_validator(mode="after")
    def _check_no_duplicate_user_ids(self) -> GetUserAiCreditUsageInput:
        seen: set[str] = set()
        for user_id in self.user_ids:
            if user_id in seen:
                raise ValueError(f"user_ids contains a duplicate: {user_id!r}")
            seen.add(user_id)
        return self


@mcp.tool(
    name="omni_get_user_ai_credit_usage",
    annotations=ToolAnnotations(
        title="Get User AI Credit Usage",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_user_ai_credit_usage(params: GetUserAiCreditUsageInput) -> str:
    """Read specific users' AI credit usage for the current billing period.

    Calls `POST /v1/ai/credit-usage/users` (a bulk lookup by ID, so it uses
    POST despite being a pure read — it changes nothing). Each user ID must
    be a member of the organization; unknown, invalid, and duplicate IDs
    fail the whole request. Users with no current usage report `0`.

    When to Use:
    - To check whether specific users are approaching their AI credit limit.
    - To build a usage report for a known set of users.

    When NOT to Use:
    - To read a user's configured *limit* rather than usage — use `omni_list_user_ai_credit_limits`.
    - To read usage for embed entity groups — use `omni_get_entity_group_ai_credit_usage`.

    Returns:
    A markdown table of `userId` → `creditsUsed` plus the billing-period
    bounds, or the raw payload as JSON when `response_format` is `json`.

    Examples:
    - `{"params": {"user_ids": ["f4a2b3c8-0d1e-4f5a-9b6c-7d8e9f0a1b2c"]}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an empty
    `user_ids` array, more than 1000 IDs, or a duplicated ID. 403 means the
    caller lacks Organization Admin permissions, or per-user AI credit
    limits are not enabled for the organization. 404 means a `user_id` is
    not a member of the organization (the response names the first invalid
    ID).
    """
    try:
        payload = {"userIds": params.user_ids}
        result = await get_client().request_json("POST", "/v1/ai/credit-usage/users", json_body=payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        users = body.get("users") or []
        rows = [{"userId": user.get("userId"), "creditsUsed": user.get("creditsUsed")} for user in users]
        lines = [
            "# User AI Credit Usage",
            "",
            f"Billing period: {_ms_to_iso(body.get('periodStart'))} to {_ms_to_iso(body.get('periodEnd'))}",
            "",
            markdown_table(rows),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


class GetEntityGroupAiCreditUsageInput(BaseModel):
    """Input for `omni_get_entity_group_ai_credit_usage`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entities: list[str] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description=(
            "Entity groups to read usage for, at most 1000 per request. Each entry is an embed `entity` "
            "(the SSO `entity` value) corresponding to an entity group in the organization; each entity "
            "must appear only once."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a usage table, `json` for the raw payload."
    )

    @model_validator(mode="after")
    def _check_no_duplicate_entities(self) -> GetEntityGroupAiCreditUsageInput:
        seen: set[str] = set()
        for entity in self.entities:
            if entity in seen:
                raise ValueError(f"entities contains a duplicate: {entity!r}")
            seen.add(entity)
        return self


@mcp.tool(
    name="omni_get_entity_group_ai_credit_usage",
    annotations=ToolAnnotations(
        title="Get Entity Group AI Credit Usage",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_entity_group_ai_credit_usage(params: GetEntityGroupAiCreditUsageInput) -> str:
    """Read specific embed entity groups' AI credit usage for the current billing period.

    Calls `POST /v1/ai/credit-usage/entity-groups` (a bulk lookup by ID, so
    it uses POST despite being a pure read — it changes nothing). Each
    `entity` must have an entity group in the organization; unknown,
    invalid, and duplicate values fail the whole request. Entity groups
    with no current usage report `0`.

    When to Use:
    - To check whether a specific embedded customer's entity group is
      approaching its AI credit limit.
    - To build a usage report for a known set of entity groups.

    When NOT to Use:
    - To read an entity group's configured *limit* rather than usage — use
      `omni_list_entity_group_ai_credit_limits`.
    - To read usage for individual users — use `omni_get_user_ai_credit_usage`.

    Returns:
    A markdown table of `entity` → `creditsUsed` plus the billing-period
    bounds, or the raw payload as JSON when `response_format` is `json`.

    Examples:
    - `{"params": {"entities": ["blobsrus"]}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an empty
    `entities` array, more than 1000 entities, or a duplicated entity. 403
    means the caller lacks Organization Admin permissions, or
    per-entity-group AI credit limits are not enabled for the organization.
    404 means an entity has no entity group in the organization (the
    response names the first invalid entity).
    """
    try:
        payload = {"entities": params.entities}
        result = await get_client().request_json("POST", "/v1/ai/credit-usage/entity-groups", json_body=payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        entity_groups = body.get("entityGroups") or []
        rows = [{"entity": group.get("entity"), "creditsUsed": group.get("creditsUsed")} for group in entity_groups]
        lines = [
            "# Entity Group AI Credit Usage",
            "",
            f"Billing period: {_ms_to_iso(body.get('periodStart'))} to {_ms_to_iso(body.get('periodEnd'))}",
            "",
            markdown_table(rows),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# AI Model Suggestions
# --------------------------------------------------------------------------


class SuggestionStatusFilter(StrEnum):
    """Status filter for `omni_list_model_suggestions`."""

    ACTIVE = "active"
    IGNORED = "ignored"
    ALL = "all"


class ListModelSuggestionsInput(BaseModel):
    """Input for `omni_list_model_suggestions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model to list suggestions for.")
    status: SuggestionStatusFilter = Field(
        default=SuggestionStatusFilter.ACTIVE,
        description="Filter by status: `active` (default, not yet dismissed), `ignored` (dismissed), or `all`.",
    )
    page_size: int = Field(default=25, ge=1, le=100, description="Suggestions per page (1-100). Defaults to 25.")
    cursor: str | None = Field(
        default=None, description="Cursor for keyset pagination — the `id` of the last suggestion in the previous page."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a bullet summary, `json` for raw records."
    )


@mcp.tool(
    name="omni_list_model_suggestions",
    annotations=ToolAnnotations(
        title="List Model Suggestions",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_model_suggestions(params: ListModelSuggestionsInput) -> str:
    """List AI-generated context suggestions for a shared model.

    Calls `GET /v1/models/{modelId}/suggestions`. Suggestions are sorted by
    priority, creation date, and ID for stable keyset pagination. Each
    suggestion proposes one or more `ai_context` edits, cites the chat
    session(s) that motivated it (when tracked), and can be dismissed or
    restored.

    When to Use:
    - To review what the AI suggests improving in a model's `ai_context`.
    - To find a suggestion's ID before dismissing, restoring, or deleting it.
    - To check whether a model has any active suggestions left to review.

    When NOT to Use:
    - To trigger new suggestions — use `omni_generate_model_suggestions`.
    - To check a generation run's progress — use `omni_get_suggestion_run_status`
      or `omni_get_latest_suggestion_run`.

    Returns:
    A markdown list of suggestions (title, status, priority, category,
    proposed-change count, evidence count, and dismiss reason if any),
    paginated via `pageInfo`, or the raw records as JSON when
    `response_format` is `json`.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "status": "ignored", "page_size": 50}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an invalid
    cursor, an invalid `model_id`, or `page_size` out of range. 403 means
    the caller lacks Organization Admin permissions. 404 means the shared
    model does not exist or is not accessible.
    """
    try:
        query: dict[str, Any] = {"status": params.status.value, "pageSize": params.page_size}
        if params.cursor:
            query["cursor"] = params.cursor
        path = f"/v1/models/{quote(params.model_id, safe='')}/suggestions"
        payload = await get_client().request_json("GET", path, params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_suggestion_summary,
            title="AI Model Suggestions",
        )
    except Exception as exc:
        return handle_api_error(exc)


class DeleteModelSuggestionInput(BaseModel):
    """Input for `omni_delete_model_suggestion`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model the suggestion belongs to.")
    suggestion_id: str = Field(..., min_length=1, description="UUID of the suggestion to permanently delete.")


@mcp.tool(
    name="omni_delete_model_suggestion",
    annotations=ToolAnnotations(
        title="Delete Model Suggestion",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_model_suggestion(params: DeleteModelSuggestionInput) -> str:
    """Permanently delete an AI-generated model suggestion.

    Calls `DELETE /v1/models/{modelId}/suggestions/{suggestionId}`. Unlike
    dismissing, this cannot be undone — there is no restore for a deleted
    suggestion.

    When to Use:
    - To permanently remove a suggestion that will never be actioned (e.g.
      generated against a model that has since been redesigned).

    When NOT to Use:
    - To temporarily hide a suggestion you might revisit — use
      `omni_dismiss_model_suggestion` instead, which is reversible via
      `omni_restore_model_suggestion`.

    Returns:
    A short confirmation string.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "suggestion_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means a malformed ID.
    403 means AI is disabled for the organization, the model is not a
    shared model, or the caller lacks Organization Admin permissions. 404
    means the suggestion or model was not found in this organization.
    """
    try:
        path = f"/v1/models/{quote(params.model_id, safe='')}/suggestions/{quote(params.suggestion_id, safe='')}"
        result = await get_client().request_json("DELETE", path)
        return _success_confirmation(result, action="Deleted", subject=f"suggestion `{params.suggestion_id}`")
    except Exception as exc:
        return handle_api_error(exc)


class GetLatestSuggestionRunInput(BaseModel):
    """Input for `omni_get_latest_suggestion_run`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model the suggestions belong to.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for the raw run object."
    )


@mcp.tool(
    name="omni_get_latest_suggestion_run",
    annotations=ToolAnnotations(
        title="Get Latest Suggestion Run",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_latest_suggestion_run(params: GetLatestSuggestionRunInput) -> str:
    """Get the most recent AI suggestion generation run for a shared model.

    Calls `GET /v1/models/{modelId}/suggestions/runs/latest`. Returns the
    active run if one is in flight, otherwise the last terminal run, or
    reports that the model has never had a run.

    When to Use:
    - To check whether a generation run is currently in progress before
      calling `omni_generate_model_suggestions` (which returns `409` if one
      already is).
    - To see when suggestions were last generated for a model.

    When NOT to Use:
    - To check a specific run you already have the ID for — use
      `omni_get_suggestion_run_status`.

    Returns:
    A markdown summary of the run (ID, status, trigger source, who
    triggered it, timestamps, error if failed), or `_No generation runs yet
    for this model._` if none exist; JSON via `response_format`.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means a malformed
    `model_id`. 403 means the feature is not enabled, AI is disabled, the
    model is not a shared model, or the caller lacks Organization Admin
    permissions. 404 means the model was not found in this organization.
    """
    try:
        path = f"/v1/models/{quote(params.model_id, safe='')}/suggestions/runs/latest"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        run = body.get("run")
        return truncate_result(f"# Latest Suggestion Generation Run\n\n{_run_markdown(run)}")
    except Exception as exc:
        return handle_api_error(exc)


class GetSuggestionRunStatusInput(BaseModel):
    """Input for `omni_get_suggestion_run_status`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model the run belongs to.")
    run_id: str = Field(..., min_length=1, description="UUID of the generation run.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a summary, `json` for the raw run object."
    )


@mcp.tool(
    name="omni_get_suggestion_run_status",
    annotations=ToolAnnotations(
        title="Get Suggestion Run Status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_suggestion_run_status(params: GetSuggestionRunStatusInput) -> str:
    """Get the status of a specific AI suggestion generation run.

    Calls `GET /v1/models/{modelId}/suggestions/runs/{runId}`. Poll this
    until `status` is terminal (`complete` or `failed`), then re-fetch the
    suggestions list with `omni_list_model_suggestions`.

    When to Use:
    - To poll a run started by `omni_generate_model_suggestions` until it
      finishes.
    - To inspect why a run `failed` (see the `error` field).

    When NOT to Use:
    - To find the most recent run when you don't already have its ID — use
      `omni_get_latest_suggestion_run`.

    Returns:
    A markdown summary of the run (ID, status, trigger source, who
    triggered it, timestamps, error if failed), or JSON via
    `response_format`.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "run_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means a malformed
    `model_id` or `run_id`. 403 means the feature is not enabled, AI is
    disabled, the model is not a shared model, or the caller lacks
    Organization Admin permissions. 404 means the run was not found, does
    not belong to this model, or does not belong to this organization.
    """
    try:
        path = f"/v1/models/{quote(params.model_id, safe='')}/suggestions/runs/{quote(params.run_id, safe='')}"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        return truncate_result(f"# Suggestion Generation Run Status\n\n{_run_markdown(body)}")
    except Exception as exc:
        return handle_api_error(exc)


class GenerateModelSuggestionsInput(BaseModel):
    """Input for `omni_generate_model_suggestions`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model to generate suggestions for.")


@mcp.tool(
    name="omni_generate_model_suggestions",
    annotations=ToolAnnotations(
        title="Generate Model Suggestions",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_generate_model_suggestions(params: GenerateModelSuggestionsInput) -> str:
    """Trigger a new AI suggestion generation run for a shared model.

    Calls `POST /v1/models/{modelId}/suggestions/generate`, enqueuing an
    async job and returning immediately. **This consumes AI credits.** A
    model can have only one active run at a time, and a cooldown period is
    enforced after a successful run (see the `Retry-After` header on a
    `429`) — calling this repeatedly is not idempotent and can fail with
    `409` or `429`.

    When to Use:
    - To refresh a shared model's suggestions after significant changes to
      its views or fields.
    - To kick off the first suggestion generation for a newly shared model.

    When NOT to Use:
    - Immediately after another run for the same model — check
      `omni_get_latest_suggestion_run` first to avoid a `409`/`429`.
    - To just view existing suggestions — use `omni_list_model_suggestions`.

    Returns:
    A short confirmation with the new run's ID and status (`queued`). Poll
    `omni_get_suggestion_run_status` until it reaches a terminal state, then
    call `omni_list_model_suggestions`.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an invalid
    `model_id`. 403 means the caller is not an Organization Admin. 404
    means the model was not found in this organization. 409 means a
    generation run is already active for this model. 429 means a run
    completed recently; retry after the cooldown named in the
    `Retry-After` header. 500 means the job failed to enqueue.
    """
    try:
        path = f"/v1/models/{quote(params.model_id, safe='')}/suggestions/generate"
        payload = await get_client().request_json("POST", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        run_id = body.get("runId", "unknown")
        status = body.get("status", "queued")
        return (
            f"Enqueued AI suggestion generation run `{run_id}` for model `{params.model_id}` "
            f"(status: {status}). This consumes AI credits. Poll `omni_get_suggestion_run_status` "
            "until the run reaches a terminal state, then call `omni_list_model_suggestions`."
        )
    except Exception as exc:
        return handle_api_error(exc)


class EnableModelSuggestionsScheduleInput(BaseModel):
    """Input for `omni_enable_model_suggestions_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model.")
    timezone: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "IANA timezone string (e.g. `America/New_York`, `Europe/London`, `UTC`) the daily "
            "generation runs in. Defaults to `UTC` when omitted."
        ),
    )


@mcp.tool(
    name="omni_enable_model_suggestions_schedule",
    annotations=ToolAnnotations(
        title="Enable Model Suggestions Schedule",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_enable_model_suggestions_schedule(params: EnableModelSuggestionsScheduleInput) -> str:
    """Enable the daily AI suggestion generation schedule for a shared model.

    Calls `PUT /v1/models/{modelId}/suggestions/schedule`. Suggestions are
    then generated daily at midnight in the given timezone. This operation
    is idempotent — calling it again (with the same or no timezone) just
    returns the persisted schedule.

    When to Use:
    - To keep a shared model's suggestions fresh automatically, without
      manually calling `omni_generate_model_suggestions` on a schedule.

    When NOT to Use:
    - To generate suggestions right now — use `omni_generate_model_suggestions`.
    - To stop the schedule — use `omni_disable_model_suggestions_schedule`.

    Returns:
    A short confirmation naming the schedule ID, its timezone, and status.

    Examples:
    - Default UTC: `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - Specific timezone: `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "timezone": "America/New_York"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an invalid
    `model_id` or an invalid IANA `timezone`. 403 means the caller is not
    an Organization Admin, AI is not enabled for the organization, or the
    model is not shared. 404 means the model does not exist.
    """
    try:
        path = f"/v1/models/{quote(params.model_id, safe='')}/suggestions/schedule"
        body_payload: dict[str, Any] | None = {"timezone": params.timezone} if params.timezone else None
        result = await get_client().request_json("PUT", path, json_body=body_payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        return (
            f"Enabled the daily suggestion schedule for model `{params.model_id}` "
            f"(schedule `{body.get('id', 'unknown')}`, timezone `{body.get('timezone', 'UTC')}`, "
            f"status: {body.get('status', 'enabled')})."
        )
    except Exception as exc:
        return handle_api_error(exc)


class DisableModelSuggestionsScheduleInput(BaseModel):
    """Input for `omni_disable_model_suggestions_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model the suggestions belong to.")


@mcp.tool(
    name="omni_disable_model_suggestions_schedule",
    annotations=ToolAnnotations(
        title="Disable Model Suggestions Schedule",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_disable_model_suggestions_schedule(params: DisableModelSuggestionsScheduleInput) -> str:
    """Disable the daily AI suggestion generation schedule for a shared model.

    Calls `DELETE /v1/models/{modelId}/suggestions/schedule`. This
    operation is idempotent — calling it again returns success even if the
    schedule was already disabled.

    When to Use:
    - To stop automatic daily suggestion generation for a model.

    When NOT to Use:
    - To pause generation temporarily while keeping the schedule — there is
      no pause; re-enable later with `omni_enable_model_suggestions_schedule`.

    Returns:
    A short confirmation string.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an invalid
    `model_id`. 403 means the caller is not an Organization Admin, AI is
    not enabled for the organization, or the model is not shared. 404
    means the model does not exist.
    """
    try:
        path = f"/v1/models/{quote(params.model_id, safe='')}/suggestions/schedule"
        result = await get_client().request_json("DELETE", path)
        return _success_confirmation(
            result, action="Disabled the daily suggestion schedule for", subject=f"model `{params.model_id}`"
        )
    except Exception as exc:
        return handle_api_error(exc)


class DismissModelSuggestionInput(BaseModel):
    """Input for `omni_dismiss_model_suggestion`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model.")
    suggestion_id: str = Field(..., min_length=1, description="UUID of the suggestion to dismiss.")
    reason: str | None = Field(
        default=None, max_length=4000, description="Optional free-text reason for dismissing the suggestion."
    )


@mcp.tool(
    name="omni_dismiss_model_suggestion",
    annotations=ToolAnnotations(
        title="Dismiss Model Suggestion",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_dismiss_model_suggestion(params: DismissModelSuggestionInput) -> str:
    """Dismiss an AI-generated suggestion, removing it from the active list.

    Calls `POST /v1/models/{modelId}/suggestions/{suggestionId}/ignore`.
    Dismissed suggestions can be brought back with
    `omni_restore_model_suggestion`, so this is safer than
    `omni_delete_model_suggestion` for suggestions you might revisit.

    When to Use:
    - To hide a suggestion that is not worth acting on right now, without
      losing it permanently.
    - To record why a suggestion was declined, via `reason`.

    When NOT to Use:
    - To permanently remove a suggestion — use `omni_delete_model_suggestion`.

    Returns:
    A short confirmation string.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "suggestion_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"}}`
    - With a reason: `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "suggestion_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901", "reason": "Already covered by an existing field description."}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means an invalid body
    or a malformed ID. 401 means missing/invalid authentication. 403 means
    the feature is not enabled, AI is disabled, the model is not a shared
    model, or the caller lacks Organization Admin permissions. 404 means
    the model or suggestion was not found, or the suggestion belongs to a
    different organization.
    """
    try:
        path = f"/v1/models/{quote(params.model_id, safe='')}/suggestions/{quote(params.suggestion_id, safe='')}/ignore"
        body_payload: dict[str, Any] | None = {"reason": params.reason} if params.reason else None
        result = await get_client().request_json("POST", path, json_body=body_payload)
        return _success_confirmation(result, action="Dismissed", subject=f"suggestion `{params.suggestion_id}`")
    except Exception as exc:
        return handle_api_error(exc)


class RestoreModelSuggestionInput(BaseModel):
    """Input for `omni_restore_model_suggestion`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="UUID of the shared model the suggestion belongs to.")
    suggestion_id: str = Field(..., min_length=1, description="UUID of the suggestion to restore.")


@mcp.tool(
    name="omni_restore_model_suggestion",
    annotations=ToolAnnotations(
        title="Restore Model Suggestion",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_restore_model_suggestion(params: RestoreModelSuggestionInput) -> str:
    """Restore a previously dismissed AI-generated suggestion back to the active list.

    Calls `POST /v1/models/{modelId}/suggestions/{suggestionId}/restore`.
    The counterpart to `omni_dismiss_model_suggestion`.

    When to Use:
    - To reconsider a suggestion that was dismissed by mistake or whose
      relevance changed.

    When NOT to Use:
    - On a suggestion that was permanently deleted with
      `omni_delete_model_suggestion` — deletion cannot be undone.

    Returns:
    A short confirmation string.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "suggestion_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"}}`

    Error Handling:
    Requires **Organization Admin** permissions. 400 means a malformed ID.
    401 means missing/invalid authentication. 403 means the feature is not
    enabled, AI is disabled, the model is not a shared model, or the
    caller lacks Organization Admin permissions. 404 means the model or
    suggestion was not found, or the suggestion belongs to a different
    organization.
    """
    try:
        path = (
            f"/v1/models/{quote(params.model_id, safe='')}/suggestions/{quote(params.suggestion_id, safe='')}/restore"
        )
        result = await get_client().request_json("POST", path)
        return _success_confirmation(result, action="Restored", subject=f"suggestion `{params.suggestion_id}`")
    except Exception as exc:
        return handle_api_error(exc)
