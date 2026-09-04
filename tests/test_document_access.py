"""Unit tests for document permission, favorite, and label tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.document_access import (
    ApplyDocumentLabelInput,
    BulkUpdateDocumentLabelsInput,
    FavoriteDocumentInput,
    GetDocumentPermissionsInput,
    GrantDocumentPermissionsInput,
    ListDocumentAccessInput,
    ListDocumentFavoritersInput,
    RemoveDocumentLabelInput,
    RevokeDocumentPermissionsInput,
    UnfavoriteDocumentInput,
    UpdateDocumentPermissionSettingsInput,
    UpdateDocumentPermissionsInput,
    omni_apply_document_label,
    omni_bulk_update_document_labels,
    omni_favorite_document,
    omni_get_document_permissions,
    omni_grant_document_permissions,
    omni_list_document_access,
    omni_list_document_favoriters,
    omni_remove_document_label,
    omni_revoke_document_permissions,
    omni_unfavorite_document,
    omni_update_document_permission_settings,
    omni_update_document_permissions,
)

DOC_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
USER_ID = "3c90c3cc-0d44-4b50-8888-8dd25736052a"
GROUP_ID = "mEhXj6ZI"
DOC_PATH = f"/v1/documents/{DOC_ID}"


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


def _http_error(
    status: int, detail: str, method: str = "GET", url: str = "https://acme.omniapp.co/api/v1/x"
) -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# ---------------------------------------------------------------------------
# omni_get_document_permissions
# ---------------------------------------------------------------------------

ABILITIES = {
    "canAnalyze": True,
    "canDownload": False,
    "canDrill": False,
    "canDuplicate": True,
    "canRequestAccess": True,
    "canSaveSpreadsheets": False,
    "canSchedule": True,
    "canUpload": False,
    "canUseDashboardAi": False,
    "canUseTimezoneOverride": False,
    "canViewWorkbook": False,
    "requirePullRequestToPublish": False,
}


async def test_get_document_permissions_abilities_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"abilities": ABILITIES})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_get_document_permissions(GetDocumentPermissionsInput(document_id=DOC_ID))

    assert "canDownload" in result
    assert "False" in result
    assert "_No permits reported for this user._" not in result
    assert fake.calls == [("GET", f"{DOC_PATH}/permissions", {"params": None})]


async def test_get_document_permissions_with_user_and_permits(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "abilities": ABILITIES,
        "permits": [
            {
                "id": USER_ID,
                "name": "Organization",
                "type": "user",
                "description": "Organization",
                "direct": {"role": "VIEWER", "accessBoost": False, "isOwner": False},
            }
        ],
    }
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_get_document_permissions(GetDocumentPermissionsInput(document_id=DOC_ID, user_id=USER_ID))

    assert "VIEWER" in result
    assert "Organization" in result
    assert fake.calls == [("GET", f"{DOC_PATH}/permissions", {"params": {"userId": USER_ID}})]


async def test_get_document_permissions_no_permits_for_user(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"abilities": ABILITIES})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_get_document_permissions(GetDocumentPermissionsInput(document_id=DOC_ID, user_id=USER_ID))

    assert "_No permits reported for this user._" in result


async def test_get_document_permissions_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"abilities": ABILITIES})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_get_document_permissions(
        GetDocumentPermissionsInput(document_id=DOC_ID, response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["abilities"]["canSchedule"] is True


async def test_get_document_permissions_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Document not found"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_get_document_permissions(GetDocumentPermissionsInput(document_id=DOC_ID))

    assert result.startswith("Error (404):")


# ---------------------------------------------------------------------------
# omni_list_document_access
# ---------------------------------------------------------------------------

ACCESS_LIST_PAYLOAD = {
    "principals": [
        {
            "id": USER_ID,
            "name": "Jane Smith",
            "email": "jane@example.com",
            "type": "user",
            "role": "EDITOR",
            "accessBoost": False,
            "accessSource": "direct",
            "isOwner": False,
        },
        {
            "id": "b2c3d4e5",
            "name": "John Doe",
            "type": "user",
            "role": "VIEWER",
            "accessBoost": False,
            "accessSource": "folder",
            "folderInfo": {"id": "f1", "name": "Marketing Reports", "path": "/Shared/Marketing Reports"},
        },
    ],
    "pageInfo": {"hasNextPage": True, "nextCursor": "eyJuYW1lIjoiSm9obiJ9", "pageSize": 20, "totalRecords": 47},
}


async def test_list_document_access_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ACCESS_LIST_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_list_document_access(ListDocumentAccessInput(document_id=DOC_ID))

    assert "Jane Smith" in result
    assert "EDITOR" in result
    assert "Marketing Reports" in result
    assert 'cursor="eyJuYW1lIjoiSm9obiJ9"' in result
    assert fake.calls == [
        (
            "GET",
            f"{DOC_PATH}/access-list",
            {"params": {"pageSize": 20, "sortField": "name", "sortDirection": "asc"}},
        )
    ]


async def test_list_document_access_with_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"principals": [], "pageInfo": {}})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    await omni_list_document_access(
        ListDocumentAccessInput(
            document_id=DOC_ID,
            page_size=5,
            cursor="abc",
            sort_field="email",
            sort_direction="desc",
            access_source="direct",
            principal_type="userGroup",
        )
    )

    assert fake.calls == [
        (
            "GET",
            f"{DOC_PATH}/access-list",
            {
                "params": {
                    "pageSize": 5,
                    "sortField": "email",
                    "sortDirection": "desc",
                    "cursor": "abc",
                    "accessSource": "direct",
                    "type": "userGroup",
                }
            },
        )
    ]


async def test_list_document_access_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ACCESS_LIST_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_list_document_access(
        ListDocumentAccessInput(document_id=DOC_ID, response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["count"] == 2
    assert payload["items"][0]["name"] == "Jane Smith"


async def test_list_document_access_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Manager permission required"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_list_document_access(ListDocumentAccessInput(document_id=DOC_ID))

    assert result.startswith("Error (403):")


def test_list_document_access_validation() -> None:
    with pytest.raises(ValidationError):
        ListDocumentAccessInput(document_id=DOC_ID, page_size=0)
    with pytest.raises(ValidationError):
        ListDocumentAccessInput(document_id=DOC_ID, page_size=101)
    with pytest.raises(ValidationError):
        ListDocumentAccessInput(document_id=DOC_ID, sort_field="bogus")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ListDocumentAccessInput(document_id=DOC_ID, principal_type="bogus")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ListDocumentAccessInput(document_id="")
    with pytest.raises(ValidationError):
        ListDocumentAccessInput(document_id=DOC_ID, unexpected="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# omni_grant_document_permissions / omni_update_document_permissions
# ---------------------------------------------------------------------------


async def test_grant_document_permissions_with_users(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_grant_document_permissions(
        GrantDocumentPermissionsInput(document_id=DOC_ID, role="EDITOR", user_ids=[USER_ID])
    )

    assert "Granted role **EDITOR**" in result
    assert "1 user(s) and 0 group(s)" in result
    assert "succeeded" in result
    assert fake.calls == [
        (
            "POST",
            f"{DOC_PATH}/permissions",
            {"json_body": {"role": "EDITOR", "accessBoost": False, "userIds": [USER_ID]}},
        )
    ]


async def test_grant_document_permissions_with_groups_and_boost(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    await omni_grant_document_permissions(
        GrantDocumentPermissionsInput(document_id=DOC_ID, role="VIEWER", user_group_ids=[GROUP_ID], access_boost=True)
    )

    assert fake.calls == [
        (
            "POST",
            f"{DOC_PATH}/permissions",
            {"json_body": {"role": "VIEWER", "accessBoost": True, "userGroupIds": [GROUP_ID]}},
        )
    ]


async def test_grant_document_permissions_missing_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_grant_document_permissions(
        GrantDocumentPermissionsInput(document_id=DOC_ID, role="EDITOR", user_ids=[USER_ID])
    )

    assert "returned without a `success` flag" in result


async def test_grant_document_permissions_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Manager permission required", method="POST"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_grant_document_permissions(
        GrantDocumentPermissionsInput(document_id=DOC_ID, role="EDITOR", user_ids=[USER_ID])
    )

    assert result.startswith("Error (403):")


def test_grant_document_permissions_validation() -> None:
    with pytest.raises(ValidationError):
        GrantDocumentPermissionsInput(document_id=DOC_ID, role="EDITOR")
    with pytest.raises(ValidationError):
        GrantDocumentPermissionsInput(document_id=DOC_ID, role="BOGUS", user_ids=[USER_ID])  # type: ignore[arg-type]


async def test_update_document_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_update_document_permissions(
        UpdateDocumentPermissionsInput(document_id=DOC_ID, role="MANAGER", user_ids=[USER_ID])
    )

    assert "Updated role **MANAGER**" in result
    assert fake.calls == [
        (
            "PATCH",
            f"{DOC_PATH}/permissions",
            {"json_body": {"role": "MANAGER", "accessBoost": False, "userIds": [USER_ID]}},
        )
    ]


def test_update_document_permissions_validation() -> None:
    with pytest.raises(ValidationError):
        UpdateDocumentPermissionsInput(document_id=DOC_ID, role="MANAGER")


# ---------------------------------------------------------------------------
# omni_update_document_permission_settings
# ---------------------------------------------------------------------------


async def test_update_document_permission_settings_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_update_document_permission_settings(
        UpdateDocumentPermissionSettingsInput(
            document_id=DOC_ID, can_save_spreadsheets=False, can_request_access=False, can_download=True
        )
    )

    assert "canSaveSpreadsheets" in result
    assert "canDownload" in result
    assert fake.calls == [
        (
            "PUT",
            f"{DOC_PATH}/permissions",
            {
                "json_body": {
                    "canDownload": True,
                    "canRequestAccess": False,
                    "canSaveSpreadsheets": False,
                }
            },
        )
    ]


async def test_update_document_permission_settings_organization_role(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    await omni_update_document_permission_settings(
        UpdateDocumentPermissionSettingsInput(document_id=DOC_ID, organization_role="VIEWER")
    )

    assert fake.calls == [("PUT", f"{DOC_PATH}/permissions", {"json_body": {"organizationRole": "VIEWER"}})]


async def test_update_document_permission_settings_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_update_document_permission_settings(UpdateDocumentPermissionSettingsInput(document_id=DOC_ID))

    assert "no fields (nothing sent)" in result
    assert fake.calls == [("PUT", f"{DOC_PATH}/permissions", {"json_body": {}})]


async def test_update_document_permission_settings_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Invalid parameter value", method="PUT"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_update_document_permission_settings(
        UpdateDocumentPermissionSettingsInput(document_id=DOC_ID, can_download=True)
    )

    assert result.startswith("Error (400):")


def test_update_document_permission_settings_validation() -> None:
    with pytest.raises(ValidationError):
        UpdateDocumentPermissionSettingsInput(document_id=DOC_ID, organization_role="OWNER")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# omni_revoke_document_permissions
# ---------------------------------------------------------------------------


async def test_revoke_document_permissions_users(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_revoke_document_permissions(
        RevokeDocumentPermissionsInput(document_id=DOC_ID, user_ids=[USER_ID])
    )

    assert "Revoked permissions" in result
    assert "1 user(s) and 0 group(s)" in result
    assert fake.calls == [("DELETE", f"{DOC_PATH}/permissions", {"json_body": {"userIds": [USER_ID]}})]


async def test_revoke_document_permissions_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    await omni_revoke_document_permissions(
        RevokeDocumentPermissionsInput(document_id=DOC_ID, user_group_ids=[GROUP_ID])
    )

    assert fake.calls == [("DELETE", f"{DOC_PATH}/permissions", {"json_body": {"userGroupIds": [GROUP_ID]}})]


async def test_revoke_document_permissions_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Document not found", method="DELETE"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_revoke_document_permissions(
        RevokeDocumentPermissionsInput(document_id=DOC_ID, user_ids=[USER_ID])
    )

    assert result.startswith("Error (404):")


def test_revoke_document_permissions_validation() -> None:
    with pytest.raises(ValidationError):
        RevokeDocumentPermissionsInput(document_id=DOC_ID)


# ---------------------------------------------------------------------------
# omni_favorite_document / omni_unfavorite_document
# ---------------------------------------------------------------------------


async def test_favorite_document(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_favorite_document(FavoriteDocumentInput(document_id=DOC_ID))

    assert result == f"Favorited document `{DOC_ID}`."
    assert fake.calls == [("PUT", f"{DOC_PATH}/favorite", {"params": None})]


async def test_favorite_document_on_behalf_of(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_favorite_document(FavoriteDocumentInput(document_id=DOC_ID, user_id=USER_ID))

    assert f"on behalf of user `{USER_ID}`" in result
    assert fake.calls == [("PUT", f"{DOC_PATH}/favorite", {"params": {"userId": USER_ID}})]


async def test_favorite_document_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "PAT cannot act on behalf of another user", method="PUT"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_favorite_document(FavoriteDocumentInput(document_id=DOC_ID, user_id=USER_ID))

    assert result.startswith("Error (403):")


async def test_unfavorite_document(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_unfavorite_document(UnfavoriteDocumentInput(document_id=DOC_ID))

    assert result == f"Unfavorited document `{DOC_ID}`."
    assert fake.calls == [("DELETE", f"{DOC_PATH}/favorite", {"params": None})]


async def test_unfavorite_document_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Document not published", method="DELETE"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_unfavorite_document(UnfavoriteDocumentInput(document_id=DOC_ID))

    assert result.startswith("Error (404):")


# ---------------------------------------------------------------------------
# omni_list_document_favoriters
# ---------------------------------------------------------------------------

FAVORITERS_PAYLOAD = {
    "pageInfo": {"hasNextPage": False, "nextCursor": None, "pageSize": 20, "totalRecords": 2},
    "records": [
        {
            "userId": "f1c2a3e4-1111-1111-1111-111111111111",
            "name": "Blob Ross",
            "email": "blob.ross@eblobsrus.com",
            "favoritedAt": "2026-04-12T10:14:02.000Z",
        },
        {
            "userId": "f1c2a3e4-2222-2222-2222-222222222222",
            "name": "Blob the Builder",
            "email": "blob.the.builder@blobsrus.com",
            "favoritedAt": "2026-05-01T17:33:21.000Z",
        },
    ],
}


async def test_list_document_favoriters_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=FAVORITERS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_list_document_favoriters(ListDocumentFavoritersInput(identifier=DOC_ID))

    assert "Blob Ross" in result
    assert "Blob the Builder" in result
    assert fake.calls == [("GET", f"{DOC_PATH}/favorites", {"params": {"pageSize": 20, "sortDirection": "asc"}})]


async def test_list_document_favoriters_cursor_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"records": [], "pageInfo": {}})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    await omni_list_document_favoriters(
        ListDocumentFavoritersInput(identifier=DOC_ID, cursor="abc123", sort_direction="desc", page_size=5)
    )

    assert fake.calls == [
        (
            "GET",
            f"{DOC_PATH}/favorites",
            {"params": {"pageSize": 5, "sortDirection": "desc", "cursor": "abc123"}},
        )
    ]


async def test_list_document_favoriters_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"records": [], "pageInfo": {"hasNextPage": False, "totalRecords": 0}})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_list_document_favoriters(ListDocumentFavoritersInput(identifier=DOC_ID))

    assert "_No records._" in result


async def test_list_document_favoriters_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=FAVORITERS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_list_document_favoriters(
        ListDocumentFavoritersInput(identifier=DOC_ID, response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["totalRecords"] == 2
    assert payload["items"][0]["name"] == "Blob Ross"


async def test_list_document_favoriters_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Organization API key required"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_list_document_favoriters(ListDocumentFavoritersInput(identifier=DOC_ID))

    assert result.startswith("Error (403):")


def test_list_document_favoriters_validation() -> None:
    with pytest.raises(ValidationError):
        ListDocumentFavoritersInput(identifier=DOC_ID, page_size=0)
    with pytest.raises(ValidationError):
        ListDocumentFavoritersInput(identifier=DOC_ID, page_size=101)
    with pytest.raises(ValidationError):
        ListDocumentFavoritersInput(identifier=DOC_ID, sort_direction="sideways")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ListDocumentFavoritersInput(identifier="")


# ---------------------------------------------------------------------------
# omni_bulk_update_document_labels
# ---------------------------------------------------------------------------


async def test_bulk_update_labels_add_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"labels": ["production", "reviewed"]})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_bulk_update_document_labels(
        BulkUpdateDocumentLabelsInput(document_id=DOC_ID, add=["production", "reviewed"])
    )

    assert "production, reviewed" in result
    assert fake.calls == [
        (
            "PATCH",
            f"{DOC_PATH}/labels",
            {"params": None, "json_body": {"add": ["production", "reviewed"]}},
        )
    ]


async def test_bulk_update_labels_remove_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"labels": []})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    await omni_bulk_update_document_labels(
        BulkUpdateDocumentLabelsInput(document_id=DOC_ID, remove=["draft", "needs-review"], user_id=USER_ID)
    )

    assert fake.calls == [
        (
            "PATCH",
            f"{DOC_PATH}/labels",
            {"params": {"userId": USER_ID}, "json_body": {"remove": ["draft", "needs-review"]}},
        )
    ]


async def test_bulk_update_labels_add_and_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"labels": ["approved"]})
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    await omni_bulk_update_document_labels(
        BulkUpdateDocumentLabelsInput(document_id=DOC_ID, add=["approved"], remove=["pending-review"])
    )

    assert fake.calls == [
        (
            "PATCH",
            f"{DOC_PATH}/labels",
            {"params": None, "json_body": {"add": ["approved"], "remove": ["pending-review"]}},
        )
    ]


async def test_bulk_update_labels_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Empty request", method="PATCH"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_bulk_update_document_labels(
        BulkUpdateDocumentLabelsInput(document_id=DOC_ID, add=["production"])
    )

    assert result.startswith("Error (400):")


def test_bulk_update_labels_validation() -> None:
    with pytest.raises(ValidationError):
        BulkUpdateDocumentLabelsInput(document_id=DOC_ID)
    with pytest.raises(ValidationError):
        BulkUpdateDocumentLabelsInput(document_id=DOC_ID, add=["dup"], remove=["DUP"])
    with pytest.raises(ValidationError):
        BulkUpdateDocumentLabelsInput(document_id=DOC_ID, add=["a"])
    with pytest.raises(ValidationError):
        BulkUpdateDocumentLabelsInput(document_id=DOC_ID, add=["x" * 26])


# ---------------------------------------------------------------------------
# omni_apply_document_label / omni_remove_document_label
# ---------------------------------------------------------------------------


async def test_apply_document_label(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_apply_document_label(ApplyDocumentLabelInput(document_id=DOC_ID, label_name="production"))

    assert "Applied label **production**" in result
    assert fake.calls == [("PUT", f"{DOC_PATH}/labels/production", {"params": None})]


async def test_apply_document_label_encodes_special_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    await omni_apply_document_label(ApplyDocumentLabelInput(document_id=DOC_ID, label_name="Q1 2024", user_id=USER_ID))

    assert fake.calls == [("PUT", f"{DOC_PATH}/labels/Q1%202024", {"params": {"userId": USER_ID}})]


async def test_apply_document_label_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Label does not exist", method="PUT"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_apply_document_label(ApplyDocumentLabelInput(document_id=DOC_ID, label_name="production"))

    assert result.startswith("Error (404):")


def test_apply_document_label_validation() -> None:
    with pytest.raises(ValidationError):
        ApplyDocumentLabelInput(document_id=DOC_ID, label_name="a")
    with pytest.raises(ValidationError):
        ApplyDocumentLabelInput(document_id=DOC_ID, label_name="x" * 26)


async def test_remove_document_label(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_remove_document_label(RemoveDocumentLabelInput(document_id=DOC_ID, label_name="draft"))

    assert "Removed label **draft**" in result
    assert fake.calls == [("DELETE", f"{DOC_PATH}/labels/draft", {"params": None})]


async def test_remove_document_label_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Label does not exist on this document", method="DELETE"))
    monkeypatch.setattr("omni_mcp.tools.document_access.get_client", lambda: fake)

    result = await omni_remove_document_label(RemoveDocumentLabelInput(document_id=DOC_ID, label_name="draft"))

    assert result.startswith("Error (404):")


def test_remove_document_label_validation() -> None:
    with pytest.raises(ValidationError):
        RemoveDocumentLabelInput(document_id=DOC_ID, label_name="a")
    with pytest.raises(ValidationError):
        RemoveDocumentLabelInput(document_id=DOC_ID, label_name="x" * 26)


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GetDocumentPermissionsInput(document_id=DOC_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        FavoriteDocumentInput(document_id=DOC_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        RemoveDocumentLabelInput(document_id=DOC_ID, label_name="draft", unexpected="x")  # type: ignore[call-arg]
