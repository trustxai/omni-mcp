"""Unit tests for the users / embed users / email-only users tools against a fake client."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.users import (
    USER_ATTRIBUTE_URN,
    CreateUserInput,
    DeleteEmbedUserInput,
    DeleteUserInput,
    GetEmbedUserInput,
    GetUserInput,
    ListEmailOnlyUsersInput,
    ListEmbedUsersInput,
    ListUsersInput,
    ReplaceUserInput,
    UpdateUserInput,
    omni_create_user,
    omni_delete_embed_user,
    omni_delete_user,
    omni_get_embed_user,
    omni_get_user,
    omni_list_email_only_users,
    omni_list_embed_users,
    omni_list_users,
    omni_replace_user,
    omni_update_user,
)

SCIM_USER: dict[str, Any] = {
    "id": "9e8719d9-276a-4964-9395-a493189a247c",
    "userName": "blob.ross@blobsrus.co",
    "displayName": "Blob Ross",
    "active": True,
    "emails": [{"primary": True, "value": "blob.ross@blobsrus.co"}],
    "groups": [{"display": "All Users", "value": "grp-1"}],
    "meta": {
        "resourceType": "User",
        "created": "2024-12-03T23:13:14.109Z",
        "lastModified": "2024-12-03T23:13:14.109Z",
    },
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    USER_ATTRIBUTE_URN: {"good_blob": "yes"},
    "urn:omni:params:scim:schemas:extension:user:2.0": {"lastLogin": "2024-01-03T00:00:00.000Z"},
}

SCIM_USERS_LIST: dict[str, Any] = {
    "Resources": [SCIM_USER],
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
    "itemsPerPage": 1,
    "totalResults": 1,
    "startIndex": 1,
}

SCIM_EMBED_USER: dict[str, Any] = {
    "id": "2212aecf-a2ba-4d99-b23b-f615bc4c6522",
    "userName": "embed-user-i_4TrN@blobsrus.embed-exploreomni.co",
    "displayName": "Blobby",
    "active": True,
    "emails": [{"primary": True, "value": "embed-user-i_4TrN@blobsrus.embed-exploreomni.co"}],
    "groups": [{"display": "All Embed Users", "value": "4GcvQ2D9"}],
    "meta": {
        "resourceType": "User",
        "created": "2024-09-30T20:25:01.822Z",
        "lastModified": "2024-09-30T20:51:47.558Z",
    },
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "embedEmail": None,
    "embedEntity": "omni",
    "embedExternalId": "blobby-manager",
}

SCIM_EMBED_USERS_LIST: dict[str, Any] = {
    "Resources": [SCIM_EMBED_USER],
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
    "itemsPerPage": 1,
    "totalResults": 1,
    "startIndex": 1,
}

EMAIL_ONLY_LIST: dict[str, Any] = {
    "pageInfo": {"hasNextPage": True, "nextCursor": "bob@example.com", "pageSize": 20, "totalRecords": 50},
    "records": [
        {
            "email": "alice@example.com",
            "user_id": "550e8400-e29b-41d4-a716-446655440000",
            "user_attributes": {"omni_user_email": "alice@example.com", "department": "Engineering"},
        }
    ],
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


def _http_error(method: str, url: str, status: int, *, detail: str = "boom") -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# ---------------------------------------------------------------------------
# omni_list_email_only_users
# ---------------------------------------------------------------------------


async def test_list_email_only_users_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=EMAIL_ONLY_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_list_email_only_users(ListEmailOnlyUsersInput())

    assert "alice@example.com" in result
    assert "550e8400-e29b-41d4-a716-446655440000" in result
    assert 'cursor="bob@example.com"' in result
    assert fake.calls == [
        ("GET", "/v1/users/email-only", {"params": {"pageSize": 20, "sortDirection": "desc"}}),
    ]


async def test_list_email_only_users_passes_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=EMAIL_ONLY_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    await omni_list_email_only_users(
        ListEmailOnlyUsersInput(email="alice", cursor="prev-cursor", page_size=5, sort_direction="asc")
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/users/email-only",
            {"params": {"pageSize": 5, "sortDirection": "asc", "email": "alice", "cursor": "prev-cursor"}},
        )
    ]


async def test_list_email_only_users_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=EMAIL_ONLY_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_list_email_only_users(ListEmailOnlyUsersInput(response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["count"] == 1
    assert payload["totalRecords"] == 50
    assert payload["items"][0]["email"] == "alice@example.com"


async def test_list_email_only_users_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("GET", "https://acme.omniapp.co/api/v1/users/email-only", 401)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_list_email_only_users(ListEmailOnlyUsersInput())

    assert result.startswith("Error (401):")


def test_list_email_only_users_rejects_page_size_over_20() -> None:
    with pytest.raises(ValueError):
        ListEmailOnlyUsersInput(page_size=21)


def test_list_email_only_users_rejects_zero_page_size() -> None:
    with pytest.raises(ValueError):
        ListEmailOnlyUsersInput(page_size=0)


def test_list_email_only_users_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ListEmailOnlyUsersInput(unexpected="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# omni_create_user
# ---------------------------------------------------------------------------


async def test_create_user_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_create_user(
        CreateUserInput(
            user_name="blob.ross@blobsrus.com",
            display_name="Blob Ross",
            user_attributes={"good_blob": "true"},
        )
    )

    assert "Created user **Blob Ross**" in result
    assert "9e8719d9-276a-4964-9395-a493189a247c" in result
    assert fake.calls == [
        (
            "POST",
            "/scim/v2/users",
            {
                "json_body": {
                    "userName": "blob.ross@blobsrus.com",
                    "displayName": "Blob Ross",
                    USER_ATTRIBUTE_URN: {"good_blob": "true"},
                }
            },
        )
    ]


async def test_create_user_scim_body_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    override = {"userName": "x@y.com", "displayName": "X Y"}
    await omni_create_user(CreateUserInput(scim_body=override))

    assert fake.calls == [("POST", "/scim/v2/users", {"json_body": override})]


async def test_create_user_requires_fields_without_scim_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_create_user(CreateUserInput(user_name="only-name@example.com"))

    assert result.startswith("Error:")
    assert fake.calls == []


async def test_create_user_error_429(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("POST", "https://acme.omniapp.co/api/scim/v2/users", 429)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_create_user(CreateUserInput(user_name="a@b.com", display_name="A B"))

    assert result.startswith("Error (429):")


def test_create_user_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        CreateUserInput(unexpected="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# omni_list_users
# ---------------------------------------------------------------------------


async def test_list_users_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USERS_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_list_users(ListUsersInput())

    assert "Blob Ross" in result
    assert "blob.ross@blobsrus.co" in result
    assert "End of results." in result
    assert fake.calls == [("GET", "/scim/v2/users", {"params": {"count": 100, "startIndex": 1}})]


async def test_list_users_passes_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USERS_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    await omni_list_users(ListUsersInput(filter='userName co "smith"', count=50, start_index=51))

    assert fake.calls == [
        ("GET", "/scim/v2/users", {"params": {"count": 50, "startIndex": 51, "filter": 'userName co "smith"'}})
    ]


async def test_list_users_shows_next_page_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {**SCIM_USERS_LIST, "totalResults": 5, "itemsPerPage": 2, "startIndex": 1}
    fake = _FakeClient(payload=body)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_list_users(ListUsersInput())

    assert "start_index=3" in result


async def test_list_users_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USERS_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    payload = json.loads(await omni_list_users(ListUsersInput(response_format=ResponseFormat.JSON)))

    assert payload["totalResults"] == 1
    assert payload["resources"][0]["id"] == "9e8719d9-276a-4964-9395-a493189a247c"


async def test_list_users_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("GET", "https://acme.omniapp.co/api/scim/v2/users", 403)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_list_users(ListUsersInput())

    assert result.startswith("Error (403):")


def test_list_users_rejects_non_positive_count() -> None:
    with pytest.raises(ValueError):
        ListUsersInput(count=0)


def test_list_users_rejects_non_positive_start_index() -> None:
    with pytest.raises(ValueError):
        ListUsersInput(start_index=0)


# ---------------------------------------------------------------------------
# omni_get_user
# ---------------------------------------------------------------------------


async def test_get_user_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_get_user(GetUserInput(user_id="9e8719d9-276a-4964-9395-a493189a247c"))

    assert "Blob Ross" in result
    assert "good_blob" in result
    assert "2024-01-03" in result
    assert fake.calls == [("GET", "/scim/v2/users/9e8719d9-276a-4964-9395-a493189a247c", {})]


async def test_get_user_quotes_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    weird_id = "abc/def@example.com"
    await omni_get_user(GetUserInput(user_id=weird_id))

    assert fake.calls == [("GET", f"/scim/v2/users/{quote(weird_id, safe='')}", {})]


async def test_get_user_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    payload = json.loads(await omni_get_user(GetUserInput(user_id="x", response_format=ResponseFormat.JSON)))

    assert payload["id"] == "9e8719d9-276a-4964-9395-a493189a247c"


async def test_get_user_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("GET", "https://acme.omniapp.co/api/scim/v2/users/bad-id", 404)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_get_user(GetUserInput(user_id="bad-id"))

    assert result.startswith("Error (404):")


# ---------------------------------------------------------------------------
# omni_replace_user
# ---------------------------------------------------------------------------


async def test_replace_user_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={**SCIM_USER, "active": False})
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_replace_user(
        ReplaceUserInput(
            user_id="9e8719d9-276a-4964-9395-a493189a247c",
            user_name="blob.ross@blobsrus.co",
            display_name="Blob Ross",
            active=False,
            user_attributes={"good_blob": "yes"},
        )
    )

    assert "Replaced user **Blob Ross**" in result
    assert "active: False" in result
    assert fake.calls == [
        (
            "PUT",
            "/scim/v2/users/9e8719d9-276a-4964-9395-a493189a247c",
            {
                "json_body": {
                    "userName": "blob.ross@blobsrus.co",
                    "displayName": "Blob Ross",
                    "active": False,
                    USER_ATTRIBUTE_URN: {"good_blob": "yes"},
                }
            },
        )
    ]


async def test_replace_user_scim_body_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    override = {"userName": "blob.ross@blobsrus.co"}
    await omni_replace_user(ReplaceUserInput(user_id="uid-1", scim_body=override))

    assert fake.calls == [("PUT", "/scim/v2/users/uid-1", {"json_body": override})]


async def test_replace_user_requires_user_name(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_replace_user(ReplaceUserInput(user_id="uid-1"))

    assert result.startswith("Error:")
    assert fake.calls == []


async def test_replace_user_error_400(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("PUT", "https://acme.omniapp.co/api/scim/v2/users/uid-1", 400)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_replace_user(ReplaceUserInput(user_id="uid-1", user_name="a@b.com"))

    assert result.startswith("Error (400):")


# ---------------------------------------------------------------------------
# omni_update_user
# ---------------------------------------------------------------------------


async def test_update_user_active_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={**SCIM_USER, "active": False})
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_update_user(UpdateUserInput(user_id="uid-1", active=False))

    assert "Updated user" in result
    assert "active: False" in result
    assert fake.calls == [
        (
            "PATCH",
            "/scim/v2/users/uid-1",
            {
                "json_body": {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                    "Operations": [{"op": "replace", "path": "active", "value": False}],
                }
            },
        )
    ]


async def test_update_user_builds_multiple_operations_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    await omni_update_user(
        UpdateUserInput(user_id="uid-1", active=True, display_name="Blob Ross", user_attributes={"good_blob": "yes"})
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"]["Operations"] == [
        {"op": "replace", "path": "active", "value": True},
        {"op": "replace", "path": "displayName", "value": "Blob Ross"},
        {"op": "replace", "path": USER_ATTRIBUTE_URN, "value": {"good_blob": "yes"}},
    ]


async def test_update_user_raw_operations_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    raw_ops = [{"op": "remove", "path": "displayName", "value": "ignored"}]
    await omni_update_user(UpdateUserInput(user_id="uid-1", active=True, operations=raw_ops))

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"]["Operations"] == raw_ops


async def test_update_user_requires_at_least_one_field(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_update_user(UpdateUserInput(user_id="uid-1"))

    assert result.startswith("Error:")
    assert fake.calls == []


async def test_update_user_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("PATCH", "https://acme.omniapp.co/api/scim/v2/users/uid-1", 404)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_update_user(UpdateUserInput(user_id="uid-1", active=False))

    assert result.startswith("Error (404):")


# ---------------------------------------------------------------------------
# omni_delete_user
# ---------------------------------------------------------------------------


async def test_delete_user_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_delete_user(DeleteUserInput(user_id="uid-1"))

    assert "Deleted user" in result
    assert "uid-1" in result
    assert fake.calls == [("DELETE", "/scim/v2/users/uid-1", {})]


async def test_delete_user_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("DELETE", "https://acme.omniapp.co/api/scim/v2/users/uid-1", 404)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_delete_user(DeleteUserInput(user_id="uid-1"))

    assert result.startswith("Error (404):")


# ---------------------------------------------------------------------------
# omni_list_embed_users
# ---------------------------------------------------------------------------


async def test_list_embed_users_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_EMBED_USERS_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_list_embed_users(ListEmbedUsersInput())

    assert "Blobby" in result
    assert "blobby-manager" in result
    assert fake.calls == [("GET", "/scim/v2/embed/users", {"params": {"count": 100, "startIndex": 1}})]


async def test_list_embed_users_passes_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_EMBED_USERS_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    await omni_list_embed_users(ListEmbedUsersInput(filter='embedExternalId co "sales"', count=25))

    assert fake.calls == [
        (
            "GET",
            "/scim/v2/embed/users",
            {"params": {"count": 25, "startIndex": 1, "filter": 'embedExternalId co "sales"'}},
        )
    ]


async def test_list_embed_users_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_EMBED_USERS_LIST)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    payload = json.loads(await omni_list_embed_users(ListEmbedUsersInput(response_format=ResponseFormat.JSON)))

    assert payload["resources"][0]["embedExternalId"] == "blobby-manager"


async def test_list_embed_users_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("GET", "https://acme.omniapp.co/api/scim/v2/embed/users", 429)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_list_embed_users(ListEmbedUsersInput())

    assert result.startswith("Error (429):")


# ---------------------------------------------------------------------------
# omni_get_embed_user
# ---------------------------------------------------------------------------


async def test_get_embed_user_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_EMBED_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_get_embed_user(GetEmbedUserInput(user_id="2212aecf-a2ba-4d99-b23b-f615bc4c6522"))

    assert "Blobby" in result
    assert "blobby-manager" in result
    assert "omni" in result
    assert fake.calls == [("GET", "/scim/v2/embed/users/2212aecf-a2ba-4d99-b23b-f615bc4c6522", {})]


async def test_get_embed_user_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCIM_EMBED_USER)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    payload = json.loads(await omni_get_embed_user(GetEmbedUserInput(user_id="x", response_format=ResponseFormat.JSON)))

    assert payload["embedExternalId"] == "blobby-manager"


async def test_get_embed_user_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("GET", "https://acme.omniapp.co/api/scim/v2/embed/users/bad-id", 404)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_get_embed_user(GetEmbedUserInput(user_id="bad-id"))

    assert result.startswith("Error (404):")


# ---------------------------------------------------------------------------
# omni_delete_embed_user
# ---------------------------------------------------------------------------


async def test_delete_embed_user_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: fake)

    result = await omni_delete_embed_user(DeleteEmbedUserInput(user_id="2212aecf-a2ba-4d99-b23b-f615bc4c6522"))

    assert "Deleted embed user" in result
    assert "2212aecf-a2ba-4d99-b23b-f615bc4c6522" in result
    assert fake.calls == [("DELETE", "/scim/v2/embed/users/2212aecf-a2ba-4d99-b23b-f615bc4c6522", {})]


async def test_delete_embed_user_error_429(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("DELETE", "https://acme.omniapp.co/api/scim/v2/embed/users/uid-1", 429)
    monkeypatch.setattr("omni_mcp.tools.users.get_client", lambda: _FakeClient(exc=error))

    result = await omni_delete_embed_user(DeleteEmbedUserInput(user_id="uid-1"))

    assert result.startswith("Error (429):")


# ---------------------------------------------------------------------------
# Shared input validation
# ---------------------------------------------------------------------------


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        GetUserInput(user_id="x", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        DeleteUserInput(user_id="x", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ReplaceUserInput(user_id="x", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        UpdateUserInput(user_id="x", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ListEmbedUsersInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        GetEmbedUserInput(user_id="x", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        DeleteEmbedUserInput(user_id="x", unexpected="x")  # type: ignore[call-arg]
