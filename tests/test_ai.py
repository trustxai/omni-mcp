"""Unit tests for the AI tools against a fake client (deterministic, offline)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.ai import (
    MAX_POLL_ATTEMPTS,
    AskAiInput,
    CancelAiJobInput,
    CreateAiJobInput,
    GenerateQueryInput,
    GetAiBrandingInput,
    GetAiConversationInput,
    GetAiJobResultInput,
    GetAiJobStatusInput,
    ListAiConversationsInput,
    PickTopicInput,
    SearchOmniDocsInput,
    omni_ask_ai,
    omni_cancel_ai_job,
    omni_create_ai_job,
    omni_generate_query,
    omni_get_ai_branding,
    omni_get_ai_conversation,
    omni_get_ai_job_result,
    omni_get_ai_job_status,
    omni_list_ai_conversations,
    omni_pick_topic,
    omni_search_omni_docs,
)

MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"
JOB_ID = "550e8400-e29b-41d4-a716-446655440000"
CONVERSATION_ID = "660e8400-e29b-41d4-a716-446655440001"

BRANDING: dict[str, Any] = {
    "name": "DataBot",
    "logoUrl": "https://example.com/custom-logo.png",
    "headline": "Welcome to DataBot",
    "body": "Your intelligent data assistant",
    "promptPlaceholder": "What would you like to know?",
}

GENERATED_QUERY: dict[str, Any] = {
    "topic": "order_items",
    "baseView": None,
    "query": {
        "model_job": {
            "model_id": MODEL_ID,
            "table": "order_items",
            "fields": ["products.item_name", "order_items.total_sale_price"],
            "limit": 10,
        }
    },
}

CONVERSATIONS: dict[str, Any] = {
    "data": [
        {
            "id": CONVERSATION_ID,
            "name": "Q1 Revenue Analysis",
            "lastUserPrompt": "Show me revenue trends by region for Q1",
            "updatedAt": "2026-05-15T14:32:10Z",
            "createdAt": "2026-05-15T09:15:30Z",
        }
    ],
    "pageInfo": {"hasNextPage": True, "nextCursor": "cursor-2", "pageSize": 50, "totalRecords": 2},
}

CONVERSATION: dict[str, Any] = {
    "id": CONVERSATION_ID,
    "userId": "990e8400-e29b-41d4-a716-446655440004",
    "organizationId": "880e8400-e29b-41d4-a716-446655440003",
    "createdAt": "2025-01-15T10:00:00.000Z",
    "updatedAt": "2025-01-15T10:05:00.000Z",
    "messages": [
        {"role": "user", "content": "What are the top 5 products by revenue?", "timestamp": "2025-01-15T10:00:00.000Z"},
        {
            "role": "assistant",
            "content": "### Top 5 Products by Revenue",
            "timestamp": "2025-01-15T10:01:30.000Z",
            "jobId": JOB_ID,
            "omniChatUrl": "https://acme.omni.co/chat/660e8400",
        },
    ],
}

CREATED_JOB: dict[str, Any] = {
    "jobId": JOB_ID,
    "conversationId": CONVERSATION_ID,
    "omniChatUrl": "https://acme.omni.co/chat/660e8400",
}

JOB_EXECUTING: dict[str, Any] = {
    "id": JOB_ID,
    "state": "EXECUTING",
    "prompt": "What are the top 5 products by revenue?",
    "conversationId": CONVERSATION_ID,
    "modelId": MODEL_ID,
    "topicName": "order_items",
    "progress": {
        "iteration": 2,
        "message": "Running query: Top products by revenue",
        "updatedAt": "2025-01-15T10:00:08.000Z",
    },
    "createdAt": "2025-01-15T10:00:00.000Z",
}

JOB_COMPLETE: dict[str, Any] = {
    "id": JOB_ID,
    "state": "COMPLETE",
    "prompt": "What are the top 5 products by revenue?",
    "conversationId": CONVERSATION_ID,
    "modelId": MODEL_ID,
    "topicName": "order_items",
    "resultSummary": "### Top 5 Products by Revenue",
    "createdAt": "2025-01-15T10:00:00.000Z",
    "completedAt": "2025-01-15T10:01:30.000Z",
}

JOB_RESULT: dict[str, Any] = {
    "topic": "order_items",
    "omniChatUrl": "https://acme.omni.co/chat/660e8400",
    "message": "### Top 5 Products by Revenue\n\n1. **Sunglasses** - $678,994",
    "resultSummary": "### Top 5 Products by Revenue",
    "actions": [
        {
            "type": "generate_query",
            "message": "I'll generate a query to find the top 5 products by total revenue.",
            "timestamp": "2025-01-15T10:00:10.000Z",
            "result": {
                "resultId": "928c5838-000d-4943-b305-f6242c1b4922",
                "queryName": "Top 5 Products by Revenue",
                "status": "success",
                "query": {"model_job": {"table": "order_items"}},
                "csvResult": 'Name,Total Revenue\nRay-Ban Sunglasses,"678,994.41"',
                "csvResultWasTruncated": False,
                "hasResults": True,
                "totalRowCount": 5,
            },
        }
    ],
}

# One event per blank-line-delimited block; the second full object supersedes the
# first, and the final `message` arrives as a multi-line `data:` event.
SSE_BODY = "\n".join(
    [
        ": heartbeat",
        "",
        "event: progress",
        'data: {"actions": [{"type": "generate_query", "message": "Generating the query",',
        'data:  "timestamp": "2025-01-15T10:00:10.000Z"}]}',
        "",
        "event: result",
        'data: {"topic": "order_items", "actions": [',
        'data: {"type": "generate_query", "message": "Generating the query", "timestamp": "2025-01-15T10:00:10.000Z"},',
        'data: {"type": "summarize", "message": "Summarizing", "timestamp": "2025-01-15T10:00:20.000Z"}],',
        'data: "message": "### Top 5 Products by Revenue"}',
        "",
        "data: [DONE]",
        "",
    ]
)

DOCS_ANSWER: dict[str, Any] = {
    "answer": 'To create a dashboard filter, click the "Add Filter" button...',
    "sources": [{"title": "Dashboard Filters", "url": "https://docs.example.com/visualize-present/dashboards/filters"}],
}


class _FakeResponse:
    """Minimal stand-in for `httpx.Response` on the streamed result endpoint."""

    def __init__(self, text: str, content_type: str = "application/json", status_code: int = 200) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"content-type": content_type}
        self.status_code = status_code


class _FakeClient:
    """Records `(method, path, kwargs)` calls; returns canned payloads/responses.

    `payloads` is a queue for tools that make several JSON calls; the last entry
    repeats once the queue is drained, so a poll loop can spin on one state.
    """

    def __init__(
        self,
        payload: Any = None,
        exc: Exception | None = None,
        payloads: list[Any] | None = None,
        response: _FakeResponse | None = None,
    ) -> None:
        self._payload = payload if payload is not None else {}
        self._payloads = list(payloads) if payloads else None
        self._exc = exc
        self._response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        if self._payloads is not None:
            return self._payloads.pop(0) if len(self._payloads) > 1 else self._payloads[0]
        return self._payload

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        assert self._response is not None, "this fake was not given a response"
        return self._response


class _Clock:
    """Fake monotonic clock: every reading advances it by `step` seconds."""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def _patch_clock(monkeypatch: pytest.MonkeyPatch, step: float = 0.0) -> list[float]:
    """Freeze (or advance) the poll clock and record the sleeps. Returns the sleeps."""
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("omni_mcp.tools.ai._sleep", _record)
    monkeypatch.setattr("omni_mcp.tools.ai._monotonic", _Clock(step))
    return slept


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> _FakeClient:
    monkeypatch.setattr("omni_mcp.tools.ai.get_client", lambda: fake)
    return fake


def _status_error(status: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/ai/branding")
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# ---------------------------------------------------------------- branding


async def test_get_ai_branding_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=BRANDING))

    result = await omni_get_ai_branding(GetAiBrandingInput())

    assert "DataBot" in result
    assert "https://example.com/custom-logo.png" in result
    assert "What would you like to know?" in result
    assert fake.calls == [("GET", "/v1/ai/branding", {})]


async def test_get_ai_branding_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=BRANDING))

    payload = json.loads(await omni_get_ai_branding(GetAiBrandingInput(response_format=ResponseFormat.JSON)))

    assert payload["name"] == "DataBot"


async def test_get_ai_branding_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload={"logoUrl": None}))

    result = await omni_get_ai_branding(GetAiBrandingInput())

    assert "Omni Agent" in result
    assert "none configured" in result


async def test_get_ai_branding_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(403, "Insufficient permissions")))

    result = await omni_get_ai_branding(GetAiBrandingInput())

    assert result.startswith("Error (403):")
    assert "Insufficient permissions" in result


# ---------------------------------------------------------- generate query


async def test_generate_query_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GENERATED_QUERY))

    result = await omni_generate_query(
        GenerateQueryInput(
            model_id=MODEL_ID, prompt="Top 10 products", current_topic_name="order_items", run_query=False
        )
    )

    assert "order_items" in result
    assert "```json" in result
    assert "omni_run_query" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/ai/generate-query",
            {
                "json_body": {
                    "modelId": MODEL_ID,
                    "prompt": "Top 10 products",
                    "currentTopicName": "order_items",
                    "runQuery": False,
                }
            },
        )
    ]


async def test_generate_query_omits_none_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GENERATED_QUERY))

    await omni_generate_query(GenerateQueryInput(model_id=MODEL_ID, prompt="Revenue"))

    assert fake.calls[0][2]["json_body"] == {"modelId": MODEL_ID, "prompt": "Revenue"}


async def test_generate_query_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=GENERATED_QUERY))

    payload = json.loads(
        await omni_generate_query(
            GenerateQueryInput(model_id=MODEL_ID, prompt="Revenue", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["query"]["model_job"]["table"] == "order_items"


async def test_generate_query_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(403, "Feature not enabled")))

    result = await omni_generate_query(GenerateQueryInput(model_id=MODEL_ID, prompt="Revenue"))

    assert result.startswith("Error (403):")
    assert "Feature not enabled" in result


# -------------------------------------------------------------- pick topic


async def test_pick_topic_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"topicId": "order_items"}))

    result = await omni_pick_topic(
        PickTopicInput(
            prompt="How many orders were placed last month?",
            model_id=MODEL_ID,
            potential_topic_names=["order_items", "customers"],
            user_id="990e8400-e29b-41d4-a716-446655440004",
        )
    )

    assert "order_items" in result
    assert "omni_generate_query" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/ai/pick-topic",
            {
                "json_body": {
                    "prompt": "How many orders were placed last month?",
                    "modelId": MODEL_ID,
                    "potentialTopicNames": ["order_items", "customers"],
                    "userId": "990e8400-e29b-41d4-a716-446655440004",
                }
            },
        )
    ]


async def test_pick_topic_without_a_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload={}))

    result = await omni_pick_topic(PickTopicInput(prompt="???", model_id=MODEL_ID))

    assert "no topic" in result


async def test_pick_topic_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(404, "Model not found")))

    result = await omni_pick_topic(PickTopicInput(prompt="Revenue", model_id=MODEL_ID))

    assert result.startswith("Error (404):")


# ----------------------------------------------------------- conversations


async def test_list_ai_conversations_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=CONVERSATIONS))

    result = await omni_list_ai_conversations(
        ListAiConversationsInput(page_size=20, cursor="cursor-1", user_id="user-9")
    )

    assert "Q1 Revenue Analysis" in result
    assert "cursor-2" in result
    assert fake.calls == [
        ("GET", "/v1/ai/conversations", {"params": {"pageSize": 20, "userId": "user-9", "cursor": "cursor-1"}})
    ]


async def test_list_ai_conversations_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=CONVERSATIONS))

    payload = json.loads(
        await omni_list_ai_conversations(ListAiConversationsInput(response_format=ResponseFormat.JSON))
    )

    assert payload["items"][0]["id"] == CONVERSATION_ID
    assert payload["nextCursor"] == "cursor-2"
    assert fake.calls == [("GET", "/v1/ai/conversations", {"params": {"pageSize": 50}})]


async def test_get_ai_conversation_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=CONVERSATION))

    result = await omni_get_ai_conversation(GetAiConversationInput(conversation_id=CONVERSATION_ID))

    assert "Top 5 Products by Revenue" in result
    assert "assistant" in result
    assert JOB_ID in result
    assert fake.calls == [("GET", f"/v1/ai/conversations/{CONVERSATION_ID}", {})]


async def test_get_ai_conversation_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=CONVERSATION))

    payload = json.loads(
        await omni_get_ai_conversation(
            GetAiConversationInput(conversation_id=CONVERSATION_ID, response_format=ResponseFormat.JSON)
        )
    )

    assert len(payload["messages"]) == 2


async def test_get_ai_conversation_quotes_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=CONVERSATION))

    await omni_get_ai_conversation(GetAiConversationInput(conversation_id="a/b c"))

    assert fake.calls == [("GET", "/v1/ai/conversations/a%2Fb%20c", {})]


# ------------------------------------------------------------------- jobs


async def test_create_ai_job_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=CREATED_JOB))

    result = await omni_create_ai_job(
        CreateAiJobInput(
            model_id=MODEL_ID,
            prompt="Analyze sales trends",
            topic_name="order_items",
            user_id="user-9",
            webhook_url="https://example.com/hook",
            progress_webhook_enabled=True,
            webhook_metadata={"slack_channel": "C123456"},
            attachments=[{"data": "iVBORw0KGgo=", "mimeType": "image/png", "name": "dash.png"}],
        )
    )

    assert JOB_ID in result
    assert CONVERSATION_ID in result
    assert "omni_get_ai_job_status" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/ai/jobs",
            {
                "params": {"userId": "user-9"},
                "json_body": {
                    "modelId": MODEL_ID,
                    "prompt": "Analyze sales trends",
                    "topicName": "order_items",
                    "progressWebhookEnabled": True,
                    "webhookUrl": "https://example.com/hook",
                    "webhookMetadata": {"slack_channel": "C123456"},
                    "attachments": [{"data": "iVBORw0KGgo=", "mimeType": "image/png", "name": "dash.png"}],
                },
            },
        )
    ]


async def test_create_ai_job_minimal_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=CREATED_JOB))

    await omni_create_ai_job(CreateAiJobInput(model_id=MODEL_ID, prompt="Revenue"))

    assert fake.calls == [
        ("POST", "/v1/ai/jobs", {"params": None, "json_body": {"modelId": MODEL_ID, "prompt": "Revenue"}})
    ]


async def test_create_ai_job_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(409, "Active job exists for conversation")))

    result = await omni_create_ai_job(CreateAiJobInput(model_id=MODEL_ID, prompt="Revenue"))

    assert result.startswith("Error (409):")
    assert "Active job exists" in result


async def test_get_ai_job_status_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=JOB_EXECUTING))

    result = await omni_get_ai_job_status(GetAiJobStatusInput(job_id=JOB_ID))

    assert "EXECUTING" in result
    assert "Running query: Top products by revenue" in result
    assert fake.calls == [("GET", f"/v1/ai/jobs/{JOB_ID}", {})]


async def test_get_ai_job_status_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = dict(JOB_COMPLETE, state="FAILED", error={"code": "QUERY_EXECUTION_ERROR", "message": "Column not found"})
    failed.pop("resultSummary")
    _patch(monkeypatch, _FakeClient(payload=failed))

    result = await omni_get_ai_job_status(GetAiJobStatusInput(job_id=JOB_ID))

    assert "FAILED" in result
    assert "QUERY_EXECUTION_ERROR" in result
    assert "terminal" in result


async def test_get_ai_job_status_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=JOB_COMPLETE))

    payload = json.loads(
        await omni_get_ai_job_status(GetAiJobStatusInput(job_id=JOB_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["state"] == "COMPLETE"


async def test_get_ai_job_status_quotes_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=JOB_COMPLETE))

    await omni_get_ai_job_status(GetAiJobStatusInput(job_id="job/1"))

    assert fake.calls == [("GET", "/v1/ai/jobs/job%2F1", {})]


async def test_get_ai_job_status_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(404, "Job not found")))

    result = await omni_get_ai_job_status(GetAiJobStatusInput(job_id=JOB_ID))

    assert result.startswith("Error (404):")


async def test_cancel_ai_job_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"jobId": JOB_ID, "state": "CANCELLED"}))

    result = await omni_cancel_ai_job(CancelAiJobInput(job_id=JOB_ID))

    assert result == f"Cancelled AI job `{JOB_ID}` (state: **CANCELLED**)."
    assert fake.calls == [("POST", f"/v1/ai/jobs/{JOB_ID}/cancel", {})]


async def test_cancel_ai_job_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(400, "Job is not in a cancellable state")))

    result = await omni_cancel_ai_job(CancelAiJobInput(job_id=JOB_ID))

    assert result.startswith("Error (400):")
    assert "cancellable" in result


# ------------------------------------------------------------ job results


async def test_get_ai_job_result_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(response=_FakeResponse(json.dumps(JOB_RESULT))))

    result = await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID))

    assert "Top 5 Products by Revenue" in result
    assert "generate_query" in result
    assert "```csv" in result
    assert "Ray-Ban Sunglasses" in result
    assert fake.calls == [("GET", f"/v1/ai/jobs/{JOB_ID}/result", {"timeout": 120.0})]


async def test_get_ai_job_result_parses_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    """A multi-line `data:` event is joined with newlines and decoded as one object."""
    fake = _patch(
        monkeypatch, _FakeClient(response=_FakeResponse(SSE_BODY, content_type="text/event-stream; charset=utf-8"))
    )

    result = await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID, timeout_seconds=300))

    assert "### Top 5 Products by Revenue" in result
    assert "generate_query" in result
    assert "summarize" in result
    assert "order_items" in result
    assert "Actions (2)" in result
    assert fake.calls == [("GET", f"/v1/ai/jobs/{JOB_ID}/result", {"timeout": 300.0})]


async def test_get_ai_job_result_last_full_sse_object_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial events never mutate the answer; the last complete object is authoritative."""
    body = "\n".join(
        [
            'data: {"message": "partial draft", "actions": []}',
            "",
            'data: {"topic": "ignored — not a full result object"}',
            "",
            'data: {"message": "final answer", "actions": [], "topic": "order_items"}',
            "",
            "data: [DONE]",
            "",
            'data: {"message": "sent after DONE and never read"}',
            "",
        ]
    )
    _patch(monkeypatch, _FakeClient(response=_FakeResponse(body, content_type="text/event-stream")))

    payload = json.loads(
        await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["result"] == {"message": "final answer", "actions": [], "topic": "order_items"}


async def test_get_ai_job_result_sse_text_only_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no complete object, raw fragments are concatenated in arrival order."""
    body = "\n".join(["data: Hello ", "", "data: world", "", "data: [DONE]", ""])
    _patch(monkeypatch, _FakeClient(response=_FakeResponse(body, content_type="text/event-stream")))

    payload = json.loads(
        await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["result"]["message"] == "Hello world"


async def test_get_ai_job_result_sse_keeps_fragments_beside_an_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-JSON fragments are appended to a complete object's message, not dropped."""
    body = "\n".join(['data: {"message": "The answer.", "actions": []}', "", "data:  Addendum.", "", "data: [DONE]"])
    _patch(monkeypatch, _FakeClient(response=_FakeResponse(body, content_type="text/event-stream")))

    payload = json.loads(
        await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["result"]["message"] == "The answer. Addendum."


async def test_get_ai_job_result_ignores_sse_when_the_body_is_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """`application/json` never goes through the SSE fallback, `data:` text or not."""
    _patch(monkeypatch, _FakeClient(response=_FakeResponse('{"message": "data: not an event"}')))

    payload = json.loads(
        await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["result"] == {"message": "data: not an event"}


async def test_get_ai_job_result_renders_a_json_array_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(response=_FakeResponse('[{"a": 1}]')))

    payload = json.loads(
        await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID, response_format=ResponseFormat.JSON))
    )

    assert json.loads(payload["result"]["message"]) == [{"a": 1}]


