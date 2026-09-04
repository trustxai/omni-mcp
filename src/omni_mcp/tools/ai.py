"""Tools for the AI job, conversation, query generation, and topic endpoints.

Covers every operation under the API's `AI` tag plus one convenience tool
(`omni_ask_ai`) that chains create-job → poll-status → fetch-result so a caller
gets an answer in a single tool call.

`GET /v1/ai/jobs/{jobId}/result` returns one JSON result object that the API
serves "streamed directly from storage" — chunked transfer of a single
document, not an event protocol. These tools never stream to the MCP client:
the whole body is read with a generous timeout and decoded here, so the tool
result is always the final, complete answer. A `text/event-stream` fallback is
kept for the case where an instance serves the endpoint as Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx
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

#: States after which polling stops — the job will not change again.
TERMINAL_JOB_STATES: frozenset[str] = frozenset({"CANCELLED", "COMPLETE", "FAILED"})

#: Sentinel `data:` payloads that close a Server-Sent Events stream.
SSE_SENTINELS: frozenset[str] = frozenset({"[DONE]", "[done]"})

#: Default read timeout (seconds) for the job-result endpoint.
RESULT_TIMEOUT_SECONDS = 120.0

#: Per-request timeout ceiling for one `omni_ask_ai` status poll.
STATUS_POLL_TIMEOUT_SECONDS = 30.0

#: Hard cap on `omni_ask_ai` status polls, so one call can never eat the key's
#: 60 requests/minute budget however the interval and wait are set.
MAX_POLL_ATTEMPTS = 60

#: How many CSV characters of a query result are shown per action in markdown.
CSV_PREVIEW_CHARS = 2_000

#: Injectable sleep for the `omni_ask_ai` poll loop (monkeypatched in tests).
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

#: Injectable monotonic clock for the `omni_ask_ai` deadline (ditto). Wall-clock
#: time, not accumulated sleep, is what bounds the wait.
_monotonic: Callable[[], float] = time.monotonic


# --------------------------------------------------------------------------
# Streamed-result decoding
# --------------------------------------------------------------------------


def _parse_sse(body: str) -> dict[str, Any]:
    """Assemble one result object from a `text/event-stream` body (fallback path).

    Parsed per the SSE wire format: an event accumulates its `data:` lines,
    joined with newlines, and is dispatched at the blank line that ends it;
    comments (`:` heartbeats) and the other fields are ignored, and a
    `[DONE]`-style sentinel ends the stream.

    Assembly is deterministic rather than heuristic: the **last** event that
    decodes to a complete result object (one carrying `message` or `actions`)
    wins, since the endpoint's documented payload is a single flat object. When
    no event carries a complete object, the raw non-JSON fragments are
    concatenated in order into `message`; fragments alongside a complete object
    are appended to its message rather than dropped.
    """
    result: dict[str, Any] = {}
    fragments: list[str] = []
    data_lines: list[str] = []
    done = False

    def _dispatch() -> None:
        nonlocal result, done
        if not data_lines:
            return
        payload = "\n".join(data_lines)
        data_lines.clear()
        stripped = payload.strip()
        if not stripped:
            return
        if stripped in SSE_SENTINELS or stripped.strip('"') in SSE_SENTINELS:
            done = True
            return
        try:
            decoded = json.loads(payload)
        except ValueError:
            fragments.append(payload)
            return
        if isinstance(decoded, dict) and ("message" in decoded or "actions" in decoded):
            result = decoded
        elif isinstance(decoded, str):
            fragments.append(decoded)

    for raw_line in body.splitlines():
        if done:
            break
        line = raw_line.rstrip("\r")
        if not line:
            _dispatch()
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field != "data":
            continue
        data_lines.append(value[1:] if value.startswith(" ") else value)
    if not done:
        _dispatch()

    text = "".join(fragments)
    if text:
        existing = result.get("message")
        result["message"] = f"{existing}{text}" if isinstance(existing, str) else text
    return result


def _decode_job_result(response: httpx.Response) -> dict[str, Any]:
    """Turn the fully-read job-result response into a result object.

    The documented payload is a single JSON object, so that is the primary
    path; `text/event-stream` falls back to :func:`_parse_sse`.
    """
    content_type = (response.headers.get("content-type") or "").lower()
    text = response.text or ""
    if "text/event-stream" in content_type:
        return _parse_sse(text)
    if not text.strip():
        return {}
    try:
        decoded = json.loads(text)
    except ValueError:
        return {"message": text}
    if isinstance(decoded, dict):
        return decoded
    if isinstance(decoded, list):
        return {"message": to_json(decoded)}
    return {"message": str(decoded)}


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------


def _fenced_json(data: Any) -> str:
    """Render a payload as a fenced JSON block the caller can copy verbatim."""
    return f"```json\n{to_json(data)}\n```"


def _dicts(value: Any) -> list[dict[str, Any]]:
    """Keep only the dict entries of a value the API says is an array."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _render_action(index: int, action: dict[str, Any]) -> list[str]:
    """Render one entry of a job result's `actions` array."""
    lines = [
        f"### {index}. `{action.get('type', 'unknown')}` — {iso_or_na(action.get('timestamp'))}",
        "",
        str(action.get("message") or "_No message._"),
        "",
    ]
    result = action.get("result")
    if not isinstance(result, dict):
        return lines
    lines.append(
        f"- Query **{result.get('queryName', 'unnamed')}** — status `{result.get('status', 'unknown')}`, "
        f"{result.get('totalRowCount', 0)} row(s)"
        + (", CSV truncated by the API" if result.get("csvResultWasTruncated") else "")
    )
    if result.get("resultId"):
        lines.append(f"- Result id: `{result['resultId']}`")
    query = result.get("query")
    if isinstance(query, dict) and query:
        lines.extend(["", "Query definition (re-runnable with `omni_run_query`):", "", _fenced_json(query)])
    csv_result = result.get("csvResult")
    if isinstance(csv_result, str) and csv_result.strip():
        preview = csv_result[:CSV_PREVIEW_CHARS]
        suffix = "\n… (CSV preview truncated)" if len(csv_result) > CSV_PREVIEW_CHARS else ""
        lines.extend(["", "```csv", preview + suffix, "```"])
    lines.append("")
    return lines


