"""Tools for the AI routine and AI eval prompt set/run endpoints. Implemented by lane t17.

Covers two tags from the spec:

- **AI Routines** — scheduled prompts delivered by email or Slack
  (`/v1/ai/routines...`).
- **AI Eval** — regression-testing a shared model's AI answers against a
  fixed set of prompts (`/v1/ai/eval/prompt-sets...`, `/v1/ai/eval/runs...`).

Both AI Routine list/get/create/update responses use camelCase field names
(`modelId`, `branchId`, `createdAt`, ...); the AI Eval endpoints use snake_case
(`model_id`, `prompt_set_id`, `created_at`, ...) — each tool builds its payload
in the casing its own endpoint expects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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

#: One line on what this module covers, for the generated README catalogue.
MODULE_SUMMARY = "Scheduled AI routines, eval prompt sets and runs"


def _quote(value: str) -> str:
    """Percent-encode a path segment (ids may contain characters unsafe for a URL)."""
    return quote(value, safe="")


def _simple_list_response(
    *,
    items: list[dict[str, Any]],
    fmt: ResponseFormat,
    item_formatter: Callable[[dict[str, Any]], str],
    title: str,
) -> str:
    """Render a list response whose endpoint has no `pageInfo` cursor envelope.

    The AI Eval list endpoints (`prompt-sets`, `runs`) return a bare array —
    unlike AI Routines, which uses the standard cursor-paginated shape.
    """
    if fmt is ResponseFormat.JSON:
        return truncate_result(to_json({"title": title, "count": len(items), "items": items}))
    lines = [f"# {title}", "", f"**{len(items):,}** record(s).", ""]
    lines.extend([item_formatter(item) for item in items] if items else ["_No records._"])
    return truncate_result("\n".join(lines))


# --------------------------------------------------------------------------
# AI Routines
# --------------------------------------------------------------------------


class ListAiRoutinesInput(BaseModel):
    """Input for `omni_list_ai_routines`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    cursor: str | None = Field(
        default=None, description="Cursor for pagination, from a previous response's pageInfo.nextCursor."
    )
    page_size: int = Field(default=20, ge=1, le=100, description="Number of results per page (1-100). Default 20.")
    sort_direction: Literal["asc", "desc"] = Field(default="desc", description="Sort direction for results.")
    sort_field: str | None = Field(default=None, description="Field to sort results by. Omit for the default sort.")
    user_id: str | None = Field(
        default=None,
        description="Requires an Organization API key. Target user membership ID (UUID) — list that user's routines instead of the caller's.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for the raw records."
    )


def _format_routine_item(item: Mapping[str, Any]) -> str:
    destination = item.get("destination") or {}
    dest_type = destination.get("type", "unknown")
    state = "paused" if item.get("disabled") else "active"
    if item.get("systemDisabled"):
        state += " (system-disabled)"
    last_run = item.get("lastRun") or {}
    last_state = last_run.get("label") or last_run.get("state") or "never run"
    return (
        f"- **{item.get('name', 'unnamed')}** (`{item.get('id')}`) — {state}; "
        f"schedule `{item.get('schedule')}` ({item.get('timezone')}); destination: {dest_type}; "
        f"last run: {last_state}"
    )


