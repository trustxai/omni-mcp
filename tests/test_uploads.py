"""Unit tests for the CSV upload tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.uploads import (
    DeleteUploadInput,
    ListUploadsInput,
    ReplaceUploadDataInput,
    SortDirection,
    UploadCsvInput,
    UploadSortField,
    UploadType,
    omni_delete_upload,
    omni_list_uploads,
    omni_replace_upload_data,
    omni_upload_csv,
)

LIST_PAYLOAD: dict[str, Any] = {
    "pageInfo": {"hasNextPage": False, "nextCursor": None, "pageSize": 20, "totalRecords": 1},
    "records": [
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "file_name": "users.csv",
            "view_name": "users",
            "connection_id": "660e8400-e29b-41d4-a716-446655440001",
            "in_db_as_table_name": "omni_upload_t550e8400",
            "model_id": "880e8400-e29b-41d4-a716-446655440003",
            "size_bytes": 1024,
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:00:00Z",
            "uploaded_by_user": {"id": "770e8400-e29b-41d4-a716-446655440002", "name": "John Doe"},
        }
    ],
}

UPLOAD_RESPONSE: dict[str, Any] = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "fileName": "users.csv",
    "viewName": "users",
    "modelId": "880e8400-e29b-41d4-a716-446655440003",
    "inDbAsTableName": "omni_upload_t550e8400",
    "rowCount": 150,
    "truncated": False,
    "viewCreated": True,
}

REPLACE_RESPONSE: dict[str, Any] = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "fileName": "users_updated.csv",
    "viewName": "users",
    "inDbAsTableName": "omni_upload_t7c1e22ab",
    "refreshedModelIds": ["880e8400-e29b-41d4-a716-446655440003"],
    "rowCount": 152,
    "truncated": False,
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


def _http_error(method: str, url: str, status: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    response = httpx.Response(status, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# --- omni_list_uploads --------------------------------------------------------


async def test_list_uploads_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LIST_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    result = await omni_list_uploads(ListUploadsInput())

    assert "users.csv" in result
    assert "John Doe" in result
    assert "550e8400-e29b-41d4-a716-446655440000" in result
    assert fake.calls == [
        (
            "GET",
            "/v1/uploads",
            {"params": {"type": "csv", "pageSize": 20, "sortField": "updatedAt", "sortDirection": "desc"}},
        )
    ]


async def test_list_uploads_passes_all_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LIST_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    await omni_list_uploads(
        ListUploadsInput(
            type=UploadType.SPREADSHEET,
            connection_id="conn-1",
            model_id="model-1",
            search_term="users",
            page_size=50,
            cursor="cursor-abc",
            sort_field=UploadSortField.FILE_NAME,
            sort_direction=SortDirection.ASC,
        )
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/uploads",
            {
                "params": {
                    "type": "spreadsheet",
                    "pageSize": 50,
                    "sortField": "fileName",
                    "sortDirection": "asc",
                    "connectionId": "conn-1",
                    "modelId": "model-1",
                    "searchTerm": "users",
                    "cursor": "cursor-abc",
                }
            },
        )
    ]


async def test_list_uploads_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LIST_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    payload = json.loads(await omni_list_uploads(ListUploadsInput(response_format=ResponseFormat.JSON)))

    assert payload["count"] == 1
    assert payload["totalRecords"] == 1
    assert payload["items"][0]["file_name"] == "users.csv"


async def test_list_uploads_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error(
        "GET",
        "https://acme.omniapp.co/api/v1/uploads",
        403,
        "Insufficient permissions. Requires MANAGE_UPLOADS permission.",
    )
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: _FakeClient(exc=error))

    result = await omni_list_uploads(ListUploadsInput())

    assert result.startswith("Error (403):")


def test_list_uploads_page_size_bounds() -> None:
    with pytest.raises(ValidationError):
        ListUploadsInput(page_size=0)
    with pytest.raises(ValidationError):
        ListUploadsInput(page_size=101)


# --- omni_upload_csv -----------------------------------------------------------


async def test_upload_csv_from_file_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    csv_file = tmp_path / "users.csv"
    csv_file.write_text("id,name\n1,Ada\n")
    fake = _FakeClient(payload=UPLOAD_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    result = await omni_upload_csv(
        UploadCsvInput(model_id="880e8400-e29b-41d4-a716-446655440003", file_path=str(csv_file))
    )

    assert "users.csv" in result
    assert "550e8400-e29b-41d4-a716-446655440000" in result
    assert "150" in result
    assert "id,name" not in result  # CSV content is never echoed back
    assert len(fake.calls) == 1
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("POST", "/v1/uploads")
    assert kwargs["data"] == {"modelId": "880e8400-e29b-41d4-a716-446655440003"}
    filename, content, content_type = kwargs["files"]["file"]
    assert filename == "users.csv"
    assert content == b"id,name\n1,Ada\n"
    assert content_type == "text/csv"


async def test_upload_csv_from_inline_content_with_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=UPLOAD_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    result = await omni_upload_csv(
        UploadCsvInput(
            model_id="880e8400-e29b-41d4-a716-446655440003",
            csv_content="id,name\n1,Ada\n",
            filename="inline.csv",
            branch_name="my-branch",
            view_name="custom_view_name",
        )
    )

    assert "Uploaded" in result
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("POST", "/v1/uploads")
    assert kwargs["data"] == {
        "modelId": "880e8400-e29b-41d4-a716-446655440003",
        "branchName": "my-branch",
        "viewName": "custom_view_name",
    }
    filename, content, content_type = kwargs["files"]["file"]
    assert filename == "inline.csv"
    # `csv_content` opts out of `str_strip_whitespace=True` — its bytes must reach the API untouched.
    assert content == b"id,name\n1,Ada\n"
    assert content_type == "text/csv"


async def test_upload_csv_default_filename_for_inline_content(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=UPLOAD_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    await omni_upload_csv(UploadCsvInput(model_id="model-1", csv_content="a,b\n1,2\n"))

    filename, _content, _content_type = fake.calls[0][2]["files"]["file"]
    assert filename == "upload.csv"


async def test_upload_csv_preserves_csv_content_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=UPLOAD_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)
    # Leading space in the header cell, trailing spaces in the last cell/line: `csv_content` opts out
    # of `str_strip_whitespace=True`, so none of this may be stripped before it reaches the API.
    csv_text = " id,name\n1,Ada  \n"

    await omni_upload_csv(UploadCsvInput(model_id="model-1", csv_content=csv_text))

    _filename, content, _content_type = fake.calls[0][2]["files"]["file"]
    assert content == csv_text.encode("utf-8")


async def test_upload_csv_missing_file_reports_clear_error(tmp_path: Any) -> None:
    missing = tmp_path / "does_not_exist.csv"

    result = await omni_upload_csv(UploadCsvInput(model_id="model-1", file_path=str(missing)))

    assert result.startswith("Error: cannot read file")
    assert str(missing) in result


async def test_upload_csv_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error("POST", "https://acme.omniapp.co/api/v1/uploads", 404, "Model not found")
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: _FakeClient(exc=error))

    result = await omni_upload_csv(UploadCsvInput(model_id="missing-model", csv_content="a,b\n1,2\n"))

    assert result.startswith("Error (404):")


def test_upload_csv_requires_exactly_one_source() -> None:
    with pytest.raises(ValidationError):
        UploadCsvInput(model_id="model-1")
    with pytest.raises(ValidationError):
        UploadCsvInput(model_id="model-1", file_path="/tmp/x.csv", csv_content="a,b\n1,2\n")


def test_upload_csv_rejects_both_branch_fields() -> None:
    with pytest.raises(ValidationError):
        UploadCsvInput(model_id="model-1", csv_content="a,b\n1,2\n", branch_id="branch-1", branch_name="branch-2")


def test_upload_csv_rejects_whitespace_only_content() -> None:
    with pytest.raises(ValidationError):
        UploadCsvInput(model_id="model-1", csv_content="   ")


def test_upload_csv_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        UploadCsvInput(model_id="model-1", csv_content="a,b\n1,2\n", unexpected="x")  # type: ignore[call-arg]


# --- omni_replace_upload_data ---------------------------------------------------


async def test_replace_upload_data_from_file_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    csv_file = tmp_path / "users_updated.csv"
    csv_file.write_text("id,name\n1,Ada\n2,Grace\n")
    fake = _FakeClient(payload=REPLACE_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    result = await omni_replace_upload_data(
        ReplaceUploadDataInput(upload_id="550e8400-e29b-41d4-a716-446655440000", file_path=str(csv_file))
    )

    assert "users_updated.csv" in result
    assert "152" in result
    assert "880e8400-e29b-41d4-a716-446655440003" in result
    assert "id,name" not in result  # CSV content is never echoed back
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("PUT", "/v1/uploads/550e8400-e29b-41d4-a716-446655440000")
    assert "data" not in kwargs
    filename, content, content_type = kwargs["files"]["file"]
    assert filename == "users_updated.csv"
    assert content == b"id,name\n1,Ada\n2,Grace\n"
    assert content_type == "text/csv"


async def test_replace_upload_data_from_inline_content(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=REPLACE_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    await omni_replace_upload_data(
        ReplaceUploadDataInput(
            upload_id="550e8400-e29b-41d4-a716-446655440000",
            csv_content="id,name\n1,Ada\n2,Grace\n",
            filename="replacement.csv",
        )
    )

    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("PUT", "/v1/uploads/550e8400-e29b-41d4-a716-446655440000")
    filename, content, content_type = kwargs["files"]["file"]
    assert filename == "replacement.csv"
    # `csv_content` opts out of `str_strip_whitespace=True` — its bytes must reach the API untouched.
    assert content == b"id,name\n1,Ada\n2,Grace\n"
    assert content_type == "text/csv"


async def test_replace_upload_data_preserves_csv_content_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=REPLACE_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)
    # Leading space in the header cell, trailing spaces in the last cell/line: `csv_content` opts out
    # of `str_strip_whitespace=True`, so none of this may be stripped before it reaches the API.
    csv_text = " id,name\n1,Ada  \n"

    await omni_replace_upload_data(
        ReplaceUploadDataInput(upload_id="550e8400-e29b-41d4-a716-446655440000", csv_content=csv_text)
    )

    _filename, content, _content_type = fake.calls[0][2]["files"]["file"]
    assert content == csv_text.encode("utf-8")


async def test_replace_upload_data_missing_file_reports_clear_error(tmp_path: Any) -> None:
    missing = tmp_path / "does_not_exist.csv"

    result = await omni_replace_upload_data(
        ReplaceUploadDataInput(upload_id="550e8400-e29b-41d4-a716-446655440000", file_path=str(missing))
    )

    assert result.startswith("Error: cannot read file")
    assert str(missing) in result


async def test_replace_upload_data_quotes_path_param(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=REPLACE_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    await omni_replace_upload_data(ReplaceUploadDataInput(upload_id="weird/id with space", csv_content="a,b\n1,2\n"))

    _method, path, _kwargs = fake.calls[0]
    assert path == "/v1/uploads/weird%2Fid%20with%20space"


async def test_replace_upload_data_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error(
        "PUT", "https://acme.omniapp.co/api/v1/uploads/missing", 404, "Upload not found or already deleted"
    )
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: _FakeClient(exc=error))

    result = await omni_replace_upload_data(ReplaceUploadDataInput(upload_id="missing", csv_content="a,b\n1,2\n"))

    assert result.startswith("Error (404):")


def test_replace_upload_data_requires_exactly_one_source() -> None:
    with pytest.raises(ValidationError):
        ReplaceUploadDataInput(upload_id="upload-1")
    with pytest.raises(ValidationError):
        ReplaceUploadDataInput(upload_id="upload-1", file_path="/tmp/x.csv", csv_content="a,b\n1,2\n")


def test_replace_upload_data_rejects_whitespace_only_content() -> None:
    with pytest.raises(ValidationError):
        ReplaceUploadDataInput(upload_id="upload-1", csv_content="   ")


def test_replace_upload_data_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReplaceUploadDataInput(upload_id="upload-1", csv_content="a,b\n1,2\n", unexpected="x")  # type: ignore[call-arg]


# --- omni_delete_upload ----------------------------------------------------------


async def test_delete_upload_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    result = await omni_delete_upload(DeleteUploadInput(upload_id="550e8400-e29b-41d4-a716-446655440000"))

    assert "Deleted upload" in result
    assert "550e8400-e29b-41d4-a716-446655440000" in result
    assert fake.calls == [("DELETE", "/v1/uploads/550e8400-e29b-41d4-a716-446655440000", {})]


async def test_delete_upload_quotes_path_param(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    await omni_delete_upload(DeleteUploadInput(upload_id="weird/id with space"))

    assert fake.calls == [("DELETE", "/v1/uploads/weird%2Fid%20with%20space", {})]


async def test_delete_upload_reports_unconfirmed_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": False})
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    result = await omni_delete_upload(DeleteUploadInput(upload_id="x"))

    assert "did not confirm success" in result


async def test_delete_upload_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error(
        "DELETE",
        "https://acme.omniapp.co/api/v1/uploads/x",
        403,
        "Insufficient permissions. Requires Organization Admin permissions.",
    )
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: _FakeClient(exc=error))

    result = await omni_delete_upload(DeleteUploadInput(upload_id="x"))

    assert result.startswith("Error (403):")


def test_delete_upload_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DeleteUploadInput(upload_id="x", unexpected="x")  # type: ignore[call-arg]


def test_list_uploads_input_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ListUploadsInput(unexpected="x")  # type: ignore[call-arg]


async def test_delete_upload_treats_an_empty_2xx_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 204/empty body is a successful delete, not an unconfirmed one."""
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    result = await omni_delete_upload(DeleteUploadInput(upload_id="770e8400-e29b-41d4-a716-446655440002"))

    assert result.startswith("Deleted upload")


async def test_list_uploads_renders_model_id_and_created_at(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "records": [
                {
                    "id": "u1",
                    "file_name": "users.csv",
                    "view_name": "users",
                    "model_id": "880e8400-e29b-41d4-a716-446655440003",
                    "connection_id": "c1",
                    "created_at": "2026-01-02T03:04:05.000Z",
                    "updated_at": "2026-01-03T03:04:05.000Z",
                }
            ],
            "pageInfo": {},
        }
    )
    monkeypatch.setattr("omni_mcp.tools.uploads.get_client", lambda: fake)

    result = await omni_list_uploads(ListUploadsInput())

    assert "880e8400-e29b-41d4-a716-446655440003" in result
    assert "created 2026-01-02" in result