def _render_job_result(job_id: str, body: dict[str, Any], fmt: ResponseFormat) -> str:
    """Render an assembled job result as markdown, or hand back the raw object."""
    if fmt is ResponseFormat.JSON:
        return truncate_result(to_json({"jobId": job_id, "result": body}))

    lines = [f"# AI job result `{job_id}`", ""]
    if body.get("topic"):
        lines.append(f"- Topic: `{body['topic']}`")
    if body.get("omniChatUrl"):
        lines.append(f"- Chat: {body['omniChatUrl']}")
    if lines[-1] != "":
        lines.append("")

    answer = body.get("message") or body.get("resultSummary")
    lines.extend(["## Answer", "", str(answer) if answer else "_The job returned no answer text._", ""])

    actions = _dicts(body.get("actions"))
    if actions:
        lines.extend([f"## Actions ({len(actions)})", ""])
        for index, action in enumerate(actions, start=1):
            lines.extend(_render_action(index, action))
    if not answer and not actions and not body:
        lines.append("_The result body was empty — the job may not have completed yet._")
    return truncate_result("\n".join(lines).rstrip() + "\n")


def _render_conversation_message(message: dict[str, Any]) -> list[str]:
    """Render one turn of a conversation transcript."""
    role = str(message.get("role", "unknown"))
    lines = [f"### {role} — {iso_or_na(message.get('timestamp'))}", ""]
    if message.get("jobId"):
        lines.append(f"- Job id: `{message['jobId']}`")
    if message.get("omniChatUrl"):
        lines.append(f"- Chat: {message['omniChatUrl']}")
    if lines[-1] != "":
        lines.append("")
    lines.extend([str(message.get("content") or "_No content._"), ""])
    return lines


# --------------------------------------------------------------------------
# Input models
# --------------------------------------------------------------------------