async def test_get_ai_job_result_handles_plain_text_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(response=_FakeResponse("just prose", content_type="text/plain")))

    result = await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID))

    assert "just prose" in result


async def test_get_ai_job_result_handles_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(response=_FakeResponse("")))

    result = await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID))

    assert "may not have completed yet" in result


async def test_get_ai_job_result_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(response=_FakeResponse(json.dumps(JOB_RESULT))))

    payload = json.loads(
        await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["jobId"] == JOB_ID
    assert payload["result"]["actions"][0]["result"]["totalRowCount"] == 5


async def test_get_ai_job_result_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(404, "Job results not yet available")))

    result = await omni_get_ai_job_result(GetAiJobResultInput(job_id=JOB_ID))

    assert result.startswith("Error (404):")
    assert "not yet available" in result


# ------------------------------------------------------------- docs search


async def test_search_omni_docs_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=DOCS_ANSWER))

    result = await omni_search_omni_docs(SearchOmniDocsInput(question="How do I create a dashboard filter?"))

    assert "Add Filter" in result
    assert "Dashboard Filters" in result
    assert "https://docs.example.com/visualize-present/dashboards/filters" in result
    assert "Snippet" not in result
    assert fake.calls == [
        ("POST", "/v1/ai/search-omni-docs", {"json_body": {"question": "How do I create a dashboard filter?"}})
    ]


