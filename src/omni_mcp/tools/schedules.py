"""Tools for the schedule and schedule recipient endpoints.

Covers the whole delivery lifecycle: creating and updating scheduled dashboard
deliveries (email, Slack, SFTP, webhook, Amazon S3), pausing/resuming/triggering
them, transferring ownership, managing their recipients, and creating the
email-only users that can receive a delivery without an Omni account.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self
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

#: Output formats a schedule can deliver.
ScheduleFormat = Literal["csv", "link_only", "json", "pdf", "png", "xlsx"]
#: Delivery destinations the API supports.
DestinationType = Literal["webhook", "email", "sftp", "slack", "s3"]
#: Alert condition types (turn a schedule into an alert).
ConditionType = Literal["RESULTS_PRESENT", "RESULTS_CHANGED", "RESULTS_MISSING", "RESULTS_UNCHANGED"]
#: Paper sizes for `pdf` deliveries.
PaperFormat = Literal["a3", "a4", "letter", "legal", "fit_page", "tabloid"]
#: Paper orientations for `pdf` deliveries.
PaperOrientation = Literal["portrait", "landscape"]
#: Whether Slack recipients are a channel or individual users.
SlackRecipientType = Literal["channel", "users"]

_SCHEDULE_ID_DESCRIPTION = (
    "UUID of the schedule. Find it in the schedule's URL after `/schedules/` "
    "(dashboard → File → Deliveries & Alerts → Edit), or via `omni_list_schedules`."
)

_USER_ATTRIBUTES_DESCRIPTION = (
    "User attributes as key/value pairs, where keys are user-attribute IDs (the **Reference** column of the "
    "User attributes page). `omni_user_timezone` is the only supported system attribute; values must match the "
    "attribute's type (numbers for `number` attributes) and multi-value attributes take arrays "
    '(`["US", "EU"]`). Unset an attribute with `null`, `""` (string attributes) or `[]` (multi-value).'
)

#: `(input field, API body key)` for every first-class create/update field.
_WRITE_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("identifier", "identifier"),
    ("name", "name"),
    ("cron", "schedule"),
    ("timezone", "timezone"),
    ("format", "format"),
    ("destination_type", "destinationType"),
    ("filter_config", "filterConfig"),
    ("condition_type", "conditionType"),
    ("condition_query_map_key", "conditionQueryMapKey"),
    ("query_identifier_map_key", "queryIdentifierMapKey"),
    ("kill_jobs_on_failure", "killJobsOnFailure"),
    ("test_now", "testNow"),
    ("hide_title", "hideTitle"),
    ("hide_hidden_fields", "hideHiddenFields"),
    ("enable_formatting", "enableFormatting"),
    ("override_row_limit", "overrideRowLimit"),
    ("max_row_limit", "maxRowLimit"),
    ("show_content_link", "showContentLink"),
    ("show_filters", "showFilters"),
    ("expand_tables_to_show_all_rows", "expandTablesToShowAllRows"),
    ("paper_format", "paperFormat"),
    ("paper_orientation", "paperOrientation"),
    ("single_column_layout", "singleColumnLayout"),
    ("url", "url"),
    ("recipients", "recipients"),
    ("subject", "subject"),
    ("text_body", "textBody"),
    ("slack_recipient_type", "slackRecipientType"),
    ("fan_out", "fanOut"),
    ("address", "address"),
    ("port", "port"),
    ("username", "username"),
    ("path", "path"),
    ("password_unencrypted", "passwordUnencrypted"),
    ("bucket_name", "bucketName"),
    ("region", "region"),
    ("role_arn", "roleArn"),
    ("key_prefix", "keyPrefix"),
    ("filename", "filename"),
)

#: Core body fields the create endpoint requires when no raw `schedule` is given.
_CREATE_REQUIRED_FIELDS: tuple[str, ...] = (
    "identifier",
    "name",
    "cron",
    "timezone",
    "format",
    "destination_type",
)


class _ScheduleWriteFields(BaseModel):
    """Shared create/update body fields (every one optional; `None` is omitted).

    Not a tool input on its own — `CreateScheduleInput` and `UpdateScheduleInput`
    extend it so both speak exactly the same body vocabulary.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    identifier: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The ID of the dashboard the task delivers. Found in File → Document settings → Settings "
            "(**Identifier**), or in the dashboard URL after `/dashboards/` (for example `12db1a0a`). "
            "**Required on create.**"
        ),
    )
    name: str | None = Field(default=None, min_length=1, description="The name of the task. **Required on create.**")
    cron: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Cron expression defining when the schedule runs, in AWS CloudWatch syntax "
            "(`minute hour day-of-month month day-of-week year`), for example `0 9 ? * MON *`. Sent as the "
            "API's `schedule` body field. Delivery times are approximate — within 15 minutes of the run time. "
            "**Required on create.**"
        ),
    )
    timezone: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "IANA timezone the task runs in, such as `America/New_York` or `Asia/Dubai` "
            "(the `TZ` column of the IANA timezone database). **Required on create.**"
        ),
    )
    format: ScheduleFormat | None = Field(
        default=None,
        description="Output format: `csv`, `link_only`, `json`, `pdf`, `png` or `xlsx`. **Required on create.**",
    )
    destination_type: DestinationType | None = Field(
        default=None,
        description=(
            "Where the output is delivered: `webhook`, `email`, `sftp`, `slack` or `s3`. Using `slack` requires a "
            "Slack workspace already connected to Omni. **Required on create.**"
        ),
    )
    filter_config: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Filter conditions applied to the task, keyed by dashboard filter key (case-sensitive, and the key "
            "must already exist on the dashboard), for example "
            '`{"region": {"kind": "EQ", "left_side": "US", "type": "string"}}`.'
        ),
    )
    condition_type: ConditionType | None = Field(
        default=None,
        description=(
            "Alert condition: `RESULTS_PRESENT`, `RESULTS_CHANGED`, `RESULTS_MISSING` or `RESULTS_UNCHANGED`. "
            "Required whenever `condition_query_map_key` is set."
        ),
    )
    condition_query_map_key: str | None = Field(
        default=None,
        description=("ID of the dashboard query to monitor for the alert. Required whenever `condition_type` is set."),
    )
    query_identifier_map_key: str | None = Field(
        default=None,
        description=(
            "ID of the query to include in a single-tile task. Required for `format: json` with "
            "`enable_formatting: true`, and for `format: xlsx` with `override_row_limit: true`."
        ),
    )
    kill_jobs_on_failure: bool | None = Field(
        default=None, description="Stop the entire job if any query in the task fails (API default `false`)."
    )
    test_now: bool | None = Field(
        default=None,
        description=(
            "Run the task immediately instead of waiting for the schedule — this sends a **real delivery** to the "
            "configured recipients. Not supported for `RESULTS_CHANGED` conditions or `s3` destinations "
            "(API default `false`)."
        ),
    )
    hide_title: bool | None = Field(
        default=None, description="`pdf`/`png` only: hide the content's title in the output (API default `false`)."
    )
    hide_hidden_fields: bool | None = Field(
        default=None,
        description="`csv`/`xlsx` only: omit fields marked as hidden from the output (API default `false`).",
    )
    enable_formatting: bool | None = Field(
        default=None,
        description=(
            "`csv`/`xlsx`/`json` only: preserve number and date formatting. With `json`, "
            "`query_identifier_map_key` is required (API default `false`)."
        ),
    )
    override_row_limit: bool | None = Field(
        default=None,
        description=(
            "`csv`/`json`/`xlsx` only: override the default row limit. With `json` and `xlsx`, "
            "`query_identifier_map_key` is required (API default `false`)."
        ),
    )
    max_row_limit: int | None = Field(
        default=None,
        ge=1,
        description="`csv`/`json`/`xlsx` only: maximum number of rows, used together with `override_row_limit`.",
    )
    show_content_link: bool | None = Field(
        default=None,
        description="All formats except `link_only`: include a link to the content (API default `true`).",
    )
    show_filters: bool | None = Field(
        default=None,
        description="All formats except `link_only` and `csv`: show the applied filters (API default `true`).",
    )
    expand_tables_to_show_all_rows: bool | None = Field(
        default=None,
        description=(
            "`pdf`/`png` only: include up to 1,000 rows of table visualizations. Cannot be combined with "
            "`paper_format: fit_page` (API default `false`)."
        ),
    )
    paper_format: PaperFormat | None = Field(
        default=None,
        description=(
            "`pdf` only: paper size — `a3`, `a4`, `letter`, `legal`, `fit_page` or `tabloid`. `fit_page` cannot be "
            "combined with `expand_tables_to_show_all_rows: true`."
        ),
    )
    paper_orientation: PaperOrientation | None = Field(
        default=None, description="`pdf` only: `portrait` or `landscape`."
    )
    single_column_layout: bool | None = Field(
        default=None,
        description="`pdf`/`png` only: arrange dashboard tiles in one vertical column (API default `false`).",
    )
    url: str | None = Field(
        default=None,
        description="**Required for `destination_type: webhook`.** A valid HTTP/HTTPS webhook URL.",
    )
    recipients: list[str] | str | None = Field(
        default=None,
        description=(
            "**Required for `destination_type: email` and `slack`.** For email: a list of email addresses. "
            'For Slack: channel or user IDs — a single channel ID (`"C01234567"` or `["C01234567"]`; only one '
            "channel per schedule, private channels need the Omni Slackbot invited) or user IDs "
            '(`["U111", "U222"]` or the comma-separated string `"U111,U222"`).'
        ),
    )
    subject: str | None = Field(
        default=None, description="**Required for `destination_type: email`.** The email subject line."
    )
    text_body: str | None = Field(
        default=None, description="Email and Slack destinations: custom message text sent with the delivery."
    )
    slack_recipient_type: SlackRecipientType | None = Field(
        default=None,
        description=(
            "**Required for `destination_type: slack`.** `channel` to deliver to a Slack channel (invite the Omni "
            "Slackbot first if it is private) or `users` to deliver to individual Slack users."
        ),
    )
    fan_out: bool | None = Field(
        default=None,
        description=(
            "Email destinations: send an individual, personalized email per recipient. The organization's delivery "
            "personalization setting can force this to `false` (Never) or `true` (Always) regardless of what is "
            "submitted (API default `false`)."
        ),
    )
    address: str | None = Field(
        default=None, description="**Required for `destination_type: sftp`.** SFTP server address."
    )
    port: int | None = Field(
        default=None, ge=1, le=65535, description="**Required for `destination_type: sftp`.** SFTP port, e.g. `22`."
    )
    username: str | None = Field(default=None, description="**Required for `destination_type: sftp`.** SFTP username.")
    path: str | None = Field(
        default=None, description="**Required for `destination_type: sftp`.** Remote file path on the SFTP server."
    )
    password_unencrypted: str | None = Field(
        default=None, description="SFTP destinations: the SFTP password. Never echoed back in tool output."
    )
    bucket_name: str | None = Field(
        default=None,
        min_length=3,
        max_length=63,
        description=(
            "**Required for `destination_type: s3`.** Target bucket name — 3-63 characters, lowercase letters, "
            "numbers, hyphens and periods only."
        ),
    )
    region: str | None = Field(
        default=None,
        description="**Required for `destination_type: s3`.** AWS region of the bucket, e.g. `us-east-1`.",
    )
    role_arn: str | None = Field(
        default=None,
        description=(
            "**Required for `destination_type: s3`.** ARN of the IAM role in the target AWS account that grants "
            "write access to the bucket, e.g. `arn:aws:iam::123456789012:role/DeliveryRole`."
        ),
    )
    key_prefix: str | None = Field(
        default=None,
        description=(
            "S3 destinations: folder path prefix for the uploaded file, e.g. `reports/daily/`. Must not contain "
            "path-traversal sequences."
        ),
    )
    filename: str | None = Field(
        default=None,
        description=(
            "S3 destinations: filename template (without extension) in Mustache syntax. Supported variables: "
            "`{{currentDate}}`, `{{currentTime}}`, `{{currentYear}}`, `{{currentMonth}}`, `{{currentDay}}`, "
            "`{{yesterdayDate}}`, `{{timeZone}}`, `{{entityName}}`, `{{format}}`, `{{scheduledTaskName}}`."
        ),
    )
    schedule: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Escape hatch: the raw request body, sent verbatim and **overriding every field above** when provided. "
            "Keys are the API's camelCase names (`identifier`, `name`, `schedule` for the cron expression, "
            "`timezone`, `format`, `destinationType`, …). Use it only for body shapes the typed fields cannot "
            "express; note the cron expression goes in the `cron` field when using the typed fields."
        ),
    )


