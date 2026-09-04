"""Unit tests for the folder / folder permission / folder label / label tools."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.folders import (
    BulkUpdateFolderLabelsInput,
    CreateFolderInput,
    CreateLabelInput,
    DeleteFolderInput,
    DeleteLabelInput,
    GetFolderPermissionsInput,
    GetLabelInput,
    GrantFolderPermissionsInput,
    ListFoldersInput,
    ListLabelsInput,
    RevokeFolderPermissionsInput,
    UpdateFolderInput,
    UpdateFolderPermissionsInput,
    UpdateLabelInput,
    omni_bulk_update_folder_labels,
    omni_create_folder,
    omni_create_label,
    omni_delete_folder,
    omni_delete_label,
    omni_get_folder_permissions,
    omni_get_label,
    omni_grant_folder_permissions,
    omni_list_folders,
    omni_list_labels,
    omni_revoke_folder_permissions,
    omni_update_folder,
    omni_update_folder_permissions,
    omni_update_label,
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


def _http_error(status: int, detail: str, url: str = "https://acme.omniapp.co/api/v1/folders") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


FOLDER: dict[str, Any] = {
    "id": "folder-1",
    "name": "Blob Sales",
    "path": "/blob-sales",
    "scope": "organization",
    "url": "https://blobsrus.omni.co/f/blob-sales-reports",
    "owner": {"id": "user-1", "name": "Blob Ross"},
    "labels": ["important", "archived"],
    "_count": {"documents": 15, "favorites": 3},
}

LABEL: dict[str, Any] = {
    "name": "Production",
    "verified": True,
    "homepage": False,
    "usage_count": 12,
    "color": "#0366d6",
    "description": "Documents verified and in prod",
}


# --------------------------------------------------------------------------
# omni_list_folders
# --------------------------------------------------------------------------


async def test_list_folders_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "records": [FOLDER],
            "pageInfo": {"hasNextPage": True, "nextCursor": "cursor-2", "pageSize": 20, "totalRecords": 45},
        }
    )
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_list_folders(ListFoldersInput())

    assert "Blob Sales" in result
    assert "folder-1" in result
    assert "Blob Ross" in result
    assert 'cursor="cursor-2"' in result
    assert fake.calls == [("GET", "/v1/folders", {"params": {"pageSize": 20}})]


async def test_list_folders_passes_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"records": [], "pageInfo": {}})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_list_folders(
        ListFoldersInput(
            path="blob-sales/*",
            labels=["important", "archived"],
            scope="restricted",
            sort_field="path",
            sort_direction="asc",
            cursor="abc",
            page_size=50,
            owner_id="owner-1",
            user_id="user-1",
            include="_count,labels",
        )
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/folders",
            {
                "params": {
                    "include": "_count,labels",
                    "path": "blob-sales/*",
                    "labels": "important,archived",
                    "scope": "restricted",
                    "sortField": "path",
                    "sortDirection": "asc",
                    "cursor": "abc",
                    "ownerId": "owner-1",
                    "userId": "user-1",
                    "pageSize": 50,
                }
            },
        )
    ]


async def test_list_folders_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"records": [FOLDER], "pageInfo": {"totalRecords": 1}})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    payload = json.loads(await omni_list_folders(ListFoldersInput(response_format=ResponseFormat.JSON)))

    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "folder-1"


async def test_list_folders_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client", lambda: _FakeClient(exc=_http_error(403, "Forbidden: scope"))
    )

    result = await omni_list_folders(ListFoldersInput())

    assert result.startswith("Error (403):")


def test_list_folders_rejects_bad_page_size() -> None:
    with pytest.raises(ValidationError):
        ListFoldersInput(page_size=0)
    with pytest.raises(ValidationError):
        ListFoldersInput(page_size=101)


def test_list_folders_rejects_invalid_scope() -> None:
    with pytest.raises(ValidationError):
        ListFoldersInput(scope="bogus")  # type: ignore[arg-type]


def test_list_folders_rejects_invalid_sort_field() -> None:
    with pytest.raises(ValidationError):
        ListFoldersInput(sort_field="bogus")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# omni_create_folder
# --------------------------------------------------------------------------


async def test_create_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=dict(FOLDER, id="new-folder"))
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_create_folder(CreateFolderInput(name="Blob Sales"))

    assert "Created folder" in result
    assert "new-folder" in result
    assert fake.calls == [("POST", "/v1/folders", {"json_body": {"name": "Blob Sales"}})]


async def test_create_folder_with_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=FOLDER)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_create_folder(
        CreateFolderInput(
            name="My Reports", parent_folder_id="parent-1", scope="restricted", user_id="user-1", breadcrumb_root=True
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/folders",
            {
                "json_body": {
                    "name": "My Reports",
                    "parentFolderId": "parent-1",
                    "scope": "restricted",
                    "userId": "user-1",
                    "breadcrumbRoot": True,
                }
            },
        )
    ]


async def test_create_folder_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(404, "Parent folder with id parent-1 does not exist")),
    )

    result = await omni_create_folder(CreateFolderInput(name="X", parent_folder_id="parent-1"))

    assert result.startswith("Error (404):")
    assert "parent-1" in result


# --------------------------------------------------------------------------
# omni_update_folder
# --------------------------------------------------------------------------


async def test_update_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "folder-1", "name": "Renamed", "path": "renamed", "breadcrumbRoot": False})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_update_folder(UpdateFolderInput(folder_id="folder-1", name="Renamed"))

    assert "Renamed" in result
    assert fake.calls == [("PATCH", "/v1/folders/folder-1", {"json_body": {"name": "Renamed"}})]


async def test_update_folder_path_and_conflict_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "folder-1", "name": "X", "path": "sales-reports"})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_update_folder(UpdateFolderInput(folder_id="folder-1", path="sales-reports", resolve_path_conflict=True))

    assert fake.calls == [
        (
            "PATCH",
            "/v1/folders/folder-1",
            {"json_body": {"path": "sales-reports", "resolvePathConflict": True}},
        )
    ]


async def test_update_folder_quotes_folder_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "a/b", "name": "X", "path": "x"})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_update_folder(UpdateFolderInput(folder_id="a/b", name="X"))

    assert fake.calls[0][1] == "/v1/folders/a%2Fb"


async def test_update_folder_requires_name_or_path() -> None:
    with pytest.raises(ValidationError):
        UpdateFolderInput(folder_id="folder-1")


async def test_update_folder_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client", lambda: _FakeClient(exc=_http_error(403, "Permission denied"))
    )

    result = await omni_update_folder(UpdateFolderInput(folder_id="folder-1", name="X"))

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------
# omni_delete_folder
# --------------------------------------------------------------------------


async def test_delete_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_delete_folder(DeleteFolderInput(folder_id="folder-1"))

    assert "Deleted folder `folder-1`" in result
    assert fake.calls == [("DELETE", "/v1/folders/folder-1", {"params": None})]


async def test_delete_folder_force(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_delete_folder(DeleteFolderInput(folder_id="folder-1", force=True))

    assert "(force)" in result
    assert fake.calls == [("DELETE", "/v1/folders/folder-1", {"params": {"force": True}})]


async def test_delete_folder_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(404, "Folder with id folder-1 does not exist")),
    )

    result = await omni_delete_folder(DeleteFolderInput(folder_id="folder-1"))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_bulk_update_folder_labels
# --------------------------------------------------------------------------


async def test_bulk_update_folder_labels_add(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"labels": ["production", "reviewed"]})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_bulk_update_folder_labels(
        BulkUpdateFolderLabelsInput(folder_id="folder-1", add=["production", "reviewed"])
    )

    assert "production" in result
    assert fake.calls == [
        (
            "PATCH",
            "/v1/folders/folder-1/labels",
            {"params": None, "json_body": {"add": ["production", "reviewed"]}},
        )
    ]


async def test_bulk_update_folder_labels_add_and_remove_with_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"labels": ["approved"]})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_bulk_update_folder_labels(
        BulkUpdateFolderLabelsInput(folder_id="folder-1", add=["approved"], remove=["pending-review"], user_id="user-9")
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v1/folders/folder-1/labels",
            {
                "params": {"userId": "user-9"},
                "json_body": {"add": ["approved"], "remove": ["pending-review"]},
            },
        )
    ]


def test_bulk_update_folder_labels_requires_add_or_remove() -> None:
    with pytest.raises(ValidationError):
        BulkUpdateFolderLabelsInput(folder_id="folder-1")


def test_bulk_update_folder_labels_rejects_overlap() -> None:
    with pytest.raises(ValidationError):
        BulkUpdateFolderLabelsInput(folder_id="folder-1", add=["shared"], remove=["Shared"])


def test_bulk_update_folder_labels_rejects_short_label() -> None:
    with pytest.raises(ValidationError):
        BulkUpdateFolderLabelsInput(folder_id="folder-1", add=["a"])


def test_bulk_update_folder_labels_rejects_long_label() -> None:
    with pytest.raises(ValidationError):
        BulkUpdateFolderLabelsInput(folder_id="folder-1", add=["x" * 26])


async def test_bulk_update_folder_labels_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client", lambda: _FakeClient(exc=_http_error(404, "Folder does not exist"))
    )

    result = await omni_bulk_update_folder_labels(BulkUpdateFolderLabelsInput(folder_id="folder-1", add=["x1"]))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_grant_folder_permissions
# --------------------------------------------------------------------------


async def test_grant_folder_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_grant_folder_permissions(
        GrantFolderPermissionsInput(folder_id="folder-1", role="EDITOR", user_ids=["user-1", "user-2"])
    )

    assert "EDITOR" in result
    assert "2 user(s)" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/folders/folder-1/permissions",
            {"json_body": {"role": "EDITOR", "userIds": ["user-1", "user-2"]}},
        )
    ]


async def test_grant_folder_permissions_group_with_access_boost(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_grant_folder_permissions(
        GrantFolderPermissionsInput(folder_id="folder-1", role="VIEWER", user_group_ids=["group-1"], access_boost=True)
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/folders/folder-1/permissions",
            {"json_body": {"role": "VIEWER", "accessBoost": True, "userGroupIds": ["group-1"]}},
        )
    ]


def test_grant_folder_permissions_requires_targets() -> None:
    with pytest.raises(ValidationError):
        GrantFolderPermissionsInput(folder_id="folder-1", role="EDITOR")


def test_grant_folder_permissions_rejects_bad_role() -> None:
    with pytest.raises(ValidationError):
        GrantFolderPermissionsInput(folder_id="folder-1", role="SUPERUSER", user_ids=["u1"])  # type: ignore[arg-type]


async def test_grant_folder_permissions_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(403, "User does not have permission to manage folder permissions")),
    )

    result = await omni_grant_folder_permissions(
        GrantFolderPermissionsInput(folder_id="folder-1", role="EDITOR", user_ids=["user-1"])
    )

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------
# omni_update_folder_permissions
# --------------------------------------------------------------------------


async def test_update_folder_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_update_folder_permissions(
        UpdateFolderPermissionsInput(folder_id="folder-1", role="MANAGER", user_group_ids=["group-1"])
    )

    assert "MANAGER" in result
    assert fake.calls == [
        (
            "PATCH",
            "/v1/folders/folder-1/permissions",
            {"json_body": {"role": "MANAGER", "userGroupIds": ["group-1"]}},
        )
    ]


def test_update_folder_permissions_requires_targets() -> None:
    with pytest.raises(ValidationError):
        UpdateFolderPermissionsInput(folder_id="folder-1", role="VIEWER")


async def test_update_folder_permissions_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(404, 'Folder with identifier "folder-1" not found')),
    )

    result = await omni_update_folder_permissions(
        UpdateFolderPermissionsInput(folder_id="folder-1", role="VIEWER", user_ids=["u1"])
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_get_folder_permissions
# --------------------------------------------------------------------------


PERMITS: dict[str, Any] = {
    "permits": [
        {
            "id": "user-1",
            "name": "Blob Ross",
            "type": "user",
            "description": "Direct grant",
            "direct": {"accessBoost": True, "isOwner": False, "role": "EDITOR"},
        }
    ]
}


async def test_get_folder_permissions_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=PERMITS)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_get_folder_permissions(GetFolderPermissionsInput(folder_id="folder-1", user_id="user-1"))

    assert "Blob Ross" in result
    assert "EDITOR" in result
    assert fake.calls == [("GET", "/v1/folders/folder-1/permissions", {"params": {"userId": "user-1"}})]


async def test_get_folder_permissions_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=PERMITS)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    payload = json.loads(
        await omni_get_folder_permissions(
            GetFolderPermissionsInput(folder_id="folder-1", user_id="user-1", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["folderId"] == "folder-1"
    assert payload["permits"][0]["direct"]["role"] == "EDITOR"


async def test_get_folder_permissions_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(403, "User does not have permission to manage folder permissions")),
    )

    result = await omni_get_folder_permissions(GetFolderPermissionsInput(folder_id="folder-1", user_id="user-1"))

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------
# omni_revoke_folder_permissions
# --------------------------------------------------------------------------


async def test_revoke_folder_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_revoke_folder_permissions(
        RevokeFolderPermissionsInput(folder_id="folder-1", user_ids=["user-1"])
    )

    assert "Revoked" in result
    assert fake.calls == [("DELETE", "/v1/folders/folder-1/permissions", {"json_body": {"userIds": ["user-1"]}})]


def test_revoke_folder_permissions_requires_targets() -> None:
    with pytest.raises(ValidationError):
        RevokeFolderPermissionsInput(folder_id="folder-1")


async def test_revoke_folder_permissions_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(404, 'Folder with identifier "folder-1" not found')),
    )

    result = await omni_revoke_folder_permissions(
        RevokeFolderPermissionsInput(folder_id="folder-1", user_group_ids=["group-1"])
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_create_label
# --------------------------------------------------------------------------


async def test_create_label(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=dict(LABEL, name="Dev", usage_count=0))
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_create_label(CreateLabelInput(name="Dev", color="#0366d6", description="Dev docs"))

    assert "Dev" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/labels",
            {"params": None, "json_body": {"name": "Dev", "color": "#0366d6", "description": "Dev docs"}},
        )
    ]


async def test_create_label_with_user_id_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LABEL)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_create_label(CreateLabelInput(name="Production", verified=True, user_id="user-9"))

    assert fake.calls == [
        (
            "POST",
            "/v1/labels",
            {"params": {"userId": "user-9"}, "json_body": {"name": "Production", "verified": True}},
        )
    ]


def test_create_label_rejects_short_name() -> None:
    with pytest.raises(ValidationError):
        CreateLabelInput(name="a")


def test_create_label_rejects_long_name() -> None:
    with pytest.raises(ValidationError):
        CreateLabelInput(name="x" * 26)


async def test_create_label_error_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(409, "A label with this name already exists")),
    )

    result = await omni_create_label(CreateLabelInput(name="Production"))

    assert result.startswith("Error (409):")


# --------------------------------------------------------------------------
# omni_list_labels
# --------------------------------------------------------------------------


async def test_list_labels_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"labels": [LABEL]})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_list_labels(ListLabelsInput())

    assert "Production" in result
    assert "verified" in result
    assert fake.calls == [("GET", "/v1/labels", {})]


async def test_list_labels_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"labels": [LABEL]})
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    payload = json.loads(await omni_list_labels(ListLabelsInput(response_format=ResponseFormat.JSON)))

    assert payload["items"][0]["name"] == "Production"


async def test_list_labels_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: _FakeClient(exc=_http_error(401, "Unauthorized")))

    result = await omni_list_labels(ListLabelsInput())

    assert result.startswith("Error (401):")


# --------------------------------------------------------------------------
# omni_get_label
# --------------------------------------------------------------------------


async def test_get_label_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LABEL)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_get_label(GetLabelInput(label_name="Production"))

    assert "Production" in result
    assert "**True**" in result
    assert fake.calls == [("GET", "/v1/labels/Production", {})]


async def test_get_label_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LABEL)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    payload = json.loads(
        await omni_get_label(GetLabelInput(label_name="Production", response_format=ResponseFormat.JSON))
    )

    assert payload["name"] == "Production"
    assert payload["usage_count"] == 12


async def test_get_label_quotes_special_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LABEL)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_get_label(GetLabelInput(label_name="In Review"))

    assert fake.calls[0][1] == "/v1/labels/In%20Review"


async def test_get_label_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client", lambda: _FakeClient(exc=_http_error(404, "Label not found"))
    )

    result = await omni_get_label(GetLabelInput(label_name="Missing"))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------
# omni_update_label
# --------------------------------------------------------------------------


async def test_update_label(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=dict(LABEL, name="Ready for Review"))
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_update_label(UpdateLabelInput(label_name="Draft", name="Ready for Review"))

    assert "Ready for Review" in result
    assert fake.calls == [
        (
            "PUT",
            "/v1/labels/Draft",
            {"params": None, "json_body": {"name": "Ready for Review"}},
        )
    ]


async def test_update_label_color_and_description_with_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LABEL)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_update_label(
        UpdateLabelInput(label_name="Production", color="#DDDDDD", description="Ready", user_id="user-9")
    )

    assert fake.calls == [
        (
            "PUT",
            "/v1/labels/Production",
            {"params": {"userId": "user-9"}, "json_body": {"color": "#DDDDDD", "description": "Ready"}},
        )
    ]


def test_update_label_rejects_short_name() -> None:
    with pytest.raises(ValidationError):
        UpdateLabelInput(label_name="Draft", name="a")


async def test_update_label_error_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(409, "The new label name already exists")),
    )

    result = await omni_update_label(UpdateLabelInput(label_name="Draft", name="Production"))

    assert result.startswith("Error (409):")


# --------------------------------------------------------------------------
# omni_delete_label
# --------------------------------------------------------------------------


async def test_delete_label(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    result = await omni_delete_label(DeleteLabelInput(label_name="Draft"))

    assert "Deleted label" in result
    assert "Draft" in result
    assert fake.calls == [("DELETE", "/v1/labels/Draft", {"params": None})]


async def test_delete_label_with_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)

    await omni_delete_label(DeleteLabelInput(label_name="Draft", user_id="user-9"))

    assert fake.calls == [("DELETE", "/v1/labels/Draft", {"params": {"userId": "user-9"}})]


async def test_delete_label_error_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omni_mcp.tools.folders.get_client",
        lambda: _FakeClient(exc=_http_error(409, "The label is currently applied to documents")),
    )

    result = await omni_delete_label(DeleteLabelInput(label_name="Draft"))

    assert result.startswith("Error (409):")


# --------------------------------------------------------------------------
# Shared input hygiene
# --------------------------------------------------------------------------


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ListFoldersInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CreateFolderInput(name="X", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        DeleteFolderInput(folder_id="folder-1", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CreateLabelInput(name="Dev", unexpected="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> _FakeClient:
    monkeypatch.setattr("omni_mcp.tools.folders.get_client", lambda: fake)
    return fake


async def test_folder_permission_writes_report_an_unsuccessful_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """`{"success": false}` is a 200 answer that means the write did not happen."""
    _install(monkeypatch, _FakeClient(payload={"success": False}))

    granted = await omni_grant_folder_permissions(
        GrantFolderPermissionsInput(folder_id="f1", role="VIEWER", user_ids=["u1"])
    )
    updated = await omni_update_folder_permissions(
        UpdateFolderPermissionsInput(folder_id="f1", role="EDITOR", user_ids=["u1"])
    )
    revoked = await omni_revoke_folder_permissions(RevokeFolderPermissionsInput(folder_id="f1", user_ids=["u1"]))

    for result in (granted, updated, revoked):
        assert "reported no success" in result


async def test_create_folder_does_not_invent_an_organization_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """An API response without `scope` is unknown, not organization-wide."""
    _install(monkeypatch, _FakeClient(payload={"id": "f1", "name": "My Reports", "path": "/my-reports"}))

    result = await omni_create_folder(CreateFolderInput(name="My Reports"))

    assert "scope **unknown**" in result


async def test_list_folders_renders_the_folder_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        _FakeClient(
            payload={
                "records": [
                    {
                        "id": "f1",
                        "name": "Reports",
                        "path": "/reports",
                        "scope": "organization",
                        "url": "https://acme.omniapp.co/folders/f1",
                    }
                ],
                "pageInfo": {},
            }
        ),
    )

    result = await omni_list_folders(ListFoldersInput())

    assert "https://acme.omniapp.co/folders/f1" in result