async def test_search_omni_docs_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=DOCS_ANSWER))

    payload = json.loads(
        await omni_search_omni_docs(SearchOmniDocsInput(question="Filters?", response_format=ResponseFormat.JSON))
    )

    assert payload["sources"][0]["title"] == "Dashboard Filters"


# ----------------------------------------------------------------- ask_ai


async def test_ask_ai_polls_twice_then_returns_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(
        monkeypatch,
        _FakeClient(
            payloads=[CREATED_JOB, JOB_EXECUTING, JOB_COMPLETE],
            response=_FakeResponse(json.dumps(JOB_RESULT)),
        ),
    )
    slept = _patch_clock(monkeypatch)

    result = await omni_ask_ai(AskAiInput(model_id=MODEL_ID, prompt="Top 5 products", topic_name="order_items"))

    assert "Top 5 Products by Revenue" in result
    assert CONVERSATION_ID in result
    assert slept == [2.0]
    assert fake.calls == [
        (
            "POST",
            "/v1/ai/jobs",
            {
                "params": None,
                "json_body": {"modelId": MODEL_ID, "prompt": "Top 5 products", "topicName": "order_items"},
            },
        ),
        ("GET", f"/v1/ai/jobs/{JOB_ID}", {"timeout": 30.0}),
        ("GET", f"/v1/ai/jobs/{JOB_ID}", {"timeout": 30.0}),
        ("GET", f"/v1/ai/jobs/{JOB_ID}/result", {"timeout": 120.0}),
    ]