class ListSchedulesInput(BaseModel):
    """Input for `omni_list_schedules`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    q: str | None = Field(
        default=None,
        description="Search term filtering by schedule name, dashboard name or owner name (case-insensitive).",
    )
    status: Literal["success", "error", "canceled", "none"] | None = Field(
        default=None, description="Filter by delivery status: `success`, `error`, `canceled` or `none`."
    )
    destination: DestinationType | None = Field(
        default=None, description="Filter by destination type: `email`, `slack`, `webhook`, `sftp` or `s3`."
    )
    schedule_type: Literal["alert", "schedule"] | None = Field(
        default=None, description="Filter by type: `alert` (has an alert condition) or `schedule`."
    )
    content_type: Literal["dashboard", "single tile"] | None = Field(
        default=None, description="Filter by content type: `dashboard` or `single tile`."
    )
    sort_field: Literal["scheduleName", "dashboardName", "ownerName", "lastRun", "lastRunStatus"] | None = Field(
        default=None,
        description=(
            "Sort by `scheduleName` (API default), `dashboardName`, `ownerName`, `lastRun` or `lastRunStatus`."
        ),
    )
    sort_direction: Literal["asc", "desc"] | None = Field(
        default=None, description="Sort direction: `asc` or `desc` (API default `desc`)."
    )
    owner_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Filter by the owner's **membership ID** (from `omni_list_users`). Organization API keys may pass any "
            "membership ID; a Personal Access Token may only pass its own or the API returns 403."
        ),
    )
    embed_entity: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Filter by embed entity. Alone it lists every schedule in the entity; combined with `owner_id` it lists "
            "that user's schedules within the entity."
        ),
    )
    identifier: str | None = Field(
        default=None,
        min_length=1,
        description="Filter by dashboard ID (the string after `/dashboards/` in the dashboard URL).",
    )
    page_size: int = Field(default=20, ge=1, le=100, description="Records per page (1-100). API default 20.")
    cursor: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Page number for this offset-based endpoint (starts at 1). Pass the `nextCursor` printed by a previous "
            "call to fetch the next page."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw records and page info.",
    )


class GetScheduleInput(BaseModel):
    """Input for `omni_get_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)
    user_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "**Organization API keys only.** Membership ID whose permissions should be checked — the endpoint "
            "verifies that user can view the dashboard before returning the schedule. A Personal Access Token "
            "passing this gets a 403."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw schedule payload.",
    )


class CreateScheduleInput(_ScheduleWriteFields):
    """Input for `omni_create_schedule`."""

    user_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "**Organization API keys only.** The **membership ID** (not the user ID) of the user who should own the "
            "schedule; omit it and the organization owns the schedule. A Personal Access Token passing this gets a "
            "403 — with a PAT the schedule owner is always the token's creator."
        ),
    )

    @model_validator(mode="after")
    def _require_core_fields(self) -> Self:
        """Enforce the endpoint's required fields unless a raw `schedule` body is supplied."""
        if self.schedule is not None:
            return self
        missing = [name for name in _CREATE_REQUIRED_FIELDS if getattr(self, name) is None]
        if missing:
            raise ValueError(
                "Missing required field(s) for creating a schedule: "
                + ", ".join(missing)
                + " (or pass the whole body in `schedule`)."
            )
        return self


class UpdateScheduleInput(_ScheduleWriteFields):
    """Input for `omni_update_schedule`."""

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)

    @model_validator(mode="after")
    def _require_one_change(self) -> Self:
        """Refuse an update that would send an empty body."""
        if self.schedule is not None:
            return self
        if not any(getattr(self, name) is not None for name, _ in _WRITE_FIELD_MAP):
            raise ValueError("Provide at least one field to update (or pass the whole body in `schedule`).")
        return self


class DeleteScheduleInput(BaseModel):
    """Input for `omni_delete_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)