@mcp.tool(
    name="omni_list_ai_routines",
    annotations=ToolAnnotations(
        title="List AI Routines",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_ai_routines(params: ListAiRoutinesInput) -> str:
    """List AI Routines for the calling user, newest first.

    Includes routines paused by the owner or disabled by Omni, but excludes
    deleted routines.

    When to Use:
    - To see what scheduled AI prompts exist before creating, updating, or
      triggering one.
    - To check whether a routine has been paused or disabled by Omni.

    When NOT to Use:
    - To read one routine's full detail (destination recipients, last-run
      timestamp) — use `omni_get_ai_routine`.

    Returns:
    A markdown list (name, id, active/paused state, schedule, timezone,
    destination type, last-run status) with a `pageInfo`-driven next-cursor
    hint, or the raw `records`/`pageInfo` payload when `response_format` is
    `json`.

    Examples:
    - First page: `{"params": {}}`
    - Next page: `{"params": {"cursor": "eyJpZCI6IjEyMzQ1In0"}}`
    - Another user's routines (Organization API key only):
      `{"params": {"user_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    400 means an invalid cursor or `user_id`; 401 means a missing/invalid API
    key; 403 means the caller lacks the `USE_AI` permission or the AI Routines
    feature (`aiScheduledTasks` flag) is not enabled for the organization; 404
    means the `user_id` membership was not found.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size, "sortDirection": params.sort_direction}
        if params.cursor:
            query["cursor"] = params.cursor
        if params.sort_field:
            query["sortField"] = params.sort_field
        if params.user_id:
            query["userId"] = params.user_id
        payload = await get_client().request_json("GET", "/v1/ai/routines", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_routine_item,
            title="AI Routines",
        )
    except Exception as exc:
        return handle_api_error(exc)


class GetAiRoutineInput(BaseModel):
    """Input for `omni_get_ai_routine`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    routine_id: str = Field(..., min_length=1, description="The UUID of the routine.")
    user_id: str | None = Field(
        default=None,
        description="Requires an Organization API key. Target user membership ID (UUID) that owns the routine.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for the raw routine."
    )


def _render_routine_detail(routine: dict[str, Any]) -> str:
    destination = routine.get("destination") or {}
    dest_type = destination.get("type", "unknown")
    dest_lines: list[str] = []
    if dest_type == "email":
        emails = destination.get("recipientEmails") or []
        groups = destination.get("userGroupIds") or []
        dest_lines.append(f"- Recipients: {len(emails)} email(s), {len(groups)} user group(s)")
    elif dest_type == "slack":
        dest_lines.append(
            f"- Slack {destination.get('slackRecipientType', 'unknown')}: `{destination.get('recipientId')}`"
        )
    last_run = routine.get("lastRun") or {}
    lines = [
        f"# AI Routine: {routine.get('name', 'unnamed')}",
        "",
        f"- ID: `{routine.get('id')}`",
        f"- Model ID: `{routine.get('modelId')}`",
        f"- Branch ID: `{routine.get('branchId') or 'main'}`",
        f"- Topic: {routine.get('topicName') or 'N/A'}",
        f"- Schedule: `{routine.get('schedule')}` ({routine.get('timezone')})",
        f"- Paused by owner: {routine.get('disabled')}",
        f"- Disabled by Omni: {routine.get('systemDisabled')}"
        + (f" — {routine.get('systemDisabledReason')}" if routine.get("systemDisabledReason") else ""),
        f"- Recipient count: {routine.get('recipientCount', 0)}",
        f"- Destination type: {dest_type}",
        *dest_lines,
        f"- Prompt: {routine.get('prompt')}",
        f"- Description: {routine.get('description') or 'N/A'}",
        f"- Last run: {last_run.get('label') or last_run.get('state') or 'never run'}"
        f" (completed {iso_or_na(last_run.get('completedAt'))})"
        if last_run
        else "- Last run: never run",
        f"- Created: {iso_or_na(routine.get('createdAt'))}",
        f"- Updated: {iso_or_na(routine.get('updatedAt'))}",
    ]
    return "\n".join(lines)


@mcp.tool(
    name="omni_get_ai_routine",
    annotations=ToolAnnotations(
        title="Get AI Routine",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_ai_routine(params: GetAiRoutineInput) -> str:
    """Retrieve details for a specific AI Routine by ID.

    Includes the status of its most recent completed run.

    When to Use:
    - To inspect a routine's full configuration (prompt, schedule, timezone,
      destination, recipient count) before updating or triggering it.
    - To check the outcome of the last scheduled or manually triggered run.

    When NOT to Use:
    - To browse many routines at once — use `omni_list_ai_routines`.

    Returns:
    A markdown summary of the routine's schedule, destination, prompt, and
    last-run status, or the raw routine payload when `response_format` is
    `json`.

    Examples:
    - `{"params": {"routine_id": "880e8400-e29b-41d4-a716-446655440003"}}`

    Error Handling:
    401 means a missing/invalid API key; 403 means the caller lacks the
    `USE_AI` permission or the AI Routines feature is not enabled; 404 means
    the routine does not exist or is not visible to the caller.
    """
    try:
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id
        path = f"/v1/ai/routines/{_quote(params.routine_id)}"
        payload = await get_client().request_json("GET", path, params=query or None)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        return truncate_result(_render_routine_detail(body))
    except Exception as exc:
        return handle_api_error(exc)


class CreateAiRoutineInput(BaseModel):
    """Input for `omni_create_ai_routine`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1, description="Customer-visible name of the routine, used as the email subject.")
    model_id: str = Field(..., min_length=1, description="The shared model (UUID) the prompt runs against.")
    prompt: str = Field(..., min_length=1, description="Natural language prompt Omni runs on each scheduled run.")
    schedule: str = Field(
        ...,
        min_length=1,
        description="Six-field cron expression (minute, hour, day-of-month, month, day-of-week, year; use `?` for an unspecified day field).",
    )
    timezone: str = Field(..., min_length=1, description="IANA timezone identifier used to evaluate the schedule.")
    destination_type: Literal["email", "slack"] = Field(
        ...,
        description="Selects delivery: `email` sends to `recipient_emails`/`user_group_ids`; `slack` posts to one channel or DMs one user.",
    )
    recipient_emails: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Email addresses that receive each scheduled run (destination_type=email). Max 100.",
    )
    user_group_ids: list[str] | None = Field(
        default=None,
        max_length=100,
        description="User group IDs (UUIDs) whose active members receive each scheduled run (destination_type=email). Omni expands each group to members' current emails. Max 100.",
    )
    recipient_id: str | None = Field(
        default=None,
        min_length=1,
        description="Slack channel ID (e.g. `C01234567`) or user ID (e.g. `U01234567`) that receives each scheduled run (destination_type=slack). Required with destination_type=slack.",
    )
    slack_recipient_type: Literal["channel", "users"] | None = Field(
        default=None,
        description="Whether `recipient_id` is a Slack channel or a user, delivered as a DM (destination_type=slack). Required with destination_type=slack.",
    )
    branch_id: str | None = Field(
        default=None,
        description="Branch of the shared model (UUID) the prompt runs against. Omit to use the main model.",
    )
    description: str | None = Field(default=None, description="Display-only notes about the routine.")
    topic_name: str | None = Field(default=None, description="Topic scoping query generation. Omit for none.")
    user_id: str | None = Field(
        default=None,
        description="Requires an Organization API key. Create the routine on behalf of this target user membership ID (UUID).",
    )
    body: dict[str, Any] | None = Field(
        default=None,
        description="Raw request body override. When provided, sent verbatim to the API instead of the payload built from the fields above.",
    )

    @model_validator(mode="after")
    def _check_destination(self) -> CreateAiRoutineInput:
        if (
            self.body is None
            and self.destination_type == "slack"
            and (not self.recipient_id or not self.slack_recipient_type)
        ):
            raise ValueError("destination_type='slack' requires both recipient_id and slack_recipient_type.")
        return self


@mcp.tool(
    name="omni_create_ai_routine",
    annotations=ToolAnnotations(
        title="Create AI Routine",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_ai_routine(params: CreateAiRoutineInput) -> str:
    """Create a new AI Routine that runs on the specified schedule.

    Each scheduled run executes once using the routine owner's permissions,
    and every recipient receives the same result.

    When to Use:
    - To stand up a recurring, scheduled AI prompt delivered by email or
      Slack (e.g. a daily sales summary).

    When NOT to Use:
    - For a one-off AI answer right now — use the query/AI-generation tools
      instead of scheduling a routine.
    - To run an existing routine off-cycle — use `omni_trigger_ai_routine`.

    Returns:
    A short confirmation naming the routine and its new id, or an
    `Error ...` string on failure.

    Examples:
    - Email routine: `{"params": {"name": "Weekly sales", "model_id": "880e8400-e29b-41d4-a716-446655440003", "prompt": "Summarize this week's sales by region", "schedule": "0 9 * * 1 ?", "timezone": "America/Los_Angeles", "destination_type": "email", "recipient_emails": ["a@example.com"]}}`
    - Slack routine: `{"params": {"name": "Daily standup", "model_id": "880e8400-e29b-41d4-a716-446655440003", "prompt": "Top 3 metrics today", "schedule": "0 8 * * ? *", "timezone": "UTC", "destination_type": "slack", "recipient_id": "C01234567", "slack_recipient_type": "channel"}}`

    Error Handling:
    400 means an invalid body, recipient configuration, timezone, or
    schedule (also returned when the schedule is more frequent than the
    organization allows); 401 means a missing/invalid API key; 403 means AI
    routines or AI query generation are not enabled for the organization, or
    the API key cannot act on behalf of `user_id`; 404 means the model,
    branch, or topic was not found or is not accessible; 429 means the
    resolved user already has the maximum number of active routines.
    """
    try:
        if params.body is not None:
            payload: dict[str, Any] = params.body
        else:
            destination: dict[str, Any] = {"type": params.destination_type}
            if params.destination_type == "email":
                if params.recipient_emails is not None:
                    destination["recipientEmails"] = params.recipient_emails
                if params.user_group_ids is not None:
                    destination["userGroupIds"] = params.user_group_ids
            else:
                destination["recipientId"] = params.recipient_id
                destination["slackRecipientType"] = params.slack_recipient_type
            payload = {
                "name": params.name,
                "modelId": params.model_id,
                "prompt": params.prompt,
                "schedule": params.schedule,
                "timezone": params.timezone,
                "destination": destination,
            }
            if params.branch_id is not None:
                payload["branchId"] = params.branch_id
            if params.description is not None:
                payload["description"] = params.description
            if params.topic_name is not None:
                payload["topicName"] = params.topic_name

        query: dict[str, Any] = {"userId": params.user_id} if params.user_id else {}
        result = await get_client().request_json("POST", "/v1/ai/routines", params=query or None, json_body=payload)
        new_id = result.get("id") if isinstance(result, dict) else None
        return truncate_result(f"Created AI Routine **{params.name}** (id `{new_id}`).")
    except Exception as exc:
        return handle_api_error(exc)


class UpdateAiRoutineInput(BaseModel):
    """Input for `omni_update_ai_routine`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    routine_id: str = Field(..., min_length=1, description="The UUID of the routine to update.")
    name: str = Field(..., min_length=1, max_length=255, description="Display name for the routine.")
    prompt: str = Field(..., min_length=1, description="The AI prompt that will be executed when the routine runs.")
    schedule: str = Field(
        ...,
        min_length=1,
        description='Cron expression or human-readable schedule (e.g., "daily", "weekly", "0 9 * * 1").',
    )
    description: str | None = Field(default=None, description="Optional description of what the routine does.")
    model_id: str | None = Field(
        default=None, description="Optional model ID (UUID) to scope the routine to a specific data model."
    )
    is_active: bool | None = Field(
        default=None, description="Whether the routine is active and will execute on schedule."
    )
    user_id: str | None = Field(
        default=None,
        description="Requires an Organization API key. Update the routine on behalf of this target user membership ID (UUID).",
    )
    body: dict[str, Any] | None = Field(
        default=None,
        description="Raw request body override. When provided, sent verbatim to the API instead of the payload built from the fields above.",
    )


@mcp.tool(
    name="omni_update_ai_routine",
    annotations=ToolAnnotations(
        title="Update AI Routine",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_ai_routine(params: UpdateAiRoutineInput) -> str:
    """Update an existing AI Routine's name, prompt, schedule, model, or active state.

    Note: unlike creation, this endpoint's body does not accept `destination`,
    `branchId`, `timezone`, or `topicName` — those cannot be changed via this
    call.

    When to Use:
    - To rename a routine, change its prompt or schedule, rescope it to a
      different model, or pause/resume it (`is_active`).

    When NOT to Use:
    - To change delivery recipients (destination) — not supported by this
      endpoint; recreate the routine instead.
    - To run the routine immediately — use `omni_trigger_ai_routine`.

    Returns:
    A short confirmation naming the routine and its id, or an
    `Error ...` string on failure.

    Examples:
    - `{"params": {"routine_id": "880e8400-e29b-41d4-a716-446655440003", "name": "Weekly sales v2", "prompt": "Summarize this week's sales by region and channel", "schedule": "0 9 * * 1 ?"}}`
    - Pause a routine: `{"params": {"routine_id": "880e8400-e29b-41d4-a716-446655440003", "name": "Weekly sales", "prompt": "...", "schedule": "0 9 * * 1 ?", "is_active": false}}`

    Error Handling:
    400 means an invalid request body; 401 means a missing/invalid API key;
    403 means the caller lacks the `USE_AI` permission, the feature is
    disabled, or has insufficient permissions to update this routine; 404
    means the routine was not found.
    """
    try:
        if params.body is not None:
            payload: dict[str, Any] = params.body
        else:
            payload = {"name": params.name, "prompt": params.prompt, "schedule": params.schedule}
            if params.description is not None:
                payload["description"] = params.description
            if params.model_id is not None:
                payload["modelId"] = params.model_id
            if params.is_active is not None:
                payload["isActive"] = params.is_active

        query: dict[str, Any] = {"userId": params.user_id} if params.user_id else {}
        path = f"/v1/ai/routines/{_quote(params.routine_id)}"
        result = await get_client().request_json("PUT", path, params=query or None, json_body=payload)
        updated_name = result.get("name") if isinstance(result, dict) else params.name
        return truncate_result(f"Updated AI Routine **{updated_name}** (id `{params.routine_id}`).")
    except Exception as exc:
        return handle_api_error(exc)


class DeleteAiRoutineInput(BaseModel):
    """Input for `omni_delete_ai_routine`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    routine_id: str = Field(..., min_length=1, description="The UUID of the routine to delete.")
    user_id: str | None = Field(
        default=None,
        description="Requires an Organization API key. Delete on behalf of this target user membership ID (UUID).",
    )


@mcp.tool(
    name="omni_delete_ai_routine",
    annotations=ToolAnnotations(
        title="Delete AI Routine",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_ai_routine(params: DeleteAiRoutineInput) -> str:
    """Permanently delete an AI Routine. This action cannot be undone.

    When to Use:
    - To remove a routine that is no longer needed. Its future scheduled
      runs stop; past deliveries already sent are unaffected.

    When NOT to Use:
    - To temporarily stop it — use `omni_update_ai_routine` with
      `is_active=false` instead, so it can be resumed later.

    Returns:
    A short confirmation with the deleted routine's id, or an
    `Error ...` string on failure.

    Examples:
    - `{"params": {"routine_id": "880e8400-e29b-41d4-a716-446655440003"}}`

    Error Handling:
    400 means an invalid routine id; 401 means a missing/invalid API key;
    403 means AI routines/AI query generation are not enabled, or a
    user-scoped API key tried to delete another user's routine; 404 means
    the routine was not found or has already been deleted.
    """
    try:
        query: dict[str, Any] = {"userId": params.user_id} if params.user_id else {}
        path = f"/v1/ai/routines/{_quote(params.routine_id)}"
        await get_client().request_json("DELETE", path, params=query or None)
        return truncate_result(f"Deleted AI Routine `{params.routine_id}`. This cannot be undone.")
    except Exception as exc:
        return handle_api_error(exc)


class TriggerAiRoutineInput(BaseModel):
    """Input for `omni_trigger_ai_routine`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    routine_id: str = Field(..., min_length=1, description="The UUID of the routine to trigger.")
    user_id: str | None = Field(
        default=None,
        description="Requires an Organization API key. Trigger on behalf of this target user membership ID (UUID).",
    )


@mcp.tool(
    name="omni_trigger_ai_routine",
    annotations=ToolAnnotations(
        title="Trigger AI Routine",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_trigger_ai_routine(params: TriggerAiRoutineInput) -> str:
    """Run an existing routine immediately, in addition to its schedule.

    Useful to get an off-cycle result or verify a routine produces the
    output you expect. This consumes AI credits. Executes once using the
    routine owner's permissions and delivers the response to every
    configured recipient. Returns as soon as the run has started; the
    result arrives asynchronously via the routine's normal delivery.

    When to Use:
    - To test a newly created or updated routine without waiting for its
      next scheduled time.
    - To get an ad-hoc, off-cycle result.

    When NOT to Use:
    - When a run is already in progress for this routine — wait for it to
      finish (the API returns 409 rather than queuing a second run).

    Returns:
    A short confirmation with the started run's job id, noting that this
    call consumes AI credits, or an `Error ...` string on failure.

    Examples:
    - `{"params": {"routine_id": "880e8400-e29b-41d4-a716-446655440003"}}`

    Error Handling:
    400 means an invalid routine id or the routine cannot run as configured
    (its model, branch, or owner is no longer accessible); 401 means a
    missing/invalid API key; 403 means AI routines/AI query generation are
    not enabled, or a user-scoped API key tried to run another user's
    routine; 404 means the routine does not exist or cannot be triggered
    (deleted, paused, or disabled by Omni); 409 means a run is already in
    progress for this routine.
    """
    try:
        query: dict[str, Any] = {"userId": params.user_id} if params.user_id else {}
        path = f"/v1/ai/routines/{_quote(params.routine_id)}/trigger"
        result = await get_client().request_json("POST", path, params=query or None)
        run_id = result.get("id") if isinstance(result, dict) else None
        return truncate_result(
            f"Triggered AI Routine `{params.routine_id}` — run id `{run_id}` started. "
            "This consumes AI credits; delivery happens asynchronously through the routine's normal destination."
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# AI Eval — prompt sets
# --------------------------------------------------------------------------


class EvalPromptCreateInput(BaseModel):
    """One prompt supplied when creating an eval prompt set."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt_text: str = Field(
        ..., min_length=1, max_length=8000, description="The natural language prompt text. Max 8000 characters."
    )
    expectation: str | None = Field(
        default=None,
        max_length=16000,
        description="Optional expectation the analysis judge scores the analysis against. Max 16000 characters.",
    )


class EvalPromptUpdateInput(BaseModel):
    """One prompt entry supplied when updating an eval prompt set's full prompt list."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str | None = Field(
        default=None,
        description="Existing prompt id (UUID). When provided, updates that prompt; when omitted, a new prompt is created. Prompts not included in the update's list are removed.",
    )
    prompt_text: str = Field(
        ..., min_length=1, max_length=8000, description="Updated or new prompt text. Max 8000 characters."
    )
    expectation: str | None = Field(
        default=None,
        max_length=16000,
        description="Optional expectation the analysis judge scores the analysis against.",
    )


class ListEvalPromptSetsInput(BaseModel):
    """Input for `omni_list_eval_prompt_sets`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    archived: bool = Field(default=False, description="When true, returns archived prompt sets instead of active ones.")
    model_ids: list[str] | None = Field(
        default=None,
        description="Optional list of model IDs (UUIDs) to filter prompt sets by. Omit to return prompt sets for every model the caller can access.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for the raw records."
    )


def _format_prompt_set_item(item: dict[str, Any]) -> str:
    return (
        f"- **{item.get('name', 'unnamed')}** (`{item.get('id')}`, slug `{item.get('slug')}`) — "
        f"model `{item.get('model_id')}`; {item.get('prompt_count', 0)} prompt(s); "
        f"archived={item.get('is_archived')}; latest run: {iso_or_na(item.get('latest_run_at'))}"
    )


@mcp.tool(
    name="omni_list_eval_prompt_sets",
    annotations=ToolAnnotations(
        title="List Eval Prompt Sets",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_eval_prompt_sets(params: ListEvalPromptSetsInput) -> str:
    """List eval prompt sets, sorted alphabetically by name.

    When `model_ids` is omitted, returns prompt sets for every shared model
    the caller can access.

    When to Use:
    - To find an existing prompt set's id before getting, updating,
      archiving, or starting a run against it.
    - To review archived prompt sets (`archived=true`) before restoring one.

    When NOT to Use:
    - To read one prompt set's full prompt list — use
      `omni_get_eval_prompt_set`.

    Returns:
    A markdown list (name, id, slug, model id, prompt count, archived state,
    latest run time), or the raw `prompt_sets` array when `response_format`
    is `json`. This endpoint has no cursor pagination.

    Examples:
    - `{"params": {}}`
    - Archived sets for one model: `{"params": {"archived": true, "model_ids": ["880e8400-e29b-41d4-a716-446655440003"]}}`

    Error Handling:
    400 means `model_ids` contains a non-UUID or `archived` is not
    `true`/`false`; 401 means a missing/invalid API key; 403 means the
    caller must have at least the Querier role on each requested model; 404
    means no eval-accessible models exist for this caller.
    """
    try:
        query: dict[str, Any] = {"archived": "true" if params.archived else "false"}
        if params.model_ids:
            query["model_ids"] = params.model_ids
        payload = await get_client().request_json("GET", "/v1/ai/eval/prompt-sets", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return _simple_list_response(
            items=list(body.get("prompt_sets") or []),
            fmt=params.response_format,
            item_formatter=_format_prompt_set_item,
            title="Eval Prompt Sets",
        )
    except Exception as exc:
        return handle_api_error(exc)


class GetEvalPromptSetInput(BaseModel):
    """Input for `omni_get_eval_prompt_set`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt_set_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the eval prompt set.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for the raw prompt set."
    )


def _render_prompt_set_detail(prompt_set: dict[str, Any]) -> str:
    prompts = prompt_set.get("prompts") or []
    rows = [
        {"id": p.get("id"), "prompt_text": p.get("prompt_text"), "expectation": p.get("expectation")} for p in prompts
    ]
    lines = [
        f"# Eval Prompt Set: {prompt_set.get('name', 'unnamed')}",
        "",
        f"- ID: `{prompt_set.get('id')}`",
        f"- Slug: `{prompt_set.get('slug')}`",
        f"- Model ID: `{prompt_set.get('model_id')}`",
        f"- Archived: {prompt_set.get('is_archived')}",
        f"- Description: {prompt_set.get('description') or 'N/A'}",
        f"- Created: {iso_or_na(prompt_set.get('created_at'))}",
        f"- Updated: {iso_or_na(prompt_set.get('updated_at'))}",
        "",
        f"## Prompts ({len(prompts)})",
        "",
        markdown_table(rows),
    ]
    return "\n".join(lines)


@mcp.tool(
    name="omni_get_eval_prompt_set",
    annotations=ToolAnnotations(
        title="Get Eval Prompt Set",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_eval_prompt_set(params: GetEvalPromptSetInput) -> str:
    """Get a single eval prompt set with all of its prompts.

    When to Use:
    - Before updating a prompt set, to see current prompts and their ids
      (needed to update rather than replace individual prompts).
    - Before starting a run, to confirm which prompts will be evaluated.

    When NOT to Use:
    - To browse many prompt sets — use `omni_list_eval_prompt_sets`.

    Returns:
    A markdown summary plus a table of prompts (id, prompt text,
    expectation), or the raw prompt set payload when `response_format` is
    `json`.

    Examples:
    - `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    400 means `promptSetId` is not a UUID; 401 means a missing/invalid API
    key; 403 means insufficient permissions; 404 means the prompt set was
    not found.
    """
    try:
        path = f"/v1/ai/eval/prompt-sets/{_quote(params.prompt_set_id)}"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        raw_prompt_set = body.get("prompt_set")
        prompt_set: dict[str, Any] = raw_prompt_set if isinstance(raw_prompt_set, dict) else body
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(prompt_set))
        return truncate_result(_render_prompt_set_detail(prompt_set))
    except Exception as exc:
        return handle_api_error(exc)


class CreateEvalPromptSetInput(BaseModel):
    """Input for `omni_create_eval_prompt_set`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="The shared model (UUID) this prompt set is bound to.")
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable name for the prompt set. 255 characters or fewer.",
    )
    slug: str = Field(
        ...,
        max_length=255,
        pattern=r"^[a-z][a-z0-9-]*$",
        description="URL-safe identifier for the prompt set. Must be unique per model_id and match `^[a-z][a-z0-9-]*$`. Max 255 characters.",
    )
    description: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional human-readable description of the prompt set. Max 1024 characters.",
    )
    prompts: list[EvalPromptCreateInput] | None = Field(
        default=None,
        max_length=25,
        description="Initial prompts for the set. Omit for an empty list. At most 25 prompts.",
    )
    body: dict[str, Any] | None = Field(
        default=None,
        description="Raw request body override. When provided, sent verbatim to the API instead of the payload built from the fields above.",
    )


@mcp.tool(
    name="omni_create_eval_prompt_set",
    annotations=ToolAnnotations(
        title="Create Eval Prompt Set",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_eval_prompt_set(params: CreateEvalPromptSetInput) -> str:
    """Create a new eval prompt set bound to a shared model.

    Initial prompts can be supplied; additional prompts can be added later
    via `omni_update_eval_prompt_set`.

    When to Use:
    - To start a regression suite of prompts for a model, so its AI answers
      can be re-evaluated after model or prompt-generation changes.

    When NOT to Use:
    - To add prompts to an existing set — use `omni_update_eval_prompt_set`.

    Returns:
    A short confirmation naming the new prompt set, its id/slug, and prompt
    count, or an `Error ...` string on failure.

    Examples:
    - `{"params": {"model_id": "880e8400-e29b-41d4-a716-446655440003", "name": "Orders regression", "slug": "orders-regression", "prompts": [{"prompt_text": "What are the top 5 products by revenue?", "expectation": "The top product by revenue should be Aniseed Syrup."}]}}`

    Error Handling:
    400 means an invalid request body; 401 means a missing/invalid API key;
    403 means the caller must have at least the Querier role on the model.
    """
    try:
        if params.body is not None:
            payload: dict[str, Any] = params.body
        else:
            payload = {"model_id": params.model_id, "name": params.name, "slug": params.slug}
            if params.description is not None:
                payload["description"] = params.description
            if params.prompts is not None:
                payload["prompts"] = [p.model_dump(exclude_none=True) for p in params.prompts]

        result = await get_client().request_json("POST", "/v1/ai/eval/prompt-sets", json_body=payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        raw_prompt_set = body.get("prompt_set")
        prompt_set: dict[str, Any] = raw_prompt_set if isinstance(raw_prompt_set, dict) else {}
        prompt_count = len(prompt_set.get("prompts") or [])
        return truncate_result(
            f"Created eval prompt set **{prompt_set.get('name', params.name)}** "
            f"(id `{prompt_set.get('id')}`, slug `{prompt_set.get('slug', params.slug)}`) with {prompt_count} prompt(s)."
        )
    except Exception as exc:
        return handle_api_error(exc)


class UpdateEvalPromptSetInput(BaseModel):
    """Input for `omni_update_eval_prompt_set`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt_set_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the eval prompt set.")
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New human-readable name for the prompt set. 255 characters or fewer.",
    )
    description: str | None = Field(
        default=None, max_length=1024, description="New description for the prompt set. Max 1024 characters."
    )
    prompts: list[EvalPromptUpdateInput] | None = Field(
        default=None,
        max_length=25,
        description=(
            "Full desired set of prompts after the update. Existing prompts omitted from this list are "
            "deleted; entries without an `id` are created; entries with a matching `id` are updated in place. "
            "Existing prompts retain their original position — reordering is not supported. At most 25 total."
        ),
    )
    body: dict[str, Any] | None = Field(
        default=None,
        description="Raw request body override. When provided, sent verbatim to the API instead of the payload built from the fields above.",
    )


@mcp.tool(
    name="omni_update_eval_prompt_set",
    annotations=ToolAnnotations(
        title="Update Eval Prompt Set",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_eval_prompt_set(params: UpdateEvalPromptSetInput) -> str:
    """Update a prompt set's name, description, and/or prompts.

    When `prompts` is supplied, it fully replaces the existing list —
    existing prompts omitted from the list are deleted, entries without an
    `id` are created, and entries with a matching `id` are updated in place.

    When to Use:
    - To rename or re-describe a prompt set.
    - To add, edit, or remove prompts (always send the *full* desired list).

    When NOT to Use:
    - To reorder existing prompts — not supported by this endpoint.
    - To archive or restore a set — use `omni_archive_eval_prompt_set` /
      `omni_restore_eval_prompt_set`.

    Returns:
    A short confirmation naming the prompt set and its new prompt count, or
    an `Error ...` string on failure.

    Examples:
    - Rename: `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000", "name": "Orders regression v2"}}`
    - Replace prompts: `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000", "prompts": [{"id": "770e8400-e29b-41d4-a716-446655440002", "prompt_text": "What are the top 10 products by revenue this quarter?"}, {"prompt_text": "New prompt with no id"}]}}`

    Error Handling:
    400 means an invalid request body; 401 means a missing/invalid API key;
    403 means insufficient permissions; 404 means the prompt set was not
    found; 422 means a `prompts[].id` in the request does not belong to this
    prompt set.
    """
    try:
        if params.body is not None:
            payload: dict[str, Any] = params.body
        else:
            payload = {}
            if params.name is not None:
                payload["name"] = params.name
            if params.description is not None:
                payload["description"] = params.description
            if params.prompts is not None:
                payload["prompts"] = [p.model_dump(exclude_none=True) for p in params.prompts]

        path = f"/v1/ai/eval/prompt-sets/{_quote(params.prompt_set_id)}"
        result = await get_client().request_json("PATCH", path, json_body=payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        raw_prompt_set = body.get("prompt_set")
        prompt_set: dict[str, Any] = raw_prompt_set if isinstance(raw_prompt_set, dict) else {}
        prompt_count = len(prompt_set.get("prompts") or [])
        return truncate_result(
            f"Updated eval prompt set **{prompt_set.get('name', params.prompt_set_id)}** "
            f"(id `{params.prompt_set_id}`) — now {prompt_count} prompt(s)."
        )
    except Exception as exc:
        return handle_api_error(exc)


class ArchiveEvalPromptSetInput(BaseModel):
    """Input for `omni_archive_eval_prompt_set`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt_set_id: str = Field(
        ..., min_length=1, description="The unique identifier (UUID) of the eval prompt set to archive."
    )


@mcp.tool(
    name="omni_archive_eval_prompt_set",
    annotations=ToolAnnotations(
        title="Archive Eval Prompt Set",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_archive_eval_prompt_set(params: ArchiveEvalPromptSetInput) -> str:
    """Archive (soft-delete) a prompt set.

    Omni also attempts to cancel every in-flight agentic job associated
    with the set — the response's `cancelled_job_count` reports how many
    were cancelled. Cancellation is best-effort: the archive is committed
    first, so a run-cancellation failure surfaces as a 500 even though the
    set is already archived. The call is idempotent — retrying drains any
    remaining runs.

    When to Use:
    - To retire a prompt set that is no longer needed, without permanently
      losing its history (it can be restored).

    When NOT to Use:
    - To permanently delete it — there is no hard-delete for prompt sets;
      archive is the only removal path.

    Returns:
    A short confirmation including how many in-flight jobs were cancelled,
    or an `Error ...` string on failure.

    Examples:
    - `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    400 means `promptSetId` is not a UUID; 401 means a missing/invalid API
    key; 403 means insufficient permissions; 404 means the prompt set was
    not found; 500 means the archive committed but a run-cancellation
    failed — the set is already archived and it is safe to retry.
    """
    try:
        path = f"/v1/ai/eval/prompt-sets/{_quote(params.prompt_set_id)}"
        result = await get_client().request_json("DELETE", path)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        cancelled = body.get("cancelled_job_count", 0)
        return truncate_result(
            f"Archived eval prompt set `{params.prompt_set_id}` (cancelled {cancelled} in-flight job(s))."
        )
    except Exception as exc:
        return handle_api_error(exc)


class RestoreEvalPromptSetInput(BaseModel):
    """Input for `omni_restore_eval_prompt_set`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt_set_id: str = Field(
        ..., min_length=1, description="The unique identifier (UUID) of the eval prompt set to restore."
    )


@mcp.tool(
    name="omni_restore_eval_prompt_set",
    annotations=ToolAnnotations(
        title="Restore Eval Prompt Set",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_restore_eval_prompt_set(params: RestoreEvalPromptSetInput) -> str:
    """Restore an archived prompt set.

    When to Use:
    - To bring back a previously archived prompt set so it appears again in
      the default (`archived=false`) list and can be run again.

    When NOT to Use:
    - On a prompt set that was never archived — this is a no-op but not an
      error.

    Returns:
    A short confirmation naming the restored prompt set, or an
    `Error ...` string on failure.

    Examples:
    - `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    400 means `promptSetId` is not a UUID; 401 means a missing/invalid API
    key; 403 means insufficient permissions; 404 means the prompt set was
    not found.
    """
    try:
        path = f"/v1/ai/eval/prompt-sets/{_quote(params.prompt_set_id)}/unarchive"
        result = await get_client().request_json("POST", path)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        raw_prompt_set = body.get("prompt_set")
        prompt_set: dict[str, Any] = raw_prompt_set if isinstance(raw_prompt_set, dict) else {}
        return truncate_result(
            f"Restored eval prompt set **{prompt_set.get('name', params.prompt_set_id)}** (id `{params.prompt_set_id}`)."
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------
# AI Eval — runs
# --------------------------------------------------------------------------


class ListEvalRunsInput(BaseModel):
    """Input for `omni_list_eval_runs`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt_set_id: str = Field(
        ..., min_length=1, description="Required — the prompt set (UUID) whose runs should be listed."
    )
    archived: bool = Field(default=False, description="When true, returns archived runs instead of active ones.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for the raw records."
    )


def _format_run_item(item: dict[str, Any]) -> str:
    stats = item.get("stats") or {}
    return (
        f"- Run #{item.get('run_number')} (`{item.get('id')}`) — status **{item.get('status')}**; "
        f"{stats.get('terminal', 0)}/{stats.get('total', 0)} job(s) terminal; "
        f"created {iso_or_na(item.get('created_at'))}; completed {iso_or_na(item.get('completed_at'))}"
    )


@mcp.tool(
    name="omni_list_eval_runs",
    annotations=ToolAnnotations(
        title="List Eval Runs",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_eval_runs(params: ListEvalRunsInput) -> str:
    """List runs for a prompt set, newest first.

    Filtered to runs whose model the caller can access.

    When to Use:
    - To see run history for a prompt set and find a run id to inspect,
      cancel, archive, or restore.

    When NOT to Use:
    - To read one run's full per-prompt results — use `omni_get_eval_run`.

    Returns:
    A markdown list (run number, id, status, terminal/total job counts,
    created/completed timestamps), or the raw `runs` array when
    `response_format` is `json`. This endpoint has no cursor pagination.

    Examples:
    - `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - Archived runs: `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000", "archived": true}}`

    Error Handling:
    400 means `prompt_set_id` is missing or invalid; 401 means a
    missing/invalid API key; 403 means insufficient permissions; 404 means
    the prompt set was not found.
    """
    try:
        query: dict[str, Any] = {
            "prompt_set_id": params.prompt_set_id,
            "archived": "true" if params.archived else "false",
        }
        payload = await get_client().request_json("GET", "/v1/ai/eval/runs", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return _simple_list_response(
            items=list(body.get("runs") or []),
            fmt=params.response_format,
            item_formatter=_format_run_item,
            title="Eval Runs",
        )
    except Exception as exc:
        return handle_api_error(exc)


class GetEvalRunInput(BaseModel):
    """Input for `omni_get_eval_run`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    run_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the eval run.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable summary, `json` for the raw run."
    )


_TERMINAL_JOB_STATES = frozenset({"COMPLETE", "FAILED", "CANCELLED"})


def _render_run_detail(run: dict[str, Any]) -> str:
    results = run.get("results") or []
    terminal = sum(1 for r in results if (r.get("agentic_job") or {}).get("state") in _TERMINAL_JOB_STATES)
    rows = []
    for r in results:
        job = r.get("agentic_job") or {}
        rows.append(
            {
                "prompt": r.get("prompt"),
                "job_state": job.get("state"),
                "score": r.get("score"),
                "cost": r.get("cost"),
                "scoring_cost": r.get("scoring_cost"),
                "timing_ms": r.get("timing_ms"),
                "error_reason": r.get("error_reason"),
            }
        )
    lines = [
        f"# Eval Run #{run.get('run_number')}",
        "",
        f"- ID: `{run.get('id')}`",
        f"- Status: **{run.get('status')}**",
        f"- Prompt set ID: `{run.get('prompt_set_id')}`",
        f"- Model ID: `{run.get('model_id')}`",
        f"- Branch: {run.get('branch_name') or 'main'} (`{run.get('branch_id') or 'N/A'}`)",
        f"- Archived: {run.get('is_archived')}",
        f"- Description: {run.get('description') or 'N/A'}",
        f"- Created: {iso_or_na(run.get('created_at'))}",
        f"- Completed: {iso_or_na(run.get('completed_at'))}",
        f"- Stats: {terminal}/{len(results)} job(s) terminal",
        "",
        f"## Results ({len(results)})",
        "",
        markdown_table(rows),
    ]
    return "\n".join(lines)


@mcp.tool(
    name="omni_get_eval_run",
    annotations=ToolAnnotations(
        title="Get Eval Run",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_eval_run(params: GetEvalRunInput) -> str:
    """Get an eval run with every per-prompt result row.

    Includes the underlying agentic job state and any scoring data.

    When to Use:
    - To check progress or final results of a run — per-prompt score, cost,
      timing, and job state.
    - To debug a failed prompt via its `error_reason`.

    When NOT to Use:
    - To browse run history for a prompt set — use `omni_list_eval_runs`.

    Returns:
    A markdown summary (status, timestamps, terminal/total counts) plus a
    per-prompt results table, or the raw run payload when `response_format`
    is `json`.

    Examples:
    - `{"params": {"run_id": "660e8400-e29b-41d4-a716-446655440001"}}`

    Error Handling:
    400 means `runId` is not a UUID; 401 means a missing/invalid API key;
    403 means insufficient permissions; 404 means the run was not found.
    """
    try:
        path = f"/v1/ai/eval/runs/{_quote(params.run_id)}"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        raw_run = body.get("run")
        run: dict[str, Any] = raw_run if isinstance(raw_run, dict) else body
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(run))
        return truncate_result(_render_run_detail(run))
    except Exception as exc:
        return handle_api_error(exc)


class StartEvalRunInput(BaseModel):
    """Input for `omni_start_eval_run`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt_set_id: str = Field(..., min_length=1, description="The prompt set (UUID) to execute.")
    description: str | None = Field(
        default=None, max_length=1024, description="Optional human-readable description for the run."
    )
    branch_id: str | None = Field(
        default=None,
        description="Optional branch ID (UUID) to run against. Must be a branch of the prompt set's model.",
    )
    body: dict[str, Any] | None = Field(
        default=None,
        description="Raw request body override. When provided, sent verbatim to the API instead of the payload built from the fields above.",
    )


@mcp.tool(
    name="omni_start_eval_run",
    annotations=ToolAnnotations(
        title="Start Eval Run",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_start_eval_run(params: StartEvalRunInput) -> str:
    """Create and start a new run against an existing prompt set.

    The run enqueues one agentic job per prompt and begins executing
    immediately. This consumes AI credits — one agentic job per prompt in
    the set. Returns the newly created run with its initial per-prompt
    result rows.

    When to Use:
    - To re-evaluate a prompt set's answers, e.g. after changing the
      underlying model or its AI query generation.

    When NOT to Use:
    - When the per-user active-run cap has been reached, or AI eval is
      paused for the organization — both surface as errors rather than
      queuing.

    Returns:
    A short confirmation with the new run's number/id and how many jobs
    were enqueued, noting that this consumes AI credits, or an
    `Error ...` string on failure.

    Examples:
    - `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - Against a branch: `{"params": {"prompt_set_id": "550e8400-e29b-41d4-a716-446655440000", "branch_id": "440e8400-e29b-41d4-a716-446655440006", "description": "Re-running after switching to gpt-4o"}}`

    Error Handling:
    400 means an invalid request body; 401 means a missing/invalid API key;
    403 means the caller must have at least the Querier role on the prompt
    set's model; 404 means the prompt set was not found, or `branch_id` does
    not match an existing branch in the organization; 422 means `branch_id`
    does not belong to the prompt set's model; 429 means the per-user
    active-run cap was reached; 503 means AI eval is paused for this
    organization.
    """
    try:
        if params.body is not None:
            payload: dict[str, Any] = params.body
        else:
            payload = {"prompt_set_id": params.prompt_set_id}
            if params.description is not None:
                payload["description"] = params.description
            if params.branch_id is not None:
                payload["run_config"] = {"branch_id": params.branch_id}

        result = await get_client().request_json("POST", "/v1/ai/eval/runs", json_body=payload)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        raw_run = body.get("run")
        run: dict[str, Any] = raw_run if isinstance(raw_run, dict) else {}
        job_count = body.get("job_count", 0)
        return truncate_result(
            f"Started eval run #{run.get('run_number')} (`{run.get('id')}`) on prompt set "
            f"`{params.prompt_set_id}` — {job_count} job(s) enqueued. This consumes AI credits; "
            "poll omni_get_eval_run for results."
        )
    except Exception as exc:
        return handle_api_error(exc)


class CancelEvalRunInput(BaseModel):
    """Input for `omni_cancel_eval_run`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    run_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the eval run to cancel.")


@mcp.tool(
    name="omni_cancel_eval_run",
    annotations=ToolAnnotations(
        title="Cancel Eval Run",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_cancel_eval_run(params: CancelEvalRunInput) -> str:
    """Cancel an in-flight eval run.

    Marks the run cancelled first so no in-progress per-prompt job can flip
    it back to COMPLETE, then cancels every non-terminal agentic job
    associated with the run. The run is also archived as part of the cancel
    (`status: CANCELLED`, `is_archived: true`); use `omni_restore_eval_run`
    to surface it in the default list again.

    When to Use:
    - To stop a run that is taking too long, was started by mistake, or is
      no longer needed, before it consumes further AI credits.

    When NOT to Use:
    - On a run that has already completed — cancelling a terminal run is a
      no-op.

    Returns:
    A short confirmation with how many jobs were cancelled and the run's
    resulting status, or an `Error ...` string on failure.

    Examples:
    - `{"params": {"run_id": "660e8400-e29b-41d4-a716-446655440001"}}`

    Error Handling:
    400 means `runId` is not a UUID; 401 means a missing/invalid API key;
    403 means insufficient permissions; 404 means the run was not found.
    """
    try:
        path = f"/v1/ai/eval/runs/{_quote(params.run_id)}/cancel"
        result = await get_client().request_json("POST", path)
        body: dict[str, Any] = result if isinstance(result, dict) else {}
        raw_run = body.get("run")
        run: dict[str, Any] = raw_run if isinstance(raw_run, dict) else {}
        cancelled = body.get("cancelled", 0)
        total = body.get("total", 0)
        return truncate_result(
            f"Cancelled eval run `{params.run_id}` — {cancelled}/{total} job(s) cancelled by this request; "
            f"run status is now {run.get('status')} and archived={run.get('is_archived')}."
        )
    except Exception as exc:
        return handle_api_error(exc)


class ArchiveEvalRunInput(BaseModel):
    """Input for `omni_archive_eval_run`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    run_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the eval run to archive.")


@mcp.tool(
    name="omni_archive_eval_run",
    annotations=ToolAnnotations(
        title="Archive Eval Run",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_archive_eval_run(params: ArchiveEvalRunInput) -> str:
    """Archive (soft-delete) an eval run.

    Any non-terminal per-prompt agentic jobs are cancelled as part of the
    archive (best-effort), and a still-RUNNING run is flipped to CANCELLED
    before archival. The call is idempotent — archiving an already-terminal
    or already-archived run is a no-op.

    When to Use:
    - To remove a run from the default (`archived=false`) list once you no
      longer need to see it there.

    When NOT to Use:
    - To stop a run that is still executing and you want confirmation of
      how many jobs were cancelled — use `omni_cancel_eval_run`, which
      reports that count explicitly.

    Returns:
    A short confirmation, or an `Error ...` string on failure.

    Examples:
    - `{"params": {"run_id": "660e8400-e29b-41d4-a716-446655440001"}}`

    Error Handling:
    400 means `runId` is not a UUID; 401 means a missing/invalid API key;
    403 means insufficient permissions; 404 means the run was not found.
    """
    try:
        path = f"/v1/ai/eval/runs/{_quote(params.run_id)}"
        await get_client().request_json("DELETE", path)
        return truncate_result(
            f"Archived eval run `{params.run_id}`. Any non-terminal per-prompt jobs were cancelled as part "
            "of the archive; a running run was flipped to CANCELLED first."
        )
    except Exception as exc:
        return handle_api_error(exc)


class RestoreEvalRunInput(BaseModel):
    """Input for `omni_restore_eval_run`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    run_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the eval run to restore.")


@mcp.tool(
    name="omni_restore_eval_run",
    annotations=ToolAnnotations(
        title="Restore Eval Run",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_restore_eval_run(params: RestoreEvalRunInput) -> str:
    """Restore an archived eval run.

    When to Use:
    - To bring an archived run back into the default (`archived=false`)
      list, e.g. after archiving or cancelling it by mistake.

    When NOT to Use:
    - To resume execution of a cancelled run — restoring only changes the
      archived flag; a cancelled run stays cancelled. Start a new run
      instead.

    Returns:
    A short confirmation, or an `Error ...` string on failure.

    Examples:
    - `{"params": {"run_id": "660e8400-e29b-41d4-a716-446655440001"}}`

    Error Handling:
    400 means `runId` is not a UUID; 401 means a missing/invalid API key;
    403 means insufficient permissions; 404 means the run was not found.
    """
    try:
        path = f"/v1/ai/eval/runs/{_quote(params.run_id)}/unarchive"
        await get_client().request_json("POST", path)
        return truncate_result(f"Restored eval run `{params.run_id}` (is_archived=false).")
    except Exception as exc:
        return handle_api_error(exc)