class GetAiBrandingInput(BaseModel):
    """Input for `omni_get_ai_branding`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw branding payload.",
    )


class GenerateQueryInput(BaseModel):
    """Input for `omni_generate_query`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The ID of the model to run the query against (UUID).")
    prompt: str = Field(description="Natural language instruction describing the desired query output.")
    current_topic_name: str | None = Field(
        default=None,
        description=(
            "Name of the base topic/table. If left empty, AI will automatically choose a topic based on the "
            "question. Use `omni_pick_topic` to resolve one first."
        ),
    )
    workbook_url: bool | None = Field(
        default=None,
        description="When `true`, returns an Omni workbook URL that opens and runs the generated query.",
    )
    branch_id: str | None = Field(
        default=None,
        description=(
            "The ID of the model branch to use to generate the query. If not provided, the `main` branch will be "
            "used. Retrieve branch IDs by listing models with `modelKind=BRANCH`."
        ),
    )
    run_query: bool | None = Field(
        default=None,
        description=(
            "When `true` (the API default), the generated query is executed against the database and results are "
            "included in the response. Set `false` to preview the generated query without incurring a database "
            "query."
        ),
    )
    context_query: dict[str, Any] | None = Field(
        default=None,
        description=(
            "A query object to provide as context, e.g. a previous query to refine. Same shape as the `query` "
            'object returned by this tool: `{"model_job": {"model_id": ..., "table": ..., "fields": [...], '
            '"filters": {...}, "sorts": [...], "limit": 10}}`.'
        ),
    )
    query_all_views: bool | None = Field(
        default=None,
        description=(
            "When `true` and the model's `query_all_views_and_fields` setting is enabled, allows the AI to query "
            "views that are not included in topics. No effect when the model setting is disabled, and unavailable "
            "to users with topic-locked permissions."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for the query rendered as a fenced JSON block, `json` for the raw payload.",
    )


class PickTopicInput(BaseModel):
    """Input for `omni_pick_topic`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str = Field(
        description="The natural language prompt to analyze. The AI picks the topic that best matches it."
    )
    model_id: str = Field(
        description=(
            "The UUID of the shared model to query against. Only shared models are supported, and the caller needs "
            "**Querier** permissions or higher on the model."
        )
    )
    branch_id: str | None = Field(
        default=None,
        description="Optional branch ID (UUID). Must be a branch of the shared model given by `model_id`.",
    )
    current_topic_name: str | None = Field(
        default=None,
        description=(
            "The name of the current topic to scope query generation. If omitted, the AI selects the best topic "
            "for the prompt."
        ),
    )
    potential_topic_names: list[str] | None = Field(
        default=None,
        description=(
            'Optional list of topic names to limit consideration to, e.g. `["order_items", "customers"]`. If '
            "omitted, every topic the user can access in the model is evaluated."
        ),
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "User ID (UUID) to evaluate topic access as, for permission-aware topic selection. **Organization API "
            "keys only** — Personal Access Tokens always act as the authenticated user."
        ),
    )
    query_all_views: bool | None = Field(
        default=None,
        description=(
            "When `true` and the model's `query_all_views_and_fields` setting is enabled, lets the AI consider "
            "views that are not included in topics. No effect when the model setting is disabled."
        ),
    )


class ListAiConversationsInput(BaseModel):
    """Input for `omni_list_ai_conversations`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str | None = Field(
        default=None,
        description=(
            "Filter conversations to one user by membership ID (UUID). **Requires an Organization API key** — "
            "user-scoped tokens only ever see their own conversations and are rejected when they send this."
        ),
    )
    page_size: int = Field(default=50, ge=1, le=100, description="Conversations to return per page (1-100).")
    cursor: str | None = Field(
        default=None, description="Pagination cursor from a previous response's `pageInfo.nextCursor`."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable list, `json` for the raw records.",
    )


class GetAiConversationInput(BaseModel):
    """Input for `omni_get_ai_conversation`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    conversation_id: str = Field(description="The unique identifier (UUID) of the conversation.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for the rendered transcript, `json` for the raw conversation payload.",
    )


class CreateAiJobInput(BaseModel):
    """Input for `omni_create_ai_job`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The ID (UUID) of the model to run the AI job against.")
    prompt: str = Field(description="Natural language instruction describing the desired task.")
    conversation_id: str | None = Field(
        default=None, description="Optional conversation ID (UUID) to continue an existing conversation."
    )
    branch_id: str | None = Field(
        default=None,
        description=(
            "Optional branch ID (UUID); must be a branch of the shared model given by `model_id`. Queries run "
            "against the branch, and model changes the agent makes are written to it. If omitted and the agent "
            "changes the model, a new branch is created automatically."
        ),
    )
    topic_name: str | None = Field(
        default=None,
        description=(
            "Topic name to scope query generation. If omitted, the AI selects the best topic (and carries the "
            "previous turn's topic forward in a conversation). The topic must exist in the model and be allowed by "
            "any `ai_chat_topics` restriction, or the API returns 404. Use `omni_pick_topic` to resolve one."
        ),
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "The ID (UUID) of the user to run the job as. **Requires an Organization API key** — Personal Access "
            "Tokens cannot act on behalf of other users."
        ),
    )
    webhook_url: str | None = Field(
        default=None,
        description=(
            "Optional webhook URL that always receives a terminal event (`job.complete` / `job.failed`) when the "
            "job finishes. Required when `progress_webhook_enabled` is `true`."
        ),
    )
    progress_webhook_enabled: bool | None = Field(
        default=None,
        description=(
            "When `true`, real-time progress events are POSTed to `webhook_url` during execution. `webhook_url` is "
            "required if enabled. Progress events are best-effort: single attempt, no retries."
        ),
    )
    webhook_signing_secret: str | None = Field(
        default=None, description="An HMAC-SHA256 secret used to sign webhook payloads."
    )
    webhook_metadata: dict[str, Any] | None = Field(
        default=None,
        description=('Opaque metadata passed back unchanged in webhook payloads, e.g. `{"slack_channel": "C123456"}`.'),
    )
    attachments: list[dict[str, Any]] | None = Field(
        default=None,
        max_length=5,
        description=(
            "Up to 5 image or PDF attachments giving the AI visual context. Each entry is "
            '`{"data": "<base64 file content>", "mimeType": "image/png|image/jpeg|image/webp|application/pdf", '
            '"name": "optional-filename.png"}`. Images are limited to 3MB each and text files to ~50KB; all '
            "attachments share a ~15MB budget with the prompt text."
        ),
    )


class GetAiJobStatusInput(BaseModel):
    """Input for `omni_get_ai_job_status`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_id: str = Field(description="The unique identifier (UUID) of the AI job.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable status, `json` for the raw job payload.",
    )


class CancelAiJobInput(BaseModel):
    """Input for `omni_cancel_ai_job`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_id: str = Field(description="The unique identifier (UUID) of the AI job to cancel.")


class GetAiJobResultInput(BaseModel):
    """Input for `omni_get_ai_job_result`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_id: str = Field(description="The unique identifier (UUID) of the AI job.")
    timeout_seconds: float = Field(
        default=RESULT_TIMEOUT_SECONDS,
        ge=5,
        le=600,
        description="How long to wait while the result body is read in full (5-600 seconds).",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for the answer plus the actions taken, `json` for the assembled raw result.",
    )


class SearchOmniDocsInput(BaseModel):
    """Input for `omni_search_omni_docs`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "A natural language question about product features, configuration, modeling, dashboards, or other "
            "topics covered in the documentation (1-2000 characters)."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for the answer plus a source table, `json` for the raw payload.",
    )


