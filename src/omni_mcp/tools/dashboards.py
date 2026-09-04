"""Tools for the dashboard download and dashboard filter endpoints.

Downloads are a three-step async flow: initiate a job, poll its status, then fetch the
finished file. `omni_export_dashboard_file` chains all three for the common case; the other
tools let a caller drive each step independently (e.g. to run several downloads concurrently).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, iso_or_na, markdown_table, to_json, truncate_result
from omni_mcp.server import mcp

#: Download responses are binary (PDF/PNG/CSV/XLSX/ZIP) or single-tile JSON; without this the
#: client's default `accept: application/json` header can make the API 406 a binary format.
_BINARY_ACCEPT_HEADERS: dict[str, str] = {"accept": "*/*"}

#: Injectable sleep for `omni_export_dashboard_file`'s polling loop — tests monkeypatch this
#: module attribute so the polling loop does not actually wait.
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

#: Injectable monotonic clock for `omni_export_dashboard_file`'s poll deadline — tests
#: monkeypatch this so a simulated clock can advance without a real wait.
_monotonic: Callable[[], float] = time.monotonic


def _dashboard_download_path(dashboard_id: str) -> str:
    return f"/v1/dashboards/{quote(dashboard_id, safe='')}/download"


def _dashboard_status_path(dashboard_id: str, job_id: str) -> str:
    return f"/v1/dashboards/{quote(dashboard_id, safe='')}/download/{quote(job_id, safe='')}/status"


def _dashboard_download_file_path(dashboard_id: str, job_id: str) -> str:
    return f"/v1/dashboards/{quote(dashboard_id, safe='')}/download/{quote(job_id, safe='')}"


def _dashboard_filters_path(dashboard_id: str) -> str:
    return f"/v1/dashboards/{quote(dashboard_id, safe='')}/filters"


class _DashboardDownloadOptions(BaseModel):
    """Shared download-format options for `omni_initiate_dashboard_download` and
    `omni_export_dashboard_file`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    format: Literal["pdf", "png", "csv", "xlsx", "json"] = Field(
        ...,
        description=(
            "Output format. PDF/PNG/XLSX support a full dashboard or a single tile. CSV supports a full "
            "dashboard (delivered as a ZIP containing one CSV per tile) or a single tile. JSON supports a "
            "single tile only and requires `query_identifier_map_key`."
        ),
    )
    filename: str | None = Field(
        default=None,
        max_length=255,
        description="Custom filename for the downloaded file. Defaults to the dashboard name.",
    )
    query_identifier_map_key: str | None = Field(
        default=None,
        description=(
            "Tile identifier to download a single tile instead of the full dashboard. Required for XLSX and "
            "JSON formats when `override_row_limit` is true."
        ),
    )
    filter_config: dict[str, Any] | None = Field(
        default=None,
        description="Dashboard filter values to apply before rendering.",
    )
    paper_format: Literal["fit_page", "letter", "legal", "tabloid", "a3", "a4"] | None = Field(
        default=None,
        description="Applicable to PDF and PNG formats. Page size (API default: `fit_page`).",
    )
    paper_orientation: Literal["portrait", "landscape"] | None = Field(
        default=None,
        description="Applicable to PDF and PNG formats. Page orientation.",
    )
    hide_title: bool | None = Field(
        default=None,
        description=(
            "Applicable to PDF and PNG formats. If true, hide the dashboard title in the output (API default: false)."
        ),
    )
    show_filters: bool | None = Field(
        default=None,
        description=(
            "Applicable to PDF and PNG formats. If true, display applied filter values in the output "
            "(API default: true)."
        ),
    )
    expand_tables_to_show_all_rows: bool | None = Field(
        default=None,
        description="Applicable to PDF and PNG formats. If true, expand table tiles to display all rows.",
    )
    single_column_layout: bool | None = Field(
        default=None,
        description="Applicable to PDF and PNG formats. If true, render tiles in a single column layout.",
    )
    enable_formatting: bool | None = Field(
        default=None,
        description=(
            "Applicable to CSV, XLSX, and JSON formats. If true, preserve number and date formatting "
            "(API default: false)."
        ),
    )
    hide_hidden_fields: bool | None = Field(
        default=None,
        description="Applicable to CSV and XLSX formats. If true, exclude hidden fields from the output "
        "(API default: false).",
    )
    override_row_limit: bool | None = Field(
        default=None,
        description=(
            "Applicable to CSV, XLSX, and JSON formats. If true, remove the default row limit (API default: "
            "false). Used with `max_row_limit`. If true for XLSX and JSON formats, `query_identifier_map_key` "
            "is required."
        ),
    )
    max_row_limit: int | None = Field(
        default=None,
        ge=1,
        le=1_000_000,
        description=(
            "Applicable to CSV, XLSX, and JSON formats. Maximum number of rows to export. Used with "
            "`override_row_limit` to export more rows than the default row limit."
        ),
    )
    cache: Literal["Standard", "SkipRequery", "SkipCache"] | None = Field(
        default=None,
        description=(
            "Cache policy for the download's queries (API default: `Standard`). `Standard` is standard "
            "caching behavior including requerying cached result sets; `SkipRequery` uses cached results if "
            "available but does not requery them; `SkipCache` bypasses the cache and always executes fresh "
            "queries."
        ),
    )
    use_cache: bool | None = Field(
        default=None,
        description="Deprecated. Use `cache=SkipCache` for fresh data instead.",
    )


