"""Tools for the CSV upload endpoints (list, upload, replace, delete). Implemented by lane t19."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, cursor_paginated_response, iso_or_na, to_json, truncate_result
from omni_mcp.server import mcp


class UploadType(StrEnum):
    """`type` filter for `omni_list_uploads`."""

    CSV = "csv"
    SPREADSHEET = "spreadsheet"


class UploadSortField(StrEnum):
    """`sortField` for `omni_list_uploads`."""

    CREATED_AT = "createdAt"
    FILE_NAME = "fileName"
    UPDATED_AT = "updatedAt"


class SortDirection(StrEnum):
    """`sortDirection` for `omni_list_uploads`."""

    ASC = "asc"
    DESC = "desc"


#: `csv_content` opts out of the model-level `str_strip_whitespace=True` — stripping it would
#: silently mutate real CSV data (a leading space in a header cell, trailing spaces in the last
#: cell of the file).
_UnstrippedStr = Annotated[str, StringConstraints(strip_whitespace=False)]


def _read_csv_payload(file_path: str | None, csv_content: str | None, filename: str | None) -> tuple[str, bytes]:
    """Resolve the `(filename, bytes)` pair sent as the multipart `file` part.

    The input model's validator guarantees exactly one of `file_path`/`csv_content` is set, and that
    `csv_content` (when given) is not empty/whitespace-only.
    """
    if file_path is not None:
        path = Path(file_path).expanduser()
        try:
            content = path.read_bytes()
        except OSError as exc:
            reason = exc.strerror or str(exc)
            raise RuntimeError(f"cannot read file {file_path}: {reason}") from exc
        name = filename or path.name or "upload.csv"
        return name, content
    name = filename or "upload.csv"
    return name, (csv_content or "").encode("utf-8")


class ListUploadsInput(BaseModel):
    """Input for `omni_list_uploads`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: UploadType = Field(default=UploadType.CSV, description="Filter by upload type.")
    connection_id: str | None = Field(default=None, description="Filter by connection ID (UUID).")
    model_id: str | None = Field(
        default=None,
        description="Filter by model ID (UUID). Shared models return non-private connection uploads; "
        "workbook models return their own uploads.",
    )
    search_term: str | None = Field(default=None, description="Search term to filter by file name (case-insensitive).")
    page_size: int = Field(default=20, ge=1, le=100, description="Number of items to return (1-100).")
    cursor: str | None = Field(default=None, description="Cursor for pagination (from a previous response).")
    sort_field: UploadSortField = Field(default=UploadSortField.UPDATED_AT, description="Field to sort by.")
    sort_direction: SortDirection = Field(default=SortDirection.DESC, description="Sort direction.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a readable list, `json` for the raw records."
    )


