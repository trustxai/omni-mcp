"""Tools for the query run, wait-for-results, and job status endpoints.

This is the "run a query and get real rows back" area: `POST /v1/query/run`
returns results as a base64-encoded Apache Arrow table, `GET /v1/query/wait`
picks up jobs that outlived the request, and `GET /v1/jobs/{jobId}/status`
reports on asynchronous jobs started elsewhere.

Both query endpoints answer with **newline-delimited JSON** — one object per
line (`jobs_submitted`, then a line per job's `result`, plus a line carrying
`remaining_job_ids` when a job outlives the request) — even though the spec
documents a single object. `_query_body` accepts either shape.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, NamedTuple
from urllib.parse import quote

import httpx
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import (
    ResponseFormat,
    decode_arrow_base64,
    format_arrow_result,
    to_json,
    truncate_result,
)
from omni_mcp.server import mcp

#: One line on what this module covers, for the generated README catalogue.
MODULE_SUMMARY = "Running queries and reading their results"

#: Generated SQL can be enormous; it is echoed fenced and cut at this length.
MAX_SQL_CHARS = 2_000

#: At most this many field names from the response summary are listed.
MAX_SUMMARY_FIELDS = 40

#: Injectable so the polling tests never actually wait. Tests monkeypatch
#: `omni_mcp.tools.queries._sleep`; the call site reads this global each time.
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

#: Injectable wall clock. The wait budget must be measured against real time,
#: not the time spent sleeping: `/v1/query/wait` is a server-side long poll, so
#: a single request can burn the whole budget on its own.
_monotonic: Callable[[], float] = time.monotonic


# --- input models -----------------------------------------------------


class RunQueryInput(BaseModel):
    """Input for `omni_run_query`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    # --- the query object (spec: request body `query`) ---
    model_id: str | None = Field(
        default=None,
        description="`query.modelId` — the ID (UUID) of the model to execute the query against. Required unless `query` is given.",
    )
    table: str | None = Field(
        default=None,
        description="`query.table` — the base table or topic name (for example `order_items`). Required unless `query` is given.",
    )
    fields: list[str] | None = Field(
        default=None,
        description='`query.fields` — the column names to include in the results, e.g. `["order_items.created_at[month]", "order_items.sale_price_sum"]`. Required unless `query` is given.',
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=75_000,
        description='`query.limit` — number of rows to return. The API defaults to 1000 when omitted; the maximum is 75000. A negative limit is rejected by the API. For unlimited results pass `query` with `"limit": null`.',
    )
    sorts: list[dict[str, Any]] | None = Field(
        default=None,
        description='`query.sorts` — sort specifications, each `{"column_name": ..., "sort_descending": true|false, "is_column_sort": ..., "null_sort": "OMNI_DEFAULT"}`.',
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description="`query.filters` — filter conditions to apply to the query, keyed by field name.",
    )
    pivots: list[dict[str, Any]] | None = Field(
        default=None, description="`query.pivots` — pivot configurations for the query."
    )
    calculations: list[dict[str, Any]] | None = Field(
        default=None, description="`query.calculations` — custom calculations to include in the query."
    )
    column_totals: dict[str, Any] | None = Field(
        default=None, description="`query.column_totals` — column total configuration."
    )
    row_totals: dict[str, Any] | None = Field(default=None, description="`query.row_totals` — row total configuration.")
    column_limit: int | None = Field(
        default=None, ge=1, description="`query.column_limit` — column limit for pivoted queries."
    )
    join_paths_from_topic_name: str | None = Field(
        default=None, description="`query.join_paths_from_topic_name` — topic name used to resolve join paths."
    )
    join_via_map: dict[str, Any] | None = Field(
        default=None, description="`query.join_via_map` — custom join path mappings."
    )
    fill_fields: list[str] | None = Field(
        default=None,
        description="`query.fill_fields` — dimensions whose missing values should be filled with placeholder rows.",
    )
    default_group_by: bool | None = Field(
        default=None, description="`query.default_group_by` — if `true`, enable default grouping behaviour."
    )
    controls: list[dict[str, Any]] | None = Field(
        default=None, description="`query.controls` — control configurations for the query."
    )
    manual_sort: bool | None = Field(
        default=None,
        description="`query.manualSort` — `true` when sorting was applied manually instead of using the default behaviour.",
    )
    dbt_mode: bool | None = Field(default=None, description="`query.dbtMode` — if `true`, enable dbt mode.")
    rewrite_sql: bool | None = Field(default=None, description="`query.rewriteSql` — if `true`, enable SQL mode.")
    dimension_index: int | None = Field(
        default=None,
        ge=0,
        description="`query.dimensionIndex` — index of the last dimension in `fields`; used to order and group fields.",
    )
    user_edited_sql: str | None = Field(
        default=None, description="`query.userEditedSQL` — user-edited SQL override for the query."
    )
    custom_summary_types: dict[str, Any] | None = Field(
        default=None, description="`query.custom_summary_types` — custom summary type configurations."
    )
    version: int | None = Field(default=None, description="`query.version` — query version number.")
    query: dict[str, Any] | None = Field(
        default=None,
        description="Raw `query` object, passed through verbatim and **winning over every first-class field above**. Copy it from a workbook's Inspector panel (bug icon, `Option/Alt + 9` → Query structure) or from the document-queries endpoint.",
    )

    # --- top-level request body options ---
    branch_id: str | None = Field(
        default=None,
        description="`branchId` — optional model branch (UUID) to run against instead of the shared model. Must belong to the same shared model as `model_id`.",
    )
    connection_environment_id: str | None = Field(
        default=None,
        description="`connectionEnvironmentId` — optional connection environment (UUID) overriding the session-derived one. The caller must have access to it.",
    )
    cache: Literal["Standard", "SkipRequery", "SkipCache"] | None = Field(
        default=None,
        description="Cache policy: `Standard` (standard caching), `SkipRequery` (use cached results but do not requery — the API default), `SkipCache` (always run a fresh query).",
    )
    result_type: Literal["csv", "json", "xlsx"] | None = Field(
        default=None,
        description="Export format. **Cannot be combined with `plan_only`.** Leave unset (the default) to get the base64 Apache Arrow table this tool decodes into rows; setting it returns the raw csv/json/xlsx export verbatim, with no table, metadata, or SQL.",
    )
    plan_only: bool | None = Field(
        default=None,
        description="If `true`, return the query execution plan (generated SQL and metadata) without running the query. **Cannot be combined with `result_type`.**",
    )
    format_results: bool | None = Field(
        default=None,
        description="**Only applicable with `result_type`.** If `true`, numeric and currency values are formatted with currency symbols and thousand separators.",
    )
    workbook_url: bool | None = Field(
        default=None,
        description="If `true`, create an ephemeral workbook reproducing the query; its URL is reported in the result metadata (best-effort — the API omits it silently if the user lacks the workbooks permission). **Cannot be combined with `plan_only`.**",
    )

    # --- transport / rendering ---
    user_id: str | None = Field(
        default=None,
        description="`userId` query parameter — run the query as this user (UUID). **Requires an Organization API key**; a Personal Access Token gets a 403.",
    )
    timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        le=600.0,
        description="How long to wait for the HTTP response before giving up (seconds).",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a summary plus a rendered table, `json` for the metadata and decoded rows.",
    )
    max_rows: int = Field(
        default=100,
        ge=1,
        le=10_000,
        description="Maximum number of decoded rows to render (the API row count is always reported in full).",
    )