def _download_options_body(options: _DashboardDownloadOptions) -> dict[str, Any]:
    """Build the initiate/export request body, omitting unset optional fields."""
    body: dict[str, Any] = {"format": options.format}
    if options.filename is not None:
        body["filename"] = options.filename
    if options.query_identifier_map_key is not None:
        body["queryIdentifierMapKey"] = options.query_identifier_map_key
    if options.filter_config is not None:
        body["filterConfig"] = options.filter_config
    if options.paper_format is not None:
        body["paperFormat"] = options.paper_format
    if options.paper_orientation is not None:
        body["paperOrientation"] = options.paper_orientation
    if options.hide_title is not None:
        body["hideTitle"] = options.hide_title
    if options.show_filters is not None:
        body["showFilters"] = options.show_filters
    if options.expand_tables_to_show_all_rows is not None:
        body["expandTablesToShowAllRows"] = options.expand_tables_to_show_all_rows
    if options.single_column_layout is not None:
        body["singleColumnLayout"] = options.single_column_layout
    if options.enable_formatting is not None:
        body["enableFormatting"] = options.enable_formatting
    if options.hide_hidden_fields is not None:
        body["hideHiddenFields"] = options.hide_hidden_fields
    if options.override_row_limit is not None:
        body["overrideRowLimit"] = options.override_row_limit
    if options.max_row_limit is not None:
        body["maxRowLimit"] = options.max_row_limit
    if options.cache is not None:
        body["cache"] = options.cache
    if options.use_cache is not None:
        body["useCache"] = options.use_cache
    return body


class InitiateDashboardDownloadInput(_DashboardDownloadOptions):
    """Input for `omni_initiate_dashboard_download`."""

    dashboard_id: str = Field(..., min_length=1, description="The dashboard identifier (ID or document UUID).")
    user_id: str | None = Field(
        default=None,
        description=(
            "The user ID to run the download as. Only valid when authenticating with an Organization API "
            "key. Use the user-listing tools to retrieve user IDs."
        ),
    )


class GetDashboardDownloadStatusInput(BaseModel):
    """Input for `omni_get_dashboard_download_status`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dashboard_id: str = Field(
        ...,
        min_length=1,
        description="The dashboard identifier. Must match the ID used in the original download request.",
    )
    job_id: str = Field(..., min_length=1, description="The job ID returned by `omni_initiate_dashboard_download`.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable status summary, `json` for the raw status payload.",
    )


class DownloadDashboardFileInput(BaseModel):
    """Input for `omni_download_dashboard_file`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dashboard_id: str = Field(
        ...,
        min_length=1,
        description="The dashboard identifier. Must match the ID used in the original download request.",
    )
    job_id: str = Field(
        ...,
        min_length=1,
        description=(
            "The job ID returned by `omni_initiate_dashboard_download`. Only call this once status is `complete`."
        ),
    )
    output_path: str = Field(
        ...,
        min_length=1,
        description="Local filesystem path to write the downloaded file to; parent directories are created as needed.",
    )
    overwrite: bool = Field(
        default=False,
        description="If true, overwrite an existing file at `output_path`. Defaults to false, refusing to overwrite.",
    )