class AskAiInput(BaseModel):
    """Input for `omni_ask_ai`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(description="The ID (UUID) of the model to run the AI job against.")
    prompt: str = Field(description="Natural language question or instruction for the AI.")
    conversation_id: str | None = Field(
        default=None, description="Optional conversation ID (UUID) to continue an existing conversation."
    )
    topic_name: str | None = Field(
        default=None,
        description=(
            "Topic name to scope query generation. If omitted, the AI picks one. Resolve it first with "
            "`omni_pick_topic` when the question could span several topics."
        ),
    )
    branch_id: str | None = Field(
        default=None, description="Optional branch ID (UUID); must be a branch of the shared model given by `model_id`."
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "The ID (UUID) of the user to run the job as. **Requires an Organization API key** — Personal Access "
            "Tokens cannot act on behalf of other users."
        ),
    )
    poll_interval_seconds: float = Field(
        default=2.0,
        ge=2,
        le=30,
        description=(
            "Seconds to wait between job-status polls (the API recommends 2-5; 2 is the floor because the key "
            "allows only 60 requests per minute)."
        ),
    )
    max_wait_seconds: float = Field(
        default=120.0,
        ge=5,
        le=900,
        description=(
            "Give up after this much elapsed wall-clock time and return the job and conversation IDs instead. Jobs "
            f"typically complete in 15-60 seconds. Polling also stops after {MAX_POLL_ATTEMPTS} status requests."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for the rendered answer, `json` for the assembled raw result.",
    )


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool(
    name="omni_get_ai_branding",
    annotations=ToolAnnotations(
        title="Get AI Agent Branding",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_ai_branding(params: GetAiBrandingInput) -> str:
    """Get the organization's AI Agent branding configuration.

    Returns the display name, custom logo URL and the copy shown on AI Agent
    landing surfaces, falling back to the product defaults when custom branding
    has not been configured.

    When to Use:
    - To label an AI surface with the organization's agent name and logo.
    - To reuse the configured headline, body copy and prompt placeholder.

    When NOT to Use:
    - To ask a question of the AI (use `omni_ask_ai` or `omni_create_ai_job`).
    - To read organization settings unrelated to the AI Agent.

    Returns:
    A markdown summary of the branding fields, or the raw payload when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {}}`
    - `{"params": {"response_format": "json"}}`

    Error Handling:
    401 means the API key is missing or invalid. 403 `Insufficient permissions`
    means the caller has no AI permissions on any model — an admin must enable
    AI/the Omni Agent and grant the caller AI access. 429 is the 60 requests per
    minute per key rate limit.
    """
    try:
        payload = await get_client().request_json("GET", "/v1/ai/branding")
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        name = body.get("name") or body.get("displayName") or "Omni Agent"
        logo = body.get("logoUrl")
        lines = [
            "# AI Agent branding",
            "",
            f"- Name: **{name}**",
            f"- Logo URL: {logo if logo else '_none configured (default branding)_'}",
            f"- Headline: {body.get('headline') or '_not set_'}",
            f"- Body: {body.get('body') or '_not set_'}",
            f"- Prompt placeholder: {body.get('promptPlaceholder') or '_not set_'}",
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_generate_query",
    annotations=ToolAnnotations(
        title="Generate Query with AI",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_generate_query(params: GenerateQueryInput) -> str:
    """Generate a structured query from a natural language prompt (consumes AI credits).

    The returned `query.model_job` object can be handed straight to the query
    tools (`omni_run_query`) to execute or re-run it. This call consumes AI
    credits, and by default it also *runs* the generated query against the
    database — pass `run_query: false` to preview the query only.

    When to Use:
    - To turn one focused question into a re-runnable query object.
    - To preview what query a prompt would produce before executing it.
    - After `omni_pick_topic` has resolved the right topic for the question.

    When NOT to Use:
    - For exploratory, multi-step or cross-topic questions — use
      `omni_create_ai_job` / `omni_ask_ai`, which iterate and summarize.
    - To execute a query you already have (use `omni_run_query` directly).

    Returns:
    The topic or base view the AI chose plus the generated query as a fenced
    JSON block, or the raw payload when `response_format` is `json`. On failure,
    an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "123e4567-e89b-12d3-a456-426614174000", "prompt": "Total revenue by product category last quarter"}}`
    - Preview only: `{"params": {"model_id": "123e...", "prompt": "Top 10 customers", "current_topic_name": "order_items", "run_query": false}}`

    Error Handling:
    400 `Invalid JSON` / `Invalid method` means the body was malformed. 403
    `Feature not enabled` means AI is not enabled for the organization — an
    admin must turn it on; a 403 can also mean the caller lacks **Querier**
    permission on the model. 429 is the 60 requests per minute per key rate
    limit.
    """
    try:
        body: dict[str, Any] = {"modelId": params.model_id, "prompt": params.prompt}
        if params.current_topic_name is not None:
            body["currentTopicName"] = params.current_topic_name
        if params.workbook_url is not None:
            body["workbookUrl"] = params.workbook_url
        if params.branch_id is not None:
            body["branchId"] = params.branch_id
        if params.run_query is not None:
            body["runQuery"] = params.run_query
        if params.context_query is not None:
            body["contextQuery"] = params.context_query
        if params.query_all_views is not None:
            body["queryAllViews"] = params.query_all_views

        payload = await get_client().request_json("POST", "/v1/ai/generate-query", json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(result))

        topic = result.get("topic")
        base_view = result.get("baseView")
        lines = ["# Generated query", ""]
        lines.append(f"- Topic: {f'`{topic}`' if topic else '_none (a base view was used)_'}")
        lines.append(f"- Base view: {f'`{base_view}`' if base_view else '_none (a topic was used)_'}")
        lines.extend(["", "Pass this object to `omni_run_query` to execute or re-run it:", ""])
        lines.append(_fenced_json(result.get("query") or {}))
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_pick_topic",
    annotations=ToolAnnotations(
        title="Pick Topic with AI",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_pick_topic(params: PickTopicInput) -> str:
    """Pick the model topic that best answers a natural language prompt (consumes AI credits).

    A preprocessing step for `omni_generate_query`, `omni_create_ai_job` and
    `omni_ask_ai`: the returned topic name goes into their `current_topic_name` /
    `topic_name` parameter. This call consumes AI credits.

    When to Use:
    - When a question could relate to several topics and you want it scoped.
    - To constrain the choice to a shortlist via `potential_topic_names`.

    When NOT to Use:
    - When the topic is already known — pass it straight to the other AI tools.
    - To list the topics in a model (use the model tools).

    Returns:
    A one-line markdown confirmation naming the chosen topic. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"model_id": "770e8400-e29b-41d4-a716-446655440002", "prompt": "How many orders were placed last month?"}}`
    - Shortlist: `{"params": {"model_id": "770e...", "prompt": "Top customers", "potential_topic_names": ["order_items", "customers"]}}`

    Error Handling:
    400 means `prompt` or `model_id` is missing or malformed. 401 is a missing
    or invalid API key. 403 means insufficient permissions — the **Querier**
    role on the model is required, and `user_id` impersonation works only with
    an Organization API key. 404 means the model was not found or has no
    accessible topics. 500 is an AI service error; retry.
    """
    try:
        body: dict[str, Any] = {"prompt": params.prompt, "modelId": params.model_id}
        if params.branch_id is not None:
            body["branchId"] = params.branch_id
        if params.current_topic_name is not None:
            body["currentTopicName"] = params.current_topic_name
        if params.potential_topic_names is not None:
            body["potentialTopicNames"] = params.potential_topic_names
        if params.user_id is not None:
            body["userId"] = params.user_id
        if params.query_all_views is not None:
            body["queryAllViews"] = params.query_all_views

        payload = await get_client().request_json("POST", "/v1/ai/pick-topic", json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        topic = result.get("topicId")
        if not topic:
            return (
                "The AI returned no topic for this prompt. Try a more specific prompt or pass `potential_topic_names`."
            )
        return (
            f"Best topic: **{topic}**. Pass it as `current_topic_name` to `omni_generate_query` or as "
            f"`topic_name` to `omni_create_ai_job` / `omni_ask_ai`."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_list_ai_conversations",
    annotations=ToolAnnotations(
        title="List AI Conversations",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_ai_conversations(params: ListAiConversationsInput) -> str:
    """List recent AI conversations, most recently active first.

    Each conversation ID can be passed as `conversation_id` to
    `omni_create_ai_job` / `omni_ask_ai` to continue the thread, or to
    `omni_get_ai_conversation` to read the full transcript.

    When to Use:
    - To find a thread to continue, or to show recent AI activity.
    - To resolve a conversation ID from the last prompt a user asked.

    When NOT to Use:
    - To read a conversation's messages (use `omni_get_ai_conversation`).
    - To list jobs — poll a known job with `omni_get_ai_job_status` instead.

    Returns:
    A paginated markdown list of conversations (id, name, last prompt, updated
    timestamp) with the next cursor, or the raw records when `response_format`
    is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"page_size": 20}}`
    - Next page: `{"params": {"cursor": "eyJpZCI6..."}}`
    - One user (Organization API key only): `{"params": {"user_id": "9e8719d9-276a-4964-9395-a493189a247c"}}`

    Error Handling:
    400 means `user_id` is not a UUID, `page_size` is outside 1-100, or the
    cursor is malformed. 403 `Insufficient permissions` means the caller cannot
    use AI on any model (an admin must enable AI/the Omni Agent), and
    `User-scoped tokens cannot filter by userId` means `user_id` needs an
    Organization API key. 404 means the `user_id` does not exist.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size}
        if params.user_id:
            query["userId"] = params.user_id
        if params.cursor:
            query["cursor"] = params.cursor

        payload = await get_client().request_json("GET", "/v1/ai/conversations", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        def _format(item: Any) -> str:
            record: dict[str, Any] = item if isinstance(item, dict) else {}
            name = record.get("name") or "_unnamed_"
            prompt = record.get("lastUserPrompt") or "_no prompts yet_"
            return (
                f"- **{name}** (`{record.get('id')}`) — updated {iso_or_na(record.get('updatedAt'))}\n"
                f"  - Last prompt: {prompt}"
            )

        return cursor_paginated_response(
            items=_dicts(body.get("data")),
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format,
            title="AI conversations",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_ai_conversation",
    annotations=ToolAnnotations(
        title="Get AI Conversation",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_ai_conversation(params: GetAiConversationInput) -> str:
    """Get one AI conversation with its full message history.

    Returns alternating user and assistant turns; each assistant turn carries
    the originating `jobId` (usable with `omni_get_ai_job_status` /
    `omni_get_ai_job_result`) and a chat deep link.

    When to Use:
    - To read what was already asked and answered before adding a follow-up.
    - To find the job ID behind an assistant answer.

    When NOT to Use:
    - To find a conversation ID (use `omni_list_ai_conversations`).
    - To fetch query data behind an answer (use `omni_get_ai_job_result`).

    Returns:
    A markdown transcript with per-turn metadata, or the raw conversation
    payload when `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"conversation_id": "660e8400-e29b-41d4-a716-446655440001"}}`
    - `{"params": {"conversation_id": "660e...", "response_format": "json"}}`

    Error Handling:
    400 means the conversation ID is not a UUID. 401 is a missing or invalid API
    key. 403 means the caller lacks the `USE_AI` permission, or a user-scoped
    token (Personal Access Token) tried to read another user's conversation —
    only Organization API keys can read any conversation in the organization.
    404 means the conversation does not exist.
    """
    try:
        path = f"/v1/ai/conversations/{quote(params.conversation_id, safe='')}"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        messages = _dicts(body.get("messages"))
        lines = [
            f"# Conversation `{body.get('id', params.conversation_id)}`",
            "",
            f"- User: `{body.get('userId', 'unknown')}`",
            f"- Organization: `{body.get('organizationId', 'unknown')}`",
            f"- Created: {iso_or_na(body.get('createdAt'))}",
            f"- Updated: {iso_or_na(body.get('updatedAt'))}",
            f"- Messages: **{len(messages)}**",
            "",
        ]
        if not messages:
            lines.append("_No messages in this conversation._")
        for message in messages:
            lines.extend(_render_conversation_message(message))
        return truncate_result("\n".join(lines).rstrip() + "\n")
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_ai_job",
    annotations=ToolAnnotations(
        title="Create AI Job",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_ai_job(params: CreateAiJobInput) -> str:
    """Submit an AI job for asynchronous execution (consumes AI credits).

    The AI analyzes the prompt, generates and executes queries against the
    model, and produces a summarized answer. Jobs typically complete in 15-60
    seconds; poll with `omni_get_ai_job_status` and fetch the answer with
    `omni_get_ai_job_result`, or configure `webhook_url` for a callback. The AI
    can also build or edit dashboards, and will publish a dashboard draft only
    when the prompt explicitly asks it to.

    When to Use:
    - For exploratory, multi-step or cross-topic questions.
    - To continue a thread by passing `conversation_id`.
    - When you want to fire the job now and collect the answer later.

    When NOT to Use:
    - When you want the answer in one call — use `omni_ask_ai`, which polls for
      you.
    - For a single structured query object — use `omni_generate_query`.

    Returns:
    A short confirmation with the job ID, conversation ID and chat URL. On
    failure, an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "123e4567-e89b-12d3-a456-426614174000", "prompt": "Analyze sales trends by region for the past year"}}`
    - Follow-up turn: `{"params": {"model_id": "123e...", "prompt": "Now split it by channel", "conversation_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"}}`
    - With a callback: `{"params": {"model_id": "123e...", "prompt": "Weekly revenue recap", "webhook_url": "https://example.com/hook", "progress_webhook_enabled": true}}`

    Error Handling:
    400 covers an invalid `model_id`/`branch_id`/`conversation_id`, a missing
    prompt, a bad webhook URL, an unsupported attachment MIME type, malformed
    base64, more than 5 attachments, or a token budget overrun. 403 `Feature not
    enabled` means an admin must enable AI/the Omni Agent, and `User-scoped API
    keys cannot act on behalf of other users` means `user_id` needs an
    Organization API key. 404 means the model was not found or the topic is
    excluded by `ai_chat_topics`. 409 `Active job exists for conversation` means
    the previous turn is still running — poll or cancel it first.
    """
    try:
        body: dict[str, Any] = {"modelId": params.model_id, "prompt": params.prompt}
        if params.conversation_id is not None:
            body["conversationId"] = params.conversation_id
        if params.branch_id is not None:
            body["branchId"] = params.branch_id
        if params.topic_name is not None:
            body["topicName"] = params.topic_name
        if params.progress_webhook_enabled is not None:
            body["progressWebhookEnabled"] = params.progress_webhook_enabled
        if params.webhook_url is not None:
            body["webhookUrl"] = params.webhook_url
        if params.webhook_signing_secret is not None:
            body["webhookSigningSecret"] = params.webhook_signing_secret
        if params.webhook_metadata is not None:
            body["webhookMetadata"] = params.webhook_metadata
        if params.attachments is not None:
            body["attachments"] = params.attachments

        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id

        payload = await get_client().request_json("POST", "/v1/ai/jobs", params=query or None, json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        job_id = result.get("jobId", "unknown")
        conversation_id = result.get("conversationId", "unknown")
        chat_url = result.get("omniChatUrl")
        suffix = f" Chat: {chat_url}" if chat_url else ""
        return (
            f"Created AI job `{job_id}` in conversation `{conversation_id}`. "
            f"Poll it with `omni_get_ai_job_status`, then read the answer with `omni_get_ai_job_result`.{suffix}"
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_ai_job_status",
    annotations=ToolAnnotations(
        title="Get AI Job Status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_ai_job_status(params: GetAiJobStatusInput) -> str:
    """Get the current state, progress and result summary of an AI job.

    States are `QUEUED`, `EXECUTING`, `DELIVERING`, `COMPLETE`, `FAILED` and
    `CANCELLED`; the last three are terminal. Poll every 2-5 seconds until the
    job reaches a terminal state. `progress` is present only while `EXECUTING`,
    `resultSummary` only when `COMPLETE`, and `error` only when `FAILED`.

    When to Use:
    - To poll a job created with `omni_create_ai_job`.
    - To read the failure reason of a `FAILED` job.
    - To check a job ID found on an assistant turn of a conversation.

    When NOT to Use:
    - To read the full answer with query data (use `omni_get_ai_job_result`).
    - To poll automatically — `omni_ask_ai` does the whole loop in one call.

    Returns:
    A markdown status card (state, prompt, model, topic, timings, progress or
    error, result summary), or the raw job payload when `response_format` is
    `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"job_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - `{"params": {"job_id": "550e...", "response_format": "json"}}`

    Error Handling:
    400 means the job ID is not a UUID. 401 is a missing or invalid API key. 404
    means the job does not exist or belongs to another organization. 429 is the
    60 requests per minute per key rate limit — poll no faster than every 2
    seconds.
    """
    try:
        path = f"/v1/ai/jobs/{quote(params.job_id, safe='')}"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        state = str(body.get("state", "unknown"))
        lines = [
            f"# AI job `{body.get('id', params.job_id)}`",
            "",
            f"- State: **{state}**" + (" (terminal)" if state.upper() in TERMINAL_JOB_STATES else ""),
            f"- Prompt: {body.get('prompt') or '_not reported_'}",
            f"- Conversation: `{body.get('conversationId', 'unknown')}`",
            f"- Model: `{body.get('modelId') or 'none'}` / branch `{body.get('branchId') or 'main'}`",
            f"- Topic: `{body.get('topicName') or 'auto-selected'}`",
            f"- Created: {iso_or_na(body.get('createdAt'))}",
            f"- Execution started: {iso_or_na(body.get('executionStartedAt'))}",
            f"- Completed: {iso_or_na(body.get('completedAt'))}",
        ]
        if body.get("cancelledAt"):
            lines.append(f"- Cancelled: {iso_or_na(body.get('cancelledAt'))} by `{body.get('cancelledBy', 'unknown')}`")
        if body.get("omniChatUrl"):
            lines.append(f"- Chat: {body['omniChatUrl']}")

        progress = body.get("progress")
        if isinstance(progress, dict) and progress:
            lines.extend(
                [
                    "",
                    "## Progress",
                    "",
                    f"Iteration {progress.get('iteration', '?')} — {progress.get('message', 'no message')} "
                    f"({iso_or_na(progress.get('updatedAt'))})",
                ]
            )
        error = body.get("error")
        if isinstance(error, dict) and error:
            lines.extend(
                [
                    "",
                    "## Error",
                    "",
                    f"- Code: `{error.get('code', 'unknown')}`",
                    f"- Message: {error.get('message', 'no message')}",
                    f"- Detail: {error.get('detail') or '_none_'}",
                ]
            )
        summary = body.get("resultSummary")
        if summary:
            lines.extend(["", "## Result summary", "", str(summary), "", "_Full answer: `omni_get_ai_job_result`._"])
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_cancel_ai_job",
    annotations=ToolAnnotations(
        title="Cancel AI Job",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_cancel_ai_job(params: CancelAiJobInput) -> str:
    """Request cancellation of an AI job.

    Idempotent: calling it on an already-cancelled or completed job succeeds and
    reports the current state. `QUEUED` jobs stop immediately, `EXECUTING` jobs
    stop after finishing the current iteration, and `DELIVERING` jobs cannot be
    cancelled because they are already finalizing results.

    When to Use:
    - To stop a long-running or mistaken job, freeing the conversation for a new
      turn (a conversation allows only one active job).
    - To clear an `Active job exists for conversation` 409 from job creation.

    When NOT to Use:
    - On a job that already reached `COMPLETE` — nothing is undone; results stay
      available for 14 days.
    - To delete a conversation or its history.

    Returns:
    A short confirmation naming the job and its new state. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    400 `Job is not in a cancellable state` means the job is `DELIVERING`. 401 is
    a missing or invalid API key. 403 means permission denied — only the job
    owner or an organization admin may cancel a job. 404 means the job does not
    exist or belongs to another organization.
    """
    try:
        path = f"/v1/ai/jobs/{quote(params.job_id, safe='')}/cancel"
        payload = await get_client().request_json("POST", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        state = body.get("state", "CANCELLED")
        return f"Cancelled AI job `{body.get('jobId', params.job_id)}` (state: **{state}**)."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_ai_job_result",
    annotations=ToolAnnotations(
        title="Get AI Job Result",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_ai_job_result(params: GetAiJobResultInput) -> str:
    """Get the full result of a completed AI job — the answer plus every action taken.

    The API serves this endpoint streamed from storage — chunked transfer of a
    single JSON result object. The whole body is read and decoded here, so the
    tool returns one finished answer, never a partial stream. Results exist only
    for jobs in `COMPLETE` state and are retained for 14 days.

    When to Use:
    - After `omni_get_ai_job_status` reports `COMPLETE`.
    - When the status summary is not enough and you need the generated queries,
      row counts and CSV data behind the answer.

    When NOT to Use:
    - While the job is still `QUEUED`/`EXECUTING` — poll the status first, or
      use `omni_ask_ai` to do both.
    - For a one-line summary (`resultSummary` on the status response is cheaper).

    Returns:
    A markdown report — the final answer, then each action with its generated
    query as fenced JSON and a CSV preview of its rows — or the assembled raw
    result when `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"job_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - Large result: `{"params": {"job_id": "550e...", "timeout_seconds": 300, "response_format": "json"}}`

    Error Handling:
    400 `Invalid job ID` means the ID is not a UUID. 401 is a missing or invalid
    API key. 404 covers `Job not found`, `Job results not yet available` (the
    job has not completed — poll `omni_get_ai_job_status`) and `Result is no
    longer available` (past the 14-day retention window). A timeout means the
    stream took longer than `timeout_seconds`; raise it and retry.
    """
    try:
        path = f"/v1/ai/jobs/{quote(params.job_id, safe='')}/result"
        response = await get_client().request("GET", path, timeout=params.timeout_seconds)
        return _render_job_result(params.job_id, _decode_job_result(response), params.response_format)
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_search_omni_docs",
    annotations=ToolAnnotations(
        title="Search Product Documentation",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_search_omni_docs(params: SearchOmniDocsInput) -> str:
    """Ask the documentation a natural language question (consumes AI credits).

    Returns a synthesized answer with links to the documentation pages it was
    built from. This searches product documentation, not the organization's
    data, and consumes AI credits.

    When to Use:
    - To look up how a product feature, setting or modeling parameter works.
    - To find the documentation page backing an answer before acting on it.

    When NOT to Use:
    - To ask questions about the organization's data — use `omni_ask_ai`.
    - To search dashboards or documents (use the content/document tools).

    Returns:
    A markdown answer followed by a table of source titles and URLs, or the
    raw payload when `response_format` is `json`. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"question": "How do I create a dashboard filter?"}}`
    - `{"params": {"question": "What does query_all_views_and_fields do?", "response_format": "json"}}`

    Error Handling:
    400 means the question is missing or longer than 2000 characters. 401 is a
    missing or invalid API key. 403 means the AI agent is not enabled for the
    organization — an admin must enable it. 500 is an AI service error; retry.
    """
    try:
        payload = await get_client().request_json(
            "POST", "/v1/ai/search-omni-docs", json_body={"question": params.question}
        )
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))

        sources = _dicts(body.get("sources"))
        rows = [{"Title": source.get("title", "untitled"), "URL": source.get("url", "")} for source in sources]
        lines = [
            "# Documentation answer",
            "",
            str(body.get("answer") or "_The search returned no answer._"),
            "",
            f"## Sources ({len(rows)})",
            "",
            markdown_table(rows),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_ask_ai",
    annotations=ToolAnnotations(
        title="Ask AI (create, poll, fetch result)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_ask_ai(params: AskAiInput) -> str:
    """Ask the AI a question and wait for the finished answer (consumes AI credits).

    A convenience chain over three endpoints: create the job, poll its status
    every `poll_interval_seconds` until it reaches a terminal state
    (`COMPLETE`, `FAILED` or `CANCELLED`), until `max_wait_seconds` of wall-clock
    time has elapsed, or until the poll cap is hit — then fetch and decode the
    result. Nothing is streamed to the caller: the answer arrives complete, once.

    When to Use:
    - For a one-shot question where you want the answer, not a job handle.
    - To continue a thread synchronously by passing `conversation_id`.

    When NOT to Use:
    - For long jobs you would rather collect later, or when you want a webhook
      callback — use `omni_create_ai_job` and poll yourself.
    - For a single structured query object — use `omni_generate_query`.

    Returns:
    The rendered answer with the queries the AI ran (or the assembled raw result
    when `response_format` is `json`). If the job fails or is cancelled, a short
    explanation; if the wait runs out, the job and conversation IDs so you can
    resume with `omni_get_ai_job_status` / `omni_get_ai_job_result`. On failure,
    an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "123e4567-e89b-12d3-a456-426614174000", "prompt": "What are the top 5 products by revenue?"}}`
    - Scoped and patient: `{"params": {"model_id": "123e...", "prompt": "Break revenue down by month", "topic_name": "order_items", "poll_interval_seconds": 5, "max_wait_seconds": 300}}`

    Error Handling:
    Every error from the three underlying endpoints surfaces here: 400 for a bad
    model/branch/conversation ID or a missing prompt, 403 `Feature not enabled`
    (an admin must enable AI/the Omni Agent) or `User-scoped API keys cannot act
    on behalf of other users` (`user_id` needs an Organization API key), 404 for
    an unknown model or a topic excluded by `ai_chat_topics`, and 409 `Active
    job exists for conversation` when the previous turn is still running.
    """
    try:
        client = get_client()
        body: dict[str, Any] = {"modelId": params.model_id, "prompt": params.prompt}
        if params.conversation_id is not None:
            body["conversationId"] = params.conversation_id
        if params.branch_id is not None:
            body["branchId"] = params.branch_id
        if params.topic_name is not None:
            body["topicName"] = params.topic_name
        query: dict[str, Any] = {}
        if params.user_id:
            query["userId"] = params.user_id

        created = await client.request_json("POST", "/v1/ai/jobs", params=query or None, json_body=body)
        created_body: dict[str, Any] = created if isinstance(created, dict) else {}
        job_id = str(created_body.get("jobId") or "")
        conversation_id = str(created_body.get("conversationId") or params.conversation_id or "unknown")
        if not job_id:
            return "Error: the API accepted the AI job but returned no job id; retry, or check recent conversations."

        status_path = f"/v1/ai/jobs/{quote(job_id, safe='')}"
        status: dict[str, Any] = {}
        state = ""
        timed_out = False
        # A monotonic deadline, not accumulated sleep: a slow status request
        # spends the budget just as surely as waiting between polls does.
        deadline = _monotonic() + params.max_wait_seconds
        polls = 0
        while True:
            remaining = deadline - _monotonic()
            if remaining <= 0 or polls >= MAX_POLL_ATTEMPTS:
                timed_out = True
                break
            polls += 1
            polled = await client.request_json("GET", status_path, timeout=min(remaining, STATUS_POLL_TIMEOUT_SECONDS))
            status = polled if isinstance(polled, dict) else {}
            state = str(status.get("state") or "").upper()
            if status.get("conversationId"):
                conversation_id = str(status["conversationId"])
            if state in TERMINAL_JOB_STATES:
                break
            await _sleep(params.poll_interval_seconds)

        resume = (
            f"Resume with `omni_get_ai_job_status` / `omni_get_ai_job_result` using job `{job_id}` "
            f"(conversation `{conversation_id}`)."
        )
        if timed_out:
            reason = (
                f"after {polls} status polls" if polls >= MAX_POLL_ATTEMPTS else f"within {params.max_wait_seconds:g}s"
            )
            return (
                f"The AI job did not finish {reason} (last state: **{state or 'unknown'}**). "
                f"It is still running. {resume}"
            )
        if state == "FAILED":
            error = status.get("error")
            detail = error.get("message", "no message") if isinstance(error, dict) else "no error detail reported"
            code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
            return f"AI job `{job_id}` **FAILED** (`{code}`): {detail}. Conversation `{conversation_id}`."
        if state == "CANCELLED":
            return (
                f"AI job `{job_id}` was **CANCELLED** before it produced an answer. Conversation `{conversation_id}`."
            )

        response = await client.request("GET", f"{status_path}/result", timeout=RESULT_TIMEOUT_SECONDS)
        result = _decode_job_result(response)
        rendered = _render_job_result(job_id, result, params.response_format)
        if params.response_format is ResponseFormat.JSON:
            return rendered
        return truncate_result(f"{rendered}\n_Conversation `{conversation_id}` — pass it back to ask a follow-up._\n")
    except Exception as exc:
        return handle_api_error(exc)