class WaitForQueryResultsInput(BaseModel):
    """Input for `omni_wait_for_query_results`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_ids: list[str] = Field(
        ...,
        min_length=1,
        description="The job IDs (UUIDs) to poll — the `remaining_job_ids` reported when `omni_run_query` timed out.",
    )
    max_wait_seconds: float = Field(
        default=120.0,
        ge=1.0,
        le=900.0,
        description="Total wall-clock time to keep polling before giving up and returning the still-pending job IDs.",
    )
    poll_interval_seconds: float = Field(
        default=2.0,
        ge=0.5,
        le=60.0,
        description="Seconds to wait between polls. Keep it at 2-5s; never poll more than once per second.",
    )
    timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        le=600.0,
        description="How long a single poll request may take (seconds). Clamped to whatever is left of `max_wait_seconds`.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a summary plus a rendered table, `json` for the metadata and decoded rows.",
    )
    max_rows: int = Field(
        default=100,
        ge=1,
        le=10_000,
        description="Maximum number of decoded rows to render (the API row count is always reported in full).",
    )


class GetJobStatusInput(BaseModel):
    """Input for `omni_get_job_status`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_id: str = Field(
        ...,
        min_length=1,
        description="The job ID (UUID) returned by an asynchronous operation such as a schema refresh.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw status payload.",
    )


