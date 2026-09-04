"""Unit tests for the content / content-validator / content-migration tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from omni_mcp.config import get_settings
from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.content import (
    ExportDashboardInput,
    FindAndReplaceContentInput,
    GetContentInput,
    ImportDashboardInput,
    SearchDashboardsInput,
    ValidateContentInput,
    omni_export_dashboard,
    omni_find_and_replace_content,
    omni_get_content,
    omni_import_dashboard,
    omni_search_dashboards,
    omni_validate_content,
)

MODEL_ID = "550e8400-e29b-41d4-a716-446655440000"
VALIDATOR_PATH = f"/v1/models/{MODEL_ID}/content-validator"

CONTENT_PAGE: dict[str, Any] = {
    "records": [
        {
            "id": "folder-1",
            "identifier": "finance",
            "name": "Finance",
            "type": "folder",
            "path": "/Finance",
            "labels": [{"name": "verified"}],
            "_count": {"documents": 4},
            "updatedAt": "2025-01-15T10:00:00Z",
        },
        {"id": "doc-1", "identifier": "12db1a0a", "name": "Revenue by region", "type": "document"},
    ],
    "pageInfo": {"hasNextPage": True, "nextCursor": "cursor-2", "pageSize": 20, "totalRecords": 42},
}

SEARCH_RESULTS: dict[str, Any] = {
    "results": [
        {
            "identifier": "12db1a0a",
            "name": "Revenue by region",
            "description": "Quarterly revenue breakdown across all regions",
            "folderPath": "/Finance/Reports",
            "tileNames": ["Revenue Trend", "Regional Breakdown"],
            "url": "https://acme.omniapp.co/dashboards/12db1a0a",
            "verified": True,
        }
    ]
}

VALIDATION: dict[str, Any] = {
    "model_id": MODEL_ID,
    "branch": None,
    "content": [
        {
            "document_id": "abc123",
            "identifier": "dashboard-1",
            "name": "Sales Dashboard",
            "type": "Published",
            "updated_at": "2025-01-15T10:00:00Z",
            "folder": {"name": "Reports", "path": "/Reports"},
            "owner": {"email": "user@example.com", "name": "Jane Doe"},
            "require_pull_request_to_publish": False,
            "queries_and_issues": [
                {
                    "query_name": "Total Revenue",
                    "query_presentation_id": "qp-123",
                    "query_id_map_key": "1",
                    "issues": ["Field 'orders.old_field' not found in model"],
                }
            ],
            "dashboard_filter_issues": ["Filter references missing view 'legacy_orders'"],
        },
        {
            "document_id": "def456",
            "identifier": "order-analysis",
            "name": "Order Analysis",
            "type": "Published",
            "queries_and_issues": [{"query_name": "Orders by Status", "issues": []}],
            "dashboard_filter_issues": [],
        },
    ],
}

REPLACE_RESULT: dict[str, Any] = {
    "replaced_queries_count": 15,
    "replaced_documents_count": 5,
    "replaced_workbook_models_count": 3,
    "replaced_dashboard_filters_count": 2,
    "skipped_pr_required_count": 1,
}

EXPORT_PAYLOAD: dict[str, Any] = {
    "dashboard": {"name": "Revenue by region"},
    "document": {"identifier": "12db1a0a"},
    "workbookModel": {"id": "wb-1"},
    "exportVersion": "0.1",
    "fileUploads": [
        {"fileUploadId": "fu-1", "fileName": "targets.csv", "contentType": "text/csv", "data": "YSxiCjEsMg=="}
    ],
}

IMPORT_RESULT: dict[str, Any] = {
    "dashboard": {"dashboardId": "9f0a1b2c-3d4e-5f60-7182-93a4b5c6d7e8"},
    "miniUuidMap": {},
    "workbook": {"id": "wb-2"},
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


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> _FakeClient:
    monkeypatch.setattr("omni_mcp.tools.content.get_client", lambda: fake)
    return fake


def _status_error(status: int, detail: str, path: str = "/v1/content") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://acme.omniapp.co/api{path}")
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# --------------------------------------------------------------------------- get content


async def test_get_content_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=CONTENT_PAGE))

    result = await omni_get_content(GetContentInput())

    assert "Finance" in result
    assert "Revenue by region" in result
    assert "cursor-2" in result
    assert fake.calls == [
        ("GET", "/v1/content", {"params": {"scope": "organization", "sortField": "name", "pageSize": 20}})
    ]


async def test_get_content_passes_all_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=CONTENT_PAGE))

    await omni_get_content(
        GetContentInput(
            labels="finance,marketing",
            scope="restricted",
            sort_field="favorites",
            sort_direction="desc",
            include="labels,_count",
            path="/Finance/**",
            creator_id="9e8719d9-276a-4964-9395-a493189a247c",
            page_size=100,
            cursor="cursor-1",
        )
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/content",
            {
                "params": {
                    "scope": "restricted",
                    "sortField": "favorites",
                    "pageSize": 100,
                    "labels": "finance,marketing",
                    "sortDirection": "desc",
                    "include": "labels,_count",
                    "path": "/Finance/**",
                    "creatorId": "9e8719d9-276a-4964-9395-a493189a247c",
                    "cursor": "cursor-1",
                }
            },
        )
    ]


async def test_get_content_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=CONTENT_PAGE))

    payload = json.loads(await omni_get_content(GetContentInput(response_format=ResponseFormat.JSON)))

    assert payload["count"] == 2
    assert payload["totalRecords"] == 42
    assert payload["nextCursor"] == "cursor-2"
    assert payload["items"][0]["identifier"] == "finance"


async def test_get_content_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(404, "User with id abc does not exist")))

    result = await omni_get_content(GetContentInput())

    assert result.startswith("Error (404):")
    assert "does not exist" in result


def test_get_content_rejects_folder_id_with_path() -> None:
    with pytest.raises(ValueError, match="cannot be used together"):
        GetContentInput(folder_id=MODEL_ID, path="/Finance")


def test_get_content_requires_creator_for_restricted_scope() -> None:
    with pytest.raises(ValueError, match="creatorId required"):
        GetContentInput(scope="restricted")


def test_get_content_rejects_bad_page_size_and_unknown_fields() -> None:
    with pytest.raises(ValueError):
        GetContentInput(page_size=0)
    with pytest.raises(ValueError):
        GetContentInput(page_size=101)
    with pytest.raises(ValueError):
        GetContentInput(unexpected="x")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- search dashboards


async def test_search_dashboards_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=SEARCH_RESULTS))

    result = await omni_search_dashboards(SearchDashboardsInput(q="revenue by region"))

    assert "Revenue by region" in result
    assert "12db1a0a" in result
    assert "/Finance/Reports" in result
    assert "Regional Breakdown" in result
    assert fake.calls == [("GET", "/v1/content/search", {"params": {"q": "revenue by region", "limit": 10}})]


async def test_search_dashboards_json_and_user_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=SEARCH_RESULTS))

    payload = json.loads(
        await omni_search_dashboards(
            SearchDashboardsInput(
                q="churn",
                limit=25,
                user_id="9e8719d9-276a-4964-9395-a493189a247c",
                response_format=ResponseFormat.JSON,
            )
        )
    )

    assert payload["count"] == 1
    assert payload["results"][0]["identifier"] == "12db1a0a"
    assert fake.calls == [
        (
            "GET",
            "/v1/content/search",
            {"params": {"q": "churn", "limit": 25, "userId": "9e8719d9-276a-4964-9395-a493189a247c"}},
        )
    ]


async def test_search_dashboards_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload={"results": []}))

    result = await omni_search_dashboards(SearchDashboardsInput(q="nothing"))

    assert "_No dashboards matched._" in result


def test_search_dashboards_validates_q_and_limit() -> None:
    with pytest.raises(ValueError):
        SearchDashboardsInput(q="   ")
    with pytest.raises(ValueError):
        SearchDashboardsInput(q="revenue", limit=26)
    with pytest.raises(ValueError):
        SearchDashboardsInput(q="revenue", limit=0)


# --------------------------------------------------------------------------- validate content


async def test_validate_content_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=VALIDATION))

    result = await omni_validate_content(ValidateContentInput(model_id=MODEL_ID))

    assert "Sales Dashboard" in result
    assert "Field 'orders.old_field' not found in model" in result
    assert "legacy_orders" in result
    assert "Clean documents (1)" in result
    assert "Order Analysis" in result
    assert fake.calls == [("GET", VALIDATOR_PATH, {"params": None})]


async def test_validate_content_scoped_to_one_field(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=VALIDATION))

    await omni_validate_content(
        ValidateContentInput(
            model_id=MODEL_ID,
            branch_id="550e8400-e29b-41d4-a716-446655440001",
            user_id="9e8719d9-276a-4964-9395-a493189a247c",
            include_personal_folders=True,
            find="orders.status",
            find_type="FIELD",
        )
    )

    assert fake.calls == [
        (
            "GET",
            VALIDATOR_PATH,
            {
                "params": {
                    "branch_id": "550e8400-e29b-41d4-a716-446655440001",
                    "userId": "9e8719d9-276a-4964-9395-a493189a247c",
                    "include_personal_folders": True,
                    "find": "orders.status",
                    "find_type": "FIELD",
                }
            },
        )
    ]


async def test_validate_content_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=VALIDATION))

    payload = json.loads(
        await omni_validate_content(ValidateContentInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["model_id"] == MODEL_ID
    assert payload["content"][0]["identifier"] == "dashboard-1"


async def test_validate_content_clean_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload={"model_id": MODEL_ID, "branch": {"name": "feature"}, "content": []}))

    result = await omni_validate_content(ValidateContentInput(model_id=MODEL_ID))

    assert "**No broken references found.**" in result
    assert "feature" in result


async def test_validate_content_quotes_path_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(exc=_status_error(403, "User-scoped API keys cannot act on behalf")))

    result = await omni_validate_content(ValidateContentInput(model_id="model/1", user_id="u-1"))

    assert result.startswith("Error (403):")
    assert fake.calls[0][1] == "/v1/models/model%2F1/content-validator"


def test_validate_content_requires_find_pair() -> None:
    with pytest.raises(ValueError, match="must be provided together"):
        ValidateContentInput(model_id=MODEL_ID, find="orders")
    with pytest.raises(ValueError, match="must be provided together"):
        ValidateContentInput(model_id=MODEL_ID, find_type="VIEW")
    with pytest.raises(ValueError, match="scoped by view name"):
        ValidateContentInput(model_id=MODEL_ID, find="status", find_type="FIELD")


# --------------------------------------------------------------------------- find and replace


async def test_find_and_replace_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=REPLACE_RESULT))

    result = await omni_find_and_replace_content(
        FindAndReplaceContentInput(
            model_id=MODEL_ID, find="old_view", replacement="new_view", find_or_replace_type="VIEW"
        )
    )

    assert result.startswith("Refused:")
    assert "confirm: true" in result
    assert fake.calls == []


async def test_find_and_replace_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=REPLACE_RESULT))

    result = await omni_find_and_replace_content(
        FindAndReplaceContentInput(
            model_id=MODEL_ID,
            find="orders.old_field",
            replacement="orders.new_field",
            find_or_replace_type="FIELD",
            confirm=True,
            branch_id="550e8400-e29b-41d4-a716-446655440001",
            include_personal_folders=False,
            only_in_workbook_id="550e8400-e29b-41d4-a716-446655440002",
            user_id="9e8719d9-276a-4964-9395-a493189a247c",
        )
    )

    assert "orders.new_field" in result
    assert "15 query(ies)" in result
    assert "1 document(s) skipped" in result
    assert fake.calls == [
        (
            "POST",
            VALIDATOR_PATH,
            {
                "params": {"userId": "9e8719d9-276a-4964-9395-a493189a247c"},
                "json_body": {
                    "find": "orders.old_field",
                    "replacement": "orders.new_field",
                    "find_or_replace_type": "FIELD",
                    "branch_id": "550e8400-e29b-41d4-a716-446655440001",
                    "include_personal_folders": False,
                    "only_in_workbook_id": "550e8400-e29b-41d4-a716-446655440002",
                },
            },
        )
    ]


async def test_find_and_replace_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(404, f"Shared model with id {MODEL_ID} does not exist")))

    result = await omni_find_and_replace_content(
        FindAndReplaceContentInput(
            model_id=MODEL_ID, find="old_view", replacement="new_view", find_or_replace_type="VIEW", confirm=True
        )
    )

    assert result.startswith("Error (404):")


def test_find_and_replace_requires_qualified_field_names() -> None:
    with pytest.raises(ValueError, match="Find field must be scoped"):
        FindAndReplaceContentInput(
            model_id=MODEL_ID, find="old_field", replacement="orders.new_field", find_or_replace_type="FIELD"
        )
    # `replacement` is NOT policed locally: only the `find` side is a documented
    # 400, so an unqualified replacement reaches the API and gets its answer.
    FindAndReplaceContentInput(
        model_id=MODEL_ID, find="orders.old_field", replacement="new_field", find_or_replace_type="FIELD"
    )


# --------------------------------------------------------------------------- export dashboard


async def test_export_dashboard_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=EXPORT_PAYLOAD))

    result = await omni_export_dashboard(ExportDashboardInput(dashboard_id="12db1a0a"))

    assert "Dashboard export" in result
    assert "`0.1`" in result
    assert "workbookModel" in result
    assert fake.calls == [("GET", "/unstable/documents/12db1a0a/export", {})]


async def test_export_dashboard_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=EXPORT_PAYLOAD))

    payload = json.loads(
        await omni_export_dashboard(ExportDashboardInput(dashboard_id="12db1a0a", response_format=ResponseFormat.JSON))
    )

    assert payload["exportVersion"] == "0.1"
    assert payload["fileUploads"][0]["fileName"] == "targets.csv"


async def test_export_dashboard_quotes_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=EXPORT_PAYLOAD))

    await omni_export_dashboard(ExportDashboardInput(dashboard_id="a/b c"))

    assert fake.calls[0][1] == "/unstable/documents/a%2Fb%20c/export"


async def test_export_dashboard_writes_output_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch(monkeypatch, _FakeClient(payload=EXPORT_PAYLOAD))
    target = tmp_path / "nested" / "export.json"

    result = await omni_export_dashboard(ExportDashboardInput(dashboard_id="12db1a0a", output_path=str(target)))

    assert str(target) in result
    assert "bytes" in result
    assert json.loads(target.read_text(encoding="utf-8")) == EXPORT_PAYLOAD


async def test_export_dashboard_refuses_to_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch(monkeypatch, _FakeClient(payload=EXPORT_PAYLOAD))
    target = tmp_path / "export.json"
    target.write_text("keep me", encoding="utf-8")

    result = await omni_export_dashboard(ExportDashboardInput(dashboard_id="12db1a0a", output_path=str(target)))

    assert result.startswith("Error: ")
    assert "overwrite" in result
    assert target.read_text(encoding="utf-8") == "keep me"


async def test_export_dashboard_overwrites_when_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch(monkeypatch, _FakeClient(payload=EXPORT_PAYLOAD))
    target = tmp_path / "export.json"
    target.write_text("stale", encoding="utf-8")

    result = await omni_export_dashboard(
        ExportDashboardInput(dashboard_id="12db1a0a", output_path=str(target), overwrite=True)
    )

    assert str(target) in result
    assert json.loads(target.read_text(encoding="utf-8"))["exportVersion"] == "0.1"


async def test_export_dashboard_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        _FakeClient(exc=_status_error(404, "Dashboard not found", path="/unstable/documents/nope/export")),
    )

    result = await omni_export_dashboard(ExportDashboardInput(dashboard_id="nope"))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- import dashboard


async def test_import_dashboard_inline_document(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=IMPORT_RESULT))

    result = await omni_import_dashboard(
        ImportDashboardInput(
            base_model_id=MODEL_ID,
            export_payload=EXPORT_PAYLOAD,
            identifier="revenue-2026",
            folder_path="/Finance/Reports",
        )
    )

    assert "9f0a1b2c-3d4e-5f60-7182-93a4b5c6d7e8" in result
    assert "/Finance/Reports" in result
    assert fake.calls == [
        (
            "POST",
            "/unstable/documents/import",
            {
                "json_body": {
                    "baseModelId": MODEL_ID,
                    "dashboard": EXPORT_PAYLOAD["dashboard"],
                    "document": EXPORT_PAYLOAD["document"],
                    "workbookModel": EXPORT_PAYLOAD["workbookModel"],
                    "exportVersion": "0.1",
                    "fileUploads": EXPORT_PAYLOAD["fileUploads"],
                    "identifier": "revenue-2026",
                    "folderPath": "/Finance/Reports",
                }
            },
        )
    ]


async def test_import_dashboard_reads_input_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=IMPORT_RESULT))
    source = tmp_path / "export.json"
    source.write_text(json.dumps(EXPORT_PAYLOAD), encoding="utf-8")

    result = await omni_import_dashboard(ImportDashboardInput(base_model_id=MODEL_ID, input_path=str(source)))

    assert "Imported dashboard" in result
    assert fake.calls[0][0] == "POST"
    assert fake.calls[0][2]["json_body"]["exportVersion"] == "0.1"


async def test_import_dashboard_missing_input_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=IMPORT_RESULT))

    result = await omni_import_dashboard(
        ImportDashboardInput(base_model_id=MODEL_ID, input_path=str(tmp_path / "absent.json"))
    )

    assert result.startswith("Error: no such file")
    assert fake.calls == []


async def test_import_dashboard_rejects_invalid_json_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=IMPORT_RESULT))
    source = tmp_path / "broken.json"
    source.write_text("{not json", encoding="utf-8")

    result = await omni_import_dashboard(ImportDashboardInput(base_model_id=MODEL_ID, input_path=str(source)))

    assert "not valid JSON" in result
    assert fake.calls == []


async def test_import_dashboard_rejects_incomplete_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=IMPORT_RESULT))

    result = await omni_import_dashboard(
        ImportDashboardInput(base_model_id=MODEL_ID, export_payload={"dashboard": {}, "exportVersion": "0.1"})
    )

    assert "missing required key(s)" in result
    assert "workbookModel" in result
    assert fake.calls == []


async def test_import_dashboard_rejects_a_null_required_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key that is present but null is as unusable as a missing one."""
    fake = _patch(monkeypatch, _FakeClient(payload=IMPORT_RESULT))

    result = await omni_import_dashboard(
        ImportDashboardInput(base_model_id=MODEL_ID, export_payload={**EXPORT_PAYLOAD, "workbookModel": None})
    )

    assert "workbookModel" in result
    assert fake.calls == []