async def test_ask_ai_times_out_on_the_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clock advancing 50s per poll exhausts a 120s budget in two polls, not sixty."""
    fake = _patch(monkeypatch, _FakeClient(payloads=[CREATED_JOB, JOB_EXECUTING]))
    slept = _patch_clock(monkeypatch, step=50.0)

    result = await omni_ask_ai(
        AskAiInput(model_id=MODEL_ID, prompt="Top 5 products", poll_interval_seconds=2, max_wait_seconds=120)
    )

    assert "did not finish within 120s" in result
    assert JOB_ID in result
    assert CONVERSATION_ID in result
    assert "omni_get_ai_job_result" in result
    assert slept == [2.0, 2.0]
    # One create + two status polls; the second is clamped to the 20s left.
    assert len(fake.calls) == 3
    assert fake.calls[1] == ("GET", f"/v1/ai/jobs/{JOB_ID}", {"timeout": 30.0})
    assert fake.calls[2] == ("GET", f"/v1/ai/jobs/{JOB_ID}", {"timeout": 20.0})


async def test_ask_ai_stops_at_the_poll_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with a stopped clock and a long budget, the request count is capped."""
    fake = _patch(monkeypatch, _FakeClient(payloads=[CREATED_JOB, JOB_EXECUTING]))
    slept = _patch_clock(monkeypatch)

    result = await omni_ask_ai(
        AskAiInput(model_id=MODEL_ID, prompt="Top 5 products", poll_interval_seconds=2, max_wait_seconds=900)
    )

    assert f"did not finish after {MAX_POLL_ATTEMPTS} status polls" in result
    assert len(fake.calls) == MAX_POLL_ATTEMPTS + 1
    assert len(slept) == MAX_POLL_ATTEMPTS


