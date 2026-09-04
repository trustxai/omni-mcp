"""Unit tests for the documents (v1) tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.documents import (
    ArchiveDocumentDraftInput,
    CreateDocumentDraftInput,
    CreateDocumentInput,
    DeleteDocumentInput,
    DuplicateDocumentInput,
    GetDocumentInput,
    GetDocumentQueriesInput,
    ListDocumentDraftsInput,
    ListDocumentsInput,
    MoveDocumentInput,
    RemoveDashboardFromDocumentInput,
    TransferDocumentOwnershipInput,
    UpgradeDashboardLayoutInput,
    omni_archive_document_draft,
    omni_create_document,
    omni_create_document_draft,
    omni_delete_document,
    omni_duplicate_document,
    omni_get_document,
    omni_get_document_queries,
    omni_list_document_drafts,
    omni_list_documents,
    omni_move_document,
    omni_remove_dashboard_from_document,
    omni_transfer_document_ownership,
    omni_upgrade_dashboard_layout,
)

MODULE = "omni_mcp.tools.documents.get_client"

DOCUMENT_RECORD: dict[str, Any] = {
    "connectionId": "conn-1",
    "deleted": False,
    "folder": {"id": "folder-1", "name": "Revenue", "path": "/finance/revenue", "scope": "organization"},
    "hasDashboard": True,
    "identifier": "abc123",
    "labels": ["finance", "weekly"],
    "name": "Sales Dashboard",
    "owner": {"id": "member-1", "name": "Blob Ross"},
    "scope": "organization",
    "type": "document",
    "updatedAt": "2026-05-06T13:44:26.000Z",
    "url": "https://acme.omniapp.co/dashboards/abc123",
    "_count": {"favorites": 3, "views": 42},
}

LIST_PAYLOAD: dict[str, Any] = {
    "pageInfo": {"hasNextPage": True, "nextCursor": "cursor-2", "pageSize": 20, "totalRecords": 57},
    "records": [DOCUMENT_RECORD],
}

DOCUMENT_DETAIL: dict[str, Any] = {
    "name": "Sales Dashboard",
    "modelId": "abc123de-f456-7890-abcd-ef1234567890",
    "facetFilters": True,
    "description": "Q1 2026 sales",
    "refreshInterval": 300,
    "filterConfig": {"date_range": {"type": "date"}},
    "filterOrder": ["date_range", "region"],
    "queryPresentations": [
        {
            "name": "Revenue by Region",
            "id": "cd54494d-318f-4105-af1c-10af59e62b2e",
            "chartType": "bar",
            "query": {"fields": ["order_items.total_revenue", "orders.region"], "table": "order_items"},
        }
    ],
}

DRAFTS_PAYLOAD: list[dict[str, Any]] = [
    {
        "identifier": "d56b7f56",
        "publishedIdentifier": "ecd01fe5",
        "workbookModelId": "d61034e2-05fe-4268-a3a7-d4cdb8d650e9",
        "branch": {"id": "a9bc51d2-1234-5678-9abc-def012345678", "name": "governance/relabel-age-average"},
        "status": "active",
        "draftOutOfDate": False,
        "createdAt": "2026-05-06T13:44:00.000Z",
        "updatedAt": "2026-05-06T13:44:26.000Z",
        "createdBy": {"name": "Blob Ross"},
        "lastEditedBy": {"name": "Blob Ross"},
    }
]

QUERIES_PAYLOAD: dict[str, Any] = {
    "queries": [
        {
            "id": "9f1c2d3e-4567-890a-bcde-f01234567890",
            "name": "Revenue by Region",
            "url": "https://acme.omniapp.co/w/abc123?key=1",
            "query": {"fields": ["order_items.total_revenue"], "table": "order_items", "limit": 1000},
        }
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


def _http_error(
    status: int, detail: str, method: str = "GET", url: str = "https://acme.omniapp.co/api/v1/documents"
) -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> _FakeClient:
    monkeypatch.setattr(MODULE, lambda: fake)
    return fake


# --------------------------------------------------------------------------- list


async def test_list_documents_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=LIST_PAYLOAD))

    result = await omni_list_documents(ListDocumentsInput())

    assert "Sales Dashboard" in result
    assert "abc123" in result
    assert "/finance/revenue" in result
    assert "Blob Ross" in result
    assert "2026-05-06T13:44:26.000Z" in result
    assert "finance, weekly" in result
    assert "cursor-2" in result
    assert fake.calls == [("GET", "/v1/documents", {"params": {"pageSize": 20}})]


async def test_list_documents_sends_every_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=LIST_PAYLOAD))

    await omni_list_documents(
        ListDocumentsInput(
            include=["_count", "labels"],
            labels=["finance", "marketing"],
            folder_id="folder-1",
            page_size=50,
            sort_field="updatedAt",
            sort_direction="asc",
            cursor="cursor-1",
            user_id="user-1",
            creator_id="creator-1",
        )
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/documents",
            {
                "params": {
                    "pageSize": 50,
                    "include": "_count,labels",
                    "labels": "finance,marketing",
                    "folderId": "folder-1",
                    "sortField": "updatedAt",
                    "sortDirection": "asc",
                    "cursor": "cursor-1",
                    "userId": "user-1",
                    "creatorId": "creator-1",
                }
            },
        )
    ]


async def test_list_documents_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=LIST_PAYLOAD))

    payload = json.loads(await omni_list_documents(ListDocumentsInput(response_format=ResponseFormat.JSON)))

    assert payload["count"] == 1
    assert payload["totalRecords"] == 57
    assert payload["nextCursor"] == "cursor-2"
    assert payload["items"][0]["identifier"] == "abc123"


async def test_list_documents_handles_root_level_document(monkeypatch: pytest.MonkeyPatch) -> None:
    record = dict(DOCUMENT_RECORD, folder=None, labels=[], hasDashboard=False, deleted=True, _count=None)
    _patch(monkeypatch, _FakeClient(payload={"pageInfo": {}, "records": [record]}))

    result = await omni_list_documents(ListDocumentsInput())

    assert "folder: root" in result
    assert "labels: none" in result
    assert "dashboard: no" in result
    assert "**deleted**" in result


async def test_list_documents_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(400, "onlySharedWithMe requires userId")))

    result = await omni_list_documents(ListDocumentsInput(include=["onlySharedWithMe"]))

    assert result.startswith("Error (400):")
    assert "onlySharedWithMe requires userId" in result


def test_list_documents_rejects_shared_with_favorites() -> None:
    with pytest.raises(ValueError, match="onlySharedWithMe cannot be combined with onlyFavorites"):
        ListDocumentsInput(include=["onlySharedWithMe", "onlyFavorites"])


def test_list_documents_rejects_shared_with_folder() -> None:
    with pytest.raises(ValueError, match="onlySharedWithMe cannot be combined with folderId"):
        ListDocumentsInput(include=["onlySharedWithMe"], folder_id="folder-1")


def test_list_documents_rejects_bad_page_size_and_enums() -> None:
    with pytest.raises(ValueError):
        ListDocumentsInput(page_size=0)
    with pytest.raises(ValueError):
        ListDocumentsInput(page_size=101)
    with pytest.raises(ValueError):
        ListDocumentsInput(sort_field="created")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ListDocumentsInput(sort_direction="sideways")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ListDocumentsInput(include=["everything"])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        ListDocumentsInput(unexpected="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------- get


async def test_get_document_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=DOCUMENT_DETAIL))

    result = await omni_get_document(GetDocumentInput(document_id="abc 123"))

    assert "# Sales Dashboard" in result
    assert "abc123de-f456-7890-abcd-ef1234567890" in result
    assert "Revenue by Region" in result
    assert "date_range, region" in result
    assert fake.calls == [("GET", "/v1/documents/abc%20123", {})]


async def test_get_document_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=DOCUMENT_DETAIL))

    payload = json.loads(await omni_get_document(GetDocumentInput(document_id="abc123", response_format="json")))

    assert payload["modelId"] == "abc123de-f456-7890-abcd-ef1234567890"
    assert payload["queryPresentations"][0]["name"] == "Revenue by Region"


async def test_get_document_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(404, 'Document with identifier "abc123" not found')))

    result = await omni_get_document(GetDocumentInput(document_id="abc123"))

    assert result.startswith("Error (404):")
    assert "not found" in result


def test_get_document_requires_identifier() -> None:
    with pytest.raises(ValueError):
        GetDocumentInput(document_id="")


# ------------------------------------------------------------------------- create


async def test_create_document(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "dashboard": {"id": "dash-1"},
        "workbook": {"id": "wb-1", "identifier": "sales-dashboard-2026", "name": "Sales Dashboard"},
    }
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_create_document(
        CreateDocumentInput(
            model_id="model-1",
            name="Sales Dashboard",
            description="Q1 2026 sales performance metrics",
            identifier="sales-dashboard-2026",
            query_presentations=[{"name": "Revenue", "query": {"table": "orders"}}],
        )
    )

    assert "Created document **Sales Dashboard**" in result
    assert "sales-dashboard-2026" in result
    assert "wb-1" in result
    assert "dash-1" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/documents",
            {
                "json_body": {
                    "modelId": "model-1",
                    "name": "Sales Dashboard",
                    "description": "Q1 2026 sales performance metrics",
                    "identifier": "sales-dashboard-2026",
                    "queryPresentations": [{"name": "Revenue", "query": {"table": "orders"}}],
                }
            },
        )
    ]


async def test_create_document_omits_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"workbook": {"id": "wb-1", "identifier": "auto1"}}))

    await omni_create_document(CreateDocumentInput(model_id="model-1", name="Bare"))

    assert fake.calls == [("POST", "/v1/documents", {"json_body": {"modelId": "model-1", "name": "Bare"}})]


async def test_create_document_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(400, "Identifier is already in use by another document")))

    result = await omni_create_document(CreateDocumentInput(model_id="model-1", name="Dup", identifier="taken"))

    assert result.startswith("Error (400):")
    assert "already in use" in result


def test_create_document_validates_lengths() -> None:
    with pytest.raises(ValueError):
        CreateDocumentInput(model_id="model-1", name="")
    with pytest.raises(ValueError):
        CreateDocumentInput(model_id="model-1", name="ok", description="x" * 1025)


# ------------------------------------------------------------------------- delete


async def test_delete_document(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_delete_document(DeleteDocumentInput(document_id="abc/123"))

    assert "Deleted document `abc/123`" in result
    assert fake.calls == [("DELETE", "/v1/documents/abc%2F123", {})]


async def test_delete_document_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(404, 'Document with identifier "gone" not found')))

    result = await omni_delete_document(DeleteDocumentInput(document_id="gone"))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- move


async def test_move_document_to_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_move_document(
        MoveDocumentInput(document_id="abc123", folder_path="/reports/2026/q1", scope="organization")
    )

    assert "Moved document `abc123` to `/reports/2026/q1`" in result
    assert "organization" in result
    assert fake.calls == [
        (
            "PUT",
            "/v1/documents/abc123/move",
            {"json_body": {"folderPath": "/reports/2026/q1", "scope": "organization"}},
        )
    ]


async def test_move_document_to_root_sends_null_folder_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_move_document(MoveDocumentInput(document_id="abc123"))

    assert "the root level" in result
    assert fake.calls == [("PUT", "/v1/documents/abc123/move", {"json_body": {"folderPath": None}})]


async def test_move_document_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(403, "User-scoped API keys cannot act on behalf of other users")))

    result = await omni_move_document(MoveDocumentInput(document_id="abc123", folder_path="/x"))

    assert result.startswith("Error (403):")
    assert "User-scoped API keys" in result


def test_move_document_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError):
        MoveDocumentInput(document_id="abc123", scope="public")  # type: ignore[arg-type]


# ---------------------------------------------------------------------- duplicate


async def test_duplicate_document(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "dashboardId": "abc123-dash-id",
        "identifier": "xyz789-new-identifier",
        "name": "My Dashboard Copy",
        "workbookId": "def456-workbook-id",
    }
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_duplicate_document(
        DuplicateDocumentInput(
            document_id="abc123",
            name="My Dashboard Copy",
            folder_path="/reports/2024/q4",
            scope="organization",
            user_id="user-1",
        )
    )

    assert "My Dashboard Copy" in result
    assert "xyz789-new-identifier" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/documents/abc123/duplicate",
            {
                "params": {"userId": "user-1"},
                "json_body": {"name": "My Dashboard Copy", "folderPath": "/reports/2024/q4", "scope": "organization"},
            },
        )
    ]


async def test_duplicate_document_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"identifier": "new1", "name": "Copy"}))

    await omni_duplicate_document(DuplicateDocumentInput(document_id="abc123", name="Copy"))

    assert fake.calls == [
        ("POST", "/v1/documents/abc123/duplicate", {"params": None, "json_body": {"name": "Copy"}}),
    ]


async def test_duplicate_document_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(403, "Document duplication is disabled")))

    result = await omni_duplicate_document(DuplicateDocumentInput(document_id="abc123", name="Copy"))

    assert result.startswith("Error (403):")
    assert "Document duplication is disabled" in result


def test_duplicate_document_validates_name_bounds() -> None:
    with pytest.raises(ValueError):
        DuplicateDocumentInput(document_id="abc123", name="")
    with pytest.raises(ValueError):
        DuplicateDocumentInput(document_id="abc123", name="x" * 256)


# ------------------------------------------------------------------------ upgrade


async def test_upgrade_dashboard_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"identifier": "abc123", "upgraded": True}))

    result = await omni_upgrade_dashboard_layout(
        UpgradeDashboardLayoutInput(document_id="abc123", clear_existing_draft=True)
    )

    assert "Upgraded document `abc123`" in result
    assert fake.calls == [("POST", "/v1/documents/abc123/upgrade", {"json_body": {"clearExistingDraft": True}})]


async def test_upgrade_dashboard_layout_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"identifier": "abc123", "upgraded": False}))

    result = await omni_upgrade_dashboard_layout(UpgradeDashboardLayoutInput(document_id="abc123"))

    assert "already uses the advanced dashboard layout" in result
    assert fake.calls == [("POST", "/v1/documents/abc123/upgrade", {"json_body": {}})]


async def test_upgrade_dashboard_layout_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(409, "A draft already exists for the published document")))

    result = await omni_upgrade_dashboard_layout(UpgradeDashboardLayoutInput(document_id="abc123"))

    assert result.startswith("Error (409):")


# ----------------------------------------------------------------------- ownership


async def test_transfer_document_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_transfer_document_ownership(
        TransferDocumentOwnershipInput(document_id="abc123", user_id="9e8719d9-276a-4964-9395-a493189a247c")
    )

    assert "Transferred ownership of document `abc123`" in result
    assert "9e8719d9-276a-4964-9395-a493189a247c" in result
    assert fake.calls == [
        (
            "PUT",
            "/v1/documents/abc123/transfer-ownership",
            {"json_body": {"userId": "9e8719d9-276a-4964-9395-a493189a247c"}},
        )
    ]


async def test_transfer_document_ownership_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(400, "New owner must have explicit document permission")))

    result = await omni_transfer_document_ownership(
        TransferDocumentOwnershipInput(document_id="abc123", user_id="user-1")
    )

    assert result.startswith("Error (400):")
    assert "New owner must have explicit document permission" in result


def test_transfer_document_ownership_requires_user_id() -> None:
    with pytest.raises(ValueError):
        TransferDocumentOwnershipInput(document_id="abc123", user_id="")


# -------------------------------------------------------------------------- drafts


async def test_create_document_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"identifier": "93874a87"}))

    result = await omni_create_document_draft(
        CreateDocumentDraftInput(document_id="abc123", branch_id="a9bc51d2-1234-5678-9abc-def012345678")
    )

    assert "93874a87" in result
    assert "a9bc51d2-1234-5678-9abc-def012345678" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/documents/abc123/draft",
            {"json_body": {"branchId": "a9bc51d2-1234-5678-9abc-def012345678"}},
        )
    ]


async def test_create_document_draft_without_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"identifier": "93874a87"}))

    await omni_create_document_draft(CreateDocumentDraftInput(document_id="abc123"))

    assert fake.calls == [("POST", "/v1/documents/abc123/draft", {"json_body": {}})]


async def test_create_document_draft_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(403, "Permission denied - EDITOR role required")))

    result = await omni_create_document_draft(CreateDocumentDraftInput(document_id="abc123"))

    assert result.startswith("Error (403):")
    assert "EDITOR role required" in result


async def test_archive_document_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"message": "Draft deleted successfully"}))

    result = await omni_archive_document_draft(ArchiveDocumentDraftInput(document_id="abc123", branch_id="branch-1"))

    assert "Archived the draft of document `abc123`" in result
    assert "branch-1" in result
    assert fake.calls == [("DELETE", "/v1/documents/abc123/draft", {"json_body": {"branchId": "branch-1"}})]


async def test_archive_document_draft_without_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"message": "Draft deleted successfully"}))

    await omni_archive_document_draft(ArchiveDocumentDraftInput(document_id="abc123"))

    assert fake.calls == [("DELETE", "/v1/documents/abc123/draft", {"json_body": {}})]


async def test_archive_document_draft_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(404, "Draft with id abc123 does not exist")))

    result = await omni_archive_document_draft(ArchiveDocumentDraftInput(document_id="abc123"))

    assert result.startswith("Error (404):")
    assert "does not exist" in result


async def test_list_document_drafts_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=DRAFTS_PAYLOAD))

    result = await omni_list_document_drafts(ListDocumentDraftsInput(document_id="ecd01fe5", include="archived"))

    assert "d56b7f56" in result
    assert "governance/relabel-age-average" in result
    assert "2026-05-06T13:44:26.000Z" in result
    assert "including archived" in result
    assert fake.calls == [("GET", "/v1/documents/ecd01fe5/drafts", {"params": {"include": "archived"}})]


async def test_list_document_drafts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=DRAFTS_PAYLOAD))

    payload = json.loads(
        await omni_list_document_drafts(
            ListDocumentDraftsInput(document_id="ecd01fe5", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["count"] == 1
    assert payload["drafts"][0]["identifier"] == "d56b7f56"
    assert fake.calls == [("GET", "/v1/documents/ecd01fe5/drafts", {"params": None})]


async def test_list_document_drafts_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=[]))

    result = await omni_list_document_drafts(ListDocumentDraftsInput(document_id="ecd01fe5"))

    assert "Showing **0** draft(s) (active only)." in result
    assert "_No rows._" in result


async def test_list_document_drafts_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(403, "You do not have permission to view this document.")))

    result = await omni_list_document_drafts(ListDocumentDraftsInput(document_id="ecd01fe5"))

    assert result.startswith("Error (403):")


def test_list_document_drafts_rejects_bad_include() -> None:
    with pytest.raises(ValueError):
        ListDocumentDraftsInput(document_id="ecd01fe5", include="deleted")  # type: ignore[arg-type]


# ------------------------------------------------------------------------ queries


async def test_get_document_queries_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=QUERIES_PAYLOAD))

    result = await omni_get_document_queries(GetDocumentQueriesInput(document_id="12db1a0a"))

    assert "Revenue by Region" in result
    assert "order_items" in result
    assert "https://acme.omniapp.co/w/abc123?key=1" in result
    assert fake.calls == [("GET", "/v1/documents/12db1a0a/queries", {})]


async def test_get_document_queries_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=QUERIES_PAYLOAD))

    payload = json.loads(
        await omni_get_document_queries(
            GetDocumentQueriesInput(document_id="12db1a0a", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["count"] == 1
    assert payload["queries"][0]["query"]["table"] == "order_items"


async def test_get_document_queries_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(403, "User cannot view document")))

    result = await omni_get_document_queries(GetDocumentQueriesInput(document_id="12db1a0a"))

    assert result.startswith("Error (403):")
    assert "User cannot view document" in result


# --------------------------------------------------------------- remove dashboard


async def test_remove_dashboard_from_document(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "identifier": "abc123",
        "draftIdentifier": "def456",
        "name": "Blob Sales",
        "description": "Overview of daily sales",
    }
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_remove_dashboard_from_document(
        RemoveDashboardFromDocumentInput(document_id="abc123", draft_id="def456")
    )

    assert "Removed the dashboard from draft `def456`" in result
    assert "Blob Sales" in result
    assert "schedules are removed on publish" in result
    assert fake.calls == [("DELETE", "/v2/documents/abc123/draft/def456/dashboard", {})]


async def test_remove_dashboard_from_document_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(422, "The document is an app, not a dashboard")))

    result = await omni_remove_dashboard_from_document(
        RemoveDashboardFromDocumentInput(document_id="abc123", draft_id="def456")
    )

    assert result.startswith("Error (422):")


def test_remove_dashboard_requires_both_ids() -> None:
    with pytest.raises(ValueError):
        RemoveDashboardFromDocumentInput(document_id="abc123", draft_id="")
    with pytest.raises(ValueError):
        RemoveDashboardFromDocumentInput(document_id="", draft_id="def456")


# ------------------------------------------------------------------ shared checks


def test_every_input_model_forbids_unknown_fields() -> None:
    models: list[type[Any]] = [
        ArchiveDocumentDraftInput,
        CreateDocumentDraftInput,
        CreateDocumentInput,
        DeleteDocumentInput,
        DuplicateDocumentInput,
        GetDocumentInput,
        GetDocumentQueriesInput,
        ListDocumentDraftsInput,
        ListDocumentsInput,
        MoveDocumentInput,
        RemoveDashboardFromDocumentInput,
        TransferDocumentOwnershipInput,
        UpgradeDashboardLayoutInput,
    ]
    for model in models:
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["str_strip_whitespace"] is True


def test_removed_v1_endpoints_are_documented_not_implemented() -> None:
    import omni_mcp.tools.documents as module

    assert not hasattr(module, "omni_update_document")
    assert not hasattr(module, "omni_rename_document")
    docstring = module.__doc__ or ""
    assert "410" in docstring
    assert "omni_patch_document_draft" in docstring
    assert "omni_rename_document_identifier" in docstring
