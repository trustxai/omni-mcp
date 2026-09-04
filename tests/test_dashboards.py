"""Unit tests for the dashboard download / dashboard filter tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.config import get_settings
from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools import dashboards
from omni_mcp.tools.dashboards import (
    DownloadDashboardFileInput,
    ExportDashboardFileInput,
    GetDashboardDownloadStatusInput,
    GetDashboardFiltersInput,
    InitiateDashboardDownloadInput,
    UpdateDashboardFiltersInput,
    omni_download_dashboard_file,
    omni_export_dashboard_file,
    omni_get_dashboard_download_status,
    omni_get_dashboard_filters,
    omni_initiate_dashboard_download,
    omni_update_dashboard_filters,
)


class _FakeResponse:
    """Minimal stand-in for `httpx.Response` for the binary-download path."""

    def __init__(self, content: bytes = b"", headers: dict[str, str] | None = None, status_code: int = 200) -> None:
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code


class _FakeClient:
    """Records `(method, path, kwargs)` calls; returns canned `request_json`/`request` results.

    `request_json` responses are consumed in order from `json_responses` (falling back to the
    single `payload`); `request` responses come from `binary_responses` (or raise
    `binary_exc`/return a default 200 response with `binary_content`).
    """

    def __init__(
        self,
        payload: Any = None,
        json_responses: list[Any] | None = None,
        exc: Exception | None = None,
        binary_responses: list[_FakeResponse] | None = None,
        binary_content: bytes = b"%PDF-1.4 fake",
        binary_headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload if payload is not None else {}
        self._json_responses = list(json_responses) if json_responses is not None else None
        self._exc = exc
        self._binary_responses = list(binary_responses) if binary_responses is not None else None
        self._binary_content = binary_content
        self._binary_headers = binary_headers if binary_headers is not None else {"content-type": "application/pdf"}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        if self._json_responses is not None:
            return self._json_responses.pop(0)
        return self._payload

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        if self._binary_responses is not None:
            return self._binary_responses.pop(0)
        return _FakeResponse(content=self._binary_content, headers=self._binary_headers, status_code=200)


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_BASE_URL", "https://acme.omniapp.co")
    monkeypatch.setenv("OMNI_API_KEY", "secret-key")
    get_settings.cache_clear()


def _http_error(status_code: int, body: dict[str, Any]) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/dashboards/abc/download")
    response = httpx.Response(status_code, json=body, request=request)
    return httpx.HTTPStatusError(f"error {status_code}", request=request, response=response)


# ---------------------------------------------------------------------------
# omni_initiate_dashboard_download
# ---------------------------------------------------------------------------


async def test_initiate_download_success(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"job_id": "job-1", "message": "Download initiated successfully"})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)

    result = await omni_initiate_dashboard_download(
        InitiateDashboardDownloadInput(dashboard_id="abc123", format="pdf", paper_orientation="landscape")
    )

    assert "job-1" in result
    assert "initiated" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/dashboards/abc123/download",
            {"params": None, "json_body": {"format": "pdf", "paperOrientation": "landscape"}},
        )
    ]


async def test_initiate_download_encodes_path_and_query(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"job_id": "job-2", "message": "ok"})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)

    await omni_initiate_dashboard_download(
        InitiateDashboardDownloadInput(dashboard_id="a/b c", format="csv", user_id="user-1")
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/dashboards/a%2Fb%20c/download",
            {"params": {"userId": "user-1"}, "json_body": {"format": "csv"}},
        )
    ]


async def test_initiate_download_omits_none_options(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload={"job_id": "job-3", "message": "ok"})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)

    await omni_initiate_dashboard_download(
        InitiateDashboardDownloadInput(
            dashboard_id="abc123",
            format="json",
            query_identifier_map_key="1",
            override_row_limit=True,
            max_row_limit=50_000,
            cache="SkipCache",
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/dashboards/abc123/download",
            {
                "params": None,
                "json_body": {
                    "format": "json",
                    "queryIdentifierMapKey": "1",
                    "overrideRowLimit": True,
                    "maxRowLimit": 50_000,
                    "cache": "SkipCache",
                },
            },
        )
    ]


async def test_initiate_download_conflict_error(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(409, {"detail": "A download is already in progress. job_id=existing-job", "status": 409})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(exc=error))

    result = await omni_initiate_dashboard_download(InitiateDashboardDownloadInput(dashboard_id="abc123", format="pdf"))

    assert result.startswith("Error (409):")
    assert "existing-job" in result


def test_initiate_download_rejects_invalid_format() -> None:
    with pytest.raises(ValueError):
        InitiateDashboardDownloadInput(dashboard_id="abc123", format="docx")  # type: ignore[arg-type]


def test_initiate_download_rejects_out_of_range_row_limit() -> None:
    with pytest.raises(ValueError):
        InitiateDashboardDownloadInput(dashboard_id="abc123", format="csv", max_row_limit=0)
    with pytest.raises(ValueError):
        InitiateDashboardDownloadInput(dashboard_id="abc123", format="csv", max_row_limit=1_000_001)


def test_initiate_download_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        InitiateDashboardDownloadInput(dashboard_id="abc123", format="pdf", unexpected="x")  # type: ignore[call-arg]


def test_initiate_download_requires_dashboard_id() -> None:
    with pytest.raises(ValueError):
        InitiateDashboardDownloadInput(dashboard_id="", format="pdf")


# ---------------------------------------------------------------------------
# omni_get_dashboard_download_status
# ---------------------------------------------------------------------------


STATUS_COMPLETE: dict[str, Any] = {
    "job_id": "job-1",
    "status": "complete",
    "format": "pdf",
    "created_at": "2024-01-15T10:30:00Z",
}


async def test_status_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=STATUS_COMPLETE)
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)

    result = await omni_get_dashboard_download_status(
        GetDashboardDownloadStatusInput(dashboard_id="abc123", job_id="job-1")
    )

    assert "complete" in result
    assert "omni_download_dashboard_file" in result
    assert fake.calls == [("GET", "/v1/dashboards/abc123/download/job-1/status", {})]


async def test_status_error_status(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    payload = dict(STATUS_COMPLETE, status="error", error="All queries failed.")
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(payload=payload))

    result = await omni_get_dashboard_download_status(
        GetDashboardDownloadStatusInput(dashboard_id="abc123", job_id="job-1")
    )

    assert "All queries failed." in result
    assert "**error**" in result


async def test_status_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(payload=STATUS_COMPLETE))

    payload = json.loads(
        await omni_get_dashboard_download_status(
            GetDashboardDownloadStatusInput(dashboard_id="abc123", job_id="job-1", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["status"] == "complete"
    assert payload["job_id"] == "job-1"


async def test_status_not_found_error(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(404, {"detail": "Job not found", "status": 404})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(exc=error))

    result = await omni_get_dashboard_download_status(
        GetDashboardDownloadStatusInput(dashboard_id="abc123", job_id="job-1")
    )

    assert result.startswith("Error (404):")


# ---------------------------------------------------------------------------
# omni_download_dashboard_file
# ---------------------------------------------------------------------------


async def test_download_file_writes_to_disk(monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any) -> None:
    fake = _FakeClient(binary_content=b"%PDF-1.4 fake pdf bytes", binary_headers={"content-type": "application/pdf"})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)
    output_path = tmp_path / "out" / "dashboard.pdf"

    result = await omni_download_dashboard_file(
        DownloadDashboardFileInput(dashboard_id="abc123", job_id="job-1", output_path=str(output_path))
    )

    assert output_path.read_bytes() == b"%PDF-1.4 fake pdf bytes"
    assert "Downloaded dashboard file" in result
    assert "application/pdf" in result
    assert str(output_path) in result
    assert fake.calls == [("GET", "/v1/dashboards/abc123/download/job-1", {})]


async def test_download_file_refuses_overwrite(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)
    output_path = tmp_path / "dashboard.pdf"
    output_path.write_bytes(b"existing content")

    result = await omni_download_dashboard_file(
        DownloadDashboardFileInput(dashboard_id="abc123", job_id="job-1", output_path=str(output_path))
    )

    assert "refusing to overwrite" in result
    assert output_path.read_bytes() == b"existing content"
    assert fake.calls == []  # never called the API


async def test_download_file_overwrite_flag_replaces_file(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any
) -> None:
    fake = _FakeClient(binary_content=b"new bytes")
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)
    output_path = tmp_path / "dashboard.pdf"
    output_path.write_bytes(b"old bytes")

    result = await omni_download_dashboard_file(
        DownloadDashboardFileInput(dashboard_id="abc123", job_id="job-1", output_path=str(output_path), overwrite=True)
    )

    assert output_path.read_bytes() == b"new bytes"
    assert "Downloaded dashboard file" in result


async def test_download_file_still_in_progress(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any
) -> None:
    fake = _FakeClient(binary_responses=[_FakeResponse(content=b"", status_code=202)])
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)
    output_path = tmp_path / "dashboard.pdf"

    result = await omni_download_dashboard_file(
        DownloadDashboardFileInput(dashboard_id="abc123", job_id="job-1", output_path=str(output_path))
    )

    assert "still in progress" in result
    assert not output_path.exists()


async def test_download_file_job_failed_410(monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any) -> None:
    error = _http_error(410, {"detail": "Job failed", "status": 410})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(exc=error))
    output_path = tmp_path / "dashboard.pdf"

    result = await omni_download_dashboard_file(
        DownloadDashboardFileInput(dashboard_id="abc123", job_id="job-1", output_path=str(output_path))
    )

    assert result.startswith("Error (410):")
    assert "omni_get_dashboard_download_status" in result
    assert not output_path.exists()


async def test_download_file_permission_error(monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any) -> None:
    error = _http_error(403, {"detail": "Insufficient permissions", "status": 403})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(exc=error))
    output_path = tmp_path / "dashboard.pdf"

    result = await omni_download_dashboard_file(
        DownloadDashboardFileInput(dashboard_id="abc123", job_id="job-1", output_path=str(output_path))
    )

    assert result.startswith("Error (403):")


# ---------------------------------------------------------------------------
# omni_export_dashboard_file
# ---------------------------------------------------------------------------


async def test_export_polls_then_downloads(monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(dashboards, "_sleep", fake_sleep)

    fake = _FakeClient(
        json_responses=[
            {"job_id": "job-9", "message": "ok"},
            {"job_id": "job-9", "status": "in_progress", "format": "pdf"},
            {"job_id": "job-9", "status": "in_progress", "format": "pdf"},
            {"job_id": "job-9", "status": "complete", "format": "pdf"},
        ],
        binary_content=b"exported pdf bytes",
    )
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)
    output_path = tmp_path / "export.pdf"

    result = await omni_export_dashboard_file(
        ExportDashboardFileInput(
            dashboard_id="abc123", format="pdf", output_path=str(output_path), poll_interval_seconds=2
        )
    )

    assert output_path.read_bytes() == b"exported pdf bytes"
    assert "Exported dashboard" in result
    assert "job-9" in result
    assert sleeps == [2, 2]
    assert fake.calls[0] == ("POST", "/v1/dashboards/abc123/download", {"params": None, "json_body": {"format": "pdf"}})
    assert fake.calls[1] == ("GET", "/v1/dashboards/abc123/download/job-9/status", {})
    assert fake.calls[-1] == ("GET", "/v1/dashboards/abc123/download/job-9", {})


async def test_export_times_out_cleanly(monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(dashboards, "_sleep", fake_sleep)

    fake = _FakeClient(
        json_responses=[
            {"job_id": "job-timeout", "message": "ok"},
            {"job_id": "job-timeout", "status": "in_progress", "format": "pdf"},
            {"job_id": "job-timeout", "status": "in_progress", "format": "pdf"},
            {"job_id": "job-timeout", "status": "in_progress", "format": "pdf"},
        ],
    )
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)
    output_path = tmp_path / "export.pdf"

    result = await omni_export_dashboard_file(
        ExportDashboardFileInput(
            dashboard_id="abc123",
            format="pdf",
            output_path=str(output_path),
            poll_interval_seconds=1,
            max_wait_seconds=3,
        )
    )

    assert "still" in result
    assert "job-timeout" in result
    assert "omni_get_dashboard_download_status" in result
    assert not output_path.exists()


async def test_export_reports_job_error_status(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any
) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(dashboards, "_sleep", fake_sleep)
    fake = _FakeClient(
        json_responses=[
            {"job_id": "job-err", "message": "ok"},
            {"job_id": "job-err", "status": "error", "format": "pdf", "error": "All queries failed."},
        ]
    )
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)
    output_path = tmp_path / "export.pdf"

    result = await omni_export_dashboard_file(
        ExportDashboardFileInput(dashboard_id="abc123", format="pdf", output_path=str(output_path))
    )

    assert result.startswith("Error:")
    assert "All queries failed." in result
    assert not output_path.exists()


async def test_export_refuses_overwrite_once_complete(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Any
) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(dashboards, "_sleep", fake_sleep)
    fake = _FakeClient(
        json_responses=[
            {"job_id": "job-9", "message": "ok"},
            {"job_id": "job-9", "status": "complete", "format": "pdf"},
        ]
    )
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)
    output_path = tmp_path / "export.pdf"
    output_path.write_bytes(b"existing")

    result = await omni_export_dashboard_file(
        ExportDashboardFileInput(dashboard_id="abc123", format="pdf", output_path=str(output_path))
    )

    assert "refusing to overwrite" in result
    assert output_path.read_bytes() == b"existing"


# ---------------------------------------------------------------------------
# omni_get_dashboard_filters
# ---------------------------------------------------------------------------


FILTERS_PAYLOAD: dict[str, Any] = {
    "identifier": "abc123",
    "filters": {
        "order_status": {
            "type": "string",
            "kind": "EQUALS",
            "values": ["completed", "pending"],
            "label": "Order Status",
            "required": False,
            "requiredScope": "dashboard",
            "hidden": False,
        },
        "order_date": {
            "type": "date",
            "kind": "WITHIN_RANGE",
            "left_side": "2024-01-01",
            "right_side": "2024-12-31",
            "label": "Order Date",
            "requiredScope": "tiles",
        },
    },
    "controls": [
        {
            "id": "field_selector",
            "type": "FIELD_SELECTION",
            "kind": "FIELD",
            "label": "Metric Selector",
            "field": "orders.revenue",
            "options": [{"label": "Revenue", "value": "orders.revenue"}],
        }
    ],
    "filterOrder": ["order_status", "order_date", "field_selector"],
}


async def test_get_filters_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=FILTERS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)

    result = await omni_get_dashboard_filters(GetDashboardFiltersInput(dashboard_id="abc123"))

    assert "order_status" in result
    assert "Order Status" in result
    assert "field_selector" in result
    assert "Metric Selector" in result
    assert fake.calls == [("GET", "/v1/dashboards/abc123/filters", {"params": None})]


async def test_get_filters_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(payload=FILTERS_PAYLOAD))

    payload = json.loads(
        await omni_get_dashboard_filters(
            GetDashboardFiltersInput(dashboard_id="abc123", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["identifier"] == "abc123"
    assert payload["filters"]["order_status"]["values"] == ["completed", "pending"]


async def test_get_filters_passes_user_id(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=FILTERS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)

    await omni_get_dashboard_filters(GetDashboardFiltersInput(dashboard_id="abc123", user_id="user-1"))

    assert fake.calls == [("GET", "/v1/dashboards/abc123/filters", {"params": {"userId": "user-1"}})]


async def test_get_filters_not_found(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(404, {"detail": "Dashboard not found", "status": 404})
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(exc=error))

    result = await omni_get_dashboard_filters(GetDashboardFiltersInput(dashboard_id="missing"))

    assert result.startswith("Error (404):")


# ---------------------------------------------------------------------------
# omni_update_dashboard_filters
# ---------------------------------------------------------------------------


async def test_update_filters_success(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=FILTERS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)

    result = await omni_update_dashboard_filters(
        UpdateDashboardFiltersInput(
            dashboard_id="abc123", filters={"order_status": {"values": ["shipped", "delivered"]}}
        )
    )

    assert "Updated dashboard" in result
    assert "1 filter(s)" in result
    assert fake.calls == [
        (
            "PATCH",
            "/v1/dashboards/abc123/filters",
            {"params": None, "json_body": {"filters": {"order_status": {"values": ["shipped", "delivered"]}}}},
        )
    ]


async def test_update_filters_clear_draft_and_controls(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=FILTERS_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: fake)

    await omni_update_dashboard_filters(
        UpdateDashboardFiltersInput(
            dashboard_id="abc123",
            clear_existing_draft=True,
            controls={"field_selector": {"label": "Choose Metric"}},
            filter_order=["order_date", "field_selector", "order_status"],
        )
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v1/dashboards/abc123/filters",
            {
                "params": None,
                "json_body": {
                    "clearExistingDraft": True,
                    "controls": {"field_selector": {"label": "Choose Metric"}},
                    "filterOrder": ["order_date", "field_selector", "order_status"],
                },
            },
        )
    ]


async def test_update_filters_requires_at_least_one_change() -> None:
    with pytest.raises(ValueError):
        UpdateDashboardFiltersInput(dashboard_id="abc123")


async def test_update_filters_conflict_error(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    error = _http_error(
        409, {"detail": "A draft already exists for this document. Set clearExistingDraft to true.", "status": 409}
    )
    monkeypatch.setattr("omni_mcp.tools.dashboards.get_client", lambda: _FakeClient(exc=error))

    result = await omni_update_dashboard_filters(
        UpdateDashboardFiltersInput(dashboard_id="abc123", filters={"order_status": {"values": ["shipped"]}})
    )

    assert result.startswith("Error (409):")
    assert "clearExistingDraft" in result


def test_update_filters_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        UpdateDashboardFiltersInput(dashboard_id="abc123", filters={"x": {}}, unexpected="y")  # type: ignore[call-arg]