async def test_ask_ai_reports_a_failed_job(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = dict(JOB_COMPLETE, state="FAILED", error={"code": "QUERY_EXECUTION_ERROR", "message": "Column not found"})
    _patch(monkeypatch, _FakeClient(payloads=[CREATED_JOB, failed]))

    result = await omni_ask_ai(AskAiInput(model_id=MODEL_ID, prompt="Top 5 products"))

    assert "FAILED" in result
    assert "QUERY_EXECUTION_ERROR" in result
    assert "Column not found" in result


async def test_ask_ai_reports_a_cancelled_job(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payloads=[CREATED_JOB, dict(JOB_COMPLETE, state="CANCELLED")]))

    result = await omni_ask_ai(AskAiInput(model_id=MODEL_ID, prompt="Top 5 products"))

    assert "CANCELLED" in result


async def test_ask_ai_without_a_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payloads=[{}]))

    result = await omni_ask_ai(AskAiInput(model_id=MODEL_ID, prompt="Top 5 products"))

    assert result.startswith("Error:")
    assert "no job id" in result


async def test_ask_ai_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        _FakeClient(payloads=[CREATED_JOB, JOB_COMPLETE], response=_FakeResponse(json.dumps(JOB_RESULT))),
    )

    payload = json.loads(
        await omni_ask_ai(AskAiInput(model_id=MODEL_ID, prompt="Top 5 products", response_format=ResponseFormat.JSON))
    )

    assert payload["jobId"] == JOB_ID
    assert payload["result"]["topic"] == "order_items"