# --- helpers ----------------------------------------------------------


def _is_true(value: Any) -> bool:
    """Truthiness for `timed_out`, which the spec types as bool *and* string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _decode_response(response: httpx.Response) -> Any:
    """Decode a response body: JSON when it parses, else raw text, else `None`.

    `omni_run_query` reads the response itself (rather than through
    `request_json`) because the workbook URL only exists as a header.
    """
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _job_ids(body: dict[str, Any]) -> list[str]:
    jobs = body.get("remaining_job_ids")
    return [str(job) for job in jobs] if isinstance(jobs, list) else []


def _submitted_job_ids(body: dict[str, Any]) -> list[str]:
    """Job ids from a `jobs_submitted` map (job id -> client result id)."""
    submitted = body.get("jobs_submitted")
    return [str(job_id) for job_id in submitted] if isinstance(submitted, dict) else []


def _parse_ndjson(text: str) -> tuple[list[Any], int]:
    """Parse newline-delimited JSON, returning the values and the reject count.

    Unparseable lines are skipped rather than failing the whole response — one
    malformed line must not cost the caller a result that did arrive — but they
    are counted so the tool can say so.
    """
    values: list[Any] = []
    unparsed = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            values.append(json.loads(stripped))
        except ValueError:
            unparsed += 1
    return values, unparsed


class _QueryBody(NamedTuple):
    """One run/wait response, normalised out of however it arrived."""

    body: dict[str, Any]
    other_result_job_ids: list[str]
    unparsed_lines: int


def _query_body(payload: Any) -> _QueryBody | None:
    """Normalise a run/wait payload into the single-object shape renderers expect.

    The API answers either with one JSON object or with newline-delimited JSON:
    a `jobs_submitted` line, a line per job carrying its `result`, and a line
    carrying `remaining_job_ids` when a job outlives the request. `.json()`
    cannot parse the latter, so the raw text is re-read line by line here.

    Merging rules: `jobs_submitted` is collected from every line that has it,
    `remaining_job_ids` is unioned across lines, and `timed_out` is true when any
    line says so. Result lines are keyed by job id so a later line supersedes an
    earlier one for the same job; the first job's result becomes the body and the
    remaining jobs' ids are reported alongside it.

    Returns `None` when the payload is neither an object nor parseable NDJSON, so
    the caller can hand the raw body back untouched.
    """
    if isinstance(payload, dict):
        return _QueryBody(payload, [], 0)
    if not isinstance(payload, str):
        return None
    values, unparsed = _parse_ndjson(payload)
    objects = [value for value in values if isinstance(value, dict)]
    if not objects:
        return None

    jobs_submitted: dict[str, Any] = {}
    remaining: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    fallback: dict[str, Any] = {}
    timed_out: bool | None = None

    for index, line in enumerate(objects):
        submitted = line.get("jobs_submitted")
        if isinstance(submitted, dict):
            jobs_submitted.update(submitted)
        for job_id in _job_ids(line):
            if job_id not in remaining:
                remaining.append(job_id)
        if "timed_out" in line:
            timed_out = bool(timed_out) or _is_true(line.get("timed_out"))
        if line.get("result"):
            # Keyed by job: re-assigning keeps the key's original position, so
            # the newest line for a job wins while job order stays first-seen.
            results[str(line.get("job_id") or f"#{index}")] = line
        elif line.get("job_id") or line.get("status") or line.get("summary"):
            fallback = line

    ordered = list(results.values())
    body: dict[str, Any] = dict(ordered[0]) if ordered else dict(fallback)
    if jobs_submitted:
        body["jobs_submitted"] = jobs_submitted
    if remaining:
        body["remaining_job_ids"] = remaining
    if timed_out is not None:
        body["timed_out"] = timed_out
    others = [str(line.get("job_id")) for line in ordered[1:] if line.get("job_id")]
    return _QueryBody(body, others, unparsed)


def _query_object(params: RunQueryInput) -> dict[str, Any]:
    """Build the `query` object, mapping snake_case inputs to the spec's keys.

    A raw `query` passthrough wins outright: it is copied from a workbook and
    already carries keys this tool does not model.
    """
    if params.query is not None:
        return dict(params.query)
    mapped: dict[str, Any] = {
        "modelId": params.model_id,
        "table": params.table,
        "fields": params.fields,
        "limit": params.limit,
        "sorts": params.sorts,
        "filters": params.filters,
        "pivots": params.pivots,
        "calculations": params.calculations,
        "column_totals": params.column_totals,
        "row_totals": params.row_totals,
        "column_limit": params.column_limit,
        "join_paths_from_topic_name": params.join_paths_from_topic_name,
        "join_via_map": params.join_via_map,
        "fill_fields": params.fill_fields,
        "default_group_by": params.default_group_by,
        "controls": params.controls,
        "manualSort": params.manual_sort,
        "dbtMode": params.dbt_mode,
        "rewriteSql": params.rewrite_sql,
        "dimensionIndex": params.dimension_index,
        "userEditedSQL": params.user_edited_sql,
        "custom_summary_types": params.custom_summary_types,
        "version": params.version,
    }
    return {key: value for key, value in mapped.items() if value is not None}


def _request_body(params: RunQueryInput) -> dict[str, Any]:
    body: dict[str, Any] = {"query": _query_object(params)}
    options: dict[str, Any] = {
        "branchId": params.branch_id,
        "connectionEnvironmentId": params.connection_environment_id,
        "cache": params.cache,
        "resultType": params.result_type,
        "planOnly": params.plan_only,
        "formatResults": params.format_results,
        "workbookUrl": params.workbook_url,
    }
    body.update({key: value for key, value in options.items() if value is not None})
    return body


def _sql_lines(summary: dict[str, Any]) -> list[str]:
    """The generated SQL, fenced and cut so it cannot swamp the result.

    Live responses carry it as `display_sql`; the spec calls it `sql`.
    """
    sql = summary.get("sql") or summary.get("display_sql")
    if not isinstance(sql, str) or not sql.strip():
        return []
    text = sql.strip()
    if len(text) > MAX_SQL_CHARS:
        text = f"{text[:MAX_SQL_CHARS]}\n-- … SQL truncated at {MAX_SQL_CHARS:,} characters …"
    return ["", "## SQL", "", "```sql", text, "```"]


def _field_names(summary: dict[str, Any]) -> list[str]:
    """Field names from the summary's field metadata, in whichever shape it uses."""
    fields = summary.get("fields")
    names: list[str] = []
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, str):
                names.append(field)
            elif isinstance(field, dict):
                label = field.get("field_name") or field.get("name") or field.get("label") or field.get("id")
                if label is not None:
                    names.append(str(label))
    elif isinstance(fields, dict):
        names = [str(key) for key in fields]
    return names


