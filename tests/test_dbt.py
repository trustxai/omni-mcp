"""Unit tests for the dbt tools (config, environments, exposures, branch env) against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.dbt import (
    CreateDbtEnvironmentInput,
    DeleteDbtConfigurationInput,
    DeleteDbtEnvironmentInput,
    GetDbtConfigurationInput,
    GetDbtExposuresInput,
    ListDbtEnvironmentsInput,
    SetDbtEnvironmentOnModelBranchInput,
    UpdateDbtConfigurationInput,
    UpdateDbtEnvironmentInput,
    omni_create_dbt_environment,
    omni_delete_dbt_configuration,
    omni_delete_dbt_environment,
    omni_get_dbt_configuration,
    omni_get_dbt_exposures,
    omni_list_dbt_environments,
    omni_set_dbt_environment_on_model_branch,
    omni_update_dbt_configuration,
    omni_update_dbt_environment,
)

CONNECTION_ID = "550e8400-e29b-41d4-a716-446655440000"
ENVIRONMENT_ID = "247dc6dc-2a58-4688-9521-c5ed3e99c1e8"
MODEL_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

CONFIGURED: dict[str, Any] = {
    "authMethod": "ssh",
    "autogenRelationships": True,
    "branch": "main",
    "dbtVersion": "Auto",
    "enableSemanticLayer": False,
    "enableVirtualSchemas": False,
    "projectRootPath": "dbt_project",
    "sshUrl": "git@github.com:org/repo.git",
    "supportsDbt": True,
}

NOT_CONFIGURED: dict[str, Any] = {
    "supportsDbt": True,
    "message": "dbt not configured for this connection",
}

ENVIRONMENT: dict[str, Any] = {
    "id": ENVIRONMENT_ID,
    "name": "Development",
    "isDefaultEnvironment": False,
    "isDeferralEnabled": True,
    "ownerId": "550e8400-e29b-41d4-a716-446655440001",
    "targetDatabase": "analytics_dev",
    "targetName": None,
    "targetRole": None,
    "targetSchema": "dev_schema",
    "variables": [
        {"id": "var-1", "name": "DBT_TARGET", "value": "dev", "isSecret": False},
        {"id": "var-2", "name": "DBT_API_KEY", "value": None, "isSecret": True},
    ],
}

DEFAULT_ENVIRONMENT: dict[str, Any] = {
    "id": "prod-env-id",
    "name": "Production",
    "isDefaultEnvironment": True,
    "isDeferralEnabled": False,
    "ownerId": None,
    "targetDatabase": None,
    "targetName": None,
    "targetRole": None,
    "targetSchema": "public",
    "variables": [],
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


def _http_error(status: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/whatever")
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# --------------------------------------------------------------------------- #
# omni_get_dbt_configuration
# --------------------------------------------------------------------------- #


async def test_get_dbt_configuration_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=CONFIGURED)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_get_dbt_configuration(GetDbtConfigurationInput(connection_id=CONNECTION_ID))

    assert "dbt Configuration" in result
    assert "main" in result
    assert "git@github.com:org/repo.git" in result
    assert fake.calls == [("GET", f"/v1/connections/{CONNECTION_ID}/dbt", {})]


async def test_get_dbt_configuration_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=NOT_CONFIGURED)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_get_dbt_configuration(GetDbtConfigurationInput(connection_id=CONNECTION_ID))

    assert "dbt not configured for this connection" in result


async def test_get_dbt_configuration_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=CONFIGURED)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_get_dbt_configuration(
        GetDbtConfigurationInput(connection_id=CONNECTION_ID, response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["branch"] == "main"
    assert payload["supportsDbt"] is True


async def test_get_dbt_configuration_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Permission denied - connection admin role required"))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_get_dbt_configuration(GetDbtConfigurationInput(connection_id=CONNECTION_ID))

    assert result.startswith("Error (403):")
    assert "connection admin" in result.lower()


# --------------------------------------------------------------------------- #
# omni_update_dbt_configuration
# --------------------------------------------------------------------------- #


async def test_update_dbt_configuration_sends_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=CONFIGURED)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_update_dbt_configuration(
        UpdateDbtConfigurationInput(
            connection_id=CONNECTION_ID,
            branch="main",
            autogen_relationships=True,
            enable_virtual_schemas=False,
            ssh_url="git@github.com:org/repo.git",
        )
    )

    assert "Updated dbt configuration" in result
    assert "main" in result
    assert fake.calls == [
        (
            "PUT",
            f"/v1/connections/{CONNECTION_ID}/dbt",
            {
                "json_body": {
                    "autogenRelationships": True,
                    "branch": "main",
                    "enableVirtualSchemas": False,
                    "sshUrl": "git@github.com:org/repo.git",
                }
            },
        )
    ]


async def test_update_dbt_configuration_optional_fields_included_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=CONFIGURED)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    await omni_update_dbt_configuration(
        UpdateDbtConfigurationInput(
            connection_id=CONNECTION_ID,
            branch="main",
            autogen_relationships=True,
            enable_virtual_schemas=False,
            ssh_url="https://github.com/org/repo.git",
            auth_method="github_app",
            github_app_installation_id="12345678",
            project_root_path="",
            dbt_version="1.11",
        )
    )

    _, _, kwargs = fake.calls[0]
    body = kwargs["json_body"]
    assert body["authMethod"] == "github_app"
    assert body["githubAppInstallationId"] == "12345678"
    assert body["projectRootPath"] == ""
    assert body["dbtVersion"] == "1.11"


async def test_update_dbt_configuration_never_echoes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=CONFIGURED)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_update_dbt_configuration(
        UpdateDbtConfigurationInput(
            connection_id=CONNECTION_ID,
            branch="main",
            autogen_relationships=True,
            enable_virtual_schemas=False,
            ssh_url="https://example.com/org/repo.git",
            auth_method="https_token",
            token="super-secret-token-123",
        )
    )

    assert "super-secret-token-123" not in result
    assert fake.calls[0][2]["json_body"]["token"] == "super-secret-token-123"


async def test_update_dbt_configuration_rejects_bad_auth_method() -> None:
    with pytest.raises(ValidationError):
        UpdateDbtConfigurationInput(
            connection_id=CONNECTION_ID,
            branch="main",
            autogen_relationships=True,
            enable_virtual_schemas=False,
            ssh_url="git@github.com:org/repo.git",
            auth_method="oauth",  # type: ignore[arg-type]
        )


async def test_update_dbt_configuration_rejects_bad_project_root_path() -> None:
    with pytest.raises(ValidationError):
        UpdateDbtConfigurationInput(
            connection_id=CONNECTION_ID,
            branch="main",
            autogen_relationships=True,
            enable_virtual_schemas=False,
            ssh_url="git@github.com:org/repo.git",
            project_root_path="/absolute/path",
        )


async def test_update_dbt_configuration_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Invalid request body"))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_update_dbt_configuration(
        UpdateDbtConfigurationInput(
            connection_id=CONNECTION_ID,
            branch="main",
            autogen_relationships=True,
            enable_virtual_schemas=False,
            ssh_url="git@github.com:org/repo.git",
        )
    )

    assert result.startswith("Error (400):")


# --------------------------------------------------------------------------- #
# omni_delete_dbt_configuration
# --------------------------------------------------------------------------- #


async def test_delete_dbt_configuration_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True, "message": "dbt repository unlinked successfully"})
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_delete_dbt_configuration(DeleteDbtConfigurationInput(connection_id=CONNECTION_ID))

    assert "Deleted dbt configuration" in result
    assert "unlinked successfully" in result
    assert fake.calls == [("DELETE", f"/v1/connections/{CONNECTION_ID}/dbt", {})]


async def test_delete_dbt_configuration_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Connection not found or dbt not configured"))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_delete_dbt_configuration(DeleteDbtConfigurationInput(connection_id=CONNECTION_ID))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_create_dbt_environment
# --------------------------------------------------------------------------- #


async def test_create_dbt_environment_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=DEFAULT_ENVIRONMENT)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_create_dbt_environment(
        CreateDbtEnvironmentInput(connection_id=CONNECTION_ID, name="Production dbt", target_schema="analytics")
    )

    assert "Created dbt environment" in result
    assert "Production" in result
    assert "shared" in result
    assert fake.calls == [
        (
            "POST",
            f"/v1/connections/{CONNECTION_ID}/dbt/environments",
            {"json_body": {"name": "Production dbt", "targetSchema": "analytics"}},
        )
    ]


async def test_create_dbt_environment_with_owner_and_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ENVIRONMENT)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    owner_id = "550e8400-e29b-41d4-a716-446655440001"
    result = await omni_create_dbt_environment(
        CreateDbtEnvironmentInput(
            connection_id=CONNECTION_ID,
            name="My dev env",
            target_schema="analytics_dev",
            owner_id=owner_id,
            variables=[{"name": "DBT_TARGET", "value": "dev", "isSecret": False}],
        )
    )

    assert "personal" in result
    assert owner_id in result
    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"]["ownerId"] == owner_id
    assert kwargs["json_body"]["variables"] == [{"name": "DBT_TARGET", "value": "dev", "isSecret": False}]


def test_create_dbt_environment_rejects_incomplete_variable() -> None:
    with pytest.raises(ValidationError):
        CreateDbtEnvironmentInput(
            connection_id=CONNECTION_ID,
            name="Env",
            target_schema="analytics",
            variables=[{"name": "DBT_TARGET"}],
        )


def test_create_dbt_environment_rejects_empty_variables_list() -> None:
    with pytest.raises(ValidationError):
        CreateDbtEnvironmentInput(connection_id=CONNECTION_ID, name="Env", target_schema="analytics", variables=[])


async def test_create_dbt_environment_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Insufficient permissions. Connection Admin permissions required."))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_create_dbt_environment(
        CreateDbtEnvironmentInput(connection_id=CONNECTION_ID, name="Env", target_schema="analytics")
    )

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------- #
# omni_list_dbt_environments
# --------------------------------------------------------------------------- #


async def test_list_dbt_environments_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[DEFAULT_ENVIRONMENT, ENVIRONMENT])
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_list_dbt_environments(ListDbtEnvironmentsInput(connection_id=CONNECTION_ID))

    assert "Production" in result
    assert "Development" in result
    assert "dev" in result  # non-secret variable value survives
    assert "DBT_API_KEY" not in result or "***" in result
    assert fake.calls == [("GET", f"/v1/connections/{CONNECTION_ID}/dbt/environments", {})]


async def test_list_dbt_environments_masks_secret_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[ENVIRONMENT])
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_list_dbt_environments(
        ListDbtEnvironmentsInput(connection_id=CONNECTION_ID, response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    variables = payload["items"][0]["variables"]
    secret_var = next(v for v in variables if v["name"] == "DBT_API_KEY")
    plain_var = next(v for v in variables if v["name"] == "DBT_TARGET")
    assert secret_var["value"] == "***"
    assert plain_var["value"] == "dev"


async def test_list_dbt_environments_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=[])
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_list_dbt_environments(ListDbtEnvironmentsInput(connection_id=CONNECTION_ID))

    assert "_No records._" in result


async def test_list_dbt_environments_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Connection not found"))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_list_dbt_environments(ListDbtEnvironmentsInput(connection_id=CONNECTION_ID))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_update_dbt_environment
# --------------------------------------------------------------------------- #


async def test_update_dbt_environment_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ENVIRONMENT)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_update_dbt_environment(
        UpdateDbtEnvironmentInput(
            connection_id=CONNECTION_ID, environment_id=ENVIRONMENT_ID, name="Development", target_schema="dev_schema"
        )
    )

    assert "Updated dbt environment" in result
    assert fake.calls == [
        (
            "PUT",
            f"/v1/connections/{CONNECTION_ID}/dbt/environments/{ENVIRONMENT_ID}",
            {"json_body": {"name": "Development", "targetSchema": "dev_schema"}},
        )
    ]


async def test_update_dbt_environment_clear_owner_sends_null(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=DEFAULT_ENVIRONMENT)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    await omni_update_dbt_environment(
        UpdateDbtEnvironmentInput(connection_id=CONNECTION_ID, environment_id=ENVIRONMENT_ID, clear_owner=True)
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"] == {"ownerId": None}


async def test_update_dbt_environment_owner_id_takes_precedence_over_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ENVIRONMENT)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)
    owner_id = "550e8400-e29b-41d4-a716-446655440099"

    await omni_update_dbt_environment(
        UpdateDbtEnvironmentInput(
            connection_id=CONNECTION_ID, environment_id=ENVIRONMENT_ID, owner_id=owner_id, clear_owner=True
        )
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"] == {"ownerId": owner_id}


async def test_update_dbt_environment_no_fields_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ENVIRONMENT)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_update_dbt_environment(
        UpdateDbtEnvironmentInput(connection_id=CONNECTION_ID, environment_id=ENVIRONMENT_ID)
    )

    assert result.startswith("Error:")
    assert fake.calls == []  # no network call made for a no-op update


def test_update_dbt_environment_rejects_variable_without_id_or_name() -> None:
    with pytest.raises(ValidationError):
        UpdateDbtEnvironmentInput(
            connection_id=CONNECTION_ID,
            environment_id=ENVIRONMENT_ID,
            variables=[{"value": "x", "isSecret": False}],
        )


def test_update_dbt_environment_allows_existing_variable_update_by_id() -> None:
    params = UpdateDbtEnvironmentInput(
        connection_id=CONNECTION_ID,
        environment_id=ENVIRONMENT_ID,
        variables=[{"id": "var-1", "isSecret": True}],
    )
    assert params.variables == [{"id": "var-1", "isSecret": True}]


async def test_update_dbt_environment_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Connection or dbt environment not found"))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_update_dbt_environment(
        UpdateDbtEnvironmentInput(connection_id=CONNECTION_ID, environment_id=ENVIRONMENT_ID, name="X")
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_delete_dbt_environment
# --------------------------------------------------------------------------- #


async def test_delete_dbt_environment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_delete_dbt_environment(
        DeleteDbtEnvironmentInput(connection_id=CONNECTION_ID, environment_id=ENVIRONMENT_ID)
    )

    assert "Deleted dbt environment" in result
    assert fake.calls == [("DELETE", f"/v1/connections/{CONNECTION_ID}/dbt/environments/{ENVIRONMENT_ID}", {})]


async def test_delete_dbt_environment_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Connection dialect does not support dbt environments"))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_delete_dbt_environment(
        DeleteDbtEnvironmentInput(connection_id=CONNECTION_ID, environment_id=ENVIRONMENT_ID)
    )

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------- #
# omni_get_dbt_exposures
# --------------------------------------------------------------------------- #

EXPOSURES_PAYLOAD: dict[str, Any] = {
    "pageInfo": {"hasNextPage": True, "nextCursor": "cursor-2", "pageSize": 20, "totalRecords": 2},
    "records": [
        {
            "dashboard_identifier": "abc123",
            "deduplication_name": "sales_overview_abc123",
            "exposure": {
                "name": "sales_overview",
                "type": "dashboard",
                "owner": {"name": "Blob Ross", "email": "blob.ross@blobsrus.com"},
                "depends_on": ["ref('orders')", "ref('customers')"],
                "label": "Sales Overview",
                "url": "https://your-org.omni.co/dashboards/abc123",
            },
        },
        {
            "dashboard_identifier": "def456",
            "deduplication_name": "empty_dash_def456",
            "exposure": None,
        },
    ],
}


async def test_get_dbt_exposures_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=EXPOSURES_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_get_dbt_exposures(GetDbtExposuresInput(model_id=MODEL_ID))

    assert "Sales Overview" in result
    assert "Blob Ross" in result
    assert "no dbt models referenced" in result
    assert 'cursor="cursor-2"' in result
    assert fake.calls == [("GET", f"/v1/models/{MODEL_ID}/dbt-exposures", {"params": {"pageSize": 20}})]


async def test_get_dbt_exposures_passes_cursor_and_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=EXPOSURES_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    await omni_get_dbt_exposures(
        GetDbtExposuresInput(model_id=MODEL_ID, page_size=50, cursor="prev-cursor", branch_id="branch-1")
    )

    assert fake.calls == [
        (
            "GET",
            f"/v1/models/{MODEL_ID}/dbt-exposures",
            {"params": {"pageSize": 50, "cursor": "prev-cursor", "branch_id": "branch-1"}},
        )
    ]


async def test_get_dbt_exposures_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=EXPOSURES_PAYLOAD)
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_get_dbt_exposures(GetDbtExposuresInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["count"] == 2
    assert payload["nextCursor"] == "cursor-2"
    assert payload["items"][0]["exposure"]["name"] == "sales_overview"


def test_get_dbt_exposures_rejects_page_size_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        GetDbtExposuresInput(model_id=MODEL_ID, page_size=0)
    with pytest.raises(ValidationError):
        GetDbtExposuresInput(model_id=MODEL_ID, page_size=101)


async def test_get_dbt_exposures_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, f"Model with id {MODEL_ID} does not exist"))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_get_dbt_exposures(GetDbtExposuresInput(model_id=MODEL_ID))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_set_dbt_environment_on_model_branch
# --------------------------------------------------------------------------- #


async def test_set_dbt_environment_on_model_branch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)
    env_id = "b2c3d4e5-f6a7-8901-bcde-f12345678901"

    result = await omni_set_dbt_environment_on_model_branch(
        SetDbtEnvironmentOnModelBranchInput(
            model_id=MODEL_ID, branch_name="feature/new-models", dbt_environment_id=env_id
        )
    )

    assert "Set dbt environment" in result
    assert env_id in result
    assert fake.calls == [
        (
            "POST",
            f"/v1/models/{MODEL_ID}/branch/feature%2Fnew-models/dbt",
            {"json_body": {"dbt_environment_id": env_id}},
        )
    ]


async def test_set_dbt_environment_on_model_branch_with_git_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)
    env_id = "b2c3d4e5-f6a7-8901-bcde-f12345678901"

    result = await omni_set_dbt_environment_on_model_branch(
        SetDbtEnvironmentOnModelBranchInput(
            model_id=MODEL_ID, branch_name="main", dbt_environment_id=env_id, dbt_git_branch="feature/branch"
        )
    )

    assert "feature/branch" in result
    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"] == {"dbt_environment_id": env_id, "dbt_git_branch": "feature/branch"}


async def test_set_dbt_environment_on_model_branch_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "dbt environment not found"))
    monkeypatch.setattr("omni_mcp.tools.dbt.get_client", lambda: fake)

    result = await omni_set_dbt_environment_on_model_branch(
        SetDbtEnvironmentOnModelBranchInput(model_id=MODEL_ID, branch_name="main", dbt_environment_id="missing-env")
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# General input-model hygiene
# --------------------------------------------------------------------------- #


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GetDbtConfigurationInput(connection_id=CONNECTION_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ListDbtEnvironmentsInput(connection_id=CONNECTION_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SetDbtEnvironmentOnModelBranchInput(
            model_id=MODEL_ID,
            branch_name="main",
            dbt_environment_id="x",
            unexpected="x",  # type: ignore[call-arg]
        )
