"""Unit tests for the identity/api-token/user-attribute tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from omni_mcp.config import get_settings
from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.identity import (
    DeleteApiTokenInput,
    GetApiTokenInput,
    ListApiTokensInput,
    ListUserAttributesInput,
    UpdateApiTokenInput,
    WhoamiInput,
    omni_delete_api_token,
    omni_get_api_token,
    omni_list_api_tokens,
    omni_list_user_attributes,
    omni_update_api_token,
    omni_whoami,
)

WHOAMI: dict[str, Any] = {
    "keyScope": "organization",
    "orgRole": "ORG_ADMIN",
    "user": {"id": "user-1", "membershipId": "membership-1"},
    "rolesByModel": {
        "model-1": {
            "baseRole": "QUERIER",
            "connectionId": "conn-1",
            "roleName": "QUERIER",
            "permissions": ["QUERY_TOPICS", "QUERY_SQL"],
        }
    },
}

TOKEN_RECORD: dict[str, Any] = {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "name": "CI deployment key",
    "type": "organization",
    "enabled": True,
    "createdAt": "2026-01-15T10:00:00.000Z",
    "membershipId": None,
}

LIST_TOKENS_PAYLOAD: dict[str, Any] = {
    "pageInfo": {"hasNextPage": False, "nextCursor": None, "pageSize": 50, "totalRecords": 1},
    "records": [TOKEN_RECORD],
}

USER_ATTRIBUTES_PAYLOAD: dict[str, Any] = {
    "records": [
        {
            "id": "",
            "name": "omni_user_id",
            "label": "Omni User ID",
            "type": "String",
            "multiple_values": False,
            "default_value": None,
            "description": None,
            "system": True,
        },
        {
            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "region",
            "label": "Region",
            "type": "String",
            "multiple_values": False,
            "default_value": "us-east",
            "description": "User region for RLS",
            "system": False,
        },
    ]
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


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_BASE_URL", "https://acme.omniapp.co")
    monkeypatch.setenv("OMNI_API_KEY", "secret-key")
    get_settings.cache_clear()


def _http_error(
    status: int, detail: str, method: str = "GET", url: str = "https://acme.omniapp.co/api/v1/x"
) -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# --------------------------------------------------------------------------
# omni_whoami
# --------------------------------------------------------------------------


async def test_whoami_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=WHOAMI)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    result = await omni_whoami(WhoamiInput())

    assert "user-1" in result
    assert "membership-1" in result
    assert "ORG_ADMIN" in result
    assert "QUERY_TOPICS" in result
    assert fake.calls == [("GET", "/v1/whoami", {"params": None})]


async def test_whoami_passes_model_filter(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=WHOAMI)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    await omni_whoami(WhoamiInput(model_id="model-1,model-2"))

    assert fake.calls == [("GET", "/v1/whoami", {"params": {"modelId": "model-1,model-2"}})]


async def test_whoami_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=WHOAMI)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    payload = json.loads(await omni_whoami(WhoamiInput(response_format=ResponseFormat.JSON)))

    assert payload["user"]["id"] == "user-1"
    assert payload["orgRole"] == "ORG_ADMIN"


async def test_whoami_reports_truncated_roles(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    payload = dict(WHOAMI, rolesByModelTruncated=True)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: _FakeClient(payload=payload))

    result = await omni_whoami(WhoamiInput())

    assert "truncated" in result


async def test_whoami_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(401, "Unauthorized: Missing or invalid API key")
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: _FakeClient(exc=error))

    result = await omni_whoami(WhoamiInput())

    assert result.startswith("Error (401):")


# --------------------------------------------------------------------------
# omni_list_api_tokens
# --------------------------------------------------------------------------


async def test_list_api_tokens_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=LIST_TOKENS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    result = await omni_list_api_tokens(ListApiTokensInput())

    assert "CI deployment key" in result
    assert "organization" in result
    assert fake.calls == [
        (
            "GET",
            "/v1/api-keys",
            {"params": {"pageSize": 20, "sortField": "createdAt", "sortDirection": "desc"}},
        )
    ]


async def test_list_api_tokens_with_type_filter_and_cursor(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=LIST_TOKENS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    await omni_list_api_tokens(
        ListApiTokensInput(type="personal", page_size=50, cursor="c1", sort_field="name", sort_direction="asc")
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/api-keys",
            {
                "params": {
                    "pageSize": 50,
                    "sortField": "name",
                    "sortDirection": "asc",
                    "type": "personal",
                    "cursor": "c1",
                }
            },
        )
    ]


async def test_list_api_tokens_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=LIST_TOKENS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    payload = json.loads(await omni_list_api_tokens(ListApiTokensInput(response_format=ResponseFormat.JSON)))

    assert payload["count"] == 1
    assert payload["items"][0]["id"] == TOKEN_RECORD["id"]


async def test_list_api_tokens_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(403, "Forbidden. Requires Organization Admin permissions.")
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: _FakeClient(exc=error))

    result = await omni_list_api_tokens(ListApiTokensInput())

    assert result.startswith("Error (403):")


def test_list_api_tokens_rejects_bad_type() -> None:
    with pytest.raises(ValidationError):
        ListApiTokensInput(type="bogus")  # type: ignore[arg-type]


def test_list_api_tokens_rejects_page_size_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        ListApiTokensInput(page_size=0)
    with pytest.raises(ValidationError):
        ListApiTokensInput(page_size=101)


def test_list_api_tokens_rejects_bad_sort_direction() -> None:
    with pytest.raises(ValidationError):
        ListApiTokensInput(sort_direction="sideways")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# omni_get_api_token
# --------------------------------------------------------------------------


async def test_get_api_token_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=TOKEN_RECORD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    result = await omni_get_api_token(GetApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890"))

    assert "CI deployment key" in result
    assert "organization" in result
    assert fake.calls == [("GET", "/v1/api-keys/a1b2c3d4-e5f6-7890-abcd-ef1234567890", {})]


async def test_get_api_token_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=TOKEN_RECORD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    payload = json.loads(
        await omni_get_api_token(
            GetApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["name"] == "CI deployment key"


async def test_get_api_token_quotes_id(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=TOKEN_RECORD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    await omni_get_api_token(GetApiTokenInput(id="weird/id value"))

    assert fake.calls == [("GET", "/v1/api-keys/weird%2Fid%20value", {})]


async def test_get_api_token_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(404, "Api key with id 00000000-0000-0000-0000-000000000000 does not exist")
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: _FakeClient(exc=error))

    result = await omni_get_api_token(GetApiTokenInput(id="00000000-0000-0000-0000-000000000000"))

    assert result.startswith("Error (404):")


def test_get_api_token_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        GetApiTokenInput(id="")


# --------------------------------------------------------------------------
# omni_update_api_token
# --------------------------------------------------------------------------


async def test_update_api_token_disable(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    disabled_record = dict(TOKEN_RECORD, enabled=False)
    fake = _FakeClient(payload=disabled_record)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    result = await omni_update_api_token(UpdateApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", enabled=False))

    assert "disabled" in result
    assert "CI deployment key" in result
    assert fake.calls == [
        (
            "PUT",
            "/v1/api-keys/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            {"json_body": {"enabled": False}},
        )
    ]


async def test_update_api_token_enable(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=TOKEN_RECORD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    result = await omni_update_api_token(UpdateApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", enabled=True))

    assert "enabled" in result
    assert fake.calls == [
        (
            "PUT",
            "/v1/api-keys/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            {"json_body": {"enabled": True}},
        )
    ]


async def test_update_api_token_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(404, "Api key with id <id> does not exist")
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: _FakeClient(exc=error))

    result = await omni_update_api_token(UpdateApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", enabled=True))

    assert result.startswith("Error (404):")


def test_update_api_token_requires_enabled_field() -> None:
    with pytest.raises(ValidationError):
        UpdateApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890")  # type: ignore[call-arg]


def test_update_api_token_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        UpdateApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", enabled=True, extra="x")  # type: ignore[call-arg]


async def test_update_api_token_quotes_id(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=TOKEN_RECORD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    await omni_update_api_token(UpdateApiTokenInput(id="weird/id value", enabled=True))

    assert fake.calls == [("PUT", "/v1/api-keys/weird%2Fid%20value", {"json_body": {"enabled": True}})]


# --------------------------------------------------------------------------
# omni_delete_api_token
# --------------------------------------------------------------------------


async def test_delete_api_token(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"message": "API token revoked", "success": True})
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    result = await omni_delete_api_token(DeleteApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890"))

    assert "API token revoked" in result
    assert "a1b2c3d4-e5f6-7890-abcd-ef1234567890" in result
    assert fake.calls == [("DELETE", "/v1/api-keys/a1b2c3d4-e5f6-7890-abcd-ef1234567890", {})]


async def test_delete_api_token_quotes_id(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"message": "API token revoked", "success": True})
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    await omni_delete_api_token(DeleteApiTokenInput(id="weird/id value"))

    assert fake.calls == [("DELETE", "/v1/api-keys/weird%2Fid%20value", {})]


async def test_delete_api_token_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(404, "Api key with id a1b2c3d4-e5f6-7890-abcd-ef1234567890 does not exist")
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: _FakeClient(exc=error))

    result = await omni_delete_api_token(DeleteApiTokenInput(id="a1b2c3d4-e5f6-7890-abcd-ef1234567890"))

    assert result.startswith("Error (404):")


def test_delete_api_token_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        DeleteApiTokenInput(id="")


# --------------------------------------------------------------------------
# omni_list_user_attributes
# --------------------------------------------------------------------------


async def test_list_user_attributes_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=USER_ATTRIBUTES_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    result = await omni_list_user_attributes(ListUserAttributesInput())

    assert "omni_user_id" in result
    assert "region" in result
    assert "User region for RLS" in result
    assert "does not paginate" in result
    assert fake.calls == [("GET", "/v1/user-attributes", {})]


async def test_list_user_attributes_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=USER_ATTRIBUTES_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    payload = json.loads(await omni_list_user_attributes(ListUserAttributesInput(response_format=ResponseFormat.JSON)))

    assert payload["count"] == 2
    assert payload["records"][1]["name"] == "region"


async def test_list_user_attributes_empty(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"records": []})
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: fake)

    result = await omni_list_user_attributes(ListUserAttributesInput())

    assert "No user attributes defined" in result


async def test_list_user_attributes_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(403, "Forbidden - User does not have required admin permissions")
    monkeypatch.setattr("omni_mcp.tools.identity.get_client", lambda: _FakeClient(exc=error))

    result = await omni_list_user_attributes(ListUserAttributesInput())

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------
# Shared input validation
# --------------------------------------------------------------------------


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WhoamiInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ListApiTokensInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        GetApiTokenInput(id="x", unexpected="y")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ListUserAttributesInput(unexpected="x")  # type: ignore[call-arg]