def _metadata_lines(
    body: dict[str, Any],
    *,
    workbook_url: str | None = None,
    other_result_job_ids: list[str] | None = None,
    unparsed_lines: int = 0,
) -> list[str]:
    """Job / summary / cache metadata rendered above the result table."""
    summary = _mapping(body.get("summary"))
    lines: list[str] = []
    for label, value in (
        ("Job ID", body.get("job_id")),
        ("Status", body.get("status")),
        ("Client result ID", body.get("client_result_id")),
    ):
        if value:
            lines.append(f"- {label}: `{value}`")

    total_rows = summary.get("total_rows")
    if isinstance(total_rows, int):
        lines.append(f"- Rows reported by the API: {total_rows:,}")
    execution_time = summary.get("execution_time_ms")
    if isinstance(execution_time, int | float):
        lines.append(f"- Execution time: {execution_time:,.0f} ms")
    server_stream = _mapping(body.get("stream_stats")).get("server_stream")
    if isinstance(server_stream, int | float):
        lines.append(f"- Result streaming: {server_stream:,.0f} ms")

    cache_metadata = _mapping(body.get("cache_metadata"))
    if cache_metadata:
        rendered = ", ".join(
            f"{key}={value}" for key, value in cache_metadata.items() if isinstance(value, str | int | float | bool)
        )
        if rendered:
            lines.append(f"- Cache: {rendered}")

    names = _field_names(summary)
    if names:
        shown = names[:MAX_SUMMARY_FIELDS]
        suffix = "" if len(shown) == len(names) else f" _(+{len(names) - len(shown):,} more)_"
        lines.append(f"- Fields ({len(names):,}): " + ", ".join(f"`{name}`" for name in shown) + suffix)

    # A run can return one job's rows while others are still going.
    still_running = _job_ids(body)
    if still_running:
        listed = ", ".join(f"`{job_id}`" for job_id in still_running)
        lines.append(f"- Still running: {listed} — poll them with `omni_wait_for_query_results`.")
    if other_result_job_ids:
        listed = ", ".join(f"`{job_id}`" for job_id in other_result_job_ids)
        lines.append(f"- Other job results in this response: {listed} — only the first is rendered below.")
    if unparsed_lines:
        lines.append(f"- Note: {unparsed_lines:,} response line(s) could not be parsed as JSON and were skipped.")
    if workbook_url:
        lines.append(f"- Workbook: {workbook_url}")
    return lines