class PauseScheduleInput(BaseModel):
    """Input for `omni_pause_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)


class ResumeScheduleInput(BaseModel):
    """Input for `omni_resume_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)


class TriggerScheduleInput(BaseModel):
    """Input for `omni_trigger_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)


class TransferScheduleOwnershipInput(BaseModel):
    """Input for `omni_transfer_schedule_ownership`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)
    user_id: str = Field(
        ...,
        min_length=1,
        description=(
            "UUID of the user receiving ownership (from `omni_list_users`). They must be in the same organization, "
            "must not already own the schedule, and must be able to view the associated dashboard."
        ),
    )


class ListScheduleRecipientsInput(BaseModel):
    """Input for `omni_list_schedule_recipients`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw destination payload.",
    )


class AddScheduleRecipientsInput(BaseModel):
    """Input for `omni_add_schedule_recipients`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)
    emails: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Email addresses to add as recipients. At least one email or one user ID must be provided.",
    )
    user_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "User UUIDs to add as recipients (from `omni_list_users` or the embed-user list). At least one email or "
            "one user ID must be provided."
        ),
    )

    @model_validator(mode="after")
    def _require_a_recipient(self) -> Self:
        """The API rejects an empty request — catch it before the round trip."""
        if not self.emails and not self.user_ids:
            raise ValueError("Provide at least one recipient in `emails` or `user_ids`.")
        return self


class RemoveScheduleRecipientsInput(BaseModel):
    """Input for `omni_remove_schedule_recipients`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schedule_id: str = Field(..., min_length=1, description=_SCHEDULE_ID_DESCRIPTION)
    emails: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Recipient email addresses to remove. At least one email or one user ID must be provided.",
    )
    user_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Recipient user UUIDs to remove. At least one email or one user ID must be provided.",
    )

    @model_validator(mode="after")
    def _require_a_recipient(self) -> Self:
        """The API rejects an empty request — catch it before the round trip."""
        if not self.emails and not self.user_ids:
            raise ValueError("Provide at least one recipient in `emails` or `user_ids`.")
        return self


class ManageEmailOnlyUserInput(BaseModel):
    """Input for `omni_manage_email_only_user`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(
        ...,
        min_length=3,
        description="The email-only user's email address. An existing match is updated rather than duplicated.",
    )
    user_attributes: dict[str, Any] | None = Field(default=None, description=_USER_ATTRIBUTES_DESCRIPTION)


class EmailOnlyUserSpec(BaseModel):
    """One `{email, userAttributes}` entry of a bulk email-only user request."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(..., min_length=3, description="The email-only user's email address.")
    user_attributes: dict[str, Any] | None = Field(default=None, description=_USER_ATTRIBUTES_DESCRIPTION)


