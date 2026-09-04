"""Unit tests for the AI Routines / AI Eval tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.ai_routines_evals import (
    ArchiveEvalPromptSetInput,
    ArchiveEvalRunInput,
    CancelEvalRunInput,
    CreateAiRoutineInput,
    CreateEvalPromptSetInput,
    DeleteAiRoutineInput,
    EvalPromptCreateInput,
    EvalPromptUpdateInput,
    GetAiRoutineInput,
    GetEvalPromptSetInput,
    GetEvalRunInput,
    ListAiRoutinesInput,
    ListEvalPromptSetsInput,
    ListEvalRunsInput,
    RestoreEvalPromptSetInput,
    RestoreEvalRunInput,
    StartEvalRunInput,
    TriggerAiRoutineInput,
    UpdateAiRoutineInput,
    UpdateEvalPromptSetInput,
    omni_archive_eval_prompt_set,
    omni_archive_eval_run,
    omni_cancel_eval_run,
    omni_create_ai_routine,
    omni_create_eval_prompt_set,
    omni_delete_ai_routine,
    omni_get_ai_routine,
    omni_get_eval_prompt_set,
    omni_get_eval_run,
    omni_list_ai_routines,
    omni_list_eval_prompt_sets,
    omni_list_eval_runs,
    omni_restore_eval_prompt_set,
    omni_restore_eval_run,
    omni_start_eval_run,
    omni_trigger_ai_routine,
    omni_update_ai_routine,
    omni_update_eval_prompt_set,
)


class _FakeClient:
    """Records `(method, path, kwargs)` calls; returns a canned payload."""

    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {}
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return self._payload


def _http_error(status: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/x")
    response = httpx.Response(status, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


ROUTINE: dict[str, Any] = {
    "id": "880e8400-e29b-41d4-a716-446655440003",
    "name": "Weekly sales",
    "modelId": "aaa0e8400-e29b-41d4-a716-446655440003",
    "branchId": None,
    "prompt": "Summarize this week's sales",
    "schedule": "0 9 * * 1 ?",
    "timezone": "America/Los_Angeles",
    "destination": {"type": "email", "recipientEmails": ["a@example.com"], "userGroupIds": []},
    "disabled": False,
    "systemDisabled": False,
    "systemDisabledReason": None,
    "recipientCount": 1,
    "topicName": None,
    "description": None,
    "lastRun": {"completedAt": "2025-01-15T10:05:00.000Z", "label": "Delivered", "state": "COMPLETE"},
    "createdAt": "2025-01-15T10:00:00.000Z",
    "updatedAt": "2025-01-15T10:00:00.000Z",
}


# --------------------------------------------------------------------------
# AI Routines
# --------------------------------------------------------------------------


async def test_list_ai_routines_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"records": [ROUTINE], "pageInfo": {"hasNextPage": False, "totalRecords": 1}})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_ai_routines(ListAiRoutinesInput())

    assert "Weekly sales" in result
    assert fake.calls == [("GET", "/v1/ai/routines", {"params": {"pageSize": 20, "sortDirection": "desc"}})]


async def test_list_ai_routines_passes_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"records": [], "pageInfo": {}})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    await omni_list_ai_routines(
        ListAiRoutinesInput(cursor="abc", sort_field="name", user_id="user-1", page_size=5, sort_direction="asc")
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/ai/routines",
            {
                "params": {
                    "pageSize": 5,
                    "sortDirection": "asc",
                    "cursor": "abc",
                    "sortField": "name",
                    "userId": "user-1",
                }
            },
        )
    ]


async def test_list_ai_routines_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"records": [ROUTINE], "pageInfo": {"totalRecords": 1}})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_ai_routines(ListAiRoutinesInput(response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["items"][0]["id"] == ROUTINE["id"]


async def test_list_ai_routines_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Forbidden"))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_ai_routines(ListAiRoutinesInput())

    assert result.startswith("Error (403):")


async def test_get_ai_routine_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ROUTINE)
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_ai_routine(GetAiRoutineInput(routine_id=ROUTINE["id"]))

    assert "Weekly sales" in result
    assert fake.calls == [("GET", f"/v1/ai/routines/{ROUTINE['id']}", {"params": None})]


async def test_get_ai_routine_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ROUTINE)
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_ai_routine(GetAiRoutineInput(routine_id=ROUTINE["id"], response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["id"] == ROUTINE["id"]


async def test_get_ai_routine_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Routine not found"))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_ai_routine(GetAiRoutineInput(routine_id="missing"))

    assert result.startswith("Error (404):")


async def test_create_ai_routine_email(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "new-id"})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_create_ai_routine(
        CreateAiRoutineInput(
            name="Weekly sales",
            model_id="model-1",
            prompt="Summarize sales",
            schedule="0 9 * * 1 ?",
            timezone="UTC",
            destination_type="email",
            recipient_emails=["a@example.com"],
        )
    )

    assert "Weekly sales" in result
    assert "new-id" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/ai/routines",
            {
                "params": None,
                "json_body": {
                    "name": "Weekly sales",
                    "modelId": "model-1",
                    "prompt": "Summarize sales",
                    "schedule": "0 9 * * 1 ?",
                    "timezone": "UTC",
                    "destination": {"type": "email", "recipientEmails": ["a@example.com"]},
                },
            },
        )
    ]


async def test_create_ai_routine_slack_requires_recipient() -> None:
    with pytest.raises(ValueError):
        CreateAiRoutineInput(
            name="x",
            model_id="model-1",
            prompt="p",
            schedule="0 9 * * 1 ?",
            timezone="UTC",
            destination_type="slack",
        )


async def test_create_ai_routine_body_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "new-id"})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    raw = {
        "name": "raw",
        "modelId": "m",
        "prompt": "p",
        "schedule": "s",
        "timezone": "UTC",
        "destination": {"type": "email"},
    }
    await omni_create_ai_routine(
        CreateAiRoutineInput(
            name="Weekly sales",
            model_id="model-1",
            prompt="p",
            schedule="s",
            timezone="UTC",
            destination_type="email",
            body=raw,
        )
    )

    assert fake.calls == [("POST", "/v1/ai/routines", {"params": None, "json_body": raw})]


async def test_create_ai_routine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Invalid schedule"))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_create_ai_routine(
        CreateAiRoutineInput(
            name="x",
            model_id="m",
            prompt="p",
            schedule="bad",
            timezone="UTC",
            destination_type="email",
        )
    )

    assert result.startswith("Error (400):")


async def test_update_ai_routine(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"name": "Weekly sales v2"})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_update_ai_routine(
        UpdateAiRoutineInput(
            routine_id=ROUTINE["id"],
            name="Weekly sales v2",
            prompt="Summarize sales v2",
            schedule="0 9 * * 1 ?",
            is_active=False,
        )
    )

    assert "Weekly sales v2" in result
    assert fake.calls == [
        (
            "PUT",
            f"/v1/ai/routines/{ROUTINE['id']}",
            {
                "params": None,
                "json_body": {
                    "name": "Weekly sales v2",
                    "prompt": "Summarize sales v2",
                    "schedule": "0 9 * * 1 ?",
                    "isActive": False,
                },
            },
        )
    ]


async def test_update_ai_routine_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Insufficient permissions to update this routine"))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_update_ai_routine(
        UpdateAiRoutineInput(routine_id=ROUTINE["id"], name="n", prompt="p", schedule="s")
    )

    assert result.startswith("Error (403):")


async def test_delete_ai_routine(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"deleted": True, "id": ROUTINE["id"]})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_delete_ai_routine(DeleteAiRoutineInput(routine_id=ROUTINE["id"]))

    assert "Deleted AI Routine" in result
    assert ROUTINE["id"] in result
    assert fake.calls == [("DELETE", f"/v1/ai/routines/{ROUTINE['id']}", {"params": None})]


async def test_delete_ai_routine_error_409_is_not_documented_but_404_is(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Routine not found or has already been deleted."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_delete_ai_routine(DeleteAiRoutineInput(routine_id="gone"))

    assert result.startswith("Error (404):")


async def test_trigger_ai_routine(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "run-job-1"})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_trigger_ai_routine(TriggerAiRoutineInput(routine_id=ROUTINE["id"]))

    assert "run-job-1" in result
    assert "AI credits" in result
    assert fake.calls == [("POST", f"/v1/ai/routines/{ROUTINE['id']}/trigger", {"params": None})]


async def test_trigger_ai_routine_error_409(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(409, "A run is already in progress for this routine."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_trigger_ai_routine(TriggerAiRoutineInput(routine_id=ROUTINE["id"]))

    assert result.startswith("Error (409):")


def test_list_ai_routines_page_size_bounds() -> None:
    with pytest.raises(ValueError):
        ListAiRoutinesInput(page_size=0)
    with pytest.raises(ValueError):
        ListAiRoutinesInput(page_size=101)


def test_ai_routine_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ListAiRoutinesInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        GetAiRoutineInput(routine_id="x", unexpected="y")  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# AI Eval — prompt sets
# --------------------------------------------------------------------------


PROMPT_SET: dict[str, Any] = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Orders regression",
    "slug": "orders-regression",
    "model_id": "880e8400-e29b-41d4-a716-446655440003",
    "description": "Regression suite",
    "is_archived": False,
    "prompts": [
        {
            "id": "770e8400-e29b-41d4-a716-446655440002",
            "prompt_text": "What are the top 5 products by revenue?",
            "expectation": "Aniseed Syrup",
            "created_at": "2025-01-15T10:00:00.000Z",
            "updated_at": "2025-01-15T10:00:00.000Z",
        }
    ],
    "created_at": "2025-01-15T10:00:00.000Z",
    "updated_at": "2025-01-15T10:00:00.000Z",
}

PROMPT_SET_LIST_ITEM: dict[str, Any] = {
    "id": PROMPT_SET["id"],
    "name": PROMPT_SET["name"],
    "slug": PROMPT_SET["slug"],
    "model_id": PROMPT_SET["model_id"],
    "description": PROMPT_SET["description"],
    "is_archived": False,
    "created_at": PROMPT_SET["created_at"],
    "updated_at": PROMPT_SET["updated_at"],
    "latest_run_at": None,
    "prompt_count": 1,
}


async def test_list_eval_prompt_sets_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_sets": [PROMPT_SET_LIST_ITEM]})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_eval_prompt_sets(ListEvalPromptSetsInput())

    assert "Orders regression" in result
    assert fake.calls == [("GET", "/v1/ai/eval/prompt-sets", {"params": {"archived": "false"}})]


async def test_list_eval_prompt_sets_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_sets": []})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    await omni_list_eval_prompt_sets(ListEvalPromptSetsInput(archived=True, model_ids=["m1", "m2"]))

    assert fake.calls == [
        ("GET", "/v1/ai/eval/prompt-sets", {"params": {"archived": "true", "model_ids": ["m1", "m2"]}})
    ]


async def test_list_eval_prompt_sets_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_sets": [PROMPT_SET_LIST_ITEM]})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_eval_prompt_sets(ListEvalPromptSetsInput(response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["items"][0]["slug"] == "orders-regression"


async def test_list_eval_prompt_sets_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "No eval-accessible models for this caller."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_eval_prompt_sets(ListEvalPromptSetsInput())

    assert result.startswith("Error (404):")


async def test_get_eval_prompt_set_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_set": PROMPT_SET})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_eval_prompt_set(GetEvalPromptSetInput(prompt_set_id=PROMPT_SET["id"]))

    assert "Orders regression" in result
    assert "top 5 products" in result
    assert fake.calls == [("GET", f"/v1/ai/eval/prompt-sets/{PROMPT_SET['id']}", {})]


async def test_get_eval_prompt_set_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_set": PROMPT_SET})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_eval_prompt_set(
        GetEvalPromptSetInput(prompt_set_id=PROMPT_SET["id"], response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["slug"] == "orders-regression"


async def test_get_eval_prompt_set_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Prompt set not found."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_eval_prompt_set(GetEvalPromptSetInput(prompt_set_id="missing"))

    assert result.startswith("Error (404):")


async def test_create_eval_prompt_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_set": PROMPT_SET})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_create_eval_prompt_set(
        CreateEvalPromptSetInput(
            model_id="880e8400-e29b-41d4-a716-446655440003",
            name="Orders regression",
            slug="orders-regression",
            prompts=[
                EvalPromptCreateInput(
                    prompt_text="What are the top 5 products by revenue?", expectation="Aniseed Syrup"
                )
            ],
        )
    )

    assert "Orders regression" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/ai/eval/prompt-sets",
            {
                "json_body": {
                    "model_id": "880e8400-e29b-41d4-a716-446655440003",
                    "name": "Orders regression",
                    "slug": "orders-regression",
                    "prompts": [
                        {"prompt_text": "What are the top 5 products by revenue?", "expectation": "Aniseed Syrup"}
                    ],
                }
            },
        )
    ]


async def test_create_eval_prompt_set_invalid_slug() -> None:
    with pytest.raises(ValueError):
        CreateEvalPromptSetInput(model_id="m", name="n", slug="Not-Valid-Slug")


async def test_create_eval_prompt_set_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Insufficient permissions."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_create_eval_prompt_set(CreateEvalPromptSetInput(model_id="m", name="n", slug="valid-slug"))

    assert result.startswith("Error (403):")


async def test_update_eval_prompt_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_set": PROMPT_SET})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_update_eval_prompt_set(
        UpdateEvalPromptSetInput(
            prompt_set_id=PROMPT_SET["id"],
            name="Orders regression v2",
            prompts=[EvalPromptUpdateInput(id="existing-id", prompt_text="Updated text")],
        )
    )

    assert "Orders regression" in result
    assert fake.calls == [
        (
            "PATCH",
            f"/v1/ai/eval/prompt-sets/{PROMPT_SET['id']}",
            {
                "json_body": {
                    "name": "Orders regression v2",
                    "prompts": [{"id": "existing-id", "prompt_text": "Updated text"}],
                }
            },
        )
    ]


async def test_update_eval_prompt_set_new_prompt_has_no_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_set": PROMPT_SET})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    await omni_update_eval_prompt_set(
        UpdateEvalPromptSetInput(
            prompt_set_id=PROMPT_SET["id"],
            prompts=[EvalPromptUpdateInput(prompt_text="Brand new prompt")],
        )
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"]["prompts"] == [{"prompt_text": "Brand new prompt"}]


async def test_update_eval_prompt_set_error_422(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(422, "prompts[].id does not belong to this prompt set."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_update_eval_prompt_set(UpdateEvalPromptSetInput(prompt_set_id=PROMPT_SET["id"], name="x"))

    assert result.startswith("Error (422):")


async def test_archive_eval_prompt_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"cancelled_job_count": 3, "is_archived": True})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_archive_eval_prompt_set(ArchiveEvalPromptSetInput(prompt_set_id=PROMPT_SET["id"]))

    assert "cancelled 3" in result
    assert fake.calls == [("DELETE", f"/v1/ai/eval/prompt-sets/{PROMPT_SET['id']}", {})]


async def test_archive_eval_prompt_set_error_500(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(500, "Archive committed but a run-cancellation failed."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_archive_eval_prompt_set(ArchiveEvalPromptSetInput(prompt_set_id=PROMPT_SET["id"]))

    assert result.startswith("Error (500):")


async def test_restore_eval_prompt_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"prompt_set": PROMPT_SET})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_restore_eval_prompt_set(RestoreEvalPromptSetInput(prompt_set_id=PROMPT_SET["id"]))

    assert "Orders regression" in result
    assert fake.calls == [("POST", f"/v1/ai/eval/prompt-sets/{PROMPT_SET['id']}/unarchive", {})]


async def test_restore_eval_prompt_set_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Prompt set not found."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_restore_eval_prompt_set(RestoreEvalPromptSetInput(prompt_set_id="missing"))

    assert result.startswith("Error (404):")


def test_eval_prompt_set_max_prompts_bound() -> None:
    with pytest.raises(ValueError):
        CreateEvalPromptSetInput(
            model_id="m",
            name="n",
            slug="valid-slug",
            prompts=[EvalPromptCreateInput(prompt_text=f"p{i}") for i in range(26)],
        )


def test_eval_prompt_set_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ListEvalPromptSetsInput(unexpected="x")  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# AI Eval — runs
# --------------------------------------------------------------------------


RUN_LIST_ITEM: dict[str, Any] = {
    "id": "660e8400-e29b-41d4-a716-446655440001",
    "prompt_set_id": PROMPT_SET["id"],
    "model_id": PROMPT_SET["model_id"],
    "run_number": 3,
    "status": "RUNNING",
    "is_archived": False,
    "branch_id": None,
    "branch_name": None,
    "description": None,
    "created_at": "2025-01-15T10:00:00.000Z",
    "completed_at": None,
    "stats": {"terminal": 8, "total": 12},
}

RUN_DETAIL: dict[str, Any] = {
    **{k: v for k, v in RUN_LIST_ITEM.items() if k != "stats"},
    "results": [
        {
            "id": "aa0e8400-e29b-41d4-a716-446655440005",
            "prompt": "What are the top 5 products by revenue?",
            "score": 0.9,
            "cost": 0.0021,
            "scoring_cost": 0.0004,
            "timing_ms": 4321,
            "error_reason": None,
            "agentic_job": {"id": "job-1", "conversation_id": "conv-1", "state": "COMPLETE"},
        }
    ],
}


async def test_list_eval_runs_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"runs": [RUN_LIST_ITEM]})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_eval_runs(ListEvalRunsInput(prompt_set_id=PROMPT_SET["id"]))

    assert "Run #3" in result
    assert fake.calls == [
        ("GET", "/v1/ai/eval/runs", {"params": {"prompt_set_id": PROMPT_SET["id"], "archived": "false"}})
    ]


async def test_list_eval_runs_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"runs": [RUN_LIST_ITEM]})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_eval_runs(
        ListEvalRunsInput(prompt_set_id=PROMPT_SET["id"], response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["items"][0]["run_number"] == 3


async def test_list_eval_runs_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Missing or invalid prompt_set_id."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_list_eval_runs(ListEvalRunsInput(prompt_set_id=PROMPT_SET["id"]))

    assert result.startswith("Error (400):")


async def test_get_eval_run_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"run": RUN_DETAIL})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_eval_run(GetEvalRunInput(run_id=RUN_LIST_ITEM["id"]))

    assert "RUNNING" in result
    assert "top 5 products" in result
    assert fake.calls == [("GET", f"/v1/ai/eval/runs/{RUN_LIST_ITEM['id']}", {})]


async def test_get_eval_run_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"run": RUN_DETAIL})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_eval_run(GetEvalRunInput(run_id=RUN_LIST_ITEM["id"], response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["status"] == "RUNNING"


async def test_get_eval_run_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Run not found."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_get_eval_run(GetEvalRunInput(run_id="missing"))

    assert result.startswith("Error (404):")


async def test_start_eval_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"job_count": 12, "run": RUN_DETAIL})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_start_eval_run(StartEvalRunInput(prompt_set_id=PROMPT_SET["id"]))

    assert "12 job(s)" in result
    assert "AI credits" in result
    assert fake.calls == [("POST", "/v1/ai/eval/runs", {"json_body": {"prompt_set_id": PROMPT_SET["id"]}})]


async def test_start_eval_run_with_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"job_count": 1, "run": RUN_DETAIL})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    await omni_start_eval_run(
        StartEvalRunInput(prompt_set_id=PROMPT_SET["id"], branch_id="branch-1", description="test run")
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/ai/eval/runs",
            {
                "json_body": {
                    "prompt_set_id": PROMPT_SET["id"],
                    "description": "test run",
                    "run_config": {"branch_id": "branch-1"},
                }
            },
        )
    ]


async def test_start_eval_run_error_429(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(429, "Per-user active-run cap reached."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_start_eval_run(StartEvalRunInput(prompt_set_id=PROMPT_SET["id"]))

    assert result.startswith("Error (429):")


async def test_start_eval_run_error_503(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(503, "AI eval is paused for this organization."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_start_eval_run(StartEvalRunInput(prompt_set_id=PROMPT_SET["id"]))

    assert result.startswith("Error (503):")


async def test_cancel_eval_run(monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled_run = dict(RUN_DETAIL, status="CANCELLED", is_archived=True)
    fake = _FakeClient(payload={"cancelled": 4, "total": 12, "run": cancelled_run})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_cancel_eval_run(CancelEvalRunInput(run_id=RUN_LIST_ITEM["id"]))

    assert "4/12" in result
    assert "CANCELLED" in result
    assert fake.calls == [("POST", f"/v1/ai/eval/runs/{RUN_LIST_ITEM['id']}/cancel", {})]


async def test_cancel_eval_run_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Run not found."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_cancel_eval_run(CancelEvalRunInput(run_id="missing"))

    assert result.startswith("Error (404):")


async def test_archive_eval_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"is_archived": True})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_archive_eval_run(ArchiveEvalRunInput(run_id=RUN_LIST_ITEM["id"]))

    assert "Archived eval run" in result
    assert fake.calls == [("DELETE", f"/v1/ai/eval/runs/{RUN_LIST_ITEM['id']}", {})]


async def test_archive_eval_run_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Invalid runId."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_archive_eval_run(ArchiveEvalRunInput(run_id="not-a-uuid"))

    assert result.startswith("Error (400):")


async def test_restore_eval_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"is_archived": False})
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_restore_eval_run(RestoreEvalRunInput(run_id=RUN_LIST_ITEM["id"]))

    assert "Restored eval run" in result
    assert fake.calls == [("POST", f"/v1/ai/eval/runs/{RUN_LIST_ITEM['id']}/unarchive", {})]


async def test_restore_eval_run_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Run not found."))
    monkeypatch.setattr("omni_mcp.tools.ai_routines_evals.get_client", lambda: fake)

    result = await omni_restore_eval_run(RestoreEvalRunInput(run_id="missing"))

    assert result.startswith("Error (404):")


def test_eval_run_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ListEvalRunsInput(prompt_set_id="x", unexpected="y")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        StartEvalRunInput(prompt_set_id="x", unexpected="y")  # type: ignore[call-arg]