async def test_ask_ai_passes_impersonation_and_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(
        monkeypatch,
        _FakeClient(payloads=[CREATED_JOB, JOB_COMPLETE], response=_FakeResponse(json.dumps(JOB_RESULT))),
    )

    await omni_ask_ai(
        AskAiInput(
            model_id=MODEL_ID,
            prompt="Top 5 products",
            conversation_id=CONVERSATION_ID,
            branch_id="branch-1",
            user_id="user-9",
        )
    )

    assert fake.calls[0] == (
        "POST",
        "/v1/ai/jobs",
        {
            "params": {"userId": "user-9"},
            "json_body": {
                "modelId": MODEL_ID,
                "prompt": "Top 5 products",
                "conversationId": CONVERSATION_ID,
                "branchId": "branch-1",
            },
        },
    )


async def test_ask_ai_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(403, "Feature not enabled")))

    result = await omni_ask_ai(AskAiInput(model_id=MODEL_ID, prompt="Top 5 products"))

    assert result.startswith("Error (403):")


# ------------------------------------------------------------- validation


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        GetAiBrandingInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        CancelAiJobInput(job_id=JOB_ID, unexpected="x")  # type: ignore[call-arg]


def test_page_size_bounds() -> None:
    with pytest.raises(ValueError):
        ListAiConversationsInput(page_size=0)
    with pytest.raises(ValueError):
        ListAiConversationsInput(page_size=101)
    assert ListAiConversationsInput(page_size=100).page_size == 100