class BulkManageEmailOnlyUsersInput(BaseModel):
    """Input for `omni_bulk_manage_email_only_users`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    users: list[EmailOnlyUserSpec] = Field(
        ...,
        min_length=1,
        max_length=20,
        description=(
            "Up to 20 users to create or update, each shaped "
            '`{"email": "person@example.com", "user_attributes": {"region": ["US"]}}` '
            "(`user_attributes` optional). Existing emails are updated in place."
        ),
    )


def _write_body(params: _ScheduleWriteFields) -> dict[str, Any]:
    """Assemble the create/update body, omitting `None`s.

    A raw `schedule` dict wins outright: it is sent verbatim so callers can
    express body shapes the typed fields do not cover.
    """
    if params.schedule is not None:
        return dict(params.schedule)
    body: dict[str, Any] = {}
    for field_name, api_key in _WRITE_FIELD_MAP:
        value = getattr(params, field_name)
        if value is not None:
            body[api_key] = value
    return body


def _schedule_status(item: Mapping[str, Any]) -> str:
    """Human-readable run state: system-disabled, paused, or active."""
    if item.get("systemDisabledAt"):
        reason = item.get("systemDisabledReason") or "unknown reason"
        return f"system-disabled ({reason})"
    if item.get("disabledAt"):
        return "paused"
    return "active"


def _recipient_count(item: Mapping[str, Any]) -> str:
    """`recipientCount` as text — the API returns `-1` for webhook destinations."""
    count = item.get("recipientCount")
    if not isinstance(count, int) or count < 0:
        return "n/a"
    return f"{count:,} recipient(s)"


def _schedule_line(item: Mapping[str, Any]) -> str:
    """One list row: name, id, dashboard, cadence, destination, status, last run."""
    name = item.get("name") or "unnamed"
    dashboard = item.get("dashboardName") or "unknown dashboard"
    cadence = item.get("schedule") or "unknown cron"
    timezone = item.get("timezone") or "unknown timezone"
    destination = item.get("destinationType") or "unknown destination"
    fmt = item.get("format") or "unknown format"
    last_status = item.get("lastStatus") or "never run"
    owner = item.get("ownerName") or "unknown owner"
    return (
        f"- **{name}** (`{item.get('id', 'unknown')}`) — dashboard **{dashboard}** "
        f"(`{item.get('identifier', 'unknown')}`); `{cadence}` {timezone}; "
        f"{destination}/{fmt}, {_recipient_count(item)}; {_schedule_status(item)}; "
        f"last run {iso_or_na(item.get('lastCompletedAt'))} ({last_status}); owner {owner}"
    )


def _recipient_emails(recipients: Any) -> str:
    """Summarize a destination's `recipients` array as an email list."""
    if not isinstance(recipients, Sequence) or isinstance(recipients, str):
        return "none"
    emails: list[str] = []
    for recipient in recipients:
        if not isinstance(recipient, Mapping):
            continue
        membership = recipient.get("membership")
        user = membership.get("user") if isinstance(membership, Mapping) else None
        if isinstance(user, Mapping) and user.get("email"):
            emails.append(str(user["email"]))
    if not emails:
        return f"{len(recipients):,} recipient(s)"
    shown = ", ".join(emails[:10])
    if len(emails) > 10:
        shown += f", … (+{len(emails) - 10:,} more)"
    return shown


def _destination_rows(destinations: Any) -> list[dict[str, Any]]:
    """Table rows for the `destinations` array returned by Get schedule."""
    rows: list[dict[str, Any]] = []
    if not isinstance(destinations, Sequence) or isinstance(destinations, str):
        return rows
    for destination in destinations:
        if not isinstance(destination, Mapping):
            continue
        metadata = destination.get("metadata")
        destination_type = metadata.get("type") if isinstance(metadata, Mapping) else None
        rows.append(
            {
                "Destination": destination_type or "unknown",
                "Format": destination.get("format") or "unknown",
                "Last status": destination.get("lastStatus") or "never run",
                "Last delivery": iso_or_na(destination.get("lastCompletedAt")),
                "Recipients": _recipient_emails(destination.get("recipients")),
            }
        )
    return rows


def _render_schedule_detail(body: Mapping[str, Any]) -> str:
    """Markdown summary of one schedule (Get schedule response)."""
    raw_owner = body.get("owner")
    owner: Mapping[str, Any] = raw_owner if isinstance(raw_owner, Mapping) else {}
    lines = [
        f"# {body.get('name') or 'unnamed schedule'}",
        "",
        f"- Schedule ID: `{body.get('id', 'unknown')}`",
        f"- Dashboard ID: `{body.get('entityId', 'unknown')}`",
        f"- Owner: {owner.get('name') or 'unknown'} (`{body.get('ownerId', 'unknown')}`)",
        f"- Cadence: `{body.get('schedule') or 'unknown cron'}` in {body.get('timezone') or 'unknown timezone'}",
        f"- Status: **{_schedule_status(body)}**",
        f"- Personalized per recipient (fanOut): {bool(body.get('fanOut'))}",
        f"- Stop job when a query fails: {bool(body.get('killJobsOnFailure'))}",
        f"- Created {iso_or_na(body.get('createdAt'))} · updated {iso_or_na(body.get('updatedAt'))}",
    ]
    if body.get("conditionType"):
        lines.append(
            f"- Alert condition: **{body.get('conditionType')}** on query "
            f"`{body.get('conditionQueryMapKey') or 'unknown'}`"
        )
    else:
        lines.append("- Alert condition: none (standard schedule)")

    filter_config = body.get("filterConfig")
    if filter_config:
        lines.extend(["", "## Filters", "", "```json", to_json(filter_config), "```"])

    rows = _destination_rows(body.get("destinations"))
    lines.extend(["", f"## Destinations ({len(rows)})", ""])
    lines.append(markdown_table(rows) if rows else "_No destinations reported._")
    return "\n".join(lines)


def _render_recipients(body: Mapping[str, Any]) -> str:
    """Markdown summary of the destination-specific recipients payload."""
    destination_type = body.get("type") or "unknown"
    lines = [f"# Schedule recipients ({destination_type})", ""]

    if destination_type == "email":
        raw = body.get("recipients")
        recipients = raw if isinstance(raw, Sequence) and not isinstance(raw, str) else []
        rows = [
            {
                "Name": item.get("name") or "",
                "Email": item.get("email") or "",
                "Email-only": bool(item.get("emailOnly")),
                "ID": item.get("id") or "",
            }
            for item in recipients
            if isinstance(item, Mapping)
        ]
        lines.append(f"**{len(rows):,}** recipient(s).")
        lines.append("")
        lines.append(markdown_table(rows) if rows else "_No recipients._")
    elif destination_type == "slack":
        raw = body.get("recipients")
        recipients = raw if isinstance(raw, Sequence) and not isinstance(raw, str) else []
        rows = [
            {"Recipient type": item.get("recipientType") or "", "Slack ID": item.get("slackId") or ""}
            for item in recipients
            if isinstance(item, Mapping)
        ]
        lines.append(f"**{len(rows):,}** Slack recipient(s).")
        lines.append("")
        lines.append(markdown_table(rows) if rows else "_No recipients._")
    elif destination_type == "sftp":
        lines.extend(
            [
                f"- Address: `{body.get('address', 'unknown')}`",
                f"- Port: {body.get('port', 'unknown')}",
                f"- Username: `{body.get('username', 'unknown')}`",
            ]
        )
    elif destination_type == "webhook":
        lines.append(f"- URL: `{body.get('url', 'unknown')}`")
    elif destination_type == "s3":
        lines.extend(
            [
                f"- Bucket: `{body.get('bucketName', 'unknown')}`",
                f"- Region: `{body.get('region', 'unknown')}`",
                f"- Role ARN: `{body.get('roleArn', 'unknown')}`",
                f"- External ID: `{body.get('externalId', 'unknown')}`",
                f"- Key prefix: `{body.get('keyPrefix') or '(none)'}`",
                f"- Filename template: `{body.get('filename') or '(default)'}`",
            ]
        )
    else:
        lines.append("_Unrecognized destination payload; re-run with `response_format: json` to see it raw._")
    return "\n".join(lines)