class UploadCsvInput(BaseModel):
    """Input for `omni_upload_csv`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., description="UUID of the model to create the view in.")
    file_path: str | None = Field(
        default=None,
        description="Path to a local CSV file to upload (must have a `.csv` extension). Mutually exclusive "
        "with `csv_content`; exactly one of the two is required.",
    )
    csv_content: _UnstrippedStr | None = Field(
        default=None,
        description="Inline CSV text to upload. Mutually exclusive with `file_path`; exactly one of the two "
        "is required, and it must not be empty or whitespace-only. Sent byte-for-byte — leading/trailing "
        "whitespace is preserved, unlike other string fields on this tool.",
    )
    filename: str | None = Field(
        default=None,
        description="File name recorded for the upload. Defaults to the `file_path` basename when `file_path` "
        "is set, or to `upload.csv` when using `csv_content`. An explicit value here always overrides that "
        "default, including alongside `file_path`.",
    )
    branch_id: str | None = Field(
        default=None,
        description="UUID of the branch to create the view in. Mutually exclusive with `branch_name`.",
    )
    branch_name: str | None = Field(
        default=None,
        description="Name of the branch to create the view in. Mutually exclusive with `branch_id`.",
    )
    view_name: str | None = Field(
        default=None, description="Override the view name. Defaults to a sanitized version of the file name."
    )

    @model_validator(mode="after")
    def _validate_source_and_branch(self) -> UploadCsvInput:
        if (self.file_path is None) == (self.csv_content is None):
            raise ValueError("Provide exactly one of `file_path` or `csv_content`.")
        if self.csv_content is not None and not self.csv_content.strip():
            raise ValueError("`csv_content` must not be empty or whitespace-only.")
        if self.branch_id is not None and self.branch_name is not None:
            raise ValueError("Provide at most one of `branch_id` or `branch_name`; they are mutually exclusive.")
        return self


class ReplaceUploadDataInput(BaseModel):
    """Input for `omni_replace_upload_data`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    upload_id: str = Field(..., description="UUID of the upload whose data should be replaced.")
    file_path: str | None = Field(
        default=None,
        description="Path to the local replacement CSV file (must have a `.csv` extension). Mutually "
        "exclusive with `csv_content`; exactly one of the two is required.",
    )
    csv_content: _UnstrippedStr | None = Field(
        default=None,
        description="Inline replacement CSV text. Mutually exclusive with `file_path`; exactly one of the "
        "two is required, and it must not be empty or whitespace-only. Sent byte-for-byte — "
        "leading/trailing whitespace is preserved, unlike other string fields on this tool.",
    )
    filename: str | None = Field(
        default=None,
        description="File name recorded for the replacement. Defaults to the `file_path` basename when "
        "`file_path` is set, or to `upload.csv` when using `csv_content`. An explicit value here always "
        "overrides that default, including alongside `file_path`.",
    )

    @model_validator(mode="after")
    def _validate_source(self) -> ReplaceUploadDataInput:
        if (self.file_path is None) == (self.csv_content is None):
            raise ValueError("Provide exactly one of `file_path` or `csv_content`.")
        if self.csv_content is not None and not self.csv_content.strip():
            raise ValueError("`csv_content` must not be empty or whitespace-only.")
        return self