class ExportDashboardFileInput(_DashboardDownloadOptions):
    """Input for `omni_export_dashboard_file`."""

    dashboard_id: str = Field(..., min_length=1, description="The dashboard identifier (ID or document UUID).")
    user_id: str | None = Field(
        default=None,
        description=(
            "The user ID to run the download as. Only valid when authenticating with an Organization API key."
        ),
    )
    output_path: str = Field(
        ...,
        min_length=1,
        description="Local filesystem path to write the downloaded file to; parent directories are created as needed.",
    )
    overwrite: bool = Field(
        default=False,
        description="If true, overwrite an existing file at `output_path`. Defaults to false, refusing to overwrite.",
    )
    poll_interval_seconds: float = Field(
        default=2.0,
        ge=1.0,
        le=60.0,
        description=(
            "Seconds to wait between status polls. The API recommends 2-5 seconds and asks callers to avoid "
            "polling more than once per second."
        ),
    )
    max_wait_seconds: float = Field(
        default=120.0,
        ge=1.0,
        le=3600.0,
        description=(
            "Maximum total seconds to wait for the job to reach `complete` before giving up and returning "
            "the job id so the caller can resume with `omni_get_dashboard_download_status` / "
            "`omni_download_dashboard_file`."
        ),
    )


class GetDashboardFiltersInput(BaseModel):
    """Input for `omni_get_dashboard_filters`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dashboard_id: str = Field(..., min_length=1, description="The dashboard identifier.")
    user_id: str | None = Field(
        default=None,
        description=(
            "Requires an Organization API key. Optional user ID that attributes the action to the specified "
            "user. Personal Access Tokens (PATs) cannot use this parameter."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw filter/control configuration.",
    )


class UpdateDashboardFiltersInput(BaseModel):
    """Input for `omni_update_dashboard_filters`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dashboard_id: str = Field(..., min_length=1, description="The dashboard identifier.")
    user_id: str | None = Field(
        default=None,
        description=(
            "Requires an Organization API key. Membership ID to act on behalf of. Personal Access Tokens "
            "(PATs) cannot use this parameter."
        ),
    )
    clear_existing_draft: bool | None = Field(
        default=None,
        description=(
            "When true, discards any existing draft before applying updates (API default: false). Required "
            "if a draft already exists for this dashboard, otherwise the API returns 409."
        ),
    )
    filters: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Map of filter ID to a partial `DashboardFilterUpdate`: {values: list[str], left_side: str, "
            "right_side: str, label: str, description: str, required: bool, requiredScope: "
            "'dashboard'|'tiles', hidden: bool}. All keys optional; only included keys are changed."
        ),
    )
    controls: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Map of control ID to a partial `DashboardControlUpdate`: {label: str, description: str, hidden: "
            "bool}. All keys optional; only included keys are changed."
        ),
    )
    filter_order: list[str] | None = Field(
        default=None,
        description="New display order for filter and control IDs.",
    )

    @model_validator(mode="after")
    def _require_at_least_one_update(self) -> UpdateDashboardFiltersInput:
        if not self.filters and not self.controls and not self.filter_order:
            raise ValueError(
                "Provide at least one of filters, controls, or filter_order, each with at least one entry."
            )
        return self