async def test_import_dashboard_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        _FakeClient(
            exc=_status_error(
                403, "User-scoped API keys cannot act on behalf of other users", path="/unstable/documents/import"
            )
        ),
    )

    result = await omni_import_dashboard(ImportDashboardInput(base_model_id=MODEL_ID, export_payload=EXPORT_PAYLOAD))

    assert result.startswith("Error (403):")


def test_import_dashboard_requires_exactly_one_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ImportDashboardInput(base_model_id=MODEL_ID)
    with pytest.raises(ValueError, match="exactly one"):
        ImportDashboardInput(base_model_id=MODEL_ID, export_payload=EXPORT_PAYLOAD, input_path="/tmp/export.json")


def test_content_inputs_reject_unknown_fields() -> None:
    for model in (
        SearchDashboardsInput,
        ValidateContentInput,
        FindAndReplaceContentInput,
        ExportDashboardInput,
        ImportDashboardInput,
    ):
        with pytest.raises(ValueError):
            model(unexpected="x")  # type: ignore[call-arg]


def test_export_dashboard_rejects_overwrite_without_a_path() -> None:
    """`overwrite` alone reads as "replace the file" but nothing is ever written."""
    with pytest.raises(ValueError, match="overwrite only applies"):
        ExportDashboardInput(dashboard_id="12db1a0a", overwrite=True)


async def test_export_dashboard_markdown_closes_its_fence_when_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON is truncated inside the fence, so the fence still closes."""
    monkeypatch.setenv("OMNI_MAX_RESULT_CHARS", "900")
    get_settings.cache_clear()
    _patch(monkeypatch, _FakeClient(payload={**EXPORT_PAYLOAD, "dashboard": {"filler": "x" * 5_000}}))

    result = await omni_export_dashboard(ExportDashboardInput(dashboard_id="12db1a0a"))

    assert result.endswith("```")
    assert result.count("```") == 2
    assert "truncated" in result
    get_settings.cache_clear()


async def test_get_content_maps_folder_id_to_the_folder_id_query_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`folder_id` is sent as `folderId`; the API rejects unrecognised query keys."""
    fake = _patch(monkeypatch, _FakeClient(payload={"records": [], "pageInfo": {}}))

    await omni_get_content(GetContentInput(folder_id="550e8400-e29b-41d4-a716-446655440000", include="labels"))

    _, _, kwargs = fake.calls[0]
    assert kwargs["params"]["folderId"] == "550e8400-e29b-41d4-a716-446655440000"
    assert "folder_id" not in kwargs["params"]
    assert kwargs["params"]["include"] == "labels"
