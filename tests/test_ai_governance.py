"""Unit tests for the AI credit control / usage / model suggestion tools."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.ai_governance import (
    DeleteModelSuggestionInput,
    DisableModelSuggestionsScheduleInput,
    DismissModelSuggestionInput,
    EnableModelSuggestionsScheduleInput,
    EntityGroupCreditLimitEntry,
    GenerateModelSuggestionsInput,
    GetAiCreditControlsInput,
    GetEntityGroupAiCreditUsageInput,
    GetLatestSuggestionRunInput,
    GetSuggestionRunStatusInput,
    GetUserAiCreditUsageInput,
    ListEntityGroupAiCreditLimitsInput,
    ListModelSuggestionsInput,
    ListUserAiCreditLimitsInput,
    RestoreModelSuggestionInput,
    SetEntityGroupAiCreditLimitsInput,
    SetUserAiCreditLimitsInput,
    UpdateAiCreditControlsInput,
    UserCreditLimitEntry,
    omni_delete_model_suggestion,
    omni_disable_model_suggestions_schedule,
    omni_dismiss_model_suggestion,
    omni_enable_model_suggestions_schedule,
    omni_generate_model_suggestions,
    omni_get_ai_credit_controls,
    omni_get_entity_group_ai_credit_usage,
    omni_get_latest_suggestion_run,
    omni_get_suggestion_run_status,
    omni_get_user_ai_credit_usage,
    omni_list_entity_group_ai_credit_limits,
    omni_list_model_suggestions,
    omni_list_user_ai_credit_limits,
    omni_restore_model_suggestion,
    omni_set_entity_group_ai_credit_limits,
    omni_set_user_ai_credit_limits,
    omni_update_ai_credit_controls,
)

MODEL_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
SUGGESTION_ID = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
RUN_ID = "c3d4e5f6-a7b8-9012-cdef-123456789012"
USER_ID = "f4a2b3c8-0d1e-4f5a-9b6c-7d8e9f0a1b2c"
ENTITY = "blobsrus"

CREDIT_CONTROLS: dict[str, Any] = {
    "accountCreditLimit": 2000,
    "creditsUsed": 450,
    "downgradeCredits": 800,
    "shutoffCredits": 1200,
    "userDefaultCredits": 100,
    "entityGroupDefaultCredits": 100,
    "periodStart": 1750000000000,
    "periodEnd": 1752600000000,
}


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


def _http_error(status: int, detail: str = "boom") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/x")
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_BASE_URL", "https://acme.omniapp.co")
    monkeypatch.setenv("OMNI_API_KEY", "secret-key")


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("omni_mcp.tools.ai_governance.get_client", lambda: fake)


# --------------------------------------------------------------------------
# omni_get_ai_credit_controls
# --------------------------------------------------------------------------


async def test_get_ai_credit_controls_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=CREDIT_CONTROLS)
    _patch(monkeypatch, fake)

    result = await omni_get_ai_credit_controls(GetAiCreditControlsInput())

    assert "2000" in result
    assert "450" in result
    assert "800" in result
    assert fake.calls == [("GET", "/v1/ai/credit-controls", {})]


async def test_get_ai_credit_controls_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=CREDIT_CONTROLS)
    _patch(monkeypatch, fake)

    result = await omni_get_ai_credit_controls(GetAiCreditControlsInput(response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["accountCreditLimit"] == 2000
    assert payload["downgradeCredits"] == 800


async def test_get_ai_credit_controls_off_and_unlimited(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    body = dict(CREDIT_CONTROLS, downgradeCredits=None, userDefaultCredits=None)
    fake = _FakeClient(payload=body)
    _patch(monkeypatch, fake)

    result = await omni_get_ai_credit_controls(GetAiCreditControlsInput())

    assert "off" in result
    assert "unlimited" in result


async def test_get_ai_credit_controls_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(403, "AI credit controls are not enabled"))
    _patch(monkeypatch, fake)

    result = await omni_get_ai_credit_controls(GetAiCreditControlsInput())

    assert result.startswith("Error (403):")
    assert "AI credit controls" in result


# --------------------------------------------------------------------------
# omni_update_ai_credit_controls
# --------------------------------------------------------------------------


async def test_update_ai_credit_controls_sends_only_set_fields(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    fake = _FakeClient(payload=CREDIT_CONTROLS)
    _patch(monkeypatch, fake)

    result = await omni_update_ai_credit_controls(UpdateAiCreditControlsInput(downgrade_credits=800))

    assert fake.calls == [("PATCH", "/v1/ai/credit-controls", {"json_body": {"downgradeCredits": 800}})]
    assert "AI Credit Controls Updated" in result
    assert "2000" in result


async def test_update_ai_credit_controls_explicit_null_turns_off(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    fake = _FakeClient(payload=CREDIT_CONTROLS)
    _patch(monkeypatch, fake)

    await omni_update_ai_credit_controls(UpdateAiCreditControlsInput(downgrade_credits=None))

    assert fake.calls == [("PATCH", "/v1/ai/credit-controls", {"json_body": {"downgradeCredits": None}})]


async def test_update_ai_credit_controls_multiple_fields(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=CREDIT_CONTROLS)
    _patch(monkeypatch, fake)

    await omni_update_ai_credit_controls(
        UpdateAiCreditControlsInput(
            downgrade_credits=800, shutoff_credits=1200, user_default_credits=None, entity_group_default_credits=50
        )
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v1/ai/credit-controls",
            {
                "json_body": {
                    "downgradeCredits": 800,
                    "shutoffCredits": 1200,
                    "userDefaultCredits": None,
                    "entityGroupDefaultCredits": 50,
                }
            },
        )
    ]


async def test_update_ai_credit_controls_empty_body_short_circuits(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    fake = _FakeClient(payload=CREDIT_CONTROLS)
    _patch(monkeypatch, fake)

    result = await omni_update_ai_credit_controls(UpdateAiCreditControlsInput())

    assert result.startswith("Error: provide at least one field")
    assert fake.calls == []


async def test_update_ai_credit_controls_negative_rejected() -> None:
    with pytest.raises(ValueError):
        UpdateAiCreditControlsInput(downgrade_credits=-1)


async def test_update_ai_credit_controls_model_validate_null_vs_omit(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    """Mirrors how the MCP layer builds this model from raw JSON arguments
    (`arg_model.model_validate(...)`, keeping sub-models un-dumped): an
    explicit `null` must survive as a value to send, while a field left out
    entirely must not appear in the payload at all."""
    fake = _FakeClient(payload=CREDIT_CONTROLS)
    _patch(monkeypatch, fake)

    explicit_null = UpdateAiCreditControlsInput.model_validate({"downgrade_credits": None})
    result = await omni_update_ai_credit_controls(explicit_null)

    assert fake.calls == [("PATCH", "/v1/ai/credit-controls", {"json_body": {"downgradeCredits": None}})]
    assert "AI Credit Controls Updated" in result

    fake.calls.clear()
    omitted = UpdateAiCreditControlsInput.model_validate({})
    result = await omni_update_ai_credit_controls(omitted)

    assert result.startswith("Error: provide at least one field")
    assert fake.calls == []


async def test_update_ai_credit_controls_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(400, "downgradeCredits greater than shutoffCredits"))
    _patch(monkeypatch, fake)

    result = await omni_update_ai_credit_controls(UpdateAiCreditControlsInput(downgrade_credits=5000))

    assert result.startswith("Error (400):")


# --------------------------------------------------------------------------
# omni_list_user_ai_credit_limits
# --------------------------------------------------------------------------


USER_LIMITS_PAGE: dict[str, Any] = {
    "records": [
        {"userId": USER_ID, "creditLimit": 50},
        {"userId": "a7b8c9d0-e1f2-3a4b-5c6d-7e8f9a0b1c2d", "creditLimit": None},
    ],
    "pageInfo": {"hasNextPage": True, "nextCursor": "eyJpZCI6IjEyMzQ1In0", "pageSize": 20, "totalRecords": 47},
}


async def test_list_user_ai_credit_limits_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=USER_LIMITS_PAGE)
    _patch(monkeypatch, fake)

    result = await omni_list_user_ai_credit_limits(ListUserAiCreditLimitsInput())

    assert USER_ID in result
    assert "unlimited" in result
    assert "50" in result
    assert 'cursor="eyJpZCI6IjEyMzQ1In0"' in result
    assert fake.calls == [("GET", "/v1/ai/credit-controls/users", {"params": {"pageSize": 20}})]


async def test_list_user_ai_credit_limits_passes_cursor(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"records": [], "pageInfo": {}})
    _patch(monkeypatch, fake)

    await omni_list_user_ai_credit_limits(ListUserAiCreditLimitsInput(page_size=5, cursor="abc"))

    assert fake.calls == [("GET", "/v1/ai/credit-controls/users", {"params": {"pageSize": 5, "cursor": "abc"}})]


async def test_list_user_ai_credit_limits_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=USER_LIMITS_PAGE)
    _patch(monkeypatch, fake)

    result = await omni_list_user_ai_credit_limits(ListUserAiCreditLimitsInput(response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["count"] == 2
    assert payload["items"][0]["userId"] == USER_ID


async def test_list_user_ai_credit_limits_page_size_bounds() -> None:
    with pytest.raises(ValueError):
        ListUserAiCreditLimitsInput(page_size=0)
    with pytest.raises(ValueError):
        ListUserAiCreditLimitsInput(page_size=101)


async def test_list_user_ai_credit_limits_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(400, "Invalid cursor value"))
    _patch(monkeypatch, fake)

    result = await omni_list_user_ai_credit_limits(ListUserAiCreditLimitsInput(cursor="bogus"))

    assert result.startswith("Error (400):")


# --------------------------------------------------------------------------
# omni_set_user_ai_credit_limits
# --------------------------------------------------------------------------


async def test_set_user_ai_credit_limits_sends_payload(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(
        payload={
            "users": [
                {"userId": USER_ID, "creditLimit": 50, "usesDefaultLimit": False},
                {"userId": "u2", "creditLimit": None, "usesDefaultLimit": False},
            ]
        }
    )
    _patch(monkeypatch, fake)

    result = await omni_set_user_ai_credit_limits(
        SetUserAiCreditLimitsInput(
            users=[
                UserCreditLimitEntry(user_id=USER_ID, credit_limit=50),
                UserCreditLimitEntry(user_id="u2", credit_limit=None),
            ]
        )
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v1/ai/credit-controls/users",
            {
                "json_body": {
                    "users": [
                        {"userId": USER_ID, "creditLimit": 50},
                        {"userId": "u2", "creditLimit": None},
                    ]
                }
            },
        )
    ]
    assert "Updated **2** user AI credit limit(s)" in result
    assert f"`{USER_ID}` → 50 credits" in result
    assert "`u2` → unlimited" in result


async def test_set_user_ai_credit_limits_use_default(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"users": [{"userId": USER_ID, "creditLimit": 100, "usesDefaultLimit": True}]})
    _patch(monkeypatch, fake)

    result = await omni_set_user_ai_credit_limits(
        SetUserAiCreditLimitsInput(users=[UserCreditLimitEntry(user_id=USER_ID, use_default_limit=True)])
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v1/ai/credit-controls/users",
            {"json_body": {"users": [{"userId": USER_ID, "useDefaultLimit": True}]}},
        )
    ]
    assert "uses organization default" in result


def test_user_credit_limit_entry_requires_exactly_one_of_limit_or_default() -> None:
    with pytest.raises(ValueError):
        UserCreditLimitEntry(user_id=USER_ID)
    with pytest.raises(ValueError):
        UserCreditLimitEntry(user_id=USER_ID, credit_limit=50, use_default_limit=True)


def test_user_credit_limit_entry_rejects_negative() -> None:
    with pytest.raises(ValueError):
        UserCreditLimitEntry(user_id=USER_ID, credit_limit=-5)


def test_set_user_ai_credit_limits_bounds() -> None:
    with pytest.raises(ValueError):
        SetUserAiCreditLimitsInput(users=[])


async def test_set_user_ai_credit_limits_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(404, "userId not a member of the organization"))
    _patch(monkeypatch, fake)

    result = await omni_set_user_ai_credit_limits(
        SetUserAiCreditLimitsInput(users=[UserCreditLimitEntry(user_id=USER_ID, credit_limit=10)])
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_list_entity_group_ai_credit_limits
# --------------------------------------------------------------------------


ENTITY_GROUP_LIMITS_PAGE: dict[str, Any] = {
    "records": [
        {"entity": ENTITY, "creditLimit": 50},
        {"entity": "blobsrus-eu", "creditLimit": None},
    ],
    "pageInfo": {"hasNextPage": False, "nextCursor": None, "pageSize": 20, "totalRecords": 2},
}


async def test_list_entity_group_ai_credit_limits_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=ENTITY_GROUP_LIMITS_PAGE)
    _patch(monkeypatch, fake)

    result = await omni_list_entity_group_ai_credit_limits(ListEntityGroupAiCreditLimitsInput())

    assert ENTITY in result
    assert "unlimited" in result
    assert "End of results." in result
    assert fake.calls == [("GET", "/v1/ai/credit-controls/entity-groups", {"params": {"pageSize": 20}})]


async def test_list_entity_group_ai_credit_limits_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=ENTITY_GROUP_LIMITS_PAGE)
    _patch(monkeypatch, fake)

    result = await omni_list_entity_group_ai_credit_limits(
        ListEntityGroupAiCreditLimitsInput(response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["items"][0]["entity"] == ENTITY


async def test_list_entity_group_ai_credit_limits_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(403, "per-entity-group AI credit limits are not enabled"))
    _patch(monkeypatch, fake)

    result = await omni_list_entity_group_ai_credit_limits(ListEntityGroupAiCreditLimitsInput())

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------
# omni_set_entity_group_ai_credit_limits
# --------------------------------------------------------------------------


async def test_set_entity_group_ai_credit_limits_sends_payload(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    fake = _FakeClient(payload={"entityGroups": [{"entity": ENTITY, "creditLimit": 50, "usesDefaultLimit": False}]})
    _patch(monkeypatch, fake)

    result = await omni_set_entity_group_ai_credit_limits(
        SetEntityGroupAiCreditLimitsInput(entity_groups=[EntityGroupCreditLimitEntry(entity=ENTITY, credit_limit=50)])
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v1/ai/credit-controls/entity-groups",
            {"json_body": {"entityGroups": [{"entity": ENTITY, "creditLimit": 50}]}},
        )
    ]
    assert f"`{ENTITY}` → 50 credits" in result


def test_entity_group_credit_limit_entry_requires_exactly_one() -> None:
    with pytest.raises(ValueError):
        EntityGroupCreditLimitEntry(entity=ENTITY)
    with pytest.raises(ValueError):
        EntityGroupCreditLimitEntry(entity=ENTITY, credit_limit=10, use_default_limit=True)


async def test_set_entity_group_ai_credit_limits_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(404, "entity has no entity group"))
    _patch(monkeypatch, fake)

    result = await omni_set_entity_group_ai_credit_limits(
        SetEntityGroupAiCreditLimitsInput(
            entity_groups=[EntityGroupCreditLimitEntry(entity="unknown-entity", credit_limit=10)]
        )
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_get_user_ai_credit_usage
# --------------------------------------------------------------------------


USER_USAGE: dict[str, Any] = {
    "periodStart": 1750000000000,
    "periodEnd": 1752600000000,
    "users": [{"userId": USER_ID, "creditsUsed": 42}],
}


async def test_get_user_ai_credit_usage_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=USER_USAGE)
    _patch(monkeypatch, fake)

    result = await omni_get_user_ai_credit_usage(GetUserAiCreditUsageInput(user_ids=[USER_ID]))

    assert USER_ID in result
    assert "42" in result
    assert fake.calls == [("POST", "/v1/ai/credit-usage/users", {"json_body": {"userIds": [USER_ID]}})]


async def test_get_user_ai_credit_usage_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=USER_USAGE)
    _patch(monkeypatch, fake)

    result = await omni_get_user_ai_credit_usage(
        GetUserAiCreditUsageInput(user_ids=[USER_ID], response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["users"][0]["creditsUsed"] == 42


def test_get_user_ai_credit_usage_bounds() -> None:
    with pytest.raises(ValueError):
        GetUserAiCreditUsageInput(user_ids=[])
    with pytest.raises(ValueError):
        GetUserAiCreditUsageInput(user_ids=[f"user-{i}" for i in range(1001)])


def test_get_user_ai_credit_usage_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        GetUserAiCreditUsageInput(user_ids=[USER_ID, USER_ID])


async def test_get_user_ai_credit_usage_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(404, "userId is not a member of the organization"))
    _patch(monkeypatch, fake)

    result = await omni_get_user_ai_credit_usage(GetUserAiCreditUsageInput(user_ids=[USER_ID]))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_get_entity_group_ai_credit_usage
# --------------------------------------------------------------------------


ENTITY_GROUP_USAGE: dict[str, Any] = {
    "periodStart": 1750000000000,
    "periodEnd": 1752600000000,
    "entityGroups": [{"entity": ENTITY, "creditsUsed": 42}],
}


async def test_get_entity_group_ai_credit_usage_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=ENTITY_GROUP_USAGE)
    _patch(monkeypatch, fake)

    result = await omni_get_entity_group_ai_credit_usage(GetEntityGroupAiCreditUsageInput(entities=[ENTITY]))

    assert ENTITY in result
    assert "42" in result
    assert fake.calls == [("POST", "/v1/ai/credit-usage/entity-groups", {"json_body": {"entities": [ENTITY]}})]


async def test_get_entity_group_ai_credit_usage_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=ENTITY_GROUP_USAGE)
    _patch(monkeypatch, fake)

    result = await omni_get_entity_group_ai_credit_usage(
        GetEntityGroupAiCreditUsageInput(entities=[ENTITY], response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["entityGroups"][0]["entity"] == ENTITY


async def test_get_entity_group_ai_credit_usage_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(403, "per-entity-group AI credit limits are not enabled"))
    _patch(monkeypatch, fake)

    result = await omni_get_entity_group_ai_credit_usage(GetEntityGroupAiCreditUsageInput(entities=[ENTITY]))

    assert result.startswith("Error (403):")


def test_get_entity_group_ai_credit_usage_rejects_duplicate_entities() -> None:
    with pytest.raises(ValueError):
        GetEntityGroupAiCreditUsageInput(entities=[ENTITY, ENTITY])


# --------------------------------------------------------------------------
# omni_list_model_suggestions
# --------------------------------------------------------------------------


SUGGESTIONS_PAGE: dict[str, Any] = {
    "records": [
        {
            "id": SUGGESTION_ID,
            "category": "missing_context",
            "title": "Document the orders view",
            "rationale": "Users frequently ask what status means.",
            "priority": 1,
            "proposedChanges": {
                "kind": "context_edits",
                "edits": [{"field": "ai_context", "target": "views.orders", "value": "..."}],
            },
            "evidence": [{"type": "ai_chat", "chatAiSessionId": "s1", "capturedAt": "2026-06-24T09:12:33Z"}],
            "ignoreReason": None,
            "ignoredAt": None,
            "ignoredBy": None,
            "aiModifiedAt": "2026-06-25T16:10:58Z",
            "createdAt": "2026-06-25T16:10:58Z",
            "updatedAt": "2026-06-25T16:10:58Z",
        }
    ],
    "pageInfo": {"hasNextPage": True, "nextCursor": SUGGESTION_ID, "pageSize": 25, "totalRecords": 47},
}


async def test_list_model_suggestions_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=SUGGESTIONS_PAGE)
    _patch(monkeypatch, fake)

    result = await omni_list_model_suggestions(ListModelSuggestionsInput(model_id=MODEL_ID))

    assert "Document the orders view" in result
    assert "status: active" in result
    assert "1 proposed change(s), 1 evidence item(s)" in result
    assert fake.calls == [
        (
            "GET",
            f"/v1/models/{MODEL_ID}/suggestions",
            {"params": {"status": "active", "pageSize": 25}},
        )
    ]


async def test_list_model_suggestions_status_and_cursor(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"records": [], "pageInfo": {}})
    _patch(monkeypatch, fake)

    await omni_list_model_suggestions(
        ListModelSuggestionsInput(model_id=MODEL_ID, status="ignored", cursor="prev-id", page_size=10)
    )

    assert fake.calls == [
        (
            "GET",
            f"/v1/models/{MODEL_ID}/suggestions",
            {"params": {"status": "ignored", "pageSize": 10, "cursor": "prev-id"}},
        )
    ]


async def test_list_model_suggestions_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=SUGGESTIONS_PAGE)
    _patch(monkeypatch, fake)

    result = await omni_list_model_suggestions(
        ListModelSuggestionsInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["items"][0]["id"] == SUGGESTION_ID


def test_list_model_suggestions_invalid_status() -> None:
    with pytest.raises(ValueError):
        ListModelSuggestionsInput(model_id=MODEL_ID, status="bogus")


async def test_list_model_suggestions_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(404, "Shared model with id <modelId> does not exist"))
    _patch(monkeypatch, fake)

    result = await omni_list_model_suggestions(ListModelSuggestionsInput(model_id=MODEL_ID))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_delete_model_suggestion
# --------------------------------------------------------------------------


async def test_delete_model_suggestion_success(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    result = await omni_delete_model_suggestion(
        DeleteModelSuggestionInput(model_id=MODEL_ID, suggestion_id=SUGGESTION_ID)
    )

    assert f"Deleted suggestion `{SUGGESTION_ID}`" in result
    assert fake.calls == [("DELETE", f"/v1/models/{MODEL_ID}/suggestions/{SUGGESTION_ID}", {})]


async def test_delete_model_suggestion_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(404, "Suggestion not found"))
    _patch(monkeypatch, fake)

    result = await omni_delete_model_suggestion(
        DeleteModelSuggestionInput(model_id=MODEL_ID, suggestion_id=SUGGESTION_ID)
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_get_latest_suggestion_run
# --------------------------------------------------------------------------


RUN_COMPLETE: dict[str, Any] = {
    "id": RUN_ID,
    "status": "complete",
    "triggerSource": "manual",
    "triggeredBy": {"userId": "u1", "name": "Jane Doe"},
    "createdAt": "2026-07-15T14:30:00Z",
    "executionStartedAt": "2026-07-15T14:30:05Z",
    "completedAt": "2026-07-15T14:32:30Z",
    "error": None,
}


async def test_get_latest_suggestion_run_with_run(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"run": RUN_COMPLETE})
    _patch(monkeypatch, fake)

    result = await omni_get_latest_suggestion_run(GetLatestSuggestionRunInput(model_id=MODEL_ID))

    assert RUN_ID in result
    assert "complete" in result
    assert "Jane Doe" in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/suggestions/runs/latest", {})]


async def test_get_latest_suggestion_run_no_runs(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"run": None})
    _patch(monkeypatch, fake)

    result = await omni_get_latest_suggestion_run(GetLatestSuggestionRunInput(model_id=MODEL_ID))

    assert "No generation runs yet" in result


async def test_get_latest_suggestion_run_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"run": RUN_COMPLETE})
    _patch(monkeypatch, fake)

    result = await omni_get_latest_suggestion_run(
        GetLatestSuggestionRunInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["run"]["id"] == RUN_ID


async def test_get_latest_suggestion_run_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(403, "Model must be shared"))
    _patch(monkeypatch, fake)

    result = await omni_get_latest_suggestion_run(GetLatestSuggestionRunInput(model_id=MODEL_ID))

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------
# omni_get_suggestion_run_status
# --------------------------------------------------------------------------


async def test_get_suggestion_run_status_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=RUN_COMPLETE)
    _patch(monkeypatch, fake)

    result = await omni_get_suggestion_run_status(GetSuggestionRunStatusInput(model_id=MODEL_ID, run_id=RUN_ID))

    assert RUN_ID in result
    assert "complete" in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/suggestions/runs/{RUN_ID}", {})]


async def test_get_suggestion_run_status_failed_shows_error(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    failed_run = dict(RUN_COMPLETE, status="failed", error={"message": "generation timed out"})
    fake = _FakeClient(payload=failed_run)
    _patch(monkeypatch, fake)

    result = await omni_get_suggestion_run_status(GetSuggestionRunStatusInput(model_id=MODEL_ID, run_id=RUN_ID))

    assert "failed" in result
    assert "generation timed out" in result


async def test_get_suggestion_run_status_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=RUN_COMPLETE)
    _patch(monkeypatch, fake)

    result = await omni_get_suggestion_run_status(
        GetSuggestionRunStatusInput(model_id=MODEL_ID, run_id=RUN_ID, response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["id"] == RUN_ID


async def test_get_suggestion_run_status_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(404, "Run not found"))
    _patch(monkeypatch, fake)

    result = await omni_get_suggestion_run_status(GetSuggestionRunStatusInput(model_id=MODEL_ID, run_id=RUN_ID))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_generate_model_suggestions
# --------------------------------------------------------------------------


async def test_generate_model_suggestions_success(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"runId": RUN_ID, "status": "queued"})
    _patch(monkeypatch, fake)

    result = await omni_generate_model_suggestions(GenerateModelSuggestionsInput(model_id=MODEL_ID))

    assert RUN_ID in result
    assert "queued" in result
    assert "AI credits" in result
    assert fake.calls == [("POST", f"/v1/models/{MODEL_ID}/suggestions/generate", {})]


async def test_generate_model_suggestions_conflict(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(409, "A generation run is already active for this model"))
    _patch(monkeypatch, fake)

    result = await omni_generate_model_suggestions(GenerateModelSuggestionsInput(model_id=MODEL_ID))

    assert result.startswith("Error (409):")


async def test_generate_model_suggestions_cooldown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(429, "A run completed recently"))
    _patch(monkeypatch, fake)

    result = await omni_generate_model_suggestions(GenerateModelSuggestionsInput(model_id=MODEL_ID))

    assert result.startswith("Error (429):")


# --------------------------------------------------------------------------
# omni_enable_model_suggestions_schedule
# --------------------------------------------------------------------------


async def test_enable_model_suggestions_schedule_default_timezone(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    fake = _FakeClient(payload={"id": "sched-1", "sharedModelId": MODEL_ID, "status": "enabled", "timezone": "UTC"})
    _patch(monkeypatch, fake)

    result = await omni_enable_model_suggestions_schedule(EnableModelSuggestionsScheduleInput(model_id=MODEL_ID))

    assert "sched-1" in result
    assert "UTC" in result
    assert fake.calls == [("PUT", f"/v1/models/{MODEL_ID}/suggestions/schedule", {"json_body": None})]


async def test_enable_model_suggestions_schedule_with_timezone(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    fake = _FakeClient(
        payload={
            "id": "sched-1",
            "sharedModelId": MODEL_ID,
            "status": "enabled",
            "timezone": "America/New_York",
        }
    )
    _patch(monkeypatch, fake)

    result = await omni_enable_model_suggestions_schedule(
        EnableModelSuggestionsScheduleInput(model_id=MODEL_ID, timezone="America/New_York")
    )

    assert "America/New_York" in result
    assert fake.calls == [
        ("PUT", f"/v1/models/{MODEL_ID}/suggestions/schedule", {"json_body": {"timezone": "America/New_York"}})
    ]


async def test_enable_model_suggestions_schedule_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(400, "timezone: Invalid timezone"))
    _patch(monkeypatch, fake)

    result = await omni_enable_model_suggestions_schedule(
        EnableModelSuggestionsScheduleInput(model_id=MODEL_ID, timezone="Not/AZone")
    )

    assert result.startswith("Error (400):")


# --------------------------------------------------------------------------
# omni_disable_model_suggestions_schedule
# --------------------------------------------------------------------------


async def test_disable_model_suggestions_schedule_success(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"success": True, "message": "AI suggestions schedule disabled"})
    _patch(monkeypatch, fake)

    result = await omni_disable_model_suggestions_schedule(DisableModelSuggestionsScheduleInput(model_id=MODEL_ID))

    assert "Disabled the daily suggestion schedule" in result
    assert fake.calls == [("DELETE", f"/v1/models/{MODEL_ID}/suggestions/schedule", {})]


async def test_disable_model_suggestions_schedule_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(404, "Model with id <modelId> does not exist"))
    _patch(monkeypatch, fake)

    result = await omni_disable_model_suggestions_schedule(DisableModelSuggestionsScheduleInput(model_id=MODEL_ID))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_dismiss_model_suggestion
# --------------------------------------------------------------------------


async def test_dismiss_model_suggestion_success(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    result = await omni_dismiss_model_suggestion(
        DismissModelSuggestionInput(model_id=MODEL_ID, suggestion_id=SUGGESTION_ID)
    )

    assert f"Dismissed suggestion `{SUGGESTION_ID}`" in result
    assert fake.calls == [("POST", f"/v1/models/{MODEL_ID}/suggestions/{SUGGESTION_ID}/ignore", {"json_body": None})]


async def test_dismiss_model_suggestion_with_reason(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    await omni_dismiss_model_suggestion(
        DismissModelSuggestionInput(model_id=MODEL_ID, suggestion_id=SUGGESTION_ID, reason="Already covered elsewhere.")
    )

    assert fake.calls == [
        (
            "POST",
            f"/v1/models/{MODEL_ID}/suggestions/{SUGGESTION_ID}/ignore",
            {"json_body": {"reason": "Already covered elsewhere."}},
        )
    ]


def test_dismiss_model_suggestion_reason_length_bound() -> None:
    with pytest.raises(ValueError):
        DismissModelSuggestionInput(model_id=MODEL_ID, suggestion_id=SUGGESTION_ID, reason="x" * 4001)


async def test_dismiss_model_suggestion_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(403, "AI disabled"))
    _patch(monkeypatch, fake)

    result = await omni_dismiss_model_suggestion(
        DismissModelSuggestionInput(model_id=MODEL_ID, suggestion_id=SUGGESTION_ID)
    )

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------
# omni_restore_model_suggestion
# --------------------------------------------------------------------------


async def test_restore_model_suggestion_success(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    result = await omni_restore_model_suggestion(
        RestoreModelSuggestionInput(model_id=MODEL_ID, suggestion_id=SUGGESTION_ID)
    )

    assert f"Restored suggestion `{SUGGESTION_ID}`" in result
    assert fake.calls == [("POST", f"/v1/models/{MODEL_ID}/suggestions/{SUGGESTION_ID}/restore", {})]


async def test_restore_model_suggestion_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(exc=_http_error(404, "Suggestion not found"))
    _patch(monkeypatch, fake)

    result = await omni_restore_model_suggestion(
        RestoreModelSuggestionInput(model_id=MODEL_ID, suggestion_id=SUGGESTION_ID)
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# Shared input hygiene
# --------------------------------------------------------------------------


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        GetAiCreditControlsInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        UpdateAiCreditControlsInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ListModelSuggestionsInput(model_id=MODEL_ID, unexpected="x")  # type: ignore[call-arg]


def test_id_fields_reject_empty_strings() -> None:
    with pytest.raises(ValueError):
        DeleteModelSuggestionInput(model_id="", suggestion_id=SUGGESTION_ID)
    with pytest.raises(ValueError):
        GetSuggestionRunStatusInput(model_id=MODEL_ID, run_id="")