@mcp.tool(
    name="omni_initiate_dashboard_download",
    annotations=ToolAnnotations(
        title="Initiate Dashboard Download",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_initiate_dashboard_download(params: InitiateDashboardDownloadInput) -> str:
    """Start an asynchronous download job for a dashboard or single tile.

    Renders the dashboard (or one tile, via `query_identifier_map_key`) to PDF, PNG, CSV,
    XLSX, or JSON and returns a job id. Poll `omni_get_dashboard_download_status` until the job
    reports `complete`, then fetch the file with `omni_download_dashboard_file` — or use
    `omni_export_dashboard_file` to do both in one call.

    When to Use:
    - To generate a downloadable export of a dashboard or one of its tiles.
    - As the first step of the manual download flow, before polling status or downloading.

    When NOT to Use:
    - When you already have a `job_id` for this dashboard and just need its status or file —
      use `omni_get_dashboard_download_status` / `omni_download_dashboard_file` instead.
    - When you want the whole flow handled for you — use `omni_export_dashboard_file`.

    Returns:
    A confirmation string with the new job id and the requested format. On failure, an
    `Error ...` string.

    Examples:
    - Full dashboard as PDF: `{"params": {"dashboard_id": "abc123", "format": "pdf", "paper_orientation": "landscape"}}`
    - Single tile as JSON: `{"params": {"dashboard_id": "abc123", "format": "json", "query_identifier_map_key": "1"}}`
    - Fresh CSV data: `{"params": {"dashboard_id": "abc123", "format": "csv", "cache": "SkipCache"}}`

    Error Handling:
    400 means a missing/invalid `format`, invalid options for the chosen format, or a malformed
    body. 403 means the caller lacks permission to download this dashboard. 404 means the
    dashboard does not exist. 409 means a download is already in progress for this dashboard —
    the error detail includes the existing job id.
    """
    try:
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id
        payload = await get_client().request_json(
            "POST",
            _dashboard_download_path(params.dashboard_id),
            params=query or None,
            json_body=_download_options_body(params),
        )
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        job_id = body.get("job_id", "unknown")
        message = body.get("message", "Download initiated successfully")
        return truncate_result(
            f"Download job **initiated** for dashboard `{params.dashboard_id}` (format `{params.format}`). "
            f"Job ID: `{job_id}`. {message}. Poll `omni_get_dashboard_download_status` next."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_dashboard_download_status",
    annotations=ToolAnnotations(
        title="Get Dashboard Download Status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_dashboard_download_status(params: GetDashboardDownloadStatusInput) -> str:
    """Check the status of a dashboard/tile download job.

    Poll this after `omni_initiate_dashboard_download` until the status is `complete`, then
    fetch the file with `omni_download_dashboard_file`. Recommended poll interval is 2-5
    seconds; avoid polling more than once per second.

    When to Use:
    - To find out whether a download job is still running, finished, or failed.
    - Between initiating a download and downloading the finished file.

    When NOT to Use:
    - To fetch the file itself once complete — use `omni_download_dashboard_file`.
    - When you want polling handled for you — use `omni_export_dashboard_file`.

    Returns:
    A markdown summary of the job id, status, format, creation time, and error detail (if
    any), or the raw status payload when `response_format` is `json`. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"dashboard_id": "abc123", "job_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    400 means the job id is not a valid UUID. 403 means the caller lacks permission. 404 means
    the job was not found, or does not belong to the specified dashboard.
    """
    try:
        payload = await get_client().request_json("GET", _dashboard_status_path(params.dashboard_id, params.job_id))
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        status = body.get("status", "unknown")
        lines = [
            "# Dashboard Download Status",
            "",
            f"- Job ID: `{body.get('job_id', params.job_id)}`",
            f"- Status: **{status}**",
            f"- Format: `{body.get('format', 'unknown')}`",
            f"- Created: {iso_or_na(body.get('created_at'))}",
        ]
        if status == "error":
            lines.append(f"- Error: {body.get('error', 'no detail provided')}")
            lines.append("")
            lines.append("Review the error above and re-initiate the download if needed.")
        elif status == "complete":
            lines.append("")
            lines.append("Ready — call `omni_download_dashboard_file` to retrieve the file.")
        else:
            lines.append("")
            lines.append("Still processing — poll again in a few seconds.")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_download_dashboard_file",
    annotations=ToolAnnotations(
        title="Download Dashboard File",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_download_dashboard_file(params: DownloadDashboardFileInput) -> str:
    """Download a completed dashboard/tile export to a local file.

    Only call this once `omni_get_dashboard_download_status` reports `complete`. The response
    body (binary PDF/PNG/XLSX/ZIP, or JSON for a single-tile JSON export) is written to
    `output_path` — never returned inline — because these payloads can be large and are not
    valid tool-result text.

    When to Use:
    - After polling status and seeing `complete`, to save the file to disk.

    When NOT to Use:
    - Before the job is `complete` — a `202` (still processing) or `410` (job failed) response
      means poll again or re-initiate instead.
    - When you'd rather have the whole flow (initiate + poll + download) handled for you — use
      `omni_export_dashboard_file`.

    Returns:
    A confirmation string with the written path, file size in bytes, and content type. If
    `output_path` already exists and `overwrite` is not `true`, refuses without writing and
    without calling the API. If the job is still processing (`202`), reports that instead of
    writing an empty/partial file. On failure, an `Error ...` string — including a job-failed
    explanation for `410`.

    Examples:
    - `{"params": {"dashboard_id": "abc123", "job_id": "550e8400-e29b-41d4-a716-446655440000", "output_path": "/tmp/dashboard.pdf"}}`
    - Overwrite an existing file: `{"params": {"dashboard_id": "abc123", "job_id": "550e8400-e29b-41d4-a716-446655440000", "output_path": "/tmp/dashboard.pdf", "overwrite": true}}`

    Error Handling:
    400 means the job id is not a valid UUID. 403 means the caller lacks permission. 404 means
    the job was not found, or does not belong to the specified dashboard. 410 means the job
    failed — call `omni_get_dashboard_download_status` for the error detail and re-initiate.
    """
    try:
        output_path = Path(params.output_path)
        if output_path.exists() and not params.overwrite:
            return truncate_result(
                f"Error: refusing to overwrite existing file at `{output_path}` — pass `overwrite=true` to replace it."
            )
        response = await get_client().request(
            "GET",
            _dashboard_download_file_path(params.dashboard_id, params.job_id),
            headers=_BINARY_ACCEPT_HEADERS,
        )
        if response.status_code == 202:
            return truncate_result(
                "The download job is still in progress (202). Poll "
                "`omni_get_dashboard_download_status` until status is `complete`, then retry."
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        content_type = response.headers.get("content-type", "application/octet-stream")
        return truncate_result(
            f"Downloaded dashboard file to `{output_path}` ({len(response.content):,} bytes, content-type "
            f"`{content_type}`)."
        )
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 410:
            return truncate_result(
                "Error (410): the download job failed. Call `omni_get_dashboard_download_status` for the "
                "failure detail, then re-initiate the download if needed."
            )
        return handle_api_error(exc)


@mcp.tool(
    name="omni_export_dashboard_file",
    annotations=ToolAnnotations(
        title="Export Dashboard File",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_export_dashboard_file(params: ExportDashboardFileInput) -> str:
    """Initiate, poll, and download a dashboard/tile export in one call.

    Convenience wrapper around `omni_initiate_dashboard_download` →
    `omni_get_dashboard_download_status` → `omni_download_dashboard_file`: starts the job,
    polls status every `poll_interval_seconds` up to `max_wait_seconds`, then writes the file
    to `output_path`.

    When to Use:
    - To export a dashboard or tile to a local file without managing the job lifecycle
      yourself.

    When NOT to Use:
    - When you need to run several downloads concurrently, or want fine control over each
      step — use the individual initiate/status/download tools instead.
    - When the export is likely to take longer than `max_wait_seconds` (very large
      dashboards) — this tool returns the job id on timeout so you can resume manually.

    Returns:
    A confirmation string with the written path, file size, content type, and job id on
    success. On timeout, a message with the job id so the caller can resume polling with
    `omni_get_dashboard_download_status` / `omni_download_dashboard_file`. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"dashboard_id": "abc123", "format": "pdf", "output_path": "/tmp/dashboard.pdf"}}`
    - Slower poll, longer wait: `{"params": {"dashboard_id": "abc123", "format": "xlsx", "output_path": "/tmp/d.xlsx", "poll_interval_seconds": 5, "max_wait_seconds": 300}}`

    Error Handling:
    Same causes as `omni_initiate_dashboard_download` (400/403/404/409) can occur when starting
    the job. A job that reaches status `error` is reported without attempting a download. A
    `410` from the download step means the job failed after previously reporting `complete`.
    """
    job_id: str | None = None
    try:
        output_path = Path(params.output_path)
        if output_path.exists() and not params.overwrite:
            return truncate_result(
                f"Error: refusing to overwrite existing file at `{output_path}` — pass `overwrite=true` to replace it."
            )

        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id
        client = get_client()
        init_payload = await client.request_json(
            "POST",
            _dashboard_download_path(params.dashboard_id),
            params=query or None,
            json_body=_download_options_body(params),
        )
        init_body: dict[str, Any] = init_payload if isinstance(init_payload, dict) else {}
        job_id = init_body.get("job_id")
        if not job_id:
            return truncate_result("Error: the API did not return a job id for the download request.")

        status = "in_progress"
        deadline = _monotonic() + params.max_wait_seconds
        while (now := _monotonic()) < deadline:
            remaining = max(0.0, deadline - now)
            status_payload = await client.request_json(
                "GET", _dashboard_status_path(params.dashboard_id, job_id), timeout=remaining
            )
            status_body: dict[str, Any] = status_payload if isinstance(status_payload, dict) else {}
            status = status_body.get("status", "unknown")
            if status == "complete":
                break
            if status == "error":
                error_detail = status_body.get("error", "no detail provided")
                return truncate_result(f"Error: download job `{job_id}` failed – {error_detail}.")
            await _sleep(params.poll_interval_seconds)

        if status != "complete":
            return truncate_result(
                f"Download job `{job_id}` is still `{status}` after waiting {params.max_wait_seconds:g}s. "
                f"Resume with `omni_get_dashboard_download_status` (job_id=`{job_id}`) and "
                "`omni_download_dashboard_file` once it reports `complete`."
            )

        if output_path.exists() and not params.overwrite:
            return truncate_result(
                f"Error: refusing to overwrite existing file at `{output_path}` — pass `overwrite=true` to "
                f"replace it. Download job `{job_id}` is complete and can still be fetched with "
                "`omni_download_dashboard_file`."
            )
        response = await client.request(
            "GET", _dashboard_download_file_path(params.dashboard_id, job_id), headers=_BINARY_ACCEPT_HEADERS
        )
        if response.status_code == 202:
            return truncate_result(
                f"Download job `{job_id}` reported complete but the file was not yet ready (202); retry with "
                "`omni_download_dashboard_file` shortly."
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        content_type = response.headers.get("content-type", "application/octet-stream")
        return truncate_result(
            f"Exported dashboard `{params.dashboard_id}` to `{output_path}` ({len(response.content):,} bytes, "
            f"content-type `{content_type}`). Job ID: `{job_id}`."
        )
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 410:
            return truncate_result(
                f"Error (410): download job `{job_id or 'unknown'}` failed. Call "
                "`omni_get_dashboard_download_status` for the failure detail, then re-initiate the download "
                "if needed."
            )
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_dashboard_filters",
    annotations=ToolAnnotations(
        title="Get Dashboard Filters",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_dashboard_filters(params: GetDashboardFiltersInput) -> str:
    """Retrieve a dashboard's filter and control configuration.

    Returns every filter and control on the dashboard — IDs, types, current default values,
    labels, and display order.

    When to Use:
    - Before calling `omni_update_dashboard_filters`, to see the current filter/control IDs
      and values.
    - To inspect what filters a dashboard exposes and their current defaults.

    When NOT to Use:
    - To change values — use `omni_update_dashboard_filters`.

    Returns:
    A markdown summary of the dashboard identifier, its filters (id, type, kind, current
    value, label, required/hidden flags), controls (id, type, kind, label, field, options), and
    the display `filterOrder`, or the raw payload when `response_format` is `json`. On failure,
    an `Error ...` string.

    Examples:
    - `{"params": {"dashboard_id": "abc123"}}`
    - Act on behalf of another user (Organization API key only): `{"params": {"dashboard_id": "abc123", "user_id": "user_456"}}`

    Error Handling:
    401 means the API key is missing or invalid. 403 means the caller lacks permission to view
    this dashboard. 404 means the dashboard does not exist.
    """
    try:
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id
        payload = await get_client().request_json(
            "GET", _dashboard_filters_path(params.dashboard_id), params=query or None
        )
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        raw_filters = body.get("filters")
        filters: dict[str, Any] = raw_filters if isinstance(raw_filters, dict) else {}
        raw_controls = body.get("controls")
        controls: list[Any] = raw_controls if isinstance(raw_controls, list) else []
        filter_order = body.get("filterOrder") or []

        filter_rows: list[dict[str, Any]] = []
        for filter_id, filter_def in filters.items():
            if not isinstance(filter_def, dict):
                continue
            value = filter_def.get("values")
            if value is None and (filter_def.get("left_side") is not None or filter_def.get("right_side") is not None):
                value = [filter_def.get("left_side"), filter_def.get("right_side")]
            filter_rows.append(
                {
                    "id": filter_id,
                    "type": filter_def.get("type"),
                    "kind": filter_def.get("kind"),
                    "value": value,
                    "label": filter_def.get("label"),
                    "required": filter_def.get("required"),
                    "requiredScope": filter_def.get("requiredScope"),
                    "hidden": filter_def.get("hidden"),
                }
            )

        control_rows: list[dict[str, Any]] = []
        for control in controls:
            if not isinstance(control, dict):
                continue
            control_rows.append(
                {
                    "id": control.get("id"),
                    "type": control.get("type"),
                    "kind": control.get("kind"),
                    "label": control.get("label"),
                    "field": control.get("field"),
                    "hidden": control.get("hidden"),
                    "options": control.get("options"),
                }
            )

        lines = [
            "# Dashboard Filters",
            "",
            f"- Dashboard: `{body.get('identifier', params.dashboard_id)}`",
            "",
            f"## Filters ({len(filter_rows)})",
            "",
            markdown_table(filter_rows),
            "",
            f"## Controls ({len(control_rows)})",
            "",
            markdown_table(control_rows),
            "",
            "## Display order",
            "",
            ", ".join(f"`{item}`" for item in filter_order) if filter_order else "_No order reported._",
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_update_dashboard_filters",
    annotations=ToolAnnotations(
        title="Update Dashboard Filters",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_update_dashboard_filters(params: UpdateDashboardFiltersInput) -> str:
    """Set or reset values for a dashboard's filters and/or controls.

    Updates are partial: only the filter/control IDs you include are changed, and only the
    fields you set within each are modified. Updates to a published dashboard go through a
    draft/publish workflow; if a draft already exists, set `clear_existing_draft=true` to
    discard it and proceed.

    When to Use:
    - To change default filter values, labels, visibility, or display order on a dashboard.
    - To relabel or hide a control.

    When NOT to Use:
    - To merely inspect current filters/controls — use `omni_get_dashboard_filters`.

    Returns:
    A short confirmation string naming the dashboard and how many filters, controls, and
    filter-order entries are now configured, read from the API's updated configuration
    response (not merely echoed from the request). On failure, an `Error ...` string.

    Examples:
    - Update filter values: `{"params": {"dashboard_id": "abc123", "filters": {"order_status": {"values": ["shipped", "delivered"]}}}}`
    - Update control label: `{"params": {"dashboard_id": "abc123", "controls": {"field_selector": {"label": "Choose Metric"}}}}`
    - Reorder: `{"params": {"dashboard_id": "abc123", "filter_order": ["order_date", "field_selector", "order_status"]}}`
    - Discard an existing draft first: `{"params": {"dashboard_id": "abc123", "clear_existing_draft": true, "filters": {"order_status": {"values": ["shipped"]}}}}`

    Error Handling:
    400 means an unknown filter/control ID, an invalid `filter_order` entry, or an invalid
    value within an update — the detail message lists the valid IDs. 401 means the API key is
    missing or invalid. 403 means the caller lacks permission to edit this dashboard. 404 means
    the dashboard does not exist. 409 means a draft already exists — retry with
    `clear_existing_draft=true`.
    """
    try:
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id
        body: dict[str, Any] = {}
        if params.clear_existing_draft is not None:
            body["clearExistingDraft"] = params.clear_existing_draft
        if params.filters:
            body["filters"] = params.filters
        if params.controls:
            body["controls"] = params.controls
        if params.filter_order:
            body["filterOrder"] = params.filter_order

        payload = await get_client().request_json(
            "PATCH", _dashboard_filters_path(params.dashboard_id), params=query or None, json_body=body
        )
        response_body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        identifier = response_body.get("identifier", params.dashboard_id)
        response_filters = response_body.get("filters")
        filter_count = len(response_filters) if isinstance(response_filters, dict) else 0
        response_controls = response_body.get("controls")
        control_count = len(response_controls) if isinstance(response_controls, list) else 0
        response_filter_order = response_body.get("filterOrder")
        filter_order_count = len(response_filter_order) if isinstance(response_filter_order, list) else 0
        return truncate_result(
            f"Updated dashboard `{identifier}` filters/controls: {filter_count} filter(s), {control_count} "
            f"control(s), {filter_order_count} filterOrder entry(ies) now configured."
        )
    except Exception as exc:
        return handle_api_error(exc)