def _pending_message(job_ids: list[str], *, reason: str) -> str:
    listed = ", ".join(f"`{job_id}`" for job_id in job_ids) if job_ids else "_none reported_"
    return truncate_result(
        f"# Query still running\n\n{reason}\n\n"
        f"- Remaining job IDs: {listed}\n\n"
        "Call `omni_wait_for_query_results` with those `job_ids` until `timed_out` is false, "
        "or narrow the query (fewer rows, fewer fields, a tighter filter) and run it again."
    )


def _render_query_result(
    body: dict[str, Any],
    fmt: ResponseFormat,
    max_rows: int,
    *,
    title: str,
    workbook_url: str | None = None,
    other_result_job_ids: list[str] | None = None,
    unparsed_lines: int = 0,
) -> str:
    """Render a run/wait response: metadata + SQL, then the decoded Arrow rows.

    Markdown goes through `format_arrow_result`; JSON keeps the same decoded
    rows but wraps them with the job metadata, which a bare row dump would drop.
    """
    result = body.get("result")
    result_b64 = result if isinstance(result, str) else ""
    summary = _mapping(body.get("summary"))

    if fmt is ResponseFormat.JSON:
        rows = decode_arrow_base64(result_b64) if result_b64 else []
        shown = rows[:max_rows]
        return truncate_result(
            to_json(
                {
                    "jobId": body.get("job_id"),
                    "status": body.get("status"),
                    "clientResultId": body.get("client_result_id"),
                    "summary": summary or None,
                    "cacheMetadata": _mapping(body.get("cache_metadata")) or None,
                    "streamStats": _mapping(body.get("stream_stats")) or None,
                    "timedOut": _is_true(body.get("timed_out")),
                    "remainingJobIds": _job_ids(body),
                    "workbookUrl": workbook_url,
                    "otherResultJobIds": other_result_job_ids or [],
                    "rowCount": len(rows),
                    "returnedRows": len(shown),
                    "truncated": len(shown) < len(rows),
                    "rows": shown,
                }
            )
        )

    lines = [f"# {title}", ""]
    lines.extend(
        _metadata_lines(
            body,
            workbook_url=workbook_url,
            other_result_job_ids=other_result_job_ids,
            unparsed_lines=unparsed_lines,
        )
        or ["_No job metadata reported._"]
    )
    lines.extend(_sql_lines(summary))
    lines.append("")
    if result_b64:
        lines.append("## Results")
        lines.append("")
        lines.append(format_arrow_result(result_b64, ResponseFormat.MARKDOWN, max_rows=max_rows))
    else:
        lines.append("_The response carried no result table (a plan-only run, or a job with no data)._")
    return truncate_result("\n".join(lines))