@mcp.tool(
    name="omni_list_schedules",
    annotations=ToolAnnotations(
        title="List Schedules",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_schedules(params: ListSchedulesInput) -> str:
    """List scheduled dashboard deliveries and alerts, with filtering and sorting.

    Calls `GET /v1/schedules`. Each record carries the schedule's cadence,
    timezone, dashboard, owner, destination type, format, recipient count, paused
    state and the outcome of the last run. Pagination is offset-based: `cursor` is
    a page number and the next one is printed with the results.

    When to Use:
    - To find a schedule's ID before getting, updating, pausing or deleting it.
    - To audit which dashboards deliver where (`destination`), or which schedules
      are failing (`status: error`).
    - To list one person's schedules (`owner_id`) or one dashboard's (`identifier`).

    When NOT to Use:
    - To read a single schedule's full configuration — use `omni_get_schedule`.
    - To see who receives a delivery — use `omni_list_schedule_recipients`.

    Returns:
    A markdown list — name, id, dashboard, cron + timezone, destination/format,
    recipient count, status, last run and owner — plus the next cursor, or the raw
    records and `pageInfo` when `response_format` is `json`.

    Examples:
    - Newest failures first: `{"params": {"status": "error", "sort_field": "lastRun", "sort_direction": "desc"}}`
    - One dashboard's schedules: `{"params": {"identifier": "12db1a0a", "page_size": 50}}`
    - Alerts only, second page: `{"params": {"schedule_type": "alert", "cursor": 2}}`

    Error Handling:
    400 means an invalid enum value, a non-UUID `owner_id`, a `page_size` above
    100, or a `cursor` past the last page (the message names the last valid page).
    403 means a Personal Access Token filtered by another user's `owner_id` — PATs
    may only pass their own membership ID. 404 means the `owner_id` user does not
    exist.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size}
        optional: dict[str, Any] = {
            "q": params.q,
            "status": params.status,
            "destination": params.destination,
            "scheduleType": params.schedule_type,
            "contentType": params.content_type,
            "sortField": params.sort_field,
            "sortDirection": params.sort_direction,
            "ownerId": params.owner_id,
            "embedEntity": params.embed_entity,
            "identifier": params.identifier,
            "cursor": params.cursor,
        }
        query.update({key: value for key, value in optional.items() if value is not None})
        payload = await get_client().request_json("GET", "/v1/schedules", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_schedule_line,
            title="Schedules",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_schedule",
    annotations=ToolAnnotations(
        title="Get Schedule",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_schedule(params: GetScheduleInput) -> str:
    """Retrieve one schedule's full configuration, destinations and current status.

    Calls `GET /v1/schedules/{scheduleId}`. Returns the cadence and timezone, the
    owner, filter configuration, alert condition, paused/system-disabled state and
    every delivery destination with its last status.

    When to Use:
    - Before updating a schedule, to see what it currently sends and to whom.
    - To debug a failing delivery (last status, system-disabled reason).
    - To confirm an alert's condition type and trigger query.

    When NOT to Use:
    - To search across schedules — use `omni_list_schedules`.
    - To read just the recipient list — `omni_list_schedule_recipients` is cheaper
      and returns destination-specific detail.

    Returns:
    A markdown summary — name, ids, owner, cron + timezone, status, alert
    condition, filters and a destinations table (format, last status, last
    delivery, recipients) — or the raw payload when `response_format` is `json`.

    Examples:
    - `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000"}}`
    - Check as a specific user (org API key): `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`
    - Raw payload: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "response_format": "json"}}`

    Error Handling:
    400 means `schedule_id` or `user_id` is not a UUID. 403 means the caller cannot
    view or schedule the dashboard, is not the schedule owner, or used `user_id`
    with a Personal Access Token (organization API keys only). 404 means the
    schedule (or the `user_id` user) is not in this organization.
    """
    try:
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id
        payload = await get_client().request_json(
            "GET",
            f"/v1/schedules/{quote(params.schedule_id, safe='')}",
            params=query or None,
        )
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        return truncate_result(_render_schedule_detail(body))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_schedule",
    annotations=ToolAnnotations(
        title="Create Schedule",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_schedule(params: CreateScheduleInput) -> str:
    """Create a scheduled delivery or alert for a dashboard.

    Calls `POST /v1/schedules`. Supports email, Slack, SFTP, webhook and Amazon S3
    destinations, dashboard filters, format options, alert conditions, and an
    immediate test delivery via `test_now`. Slack destinations need a Slack
    workspace connected to Omni first.

    When to Use:
    - To schedule a recurring dashboard delivery (`cron` + `timezone`).
    - To create an alert: set `condition_type` and `condition_query_map_key`.
    - To send a one-off test of a configuration with `test_now: true`.

    When NOT to Use:
    - To change an existing schedule — use `omni_update_schedule`.
    - To run an existing schedule now — use `omni_trigger_schedule`.
    - To add people to an existing email delivery — use
      `omni_add_schedule_recipients`.

    Returns:
    A confirmation with the new schedule's id, dashboard, cadence and destination.
    For S3 destinations it also reports the deliverer role ARN and external ID
    needed for the bucket's IAM trust policy. On failure, an `Error ...` string.

    Examples:
    - Email PDF: `{"params": {"identifier": "12db1a0a", "name": "Daily Sales", "cron": "0 9 ? * * *", "timezone": "UTC", "format": "pdf", "destination_type": "email", "recipients": ["analyst@example.com"], "subject": "Daily Sales report", "paper_format": "letter", "paper_orientation": "landscape"}}`
    - Slack channel: `{"params": {"identifier": "12db1a0a", "name": "Weekly Report", "cron": "0 9 ? * MON *", "timezone": "America/New_York", "format": "pdf", "destination_type": "slack", "recipients": "C01234567", "slack_recipient_type": "channel", "text_body": "Here is your weekly report"}}`
    - Webhook JSON: `{"params": {"identifier": "12db1a0a", "name": "Webhook feed", "cron": "0 9 ? * * *", "timezone": "UTC", "format": "json", "destination_type": "webhook", "url": "https://example.com/hooks/1234"}}`
    - SFTP XLSX: `{"params": {"identifier": "12db1a0a", "name": "Nightly export", "cron": "0 2 ? * * *", "timezone": "UTC", "format": "xlsx", "destination_type": "sftp", "address": "sftp.example.com", "port": 22, "username": "reporting", "path": "/uploads/nightly", "password_unencrypted": "…"}}`
    - Amazon S3 CSV: `{"params": {"identifier": "12db1a0a", "name": "Daily export", "cron": "0 9 ? * * *", "timezone": "UTC", "format": "csv", "destination_type": "s3", "bucket_name": "reports-bucket", "region": "us-east-1", "role_arn": "arn:aws:iam::123456789012:role/DeliveryRole", "key_prefix": "reports/daily/", "filename": "{{entityName}}-{{currentDate}}"}}`
    - Alert on results: `{"params": {"identifier": "12db1a0a", "name": "Data alert", "cron": "0 */6 * * ? *", "timezone": "UTC", "format": "json", "destination_type": "webhook", "url": "https://example.com/hooks/alert", "condition_type": "RESULTS_PRESENT", "condition_query_map_key": "1", "query_identifier_map_key": "1", "override_row_limit": true, "max_row_limit": 1000}}`
    - Raw body escape hatch: `{"params": {"schedule": {"identifier": "12db1a0a", "name": "Raw", "schedule": "0 9 ? * * *", "timezone": "UTC", "format": "csv", "destinationType": "email", "recipients": ["analyst@example.com"], "subject": "Raw"}}}`

    Error Handling:
    400 covers invalid cron expressions, non-IANA timezones, filter keys the
    dashboard does not have, format-incompatible print options, `fit_page` with
    table expansion, a missing `slack_recipient_type`, more than one Slack channel,
    a missing Slack credential, an invalid bucket/region/role ARN, a key prefix
    with path traversal, and `test_now` with `RESULTS_CHANGED` or S3. 403 means a
    Personal Access Token tried to create a schedule for another user via
    `user_id`. 404 means the dashboard `identifier` does not exist. Requires
    permission to view and schedule the dashboard.
    """
    try:
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id
        body = _write_body(params)
        payload = await get_client().request_json("POST", "/v1/schedules", params=query or None, json_body=body)
        response: dict[str, Any] = payload if isinstance(payload, dict) else {}
        name = body.get("name", "unnamed")
        lines = [
            f"Created schedule **{name}** (id `{response.get('id', 'unknown')}`) on dashboard "
            f"`{body.get('identifier', 'unknown')}` — {body.get('destinationType', 'unknown')}/"
            f"{body.get('format', 'unknown')}, `{body.get('schedule', 'unknown')}` "
            f"in {body.get('timezone', 'unknown')}."
        ]
        if body.get("testNow"):
            lines.append("A test delivery was requested and has been sent to the configured recipients.")
        if response.get("delivererRoleArn"):
            lines.append(
                f"S3 trust policy — deliverer role ARN `{response.get('delivererRoleArn')}`, "
                f"external ID `{response.get('externalId', 'unknown')}`."
            )
        return truncate_result(" ".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_update_schedule",
    annotations=ToolAnnotations(
        title="Update Schedule",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_schedule(params: UpdateScheduleInput) -> str:
    """Update an existing schedule; omitted properties keep their current values.

    Calls `PUT /v1/schedules/{scheduleId}` with the same body vocabulary as
    `omni_create_schedule`, every field optional. Changes apply to future runs —
    a job already running is unaffected.

    When to Use:
    - To change a schedule's cadence, timezone, format or print options.
    - To repoint a delivery at a different destination or recipient list.
    - To add, change or remove an alert condition.

    When NOT to Use:
    - To only add or remove email recipients — `omni_add_schedule_recipients` and
      `omni_remove_schedule_recipients` are safer (they leave the rest untouched).
    - To stop deliveries temporarily — use `omni_pause_schedule`.

    Returns:
    A confirmation naming the schedule id and the fields that were sent. On
    failure, an `Error ...` string.

    Examples:
    - Move to Mondays at 09:00: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "cron": "0 9 ? * MON *", "timezone": "America/New_York"}}`
    - Change the email subject and recipients: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "subject": "Weekly Sales", "recipients": ["lead@example.com"]}}`
    - Switch to landscape letter PDF: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "format": "pdf", "paper_format": "letter", "paper_orientation": "landscape"}}`
    - Raw body escape hatch: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "schedule": {"name": "Weekly Sales Report", "schedule": "0 9 ? * MON *", "timezone": "America/New_York"}}}`

    Error Handling:
    400 covers an invalid UUID, an invalid cron expression, a non-IANA timezone,
    unknown filter keys, print options incompatible with the format, a missing
    `slack_recipient_type`, more than one Slack channel, a missing Slack
    credential, and invalid S3 bucket/region/role ARN or key prefix. 404 means the
    schedule does not exist. Requires permission to manage the schedule.
    """
    try:
        body = _write_body(params)
        await get_client().request_json(
            "PUT",
            f"/v1/schedules/{quote(params.schedule_id, safe='')}",
            json_body=body,
        )
        fields = ", ".join(f"`{key}`" for key in body) or "no fields"
        return truncate_result(f"Updated schedule `{params.schedule_id}` — set {fields}.")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_delete_schedule",
    annotations=ToolAnnotations(
        title="Delete Schedule",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_schedule(params: DeleteScheduleInput) -> str:
    """Permanently delete a schedule and stop all of its future deliveries.

    Calls `DELETE /v1/schedules/{scheduleId}`. This cannot be undone — the
    schedule, its destinations and its recipient list are gone.

    When to Use:
    - To retire a delivery nobody needs any more.
    - To clean up a schedule created by mistake.

    When NOT to Use:
    - To stop deliveries temporarily — `omni_pause_schedule` is reversible.
    - To drop a few recipients — use `omni_remove_schedule_recipients`.

    Returns:
    A short confirmation naming the deleted schedule id, or an `Error ...` string.

    Examples:
    - `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000"}}`

    Error Handling:
    400 means `schedule_id` is not a UUID. 404 means the schedule does not exist —
    it may already have been deleted. Requires permission to manage the schedule.
    """
    try:
        await get_client().request_json("DELETE", f"/v1/schedules/{quote(params.schedule_id, safe='')}")
        return f"Deleted schedule `{params.schedule_id}`. Future deliveries are cancelled."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_pause_schedule",
    annotations=ToolAnnotations(
        title="Pause Schedule",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_pause_schedule(params: PauseScheduleInput) -> str:
    """Pause a schedule so it stops running until it is resumed.

    Calls `PUT /v1/schedules/{scheduleId}/pause`, which sets the schedule's
    `disabledAt`. The configuration and recipients are preserved.

    When to Use:
    - To stop deliveries during a data migration, freeze or holiday.
    - To silence a noisy alert without losing its configuration.

    When NOT to Use:
    - To remove a delivery for good — use `omni_delete_schedule`.
    - On a schedule the system disabled — pausing that returns 400; fix the
      underlying problem (missing query, lost access, orphaned filter keys) first.

    Returns:
    A short confirmation naming the paused schedule id, or an `Error ...` string.

    Examples:
    - `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000"}}`

    Error Handling:
    400 means the schedule is already paused or is system-disabled. 404 means the
    schedule does not exist. Requires permission to manage the schedule.
    """
    try:
        await get_client().request_json("PUT", f"/v1/schedules/{quote(params.schedule_id, safe='')}/pause")
        return f"Paused schedule `{params.schedule_id}`. It will not run until resumed."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_resume_schedule",
    annotations=ToolAnnotations(
        title="Resume Schedule",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_resume_schedule(params: ResumeScheduleInput) -> str:
    """Resume a paused schedule so it runs on its cadence again.

    Calls `PUT /v1/schedules/{scheduleId}/resume`, clearing the schedule's
    `disabledAt`. The next delivery happens at the next cron occurrence, not
    immediately.

    When to Use:
    - To restart deliveries after a pause.
    - After fixing whatever caused a delivery to be paused.

    When NOT to Use:
    - To send a delivery right now — use `omni_trigger_schedule`.
    - On a system-disabled schedule — that returns 400 until the underlying
      problem is fixed.

    Returns:
    A short confirmation naming the resumed schedule id, or an `Error ...` string.

    Examples:
    - `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000"}}`

    Error Handling:
    400 means the schedule is already active or is system-disabled. 404 means the
    schedule does not exist. Requires permission to manage the schedule.
    """
    try:
        await get_client().request_json("PUT", f"/v1/schedules/{quote(params.schedule_id, safe='')}/resume")
        return f"Resumed schedule `{params.schedule_id}`. It runs again on its next scheduled occurrence."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_trigger_schedule",
    annotations=ToolAnnotations(
        title="Trigger Schedule Now (sends a real delivery)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_trigger_schedule(params: TriggerScheduleInput) -> str:
    """Run a schedule immediately — this sends a real delivery to real recipients.

    Calls `POST /v1/schedules/{scheduleId}/trigger`. The delivery goes out at once,
    outside the normal cadence, to every configured recipient (email inboxes, the
    Slack channel, the SFTP server, the webhook or the S3 bucket). It cannot be
    recalled, so confirm with the user before triggering a schedule with an
    external audience.

    When to Use:
    - To verify a schedule end to end after changing it.
    - To re-send a delivery that failed or was missed.
    - To publish an off-cycle run on request.

    When NOT to Use:
    - As a way to fetch data — use the query tools instead; this mails people.
    - On a paused or system-disabled schedule (the API returns 409).
    - To test a configuration that does not exist yet — create it with
      `test_now: true` instead.

    Returns:
    A short confirmation naming the triggered schedule id, or an `Error ...`
    string.

    Examples:
    - `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000"}}`

    Error Handling:
    400 means `schedule_id` is not a UUID. 404 means the schedule does not exist.
    409 means either another execution is still in progress (large fan-out email
    schedules with more than 50 recipients) or the schedule is paused or
    system-disabled — resume it first. 500 indicates a dispatch failure worth
    retrying. Requires permission to manage the schedule.
    """
    try:
        await get_client().request_json("POST", f"/v1/schedules/{quote(params.schedule_id, safe='')}/trigger")
        return (
            f"Triggered schedule `{params.schedule_id}`. A real delivery is on its way to its recipients; "
            "check `omni_get_schedule` for the resulting status."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_transfer_schedule_ownership",
    annotations=ToolAnnotations(
        title="Transfer Schedule Ownership",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_transfer_schedule_ownership(params: TransferScheduleOwnershipInput) -> str:
    """Transfer a schedule to a new owner in the same organization (irreversible here).

    Calls `PUT /v1/schedules/{scheduleId}/transfer-ownership`. Ownership changes
    immediately and this endpoint cannot reverse it — transferring back requires a
    second transfer. For Slack, SFTP and webhook destinations (and email without
    personalization) future jobs run with the new owner's permissions, data access
    and user attributes; with personalized email deliveries each recipient's own
    access is used instead.

    When to Use:
    - When a schedule's owner leaves the team or changes role.
    - To move a delivery onto an account with the right data access.

    When NOT to Use:
    - To change who receives the delivery — that is
      `omni_add_schedule_recipients` / `omni_remove_schedule_recipients`.
    - To assign a brand-new schedule's owner — pass `user_id` to
      `omni_create_schedule` instead.

    Returns:
    A short confirmation naming the schedule and the new owner, or an
    `Error ...` string.

    Examples:
    - `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`

    Error Handling:
    400 means `user_id` is missing, not a UUID, or already the owner. 403 means the
    new owner cannot view the schedule's dashboard. 404 means the schedule does not
    exist or the new owner is not a member of the organization.
    """
    try:
        await get_client().request_json(
            "PUT",
            f"/v1/schedules/{quote(params.schedule_id, safe='')}/transfer-ownership",
            json_body={"userId": params.user_id},
        )
        return (
            f"Transferred ownership of schedule `{params.schedule_id}` to user `{params.user_id}`. "
            "Future runs use the new owner's permissions and data access."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_list_schedule_recipients",
    annotations=ToolAnnotations(
        title="List Schedule Recipients",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_schedule_recipients(params: ListScheduleRecipientsInput) -> str:
    """List who or what a schedule delivers to, with destination-specific detail.

    Calls `GET /v1/schedules/{scheduleId}/recipients`. The response shape depends
    on the destination: email returns the recipients (including whether each is an
    email-only user), Slack returns channel/user IDs, SFTP returns address, port
    and username, webhook returns the URL, and S3 returns the bucket, region, role
    ARN, external ID, key prefix and filename template.

    When to Use:
    - Before adding or removing recipients, to see the current list.
    - To audit who receives a dashboard's data.
    - To read an S3 destination's external ID for an IAM trust policy.

    When NOT to Use:
    - To read the schedule's cadence or format — use `omni_get_schedule`.
    - To search across schedules — use `omni_list_schedules`.

    Returns:
    A markdown summary of the destination and its recipients, or the raw payload
    when `response_format` is `json`.

    Examples:
    - `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000"}}`
    - Raw payload: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "response_format": "json"}}`

    Error Handling:
    400 means `schedule_id` is not a UUID. 404 means the schedule does not exist.
    Requires permission to view the schedule.
    """
    try:
        payload = await get_client().request_json(
            "GET", f"/v1/schedules/{quote(params.schedule_id, safe='')}/recipients"
        )
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        return truncate_result(_render_recipients(body))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_add_schedule_recipients",
    annotations=ToolAnnotations(
        title="Add Schedule Recipients",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_add_schedule_recipients(params: AddScheduleRecipientsInput) -> str:
    """Add recipients to an existing scheduled email delivery.

    Calls `PUT /v1/schedules/{scheduleId}/add-recipients` with email addresses,
    user IDs, or both. Email destinations only — every other destination type is
    rejected with a 400.

    When to Use:
    - To add people to an existing email delivery without touching the rest of its
      configuration.
    - To subscribe a new team member to a report.

    When NOT to Use:
    - On Slack, SFTP, webhook or S3 schedules — change `recipients` (or the
      destination fields) with `omni_update_schedule` instead.
    - To replace the whole recipient list — remove the old ones first, or use
      `omni_update_schedule`.

    Returns:
    A confirmation with the number of recipients added, or an `Error ...` string.

    Examples:
    - By email: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "emails": ["analyst@example.com", "lead@example.com"]}}`
    - By user ID: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "user_ids": ["9e8719d9-276a-4964-9395-a493189a247c"]}}`

    Error Handling:
    400 means an invalid UUID or email, an empty request, users that do not exist
    or lack access to the organization, or a non-email destination type. 404 means
    the schedule does not exist. Requires permission to manage the schedule.
    """
    try:
        body: dict[str, Any] = {}
        if params.emails:
            body["emails"] = params.emails
        if params.user_ids:
            body["userIds"] = params.user_ids
        payload = await get_client().request_json(
            "PUT",
            f"/v1/schedules/{quote(params.schedule_id, safe='')}/add-recipients",
            json_body=body,
        )
        response: dict[str, Any] = payload if isinstance(payload, dict) else {}
        count = response.get("addedRecipientsCount")
        added = f"{count:,}" if isinstance(count, int) else "an unreported number of"
        return f"Added {added} recipient(s) to schedule `{params.schedule_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_remove_schedule_recipients",
    annotations=ToolAnnotations(
        title="Remove Schedule Recipients",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_remove_schedule_recipients(params: RemoveScheduleRecipientsInput) -> str:
    """Remove recipients from an existing scheduled email delivery.

    Calls `PUT /v1/schedules/{scheduleId}/remove-recipients` with email addresses,
    user IDs, or both. Email destinations only. The removed people stop receiving
    the delivery immediately; re-adding them takes another call.

    When to Use:
    - To unsubscribe someone who left the team or asked to be removed.
    - To trim a delivery list before triggering a run.

    When NOT to Use:
    - On Slack, SFTP, webhook or S3 schedules — the API rejects those.
    - To stop the delivery for everyone — pause or delete the schedule instead.

    Returns:
    A confirmation with the number of recipients removed, or an `Error ...`
    string.

    Examples:
    - By email: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "emails": ["former.member@example.com"]}}`
    - By user ID: `{"params": {"schedule_id": "123e4567-e89b-12d3-a456-426614174000", "user_ids": ["9e8719d9-276a-4964-9395-a493189a247c"]}}`

    Error Handling:
    400 means an invalid UUID or email, an empty request, unknown users, or a
    destination type whose recipients cannot be changed. 404 means the schedule
    does not exist. Requires permission to manage the schedule.
    """
    try:
        body: dict[str, Any] = {}
        if params.emails:
            body["emails"] = params.emails
        if params.user_ids:
            body["userIds"] = params.user_ids
        payload = await get_client().request_json(
            "PUT",
            f"/v1/schedules/{quote(params.schedule_id, safe='')}/remove-recipients",
            json_body=body,
        )
        response: dict[str, Any] = payload if isinstance(payload, dict) else {}
        count = response.get("removedRecipientsCount")
        removed = f"{count:,}" if isinstance(count, int) else "an unreported number of"
        return f"Removed {removed} recipient(s) from schedule `{params.schedule_id}`."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_manage_email_only_user",
    annotations=ToolAnnotations(
        title="Create or Update Email-Only User",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_manage_email_only_user(params: ManageEmailOnlyUserInput) -> str:
    """Create or update an email-only delivery recipient (no Omni account needed).

    Calls `POST /v1/users/email-only`. An email-only user receives deliveries
    without having an Omni account; matching an existing email updates that user's
    attributes instead of creating a duplicate. **Organization API keys only** —
    Personal Access Tokens are not accepted.

    When to Use:
    - To give an external stakeholder a personalized delivery.
    - To set or change the user attributes (row-level filters, timezone) applied
      when a personalized delivery is rendered for that address.

    When NOT to Use:
    - For more than one person — use `omni_bulk_manage_email_only_users` (up to 20
      per call).
    - To create a real Omni account — use the user tools.
    - To attach the recipient to a schedule — that is
      `omni_add_schedule_recipients`.

    Returns:
    A confirmation with the email and the resulting user ID, or an `Error ...`
    string.

    Examples:
    - Minimal: `{"params": {"email": "stakeholder@example.com"}}`
    - With attributes: `{"params": {"email": "stakeholder@example.com", "user_attributes": {"region": ["US", "EU"], "omni_user_timezone": "America/New_York", "is_admin": 0}}}`
    - Unset attributes: `{"params": {"email": "stakeholder@example.com", "user_attributes": {"is_admin": null, "is_sales_team": "", "region": []}}}`

    Error Handling:
    400 means an invalid or missing email, attribute names that do not exist, a
    value whose type does not match the attribute (a `number` attribute needs a
    number), a single value passed to a multi-value attribute (wrap it in an
    array), or an unsupported timezone (use `America/New_York`, not `EST`). 403
    means a Personal Access Token was used — this endpoint requires an
    Organization API key.
    """
    try:
        body: dict[str, Any] = {"email": params.email}
        if params.user_attributes is not None:
            body["userAttributes"] = params.user_attributes
        payload = await get_client().request_json("POST", "/v1/users/email-only", json_body=body)
        response: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return (
            f"Created or updated email-only user **{response.get('email', params.email)}** "
            f"(id `{response.get('userId', 'unknown')}`)."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_bulk_manage_email_only_users",
    annotations=ToolAnnotations(
        title="Create or Update Email-Only Users in Bulk",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_bulk_manage_email_only_users(params: BulkManageEmailOnlyUsersInput) -> str:
    """Create or update up to 20 email-only delivery recipients in one call.

    Calls `POST /v1/users/email-only/bulk`. Each entry is an email plus optional
    user attributes; existing emails are updated rather than duplicated.
    **Organization API keys only** — Personal Access Tokens are not accepted.

    When to Use:
    - To onboard a whole distribution list before creating an email schedule.
    - To re-apply user attributes across many external recipients at once.

    When NOT to Use:
    - For a single recipient — `omni_manage_email_only_user` is simpler.
    - For more than 20 people in one call — split the list into batches of 20.

    Returns:
    A confirmation listing each email and its resulting user ID, or an
    `Error ...` string.

    Examples:
    - Minimal: `{"params": {"users": [{"email": "analyst@example.com"}, {"email": "lead@example.com"}]}}`
    - With attributes: `{"params": {"users": [{"email": "analyst@example.com", "user_attributes": {"region": ["US"], "omni_user_timezone": "America/New_York"}}, {"email": "lead@example.com", "user_attributes": {"region": ["EU"], "is_admin": 1}}]}}`

    Error Handling:
    400 means more than 20 users, a missing or invalid email, attribute names that
    do not exist, a type mismatch (a `number` attribute needs a number), a single
    value passed to a multi-value attribute, or an unsupported timezone. 403 means
    a Personal Access Token was used — this endpoint requires an Organization API
    key.
    """
    try:
        users: list[dict[str, Any]] = []
        for user in params.users:
            entry: dict[str, Any] = {"email": user.email}
            if user.user_attributes is not None:
                entry["userAttributes"] = user.user_attributes
            users.append(entry)
        payload = await get_client().request_json("POST", "/v1/users/email-only/bulk", json_body={"users": users})
        response: dict[str, Any] = payload if isinstance(payload, dict) else {}
        raw_results = response.get("results")
        results = raw_results if isinstance(raw_results, list) else []
        lines = [f"Created or updated **{len(results):,}** email-only user(s)."]
        for result in results:
            if isinstance(result, Mapping):
                lines.append(f"- {result.get('email', 'unknown')} → `{result.get('userId', 'unknown')}`")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)
