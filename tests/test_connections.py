"""Unit tests for the connections/connection-environments/schema-refresh-schedule tools."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.connections import (
    CreateConnectionEnvironmentsInput,
    CreateConnectionInput,
    CreateSchemaRefreshScheduleInput,
    DeleteConnectionEnvironmentInput,
    DeleteConnectionInput,
    DeleteSchemaRefreshScheduleInput,
    EnvironmentUserAttributeInput,
    GetConnectionInput,
    GetSchemaRefreshScheduleInput,
    ListConnectionsInput,
    ListSchemaRefreshSchedulesInput,
    UpdateConnectionEnvironmentInput,
    UpdateConnectionInput,
    UpdateSchemaRefreshScheduleInput,
    omni_create_connection,
    omni_create_connection_environments,
    omni_create_schema_refresh_schedule,
    omni_delete_connection,
    omni_delete_connection_environment,
    omni_delete_schema_refresh_schedule,
    omni_get_connection,
    omni_get_schema_refresh_schedule,
    omni_list_connections,
    omni_list_schema_refresh_schedules,
    omni_update_connection,
    omni_update_connection_environment,
    omni_update_schema_refresh_schedule,
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


def _http_error(status: int, detail: str, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/connections/x")
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request, headers=headers)
    return httpx.HTTPStatusError(detail, request=request, response=response)


CONNECTION: dict[str, Any] = {
    "id": "c0f12353-4817-4398-bcc0-d501e6dd2f64",
    "name": "Production Snowflake",
    "dialect": "snowflake",
    "database": "analytics_db",
    "defaultSchema": "public",
    "deletedAt": None,
    "baseRole": "QUERIER",
    "branchConnectionEnvironmentOverridesUserAttr": False,
    "environmentConnectionSwitchesSchemaModel": False,
    "userAttributeNameForConnectionEnvironments": None,
    "userAttributeValuesForDefaultEnvironment": None,
    "createdAt": "2025-01-15T10:30:00.000Z",
    "updatedAt": "2025-01-15T10:30:00.000Z",
}

SCHEDULE: dict[str, Any] = {
    "scheduleId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "connectionId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "schedule": "00 09 * * ? *",
    "timezone": "America/New_York",
    "description": "At 09:00 AM EDT",
    "hardRefresh": False,
    "createdAt": "2025-01-15T10:30:00.000Z",
    "updatedAt": "2025-01-15T10:30:00.000Z",
    "disabledAt": None,
}


# --------------------------------------------------------------------------- #
# omni_list_connections
# --------------------------------------------------------------------------- #


async def test_list_connections_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"connections": [CONNECTION]})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_list_connections(ListConnectionsInput())

    assert "Production Snowflake" in result
    assert "snowflake" in result
    assert fake.calls == [("GET", "/v1/connections", {"params": None})]


async def test_list_connections_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"connections": [CONNECTION]})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_list_connections(ListConnectionsInput(response_format=ResponseFormat.JSON))
    payload = json.loads(result)

    assert payload["count"] == 1
    assert payload["connections"][0]["id"] == CONNECTION["id"]


async def test_list_connections_applies_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"connections": []})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    await omni_list_connections(
        ListConnectionsInput(
            name="prod",
            database="analytics",
            dialects=["postgres", "mysql"],
            sort_field="name",
            sort_direction="asc",
            include_deleted=True,
        )
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/connections",
            {
                "params": {
                    "name": "prod",
                    "database": "analytics",
                    "dialect": "postgres,mysql",
                    "sortField": "name",
                    "sortDirection": "asc",
                    "includeDeleted": True,
                }
            },
        )
    ]


async def test_list_connections_masks_password_like_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    tainted = dict(CONNECTION, oauthClientSecretUnencrypted="super-secret-value")
    fake = _FakeClient(payload={"connections": [tainted]})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    md_result = await omni_list_connections(ListConnectionsInput())
    json_result = await omni_list_connections(ListConnectionsInput(response_format=ResponseFormat.JSON))

    assert "super-secret-value" not in md_result
    assert "super-secret-value" not in json_result
    assert "***MASKED***" in json.loads(json_result)["connections"][0]["oauthClientSecretUnencrypted"]


async def test_list_connections_invalid_dialect_rejected() -> None:
    with pytest.raises(ValueError):
        ListConnectionsInput(dialects=["not_a_real_dialect"])  # type: ignore[list-item]


# --------------------------------------------------------------------------- #
# omni_get_connection
# --------------------------------------------------------------------------- #


async def test_get_connection_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"connection": CONNECTION})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_get_connection(GetConnectionInput(connection_id=CONNECTION["id"]))

    assert "Production Snowflake" in result
    assert CONNECTION["id"] in result
    assert fake.calls == [("GET", f"/v1/connections/{CONNECTION['id']}", {})]


async def test_get_connection_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"connection": CONNECTION})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_get_connection(
        GetConnectionInput(connection_id=CONNECTION["id"], response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["connection"]["dialect"] == "snowflake"


async def test_get_connection_masks_password_like_fields_both_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    tainted = dict(CONNECTION, apiSecretToken="do-not-leak")
    fake = _FakeClient(payload={"connection": tainted})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    md_result = await omni_get_connection(GetConnectionInput(connection_id=CONNECTION["id"]))
    json_result = await omni_get_connection(
        GetConnectionInput(connection_id=CONNECTION["id"], response_format=ResponseFormat.JSON)
    )

    assert "do-not-leak" not in md_result
    assert "do-not-leak" not in json_result
    assert json.loads(json_result)["connection"]["apiSecretToken"] == "***MASKED***"


async def test_get_connection_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Connection not found"))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_get_connection(GetConnectionInput(connection_id="missing-id"))

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_create_connection
# --------------------------------------------------------------------------- #


async def test_create_connection_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True, "data": "new-connection-id"})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_create_connection(
        CreateConnectionInput(
            dialect="postgres",
            name="My Postgres Connection",
            host="postgres.example.com",
            port=5432,
            database="mydb",
            username="dbuser",
            password_unencrypted="mypassword",
        )
    )

    assert "new-connection-id" in result
    assert "My Postgres Connection" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/connections",
            {
                "json_body": {
                    "dialect": "postgres",
                    "name": "My Postgres Connection",
                    "passwordUnencrypted": "mypassword",
                    "host": "postgres.example.com",
                    "port": 5432,
                    "database": "mydb",
                    "username": "dbuser",
                }
            },
        )
    ]


async def test_create_connection_settings_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True, "data": "id-2"})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    await omni_create_connection(
        CreateConnectionInput(
            dialect="bigquery",
            name="My BigQuery Connection",
            password_unencrypted="<SERVICE_ACCOUNT_JSON>",
            region="us",
            settings={"maxBillingBytes": "1000000000"},
        )
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"]["maxBillingBytes"] == "1000000000"
    assert kwargs["json_body"]["region"] == "us"


async def test_create_connection_error_400(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Missing required field for dialect"))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_create_connection(
        CreateConnectionInput(dialect="postgres", name="X", password_unencrypted="pw")
    )

    assert result.startswith("Error (400):")


async def test_create_connection_port_bounds_rejected() -> None:
    with pytest.raises(ValueError):
        CreateConnectionInput(dialect="postgres", name="X", password_unencrypted="pw", port=70000)


async def test_create_connection_query_timeout_bounds_rejected() -> None:
    with pytest.raises(ValueError):
        CreateConnectionInput(dialect="postgres", name="X", password_unencrypted="pw", query_timeout_seconds=3601)


async def test_create_connection_invalid_dialect_rejected() -> None:
    with pytest.raises(ValueError):
        CreateConnectionInput(dialect="not_a_real_dialect", name="X", password_unencrypted="pw")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# omni_update_connection
# --------------------------------------------------------------------------- #


async def test_update_connection_rotate_password(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True, "message": "Updated connection credentials."})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_update_connection(
        UpdateConnectionInput(connection_id="conn-1", password_unencrypted="new-password-here")
    )

    assert "conn-1" in result
    assert "passwordUnencrypted" in result
    assert fake.calls == [
        ("PATCH", "/v1/connections/conn-1", {"json_body": {"passwordUnencrypted": "new-password-here"}})
    ]


async def test_update_connection_clear_environment_user_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    await omni_update_connection(UpdateConnectionInput(connection_id="conn-1", clear_environment_user_attribute=True))

    assert fake.calls == [("PATCH", "/v1/connections/conn-1", {"json_body": {"environmentUserAttribute": None}})]


async def test_update_connection_sets_environment_user_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    await omni_update_connection(
        UpdateConnectionInput(
            connection_id="conn-1",
            environment_user_attribute=EnvironmentUserAttributeInput(
                attribute_name="environment", default_values=["prod"]
            ),
        )
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v1/connections/conn-1",
            {"json_body": {"environmentUserAttribute": {"attributeName": "environment", "defaultValues": ["prod"]}}},
        )
    ]


async def test_update_connection_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError):
        UpdateConnectionInput(connection_id="conn-1")


async def test_update_connection_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "User does not have Connection Admin permissions."))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_update_connection(UpdateConnectionInput(connection_id="conn-1", base_role="QUERIER"))

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------- #
# omni_delete_connection
# --------------------------------------------------------------------------- #


async def test_delete_connection_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_delete_connection(DeleteConnectionInput(connection_id="conn-1"))

    assert "Archived" in result
    assert "conn-1" in result
    assert fake.calls == [("DELETE", "/v1/connections/conn-1", {})]


async def test_delete_connection_already_archived_410(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(410, "The connection has already been archived."))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_delete_connection(DeleteConnectionInput(connection_id="conn-1"))

    assert result.startswith("Error (410):")


# --------------------------------------------------------------------------- #
# omni_create_connection_environments
# --------------------------------------------------------------------------- #


async def test_create_connection_environments_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "success": True,
            "data": [{"id": "env-1", "environmentConnectionId": "conn-2"}],
        }
    )
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_create_connection_environments(
        CreateConnectionEnvironmentsInput(base_connection_id="base-1", environment_connection_ids=["conn-2", "conn-3"])
    )

    assert "env-1" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/connection-environments",
            {"json_body": {"baseConnectionId": "base-1", "environmentConnectionIds": "conn-2,conn-3"}},
        )
    ]


async def test_create_connection_environments_rejects_blank_id() -> None:
    with pytest.raises(ValueError):
        CreateConnectionEnvironmentsInput(base_connection_id="base-1", environment_connection_ids=["  "])


async def test_create_connection_environments_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Connection with id base-1 does not exist"))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_create_connection_environments(
        CreateConnectionEnvironmentsInput(base_connection_id="base-1", environment_connection_ids=["conn-2"])
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_update_connection_environment
# --------------------------------------------------------------------------- #


async def test_update_connection_environment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_update_connection_environment(
        UpdateConnectionEnvironmentInput(connection_environment_id="env-1", user_attribute_values=["dev", "staging"])
    )

    assert "env-1" in result
    assert "dev" in result
    assert fake.calls == [
        (
            "PUT",
            "/v1/connection-environments/env-1",
            {"json_body": {"userAttributeValues": "dev,staging"}},
        )
    ]


async def test_update_connection_environment_rejects_blank_value() -> None:
    with pytest.raises(ValueError):
        UpdateConnectionEnvironmentInput(connection_environment_id="env-1", user_attribute_values=[""])


# --------------------------------------------------------------------------- #
# omni_delete_connection_environment
# --------------------------------------------------------------------------- #


async def test_delete_connection_environment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_delete_connection_environment(
        DeleteConnectionEnvironmentInput(connection_environment_id="env-1")
    )

    assert "env-1" in result
    assert fake.calls == [("DELETE", "/v1/connection-environments/env-1", {})]


async def test_delete_connection_environment_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Connection environment with id env-1 does not exist"))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_delete_connection_environment(
        DeleteConnectionEnvironmentInput(connection_environment_id="env-1")
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_list_schema_refresh_schedules
# --------------------------------------------------------------------------- #


async def test_list_schema_refresh_schedules_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"schedules": [SCHEDULE]})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_list_schema_refresh_schedules(
        ListSchemaRefreshSchedulesInput(connection_id=SCHEDULE["connectionId"])
    )

    assert SCHEDULE["scheduleId"] in result
    assert "America/New_York" in result
    assert fake.calls == [("GET", f"/v1/connections/{SCHEDULE['connectionId']}/schedules", {})]


async def test_list_schema_refresh_schedules_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"schedules": [SCHEDULE]})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_list_schema_refresh_schedules(
        ListSchemaRefreshSchedulesInput(connection_id=SCHEDULE["connectionId"], response_format=ResponseFormat.JSON)
    )
    payload = json.loads(result)

    assert payload["count"] == 1
    assert payload["schedules"][0]["scheduleId"] == SCHEDULE["scheduleId"]


async def test_list_schema_refresh_schedules_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Connection Admin permissions required."))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_list_schema_refresh_schedules(ListSchemaRefreshSchedulesInput(connection_id="conn-1"))

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------- #
# omni_get_schema_refresh_schedule
# --------------------------------------------------------------------------- #


async def test_get_schema_refresh_schedule_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCHEDULE)
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_get_schema_refresh_schedule(
        GetSchemaRefreshScheduleInput(connection_id=SCHEDULE["connectionId"], schedule_id=SCHEDULE["scheduleId"])
    )

    assert SCHEDULE["scheduleId"] in result
    assert fake.calls == [
        (
            "GET",
            f"/v1/connections/{SCHEDULE['connectionId']}/schedules/{SCHEDULE['scheduleId']}",
            {},
        )
    ]


async def test_get_schema_refresh_schedule_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCHEDULE)
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_get_schema_refresh_schedule(
        GetSchemaRefreshScheduleInput(
            connection_id=SCHEDULE["connectionId"],
            schedule_id=SCHEDULE["scheduleId"],
            response_format=ResponseFormat.JSON,
        )
    )
    payload = json.loads(result)

    assert payload["timezone"] == "America/New_York"


async def test_get_schema_refresh_schedule_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Schedule not found"))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_get_schema_refresh_schedule(
        GetSchemaRefreshScheduleInput(connection_id="conn-1", schedule_id="sched-1")
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_create_schema_refresh_schedule
# --------------------------------------------------------------------------- #


async def test_create_schema_refresh_schedule_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCHEDULE)
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_create_schema_refresh_schedule(
        CreateSchemaRefreshScheduleInput(
            connection_id=SCHEDULE["connectionId"], schedule="00 09 * * ? *", timezone="America/New_York"
        )
    )

    assert SCHEDULE["scheduleId"] in result
    assert fake.calls == [
        (
            "POST",
            f"/v1/connections/{SCHEDULE['connectionId']}/schedules",
            {"json_body": {"schedule": "00 09 * * ? *", "timezone": "America/New_York"}},
        )
    ]


async def test_create_schema_refresh_schedule_hard_refresh_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=SCHEDULE)
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    await omni_create_schema_refresh_schedule(
        CreateSchemaRefreshScheduleInput(
            connection_id="conn-1", schedule="00 09 * * ? *", timezone="UTC", hard_refresh=True
        )
    )

    _, _, kwargs = fake.calls[0]
    assert kwargs["json_body"]["hardRefresh"] is True


async def test_create_schema_refresh_schedule_error_400(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Invalid timezone"))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_create_schema_refresh_schedule(
        CreateSchemaRefreshScheduleInput(connection_id="conn-1", schedule="00 09 * * ? *", timezone="Not/AZone")
    )

    assert result.startswith("Error (400):")


def test_create_schema_refresh_schedule_rejects_5_field_cron() -> None:
    with pytest.raises(ValueError):
        CreateSchemaRefreshScheduleInput(connection_id="conn-1", schedule="00 09 * * ?", timezone="UTC")


def test_create_schema_refresh_schedule_rejects_7_field_cron() -> None:
    with pytest.raises(ValueError):
        CreateSchemaRefreshScheduleInput(connection_id="conn-1", schedule="00 09 * * ? * *", timezone="UTC")


def test_create_schema_refresh_schedule_accepts_6_field_cron() -> None:
    params = CreateSchemaRefreshScheduleInput(connection_id="conn-1", schedule="00 09 * * ? *", timezone="UTC")

    assert params.schedule == "00 09 * * ? *"


# --------------------------------------------------------------------------- #
# omni_update_schema_refresh_schedule
# --------------------------------------------------------------------------- #


async def test_update_schema_refresh_schedule_success(monkeypatch: pytest.MonkeyPatch) -> None:
    updated = dict(SCHEDULE, schedule="00 18 ? * * *", timezone="Europe/London", description="At 06:00 PM GMT")
    fake = _FakeClient(payload=updated)
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_update_schema_refresh_schedule(
        UpdateSchemaRefreshScheduleInput(
            connection_id=SCHEDULE["connectionId"],
            schedule_id=SCHEDULE["scheduleId"],
            schedule="00 18 ? * * *",
            timezone="Europe/London",
        )
    )

    assert SCHEDULE["scheduleId"] in result
    assert "GMT" in result
    assert fake.calls == [
        (
            "PUT",
            f"/v1/connections/{SCHEDULE['connectionId']}/schedules/{SCHEDULE['scheduleId']}",
            {"json_body": {"schedule": "00 18 ? * * *", "timezone": "Europe/London"}},
        )
    ]


def test_update_schema_refresh_schedule_rejects_invalid_cron() -> None:
    with pytest.raises(ValueError):
        UpdateSchemaRefreshScheduleInput(
            connection_id="conn-1", schedule_id="sched-1", schedule="not a cron", timezone="UTC"
        )


async def test_update_schema_refresh_schedule_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Schedule not found"))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_update_schema_refresh_schedule(
        UpdateSchemaRefreshScheduleInput(
            connection_id="conn-1", schedule_id="sched-1", schedule="00 09 * * ? *", timezone="UTC"
        )
    )

    assert result.startswith("Error (404):")


# --------------------------------------------------------------------------- #
# omni_delete_schema_refresh_schedule
# --------------------------------------------------------------------------- #


async def test_delete_schema_refresh_schedule_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"success": True})
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_delete_schema_refresh_schedule(
        DeleteSchemaRefreshScheduleInput(connection_id=SCHEDULE["connectionId"], schedule_id=SCHEDULE["scheduleId"])
    )

    assert SCHEDULE["scheduleId"] in result
    assert fake.calls == [
        (
            "DELETE",
            f"/v1/connections/{SCHEDULE['connectionId']}/schedules/{SCHEDULE['scheduleId']}",
            {},
        )
    ]


async def test_delete_schema_refresh_schedule_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Connection Admin permissions required."))
    monkeypatch.setattr("omni_mcp.tools.connections.get_client", lambda: fake)

    result = await omni_delete_schema_refresh_schedule(
        DeleteSchemaRefreshScheduleInput(connection_id="conn-1", schedule_id="sched-1")
    )

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------------- #
# Shared input hygiene
# --------------------------------------------------------------------------- #


def test_inputs_reject_unknown_fields() -> None:
    models_and_kwargs: list[tuple[type, dict[str, Any]]] = [
        (ListConnectionsInput, {}),
        (GetConnectionInput, {"connection_id": "c1"}),
        (CreateConnectionInput, {"dialect": "postgres", "name": "n", "password_unencrypted": "p"}),
        (UpdateConnectionInput, {"connection_id": "c1", "base_role": "QUERIER"}),
        (DeleteConnectionInput, {"connection_id": "c1"}),
        (CreateConnectionEnvironmentsInput, {"base_connection_id": "b1", "environment_connection_ids": ["c2"]}),
        (UpdateConnectionEnvironmentInput, {"connection_environment_id": "e1", "user_attribute_values": ["dev"]}),
        (DeleteConnectionEnvironmentInput, {"connection_environment_id": "e1"}),
        (ListSchemaRefreshSchedulesInput, {"connection_id": "c1"}),
        (GetSchemaRefreshScheduleInput, {"connection_id": "c1", "schedule_id": "s1"}),
        (
            CreateSchemaRefreshScheduleInput,
            {"connection_id": "c1", "schedule": "00 09 * * ? *", "timezone": "UTC"},
        ),
        (
            UpdateSchemaRefreshScheduleInput,
            {"connection_id": "c1", "schedule_id": "s1", "schedule": "00 09 * * ? *", "timezone": "UTC"},
        ),
        (DeleteSchemaRefreshScheduleInput, {"connection_id": "c1", "schedule_id": "s1"}),
    ]
    for model, kwargs in models_and_kwargs:
        with pytest.raises(ValueError):
            model(**kwargs, unexpected="x")
