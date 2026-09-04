"""Unit tests for the model / YAML / schema / cache / topic tools."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.models import (
    ArchiveModelInput,
    CreateModelInput,
    DeleteModelBranchInput,
    DeleteModelViewInput,
    DeleteModelYamlFileInput,
    GetModelAiAgentActionsInput,
    GetModelYamlInput,
    GetTopicInput,
    ListModelSchemasInput,
    ListModelsInput,
    MigrateModelInput,
    RefreshModelSchemaInput,
    RenameModelInput,
    ResetModelCacheInput,
    UpdateModelYamlInput,
    ValidateModelInput,
    omni_archive_model,
    omni_create_model,
    omni_delete_model_branch,
    omni_delete_model_view,
    omni_delete_model_yaml_file,
    omni_get_model_ai_agent_actions,
    omni_get_model_yaml,
    omni_get_topic,
    omni_list_model_schemas,
    omni_list_models,
    omni_migrate_model,
    omni_refresh_model_schema,
    omni_rename_model,
    omni_reset_model_cache,
    omni_update_model_yaml,
    omni_validate_model,
)

MODEL_ID = "6a1b0000-0000-0000-0000-000000000001"
BRANCH_ID = "0d2e0000-0000-0000-0000-000000000002"

MODEL_RECORDS: dict[str, Any] = {
    "records": [
        {
            "id": MODEL_ID,
            "name": "Orders",
            "modelKind": "SHARED",
            "connectionId": "conn-1",
            "baseModelId": "base-1",
            "updatedAt": "2026-01-02T03:04:05Z",
            "branches": [{"id": BRANCH_ID, "name": "pricing-fix"}],
        }
    ],
    "pageInfo": {"hasNextPage": True, "nextCursor": "cur-2", "pageSize": 20, "totalRecords": 3},
}

YAML_PAYLOAD: dict[str, Any] = {
    "files": {"orders.topic": "label: Orders\n"},
    "checksums": {"orders.topic": "9f2c"},
    "version": 1,
    "viewNames": {},
}

VALIDATION_ISSUES: list[dict[str, Any]] = [
    {
        "message": 'No view "blob_sales". Set base_view to a valid, existing view.',
        "is_warning": False,
        "yaml_path": "blob_sales.topic",
        "auto_fix": {"description_short": 'Delete topic "blob_sales"'},
    },
    {"message": "Unused view", "is_warning": True, "yaml_path": "main__orders.view"},
]

TOPIC_PAYLOAD: dict[str, Any] = {
    "success": True,
    "topic": {
        "name": "Customers",
        "label": "Customers",
        "base_view_name": "main__customers",
        "ide_file_name": "Customers.topic",
        "extension_model_id": "ext-1",
        "join_via_map": {"main__orders": [], "main__order_items": ["main__orders"]},
        "relationships": [
            {
                "left_view_name": "main__orders",
                "right_view_name": "main__customers",
                "join_type": "ALWAYS_LEFT",
                "type": "ASSUMED_MANY_TO_ONE",
                "bidirectional": False,
                "ignored": False,
                "sql": "${main__orders.customer_id} = ${main__customers.id}",
            }
        ],
        "views": [
            {
                "name": "main__customers",
                "label": "Customers",
                "schema": "main",
                "table_name": "customers",
                "dimensions": [{"field_name": "country"}, {"field_name": "created_date"}],
                "measures": [{"field_name": "count"}],
            }
        ],
    },
}

AI_ACTIONS: dict[str, Any] = {
    "success": True,
    "records": [
        {
            "type": "sample_query",
            "id": "revenue_by_region",
            "label": "Revenue by region",
            "description": "Show total revenue grouped by region",
            "prompt": "Show me revenue by region",
        },
        {
            "type": "skill",
            "id": "analyze_trends",
            "label": "Analyze trends",
            "description": "Analyze trends in the data",
            "prompt": "Skill: analyze_trends\nAnalyze trends in revenue over time",
        },
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


class _SequenceClient:
    """Returns queued payloads (or raises queued exceptions) call by call."""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        result = self._results.pop(0) if self._results else {}
        if isinstance(result, Exception):
            raise result
        return result


def _http_error(status: int, detail: str, path: str = "/v1/models") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://acme.omniapp.co/api{path}")
    response = httpx.Response(status, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


def _patch(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    monkeypatch.setattr("omni_mcp.tools.models.get_client", lambda: client)


# --------------------------------------------------------------------------- list


async def test_list_models_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=MODEL_RECORDS)
    _patch(monkeypatch, fake)

    result = await omni_list_models(ListModelsInput())

    assert "# Models" in result
    assert "**Orders**" in result
    assert "kind `SHARED`" in result
    assert "active branches: pricing-fix" in result
    assert 'cursor="cur-2"' in result
    assert fake.calls == [("GET", "/v1/models", {"params": {"pageSize": 20}})]


async def test_list_models_sends_every_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=MODEL_RECORDS)
    _patch(monkeypatch, fake)

    await omni_list_models(
        ListModelsInput(
            base_model_id="base-1",
            connection_id="conn-1",
            include_deleted=True,
            model_id=MODEL_ID,
            model_kind="BRANCH",
            name="pricing",
            explorable=False,
            sort_field="createdAt",
            sort_direction="asc",
            include="activeBranches",
            page_size=50,
            cursor="cur-1",
        )
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/models",
            {
                "params": {
                    "pageSize": 50,
                    "cursor": "cur-1",
                    "baseModelId": "base-1",
                    "connectionId": "conn-1",
                    "includeDeleted": True,
                    "modelId": MODEL_ID,
                    "modelKind": "BRANCH",
                    "name": "pricing",
                    "explorable": False,
                    "sortField": "createdAt",
                    "sortDirection": "asc",
                    "include": "activeBranches",
                }
            },
        )
    ]


async def test_list_models_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=MODEL_RECORDS))

    payload = json.loads(await omni_list_models(ListModelsInput(response_format=ResponseFormat.JSON)))

    assert payload["title"] == "Models"
    assert payload["items"][0]["id"] == MODEL_ID
    assert payload["nextCursor"] == "cur-2"


async def test_list_models_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(403, "Forbidden")))

    result = await omni_list_models(ListModelsInput())

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------- create


async def test_create_model_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True, "model": {"id": "m-9", "modelKind": "SCHEMA", "name": "Warehouse"}})
    _patch(monkeypatch, fake)

    result = await omni_create_model(
        CreateModelInput(connection_id="conn-1", model_kind="SCHEMA", model_name="Warehouse")
    )

    assert "Created model **Warehouse** (id `m-9`, kind `SCHEMA`)" in result
    assert "omni_refresh_model_schema" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/models",
            {"json_body": {"connectionId": "conn-1", "modelKind": "SCHEMA", "modelName": "Warehouse"}},
        )
    ]


async def test_create_model_extension_with_access_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"model": {"id": "m-10", "modelKind": "SHARED_EXTENSION", "name": "Finance"}})
    _patch(monkeypatch, fake)

    result = await omni_create_model(
        CreateModelInput(
            connection_id="conn-1",
            model_kind="SHARED_EXTENSION",
            model_name="Finance",
            base_model_id="base-1",
            access_grants=[{"name": "region", "accessBoostable": True}],
            allow_as_workbook_base=True,
            use_isolated_branches=False,
        )
    )

    assert "omni_update_model_yaml" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/models",
            {
                "json_body": {
                    "connectionId": "conn-1",
                    "modelKind": "SHARED_EXTENSION",
                    "modelName": "Finance",
                    "baseModelId": "base-1",
                    "accessGrants": [{"name": "region", "accessBoostable": True}],
                    "allowAsWorkbookBase": True,
                    "useIsolatedBranches": False,
                }
            },
        )
    ]


async def test_create_model_reports_body_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        _FakeClient(payload={"success": False, "error": "Schema model already exists for the connection"}),
    )

    result = await omni_create_model(CreateModelInput(connection_id="conn-1"))

    assert result.startswith("Error:")
    assert "Schema model already exists for the connection" in result


# --------------------------------------------------------------------------- rename / archive


async def test_rename_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": MODEL_ID, "name": "customer_metrics_v2", "modelKind": "SHARED"})
    _patch(monkeypatch, fake)

    result = await omni_rename_model(RenameModelInput(model_id=MODEL_ID, name="customer_metrics_v2"))

    assert f"Renamed model `{MODEL_ID}` to **customer_metrics_v2** (kind `SHARED`)." == result
    assert fake.calls == [("PATCH", f"/v1/models/{MODEL_ID}", {"json_body": {"name": "customer_metrics_v2"}})]


async def test_rename_model_rejects_workbook_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(400, "Bad Request: Cannot rename models of kind WORKBOOK")))

    result = await omni_rename_model(RenameModelInput(model_id=MODEL_ID, name="new"))

    assert result.startswith("Error (400):")
    assert "Cannot rename models of kind WORKBOOK" in result


async def test_archive_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    result = await omni_archive_model(ArchiveModelInput(model_id=MODEL_ID))

    assert f"Archived model `{MODEL_ID}`" in result
    assert "trash" in result
    assert fake.calls == [("DELETE", f"/v1/models/{MODEL_ID}", {})]


async def test_archive_model_rejects_other_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(400, "Bad Request: Cannot delete models of this kind")))

    result = await omni_archive_model(ArchiveModelInput(model_id=MODEL_ID))

    assert result.startswith("Error (400):")
    assert "Cannot delete models of this kind" in result


async def test_archive_model_reports_unsuccessful_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload={"success": False}))

    result = await omni_archive_model(ArchiveModelInput(model_id=MODEL_ID))

    assert result.startswith("Error: the API did not archive model")


# --------------------------------------------------------------------------- get YAML


async def test_get_model_yaml_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=YAML_PAYLOAD)
    _patch(monkeypatch, fake)

    result = await omni_get_model_yaml(GetModelYamlInput(model_id=MODEL_ID))

    assert f"# Model YAML — `{MODEL_ID}`" in result
    assert "| orders.topic | 14 | 9f2c |" in result
    assert "```yaml" in result
    assert "label: Orders" in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/yaml", {"params": {"includeChecksums": True}})]


async def test_get_model_yaml_sends_every_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=YAML_PAYLOAD)
    _patch(monkeypatch, fake)

    await omni_get_model_yaml(
        GetModelYamlInput(
            model_id=MODEL_ID,
            branch_id=BRANCH_ID,
            file_name="orders.topic",
            mode="staged",
            include_checksums=False,
            schema_name="ANALYTICS",
            fully_resolved=True,
        )
    )

    assert fake.calls == [
        (
            "GET",
            f"/v1/models/{MODEL_ID}/yaml",
            {
                "params": {
                    "includeChecksums": False,
                    "branchId": BRANCH_ID,
                    "fileName": "orders.topic",
                    "mode": "staged",
                    "includeSchemas": "ANALYTICS",
                    "fullyResolved": True,
                }
            },
        )
    ]


async def test_get_model_yaml_warns_when_checksums_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload={"files": {"orders.topic": "label: Orders\n"}, "version": 1}))

    result = await omni_get_model_yaml(GetModelYamlInput(model_id=MODEL_ID, include_checksums=False))

    assert "No checksums in this response" in result
    assert "not requested" in result


async def test_get_model_yaml_lists_only_when_many_files(monkeypatch: pytest.MonkeyPatch) -> None:
    files = {f"view_{index}.view": f"name: v{index}\n" for index in range(6)}
    _patch(monkeypatch, _FakeClient(payload={"files": files, "checksums": {}, "version": 2}))

    result = await omni_get_model_yaml(GetModelYamlInput(model_id=MODEL_ID))

    assert "Files returned: **6**" in result
    assert "Contents omitted" in result
    assert "```yaml" not in result


async def test_get_model_yaml_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=YAML_PAYLOAD))

    payload = json.loads(
        await omni_get_model_yaml(GetModelYamlInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["checksums"]["orders.topic"] == "9f2c"
    assert payload["files"]["orders.topic"] == "label: Orders\n"


async def test_get_model_yaml_branch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(404, "Branch does not exist")))

    result = await omni_get_model_yaml(GetModelYamlInput(model_id=MODEL_ID, branch_id=BRANCH_ID))

    assert result.startswith("Error (404):")
    assert "Branch does not exist" in result


# --------------------------------------------------------------------------- update YAML


async def test_update_model_yaml_sends_files_and_checksums(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _SequenceClient([{"success": True, "fileName": "orders.topic"}, {"success": True, "fileName": "model"}])
    _patch(monkeypatch, fake)

    result = await omni_update_model_yaml(
        UpdateModelYamlInput(
            model_id=MODEL_ID,
            files={"orders.topic": "label: Orders\n", "model": "name: Sales\n"},
            checksums={"orders.topic": "9f2c"},
            branch_id=BRANCH_ID,
            commit_message="Relabel orders",
            fully_resolved=False,
        )
    )

    assert "Updated **2** YAML file(s)" in result
    assert f"branch `{BRANCH_ID}`" in result
    assert "Checksum-guarded: `orders.topic`." in result
    assert "omni_validate_model" in result
    assert fake.calls == [
        (
            "POST",
            f"/v1/models/{MODEL_ID}/yaml",
            {
                "json_body": {
                    "fileName": "orders.topic",
                    "yaml": "label: Orders\n",
                    "mode": "combined",
                    "branchId": BRANCH_ID,
                    "commitMessage": "Relabel orders",
                    "previousChecksum": "9f2c",
                    "fullyResolved": False,
                }
            },
        ),
        (
            "POST",
            f"/v1/models/{MODEL_ID}/yaml",
            {
                "json_body": {
                    "fileName": "model",
                    "yaml": "name: Sales\n",
                    "mode": "combined",
                    "branchId": BRANCH_ID,
                    "commitMessage": "Relabel orders",
                    "fullyResolved": False,
                }
            },
        ),
    ]


async def test_update_model_yaml_without_checksums_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _SequenceClient([{"success": True}])
    _patch(monkeypatch, fake)

    result = await omni_update_model_yaml(
        UpdateModelYamlInput(model_id=MODEL_ID, files={"legacy.view": ""}, mode="extension")
    )

    assert "No checksums were sent" in result
    assert fake.calls == [
        (
            "POST",
            f"/v1/models/{MODEL_ID}/yaml",
            {"json_body": {"fileName": "legacy.view", "yaml": "", "mode": "extension"}},
        )
    ]


async def test_update_model_yaml_reports_stale_checksum_after_partial_write(monkeypatch: pytest.MonkeyPatch) -> None:
    conflict = _http_error(400, "File has been modified since it was fetched", path=f"/v1/models/{MODEL_ID}/yaml")
    _patch(monkeypatch, _SequenceClient([{"success": True}, conflict]))

    result = await omni_update_model_yaml(
        UpdateModelYamlInput(
            model_id=MODEL_ID,
            files={"orders.topic": "label: Orders\n", "customers.topic": "label: Customers\n"},
            checksums={"orders.topic": "9f2c", "customers.topic": "stale"},
        )
    )

    assert "Wrote 1 file(s) first: `orders.topic`." in result
    assert "Failed on `customers.topic`" in result
    assert "File has been modified since it was fetched" in result


def test_update_model_yaml_rejects_unsupported_file_names() -> None:
    with pytest.raises(ValueError, match="Invalid YAML file name"):
        UpdateModelYamlInput(model_id=MODEL_ID, files={"orders.yaml": "x"})
    with pytest.raises(ValueError, match="at least one file"):
        UpdateModelYamlInput(model_id=MODEL_ID, files={})


def test_update_model_yaml_rejects_checksums_for_unknown_files() -> None:
    with pytest.raises(ValueError, match="absent from `files`"):
        UpdateModelYamlInput(model_id=MODEL_ID, files={"orders.topic": "x"}, checksums={"customers.topic": "9f2c"})


def test_update_model_yaml_accepts_special_and_suffixed_names() -> None:
    params = UpdateModelYamlInput(
        model_id=MODEL_ID,
        files={"model": "a", "relationships": "b", "x.topic": "c", "y.composite_topic": "d", "z.view": "e"},
    )

    assert set(params.files) == {"model", "relationships", "x.topic", "y.composite_topic", "z.view"}


# --------------------------------------------------------------------------- delete YAML


async def test_delete_model_yaml_file(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True, "fileName": "legacy_orders.topic"})
    _patch(monkeypatch, fake)

    result = await omni_delete_model_yaml_file(
        DeleteModelYamlFileInput(
            model_id=MODEL_ID,
            file_name="legacy_orders.topic",
            branch_id=BRANCH_ID,
            mode="combined",
            commit_message="Drop unused topic",
        )
    )

    assert "Deleted YAML file `legacy_orders.topic`" in result
    assert fake.calls == [
        (
            "DELETE",
            f"/v1/models/{MODEL_ID}/yaml",
            {
                "params": {
                    "fileName": "legacy_orders.topic",
                    "branchId": BRANCH_ID,
                    "mode": "combined",
                    "commitMessage": "Drop unused topic",
                }
            },
        )
    ]


def test_delete_model_yaml_file_rejects_special_and_unknown_suffixes() -> None:
    for name in ("model", "relationships", "orders.yaml", "orders"):
        with pytest.raises(ValueError, match="Invalid YAML file name"):
            DeleteModelYamlFileInput(model_id=MODEL_ID, file_name=name)


async def test_delete_model_yaml_file_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(404, "File does not exist")))

    result = await omni_delete_model_yaml_file(DeleteModelYamlFileInput(model_id=MODEL_ID, file_name="gone.view"))

    assert result.startswith("Error (404):")
    assert "File does not exist" in result


# --------------------------------------------------------------------------- validate


async def test_validate_model_groups_issues_by_file(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=VALIDATION_ISSUES)
    _patch(monkeypatch, fake)

    result = await omni_validate_model(ValidateModelInput(model_id=MODEL_ID, branch_id=BRANCH_ID))

    assert "**1** error(s), **1** warning(s)." in result
    assert "## `blob_sales.topic` (1)" in result
    assert "## `main__orders.view` (1)" in result
    assert 'auto-fix available: Delete topic "blob_sales"' in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/validate", {"params": {"branchId": BRANCH_ID}})]


async def test_validate_model_without_branch_and_no_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[])
    _patch(monkeypatch, fake)

    result = await omni_validate_model(ValidateModelInput(model_id=MODEL_ID))

    assert "_No validation issues._" in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/validate", {"params": None})]


async def test_validate_model_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=VALIDATION_ISSUES))

    payload = json.loads(
        await omni_validate_model(ValidateModelInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["issueCount"] == 2
    assert payload["issues"][0]["yaml_path"] == "blob_sales.topic"


# --------------------------------------------------------------------------- schemas


async def test_list_model_schemas_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=["ANALYTICS", "PUBLIC", "STAGING"])
    _patch(monkeypatch, fake)

    result = await omni_list_model_schemas(ListModelSchemasInput(model_id=MODEL_ID, branch_id=BRANCH_ID))

    assert "**3** schema(s)." in result
    assert "- `ANALYTICS`" in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/schemas", {"params": {"branchId": BRANCH_ID}})]


async def test_list_model_schemas_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=["ANALYTICS"])
    _patch(monkeypatch, fake)

    payload = json.loads(
        await omni_list_model_schemas(ListModelSchemasInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["schemas"] == ["ANALYTICS"]
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/schemas", {"params": None})]


# --------------------------------------------------------------------------- refresh


async def test_refresh_model_schema_hard(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"jobId": "job-1", "modelId": MODEL_ID, "status": "running"})
    _patch(monkeypatch, fake)

    result = await omni_refresh_model_schema(RefreshModelSchemaInput(model_id=MODEL_ID))

    assert "job `job-1`" in result
    assert "hard (drops removed objects)" in result
    assert fake.calls == [("POST", f"/v1/models/{MODEL_ID}/refresh", {"params": {"hard_refresh": True}})]


async def test_refresh_model_schema_selective_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"jobId": "job-2", "status": "running"})
    _patch(monkeypatch, fake)

    result = await omni_refresh_model_schema(
        RefreshModelSchemaInput(
            model_id=MODEL_ID,
            branch_id=BRANCH_ID,
            hard_refresh=False,
            schemas="ANALYTICS",
            tables="orders,customers",
        )
    )

    assert "soft (additive only)" in result
    assert "tables: orders,customers" in result
    assert fake.calls == [
        (
            "POST",
            f"/v1/models/{MODEL_ID}/refresh",
            {
                "params": {
                    "hard_refresh": False,
                    "branch_id": BRANCH_ID,
                    "schemas": "ANALYTICS",
                    "tables": "orders,customers",
                }
            },
        )
    ]


def test_refresh_model_schema_rejects_selective_hard_refresh() -> None:
    with pytest.raises(ValueError, match="require hard_refresh=false"):
        RefreshModelSchemaInput(model_id=MODEL_ID, schemas="ANALYTICS")
    with pytest.raises(ValueError, match="require hard_refresh=false"):
        RefreshModelSchemaInput(model_id=MODEL_ID, hard_refresh=True, tables="orders")


async def test_refresh_model_schema_branch_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        _FakeClient(exc=_http_error(400, "Bad Request: branch_id is required when branch schema refresh is enabled")),
    )

    result = await omni_refresh_model_schema(RefreshModelSchemaInput(model_id=MODEL_ID))

    assert result.startswith("Error (400):")
    assert "branch_id is required when branch schema refresh is enabled" in result


# --------------------------------------------------------------------------- cache reset


async def test_reset_model_cache_now(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True, "cache_reset": {"id": "cr-1", "reset_at": "2026-02-01T00:00:00Z"}})
    _patch(monkeypatch, fake)

    result = await omni_reset_model_cache(ResetModelCacheInput(model_id=MODEL_ID, cache_policy_name="daily"))

    assert "Reset cache policy `daily`" in result
    assert "record `cr-1`" in result
    assert fake.calls == [("POST", f"/v1/models/{MODEL_ID}/cache_reset/daily", {"json_body": None})]


async def test_reset_model_cache_with_reset_at(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"cache_reset": {"id": "cr-2"}})
    _patch(monkeypatch, fake)

    result = await omni_reset_model_cache(
        ResetModelCacheInput(model_id=MODEL_ID, cache_policy_name="hourly cache", reset_at="2020-01-31T12:00:00Z")
    )

    assert "2020-01-31T12:00:00Z" in result
    assert fake.calls == [
        (
            "POST",
            f"/v1/models/{MODEL_ID}/cache_reset/hourly%20cache",
            {"json_body": {"resetAt": "2020-01-31T12:00:00Z"}},
        )
    ]


def test_reset_model_cache_rejects_future_and_invalid_timestamps() -> None:
    with pytest.raises(ValueError, match="cannot be future dated"):
        ResetModelCacheInput(model_id=MODEL_ID, cache_policy_name="daily", reset_at="2999-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="ISO-8601"):
        ResetModelCacheInput(model_id=MODEL_ID, cache_policy_name="daily", reset_at="yesterday")


# --------------------------------------------------------------------------- migrate


async def test_migrate_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    result = await omni_migrate_model(
        MigrateModelInput(
            model_id=MODEL_ID,
            git_ref="main",
            target_model_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            branch_name="migrate-from-prod",
            commit_message="Migrate model from production",
            delete_views_and_topics_missing_from_source=False,
        )
    )

    assert "Migrated model" in result
    assert "branch `migrate-from-prod`" in result
    assert fake.calls == [
        (
            "POST",
            f"/v1/models/{MODEL_ID}/migrate",
            {
                "json_body": {
                    "gitRef": "main",
                    "targetModelId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "branchName": "migrate-from-prod",
                    "commitMessage": "Migrate model from production",
                    "deleteViewsAndTopicsMissingFromSource": False,
                }
            },
        )
    ]


async def test_migrate_model_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        _FakeClient(exc=_http_error(403, "Forbidden: User does not have access to the source or target model")),
    )

    result = await omni_migrate_model(MigrateModelInput(model_id=MODEL_ID, git_ref="main", target_model_id="target-1"))

    assert result.startswith("Error (403):")
    assert "does not have access to the source or target model" in result


# --------------------------------------------------------------------------- branch / view deletes


async def test_delete_model_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    result = await omni_delete_model_branch(DeleteModelBranchInput(model_id=MODEL_ID, branch_name="team/pricing-fix"))

    assert "Deleted branch `team/pricing-fix`" in result
    assert fake.calls == [("DELETE", f"/v1/models/{MODEL_ID}/branch/team%2Fpricing-fix", {})]


async def test_delete_model_branch_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(404, "Shared model or branch model does not exist")))

    result = await omni_delete_model_branch(DeleteModelBranchInput(model_id=MODEL_ID, branch_name="gone"))

    assert result.startswith("Error (404):")
    assert "Shared model or branch model does not exist" in result


async def test_delete_model_view(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    result = await omni_delete_model_view(
        DeleteModelViewInput(model_id=MODEL_ID, view_name="main__orders", branch_id=BRANCH_ID, mode="COMBINED")
    )

    assert "Deleted or ignored view `main__orders`" in result
    assert "mode `COMBINED`" in result
    assert fake.calls == [
        (
            "DELETE",
            f"/v1/models/{MODEL_ID}/view/main__orders",
            {"params": {"branchId": BRANCH_ID, "mode": "COMBINED"}},
        )
    ]


async def test_delete_model_view_without_options(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    _patch(monkeypatch, fake)

    result = await omni_delete_model_view(DeleteModelViewInput(model_id=MODEL_ID, view_name="temp view"))

    assert "the API default" in result
    assert fake.calls == [("DELETE", f"/v1/models/{MODEL_ID}/view/temp%20view", {"params": None})]


def test_delete_model_view_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        DeleteModelViewInput(model_id=MODEL_ID, view_name="v", mode="combined")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- AI actions / topic


async def test_get_model_ai_agent_actions_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=AI_ACTIONS)
    _patch(monkeypatch, fake)

    result = await omni_get_model_ai_agent_actions(GetModelAiAgentActionsInput(model_id=MODEL_ID))

    assert "**2** action(s): 1 sample query/queries, 1 skill(s)." in result
    assert "**Revenue by region** (`revenue_by_region`, sample_query)" in result
    assert "Skill: analyze_trends Analyze trends in revenue over time" in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/ai-agent-actions", {})]


async def test_get_model_ai_agent_actions_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload={"success": True, "records": []}))

    result = await omni_get_model_ai_agent_actions(GetModelAiAgentActionsInput(model_id=MODEL_ID))

    assert "_No AI agent actions are configured" in result


async def test_get_model_ai_agent_actions_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=AI_ACTIONS))

    payload = json.loads(
        await omni_get_model_ai_agent_actions(
            GetModelAiAgentActionsInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON)
        )
    )

    assert [record["id"] for record in payload["records"]] == ["revenue_by_region", "analyze_trends"]


async def test_get_topic_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=TOPIC_PAYLOAD)
    _patch(monkeypatch, fake)

    result = await omni_get_topic(GetTopicInput(model_id=MODEL_ID, topic_name="Customers"))

    assert "# Topic `Customers`" in result
    assert "Base view: `main__customers`" in result
    assert "Views: **1** — 2 dimension(s), 1 measure(s)" in result
    assert "| main__customers | Customers | main | customers | 2 | 1 |" in result
    assert "ALWAYS_LEFT" in result
    assert "- `main__order_items` via main__orders" in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/topic/Customers", {})]


async def test_get_topic_json_and_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=TOPIC_PAYLOAD)
    _patch(monkeypatch, fake)

    payload = json.loads(
        await omni_get_topic(
            GetTopicInput(model_id=MODEL_ID, topic_name="order items/v2", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["topic"]["name"] == "Customers"
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/topic/order%20items%2Fv2", {})]


async def test_get_topic_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_http_error(404, "Topic not found or access not permitted")))

    result = await omni_get_topic(GetTopicInput(model_id=MODEL_ID, topic_name="Ghost"))

    assert result.startswith("Error (404):")


async def test_get_topic_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_get_topic(GetTopicInput(model_id=MODEL_ID, topic_name="Ghost"))

    assert "_No topic named `Ghost` was returned" in result


# --------------------------------------------------------------------------- input hygiene


def test_inputs_reject_unknown_fields() -> None:
    for model in (
        ListModelsInput,
        CreateModelInput,
        RenameModelInput,
        ArchiveModelInput,
        GetModelYamlInput,
        UpdateModelYamlInput,
        DeleteModelYamlFileInput,
        ValidateModelInput,
        ListModelSchemasInput,
        RefreshModelSchemaInput,
        ResetModelCacheInput,
        MigrateModelInput,
        DeleteModelBranchInput,
        DeleteModelViewInput,
        GetModelAiAgentActionsInput,
        GetTopicInput,
    ):
        with pytest.raises(ValueError):
            model(unexpected="x")  # type: ignore[call-arg]