def test_question_length_bounds() -> None:
    with pytest.raises(ValueError):
        SearchOmniDocsInput(question="")
    with pytest.raises(ValueError):
        SearchOmniDocsInput(question="x" * 2001)
    assert SearchOmniDocsInput(question="x" * 2000).question


def test_attachment_count_bound() -> None:
    attachment = {"data": "AA==", "mimeType": "image/png"}
    with pytest.raises(ValueError):
        CreateAiJobInput(model_id=MODEL_ID, prompt="p", attachments=[attachment] * 6)
    assert CreateAiJobInput(model_id=MODEL_ID, prompt="p", attachments=[attachment] * 5).attachments


def test_ask_ai_poll_bounds() -> None:
    with pytest.raises(ValueError):
        AskAiInput(model_id=MODEL_ID, prompt="p", poll_interval_seconds=1)
    with pytest.raises(ValueError):
        AskAiInput(model_id=MODEL_ID, prompt="p", poll_interval_seconds=31)
    assert AskAiInput(model_id=MODEL_ID, prompt="p", poll_interval_seconds=2).poll_interval_seconds == 2
    with pytest.raises(ValueError):
        AskAiInput(model_id=MODEL_ID, prompt="p", max_wait_seconds=1)
    with pytest.raises(ValueError):
        AskAiInput(model_id=MODEL_ID, prompt="p", max_wait_seconds=901)
    assert AskAiInput(model_id=MODEL_ID, prompt="p", max_wait_seconds=900).max_wait_seconds == 900


def test_job_result_timeout_bounds() -> None:
    with pytest.raises(ValueError):
        GetAiJobResultInput(job_id=JOB_ID, timeout_seconds=1)
    with pytest.raises(ValueError):
        GetAiJobResultInput(job_id=JOB_ID, timeout_seconds=601)
    assert GetAiJobResultInput(job_id=JOB_ID, timeout_seconds=600).timeout_seconds == 600