# --- tools ------------------------------------------------------------


@mcp.tool(
    name="omni_run_query",
    annotations=ToolAnnotations(
        title="Run Omni Query",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_run_query(params: RunQueryInput) -> str:
    """Run a query against an Omni model and return the resulting rows.

    Calls `POST /v1/query/run`. The API answers with a base64-encoded Apache
    Arrow table — as one JSON object or as newline-delimited JSON, both of which
    this tool decodes into rows and renders as a markdown table (or JSON)
    alongside the generated SQL and execution metadata. Supply
    the query either field-by-field (`model_id` + `table` + `fields` + …) or as
    a raw `query` object copied from a workbook's Inspector panel — the raw
    object wins whenever it is present.

    When to Use:
    - To answer a data question with real numbers from the warehouse.
    - To re-run a query taken from a document/workbook (`query` passthrough).
    - To inspect the SQL a query would generate, with `plan_only: true`.

    When NOT to Use:
    - To browse models, topics, or fields — use the model tools first to learn
      the valid `table` and `fields` values.
    - To resume a query that already timed out — use
      `omni_wait_for_query_results` with the reported job IDs instead.
    - To check a schema-refresh job — that is `omni_get_job_status`.

    Returns:
    A markdown report — job ID and status, row count, execution time, cache and
    field metadata, any workbook URL and still-running job IDs, the generated SQL
    (fenced and truncated), then the result table capped at `max_rows` — or the
    same metadata with decoded rows when `response_format` is `json`. Setting
    `result_type` returns the raw export instead. If the request soft-timed out, a message with
    the remaining job IDs and what to do next. On failure, an `Error ...` string.

    Examples:
    - Fields first-class: `{"params": {"model_id": "bcf0cffd-ec1b-44d5-945a-a261ebe407fc", "table": "order_items", "fields": ["inventory_items.product_department", "inventory_items.product_category", "inventory_items.count"], "limit": 10, "sorts": [{"column_name": "inventory_items.product_department", "sort_descending": false}], "join_paths_from_topic_name": "order_items"}}`
    - Raw query object from a workbook: `{"params": {"query": {"modelId": "7155f419-a071-405c-8426-b4b5d3939049", "table": "order_items", "fields": ["order_items.created_at[month]", "order_items.sale_price_sum"], "filters": {}, "sorts": [{"column_name": "order_items.created_at[month]", "sort_descending": false}], "limit": 1000}}}`
    - SQL only, no execution: `{"params": {"model_id": "bcf0cffd-…", "table": "order_items", "fields": ["order_items.count"], "plan_only": true}}`
    - As another user (Organization API key only): `{"params": {"query": {"modelId": "…", "table": "order_items", "fields": ["order_items.count"]}, "user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`

    Error Handling:
    408 means the query outlived the request: the error lists
    `remaining_job_ids` — pass them to `omni_wait_for_query_results` until
    `timed_out` is false. 403 means either the caller cannot read the model or a
    Personal Access Token tried to use `user_id`; impersonation needs an
    Organization API key, and running a query needs query permission on the
    model. 400 flags an invalid body — a negative `limit`, `plan_only` combined
    with `result_type` or `workbook_url`, `format_results` without
    `result_type`, or a `branch_id` that is not a UUID. 404 means the model,
    topic, view, branch, or `user_id` does not exist. Every failure comes back
    as an `Error ...` string; the tool never raises.
    """
    try:
        if params.query is None and not (params.model_id and params.table and params.fields):
            return (
                "Error: a query needs `model_id`, `table` and `fields` (or a raw `query` object). "
                "List the models and topics first, or copy the query JSON from a workbook's Inspector panel."
            )

        query_params = {"userId": params.user_id} if params.user_id else None
        # The response, not just its body: `workbook_url` is delivered as a header.
        response = await get_client().request(
            "POST",
            "/v1/query/run",
            params=query_params,
            json_body=_request_body(params),
            timeout=params.timeout_seconds,
        )
        payload = _decode_response(response)
        if payload is None:
            return "_The API returned an empty response._"
        if params.result_type is not None:
            # An export was requested, so the body is csv/json/xlsx rather than the
            # envelope with the base64 Arrow table. Branch on the request, not on
            # the decoded type: a JSON export parses into a dict or a list.
            return truncate_result(payload if isinstance(payload, str) else to_json(payload))
        decoded = _query_body(payload)
        if decoded is None:
            return truncate_result(str(payload))

        body = decoded.body
        workbook_url = response.headers.get("x-omni-workbook-url")
        if not body.get("result"):
            pending = _job_ids(body)
            reason = "The request timed out before the query finished."
            if not pending and not body.get("status") and not body.get("summary"):
                # Nothing but the submission acknowledgement came back.
                pending = _submitted_job_ids(body)
                reason = "The API accepted the query and reported its job ids, but no results yet."
            if pending or _is_true(body.get("timed_out")):
                return _pending_message(pending, reason=reason)
        return _render_query_result(
            body,
            params.response_format,
            params.max_rows,
            title="Query results",
            workbook_url=workbook_url,
            other_result_job_ids=decoded.other_result_job_ids,
            unparsed_lines=decoded.unparsed_lines,
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_wait_for_query_results",
    annotations=ToolAnnotations(
        title="Wait For Omni Query Results",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_wait_for_query_results(params: WaitForQueryResultsInput) -> str:
    """Poll query jobs that outlived their request and return their results.

    Calls `GET /v1/query/wait` with the job IDs (sent as a JSON array, exactly
    as the API expects, and reading back either one JSON object or the
    newline-delimited JSON the endpoint streams) and keeps polling every
    `poll_interval_seconds` until
    the response reports `timed_out: false` or `max_wait_seconds` of **wall
    clock** time has passed. Each request is a server-side long poll, so its
    timeout is clamped to whatever is left of that budget — the tool cannot
    outstay it. Results are decoded from base64 Apache Arrow and rendered like
    `omni_run_query`'s.

    When to Use:
    - Right after `omni_run_query` returned a 408 with `remaining_job_ids`.
    - When a previous wait gave up and handed back still-pending job IDs.

    When NOT to Use:
    - To start a query — that is `omni_run_query`.
    - To check a schema-refresh job — that is `omni_get_job_status`.

    Returns:
    The same report as `omni_run_query` — metadata, SQL, and the result table
    (or JSON rows) — once the jobs finish. If the budget runs out first, a
    message naming the job IDs still pending so the call can be repeated. On
    failure, an `Error ...` string.

    Examples:
    - `{"params": {"job_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"]}}`
    - Two jobs, patient poll: `{"params": {"job_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890", "b2c3d4e5-f6a7-8901-bcde-f12345678901"], "max_wait_seconds": 300, "poll_interval_seconds": 5}}`
    - Raw rows: `{"params": {"job_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"], "response_format": "json", "max_rows": 500}}`

    Error Handling:
    400 means `job_ids` was missing or not a valid JSON array. 404 means one of
    the job IDs does not exist — job IDs expire, so re-run the query rather than
    polling forever. 403 means the caller lost read access to the underlying
    model. A 408 from the API is handled by polling; when the local budget runs
    out the tool returns the pending IDs instead of an error. Every failure
    comes back as an `Error ...` string; the tool never raises.
    """
    try:
        client = get_client()
        pending = [job_id for job_id in params.job_ids if job_id]
        start = _monotonic()
        elapsed = 0.0
        while True:
            remaining = params.max_wait_seconds - elapsed
            response = await client.request(
                "GET",
                "/v1/query/wait",
                params={"job_ids": json.dumps(pending)},
                # The endpoint long-polls server-side, so one request can consume
                # the whole budget: never let it outlive what is left of it.
                timeout=min(params.timeout_seconds, remaining) if remaining > 0 else params.timeout_seconds,
            )
            # This endpoint streams the same newline-delimited JSON as the run one.
            decoded = _query_body(_decode_response(response)) or _QueryBody({}, [], 0)
            body = decoded.body
            if not _is_true(body.get("timed_out")):
                return _render_query_result(
                    body,
                    params.response_format,
                    params.max_rows,
                    title="Query results",
                    other_result_job_ids=decoded.other_result_job_ids,
                    unparsed_lines=decoded.unparsed_lines,
                )
            pending = _job_ids(body) or pending
            elapsed = _monotonic() - start
            if elapsed + params.poll_interval_seconds >= params.max_wait_seconds:
                break
            await _sleep(params.poll_interval_seconds)
            elapsed = _monotonic() - start

        return _pending_message(
            pending,
            reason=(
                f"Still running after {elapsed:,.0f}s of the {params.max_wait_seconds:,.0f}s budget "
                f"(poll interval {params.poll_interval_seconds:g}s)."
            ),
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_job_status",
    annotations=ToolAnnotations(
        title="Get Omni Job Status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_job_status(params: GetJobStatusInput) -> str:
    """Get the status of an asynchronous job, such as a schema refresh.

    Calls `GET /v1/jobs/{jobId}/status` and reports whether the job is
    `RUNNING`, `COMPLETED` or `FAILED`. Only schema-refresh jobs are supported
    today; other job types answer 403. Poll no more than once every 2-5 seconds.

    When to Use:
    - After starting a schema refresh, to find out when it finished.
    - To confirm a long-running job failed rather than still running.

    When NOT to Use:
    - For query jobs — poll those with `omni_wait_for_query_results`, which
      also returns the rows.
    - To start a job; this tool only reads status.

    Returns:
    A markdown summary — job ID, job type, status, and what to do next — or the
    raw status payload when `response_format` is `json`. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"job_id": "4e6953a9-a71b-4c0b-8b63-a9ea308f6aaf"}}`
    - Raw payload: `{"params": {"job_id": "4e6953a9-a71b-4c0b-8b63-a9ea308f6aaf", "response_format": "json"}}`

    Error Handling:
    400 means `job_id` is not a valid UUID. 403 means either the job type is not
    supported for status checks or the caller lacks read permission on the
    connection. 404 means the job does not exist (job records expire). Every
    failure comes back as an `Error ...` string; the tool never raises.
    """
    try:
        payload = await get_client().request_json("GET", f"/v1/jobs/{quote(params.job_id, safe='')}/status")
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        status = str(body.get("status", "unknown"))
        next_step = {
            "RUNNING": "Still executing — poll again in 2-5 seconds.",
            "COMPLETED": "Finished successfully.",
            "FAILED": "The job failed; re-run the operation that started it.",
        }.get(status, 'Unrecognised status — check the raw payload with `response_format: "json"`.')
        lines = [
            "# Job status",
            "",
            f"- Job ID: `{body.get('job_id', params.job_id)}`",
            f"- Job type: `{body.get('job_type', 'unknown')}`",
            f"- Status: **{status}**",
            "",
            next_step,
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)