class DeleteUploadInput(BaseModel):
    """Input for `omni_delete_upload`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    upload_id: str = Field(..., description="UUID of the upload to delete.")


def _format_upload_item(item: Mapping[str, Any]) -> str:
    uploaded_by = item.get("uploaded_by_user")
    uploader = uploaded_by.get("name") if isinstance(uploaded_by, dict) else None
    size = item.get("size_bytes")
    size_text = f"{size:,} bytes" if isinstance(size, int) else "unknown size"
    table_name = item.get("in_db_as_table_name") or "none (no table upload schema on this connection)"
    return (
        f"- **{item.get('file_name', 'unnamed')}** (`{item.get('id')}`) — view `{item.get('view_name')}`, "
        f"model `{item.get('model_id') or 'N/A'}`, connection `{item.get('connection_id')}`, "
        f"table `{table_name}`, {size_text}, created {iso_or_na(item.get('created_at'))}, "
        f"updated {iso_or_na(item.get('updated_at'))}, uploaded by {uploader or 'unknown'}"
    )


@mcp.tool(
    name="omni_list_uploads",
    annotations=ToolAnnotations(
        title="List Uploads",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_uploads(params: ListUploadsInput) -> str:
    """List CSV and spreadsheet uploads (data input tables) in the organization.

    Calls `GET /v1/uploads` with metadata and optional filtering. This endpoint requires
    **Organization Admin** permissions.

    When to Use:
    - To find the upload ID behind a data input table before replacing or deleting it.
    - To audit which CSV/spreadsheet files are attached to a connection or model.
    - To search uploads by file name, or page through all of them.

    When NOT to Use:
    - To read the data *inside* an upload — query the model view the upload created instead.
    - To find the underlying rows without an id — this only returns metadata, never file contents.

    Returns:
    A markdown list (file name, upload id, view name, connection id, scratch-schema table name, size,
    last-updated timestamp, uploader) with pagination info, or the raw `records`/`pageInfo` payload
    when `response_format` is `json`.

    Examples:
    - List recent CSV uploads: `{"params": {}}`
    - Search by file name: `{"params": {"search_term": "users"}}`
    - Filter by connection: `{"params": {"connection_id": "660e8400-e29b-41d4-a716-446655440001"}}`
    - Page through results: `{"params": {"page_size": 50, "cursor": "<nextCursor from the previous call>"}}`

    Error Handling:
    400 means an invalid `connection_id`/`model_id` UUID; 403 means the caller lacks Organization Admin
    (or `MANAGE_UPLOADS`) permissions; 404 means `model_id` does not match any model visible to the caller.
    """
    try:
        query: dict[str, Any] = {
            "type": params.type.value,
            "pageSize": params.page_size,
            "sortField": params.sort_field.value,
            "sortDirection": params.sort_direction.value,
        }
        if params.connection_id is not None:
            query["connectionId"] = params.connection_id
        if params.model_id is not None:
            query["modelId"] = params.model_id
        if params.search_term is not None:
            query["searchTerm"] = params.search_term
        if params.cursor is not None:
            query["cursor"] = params.cursor

        payload = await get_client().request_json("GET", "/v1/uploads", params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_upload_item,
            title="Uploads",
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_upload_csv",
    annotations=ToolAnnotations(
        title="Upload a CSV File",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_upload_csv(params: UploadCsvInput) -> str:
    """Upload a CSV file to create a new data input table and view.

    Calls `POST /v1/uploads`. The file is parsed, converted to Arrow format, uploaded to storage,
    written to the connection's table upload (scratch) schema, and a view is created in the specified
    model. Requires the **Upload data** setting (Settings > Content permissions) to be enabled by an
    Organization Admin, and the caller must have **Restricted Querier** permissions or higher on the
    target model. The file must have a `.csv` extension and is capped at 500,000 rows — larger files
    are truncated (see the `truncated` flag in the result).

    When to Use:
    - To bring a local CSV, or CSV text you already have in hand, into an Omni model as a queryable view.
    - To create an upload whose id you will bind as a `csv`/`spreadsheet` source tab on a document.

    When NOT to Use:
    - To update an existing upload's data in place while keeping the same id — use
      `omni_replace_upload_data` instead of creating a duplicate.

    Returns:
    A short confirmation with the new upload id, file name, view name, model id, scratch-schema table
    name, row count, and whether the file was truncated or a view was created. The CSV content itself
    is never echoed back.

    Examples:
    - From a local file: `{"params": {"model_id": "880e8400-e29b-41d4-a716-446655440003", "file_path": "/tmp/users.csv"}}`
    - Inline content: `{"params": {"model_id": "880e8400-e29b-41d4-a716-446655440003", "csv_content": "id,name\\n1,Ada\\n", "filename": "users.csv"}}`
    - Into a named branch: `{"params": {"model_id": "880e8400-e29b-41d4-a716-446655440003", "file_path": "/tmp/users.csv", "branch_name": "my-branch"}}`

    Error Handling:
    400 covers a missing `file`/`modelId`, a non-CSV file, a CSV parse failure, an invalid UUID, or both
    `branch_id` and `branch_name` given (also rejected client-side before the call is made); 403 means
    the caller lacks Restricted Querier or higher on the model, or Upload data is disabled; 404 means
    the model or branch was not found.
    """
    try:
        filename, content = _read_csv_payload(params.file_path, params.csv_content, params.filename)
        data: dict[str, Any] = {"modelId": params.model_id}
        if params.branch_id is not None:
            data["branchId"] = params.branch_id
        if params.branch_name is not None:
            data["branchName"] = params.branch_name
        if params.view_name is not None:
            data["viewName"] = params.view_name

        payload = await get_client().request_json(
            "POST",
            "/v1/uploads",
            data=data,
            files={"file": (filename, content, "text/csv")},
        )
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        row_count = body.get("rowCount")
        rows_line = f"- Rows: {row_count if row_count is not None else 'unknown'}"
        if body.get("truncated"):
            rows_line += " (truncated at 500,000)"
        lines = [
            f"Uploaded **{body.get('fileName', filename)}** (id `{body.get('id')}`) as view "
            f"**{body.get('viewName')}** in model `{body.get('modelId', params.model_id)}`.",
            f"- Scratch table: `{body.get('inDbAsTableName', 'N/A')}`",
            rows_line,
            f"- View created: {body.get('viewCreated', 'unknown')}",
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_replace_upload_data",
    annotations=ToolAnnotations(
        title="Replace Upload Data",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_replace_upload_data(params: ReplaceUploadDataInput) -> str:
    """Replace the data in an existing CSV upload, keeping its id and view name.

    Calls `PUT /v1/uploads/{uploadId}`. Requires **Organization Admin** permissions. This FULLY
    REPLACES the upload's existing data with the new file: the view's `uploaded_table_name` and every
    reference to it (returned as `refreshedModelIds`) are automatically re-pointed at the new data, but
    the upload id and view name never change. The previous data's artifacts are left untouched but are
    no longer reachable through this upload — there is no undo.

    When to Use:
    - To refresh a data input table with a new export while keeping every document, query, and view
      that references it pointed at the same upload id.

    When NOT to Use:
    - To create a brand-new, independent upload — use `omni_upload_csv`.
    - When the column names or the set of columns are changing and referencing content has not been
      checked — renaming or removing columns can break fields, calculations, and filters that used them.

    Returns:
    A short confirmation with the upload id, new file name, view name, scratch-schema table name, the
    model ids whose views were re-pointed, the new row count, and whether it was truncated. The CSV
    content itself is never echoed back.

    Examples:
    - From a local file: `{"params": {"upload_id": "550e8400-e29b-41d4-a716-446655440000", "file_path": "/tmp/users_updated.csv"}}`
    - Inline content: `{"params": {"upload_id": "550e8400-e29b-41d4-a716-446655440000", "csv_content": "id,name\\n1,Ada\\n"}}`

    Error Handling:
    400 covers a missing/non-CSV file, a parse failure, or attempting to replace a non-CSV upload; 403
    means the caller lacks Organization Admin (or `MANAGE_UPLOADS`) permissions; 404 means the upload
    does not exist or was already deleted.
    """
    try:
        filename, content = _read_csv_payload(params.file_path, params.csv_content, params.filename)
        upload_id = quote(params.upload_id, safe="")
        payload = await get_client().request_json(
            "PUT",
            f"/v1/uploads/{upload_id}",
            files={"file": (filename, content, "text/csv")},
        )
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        refreshed = body.get("refreshedModelIds") or []
        refreshed_text = ", ".join(f"`{model_id}`" for model_id in refreshed) if refreshed else "none"
        rows_line = f"- Rows: {body.get('rowCount', 'unknown')}"
        if body.get("truncated"):
            rows_line += " (truncated at 500,000)"
        lines = [
            f"Replaced data for upload `{body.get('id', params.upload_id)}` — view "
            f"**{body.get('viewName')}** now serves **{body.get('fileName', filename)}**.",
            f"- Scratch table: `{body.get('inDbAsTableName') or 'N/A'}`",
            rows_line,
            f"- Re-pointed models: {refreshed_text}",
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_delete_upload",
    annotations=ToolAnnotations(
        title="Delete Upload",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_upload(params: DeleteUploadInput) -> str:
    """Delete a CSV upload, removing its file from storage.

    Calls `DELETE /v1/uploads/{uploadId}`, which removes the file from storage and marks the upload
    record as deleted. Requires **Organization Admin** permissions. There is no undo — the file and its
    data are gone once deleted.

    When to Use:
    - To remove an upload that is no longer needed, after confirming with `omni_list_uploads` that
      nothing important still depends on it.

    When NOT to Use:
    - To update the data in place while keeping documents/queries pointed at the same id — use
      `omni_replace_upload_data` instead.

    Returns:
    A short confirmation once the API reports `success`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"upload_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    400 means the upload id is not a valid UUID; 403 means the caller lacks Organization Admin
    permissions; 404 means the upload does not exist or was already deleted.
    """
    try:
        upload_id = quote(params.upload_id, safe="")
        payload = await get_client().request_json("DELETE", f"/v1/uploads/{upload_id}")
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        # A 2xx with no body at all is a successful delete; only a body that
        # carries `success` has to say `true`.
        if not body or body.get("success"):
            return f"Deleted upload `{params.upload_id}`."
        return truncate_result(f"Delete request completed but the API did not confirm success: {to_json(body)}")
    except Exception as exc:
        return handle_api_error(exc)
