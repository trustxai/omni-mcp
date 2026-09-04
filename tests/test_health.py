"""Unit tests for the health/api-info tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.config import get_settings
from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools import available_tool_modules, registered_tool_modules
from omni_mcp.tools.health import (
    ApiInfoInput,
    HealthCheckInput,
    omni_get_api_info,
    omni_health_check,
)

WHOAMI: dict[str, Any] = {
    "keyScope": "organization",
    "orgRole": "ORG_ADMIN",
    "user": {"id": "user-1", "membershipId": "membership-1"},
    "rolesByModel": {
        "model-1": {
            "baseRole": "QUERIER",
            "connectionId": "conn-1",
            "roleName": "QUERIER",
            "permissions": ["QUERY_TOPICS", "QUERY_SQL"],
        }
    },
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


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_BASE_URL", "https://acme.omniapp.co")
    monkeypatch.setenv("OMNI_API_KEY", "secret-key")
    get_settings.cache_clear()


async def test_health_check_markdown(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=WHOAMI)
    monkeypatch.setattr("omni_mcp.tools.health.get_client", lambda: fake)

    result = await omni_health_check(HealthCheckInput())

    assert "**OK**" in result
    assert "https://acme.omniapp.co/api" in result
    assert "user-1" in result
    assert "membership-1" in result
    assert "organization" in result
    assert "ORG_ADMIN" in result
    assert "QUERY_TOPICS" in result
    assert "secret-key" not in result
    assert fake.calls == [("GET", "/v1/whoami", {"params": None})]


async def test_health_check_passes_model_filter(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=WHOAMI)
    monkeypatch.setattr("omni_mcp.tools.health.get_client", lambda: fake)

    await omni_health_check(HealthCheckInput(model_id="model-1,model-2"))

    assert fake.calls == [("GET", "/v1/whoami", {"params": {"modelId": "model-1,model-2"}})]


async def test_health_check_json(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    fake = _FakeClient(payload=WHOAMI)
    monkeypatch.setattr("omni_mcp.tools.health.get_client", lambda: fake)

    payload = json.loads(await omni_health_check(HealthCheckInput(response_format=ResponseFormat.JSON)))

    assert payload["status"] == "ok"
    assert payload["apiRoot"] == "https://acme.omniapp.co/api"
    assert payload["apiKeyConfigured"] is True
    assert payload["identity"]["user"]["id"] == "user-1"


async def test_health_check_reports_truncated_roles(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    payload = dict(WHOAMI, rolesByModelTruncated=True)
    monkeypatch.setattr("omni_mcp.tools.health.get_client", lambda: _FakeClient(payload=payload))

    result = await omni_health_check(HealthCheckInput())

    assert "truncated" in result


async def test_health_check_handles_missing_roles(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    monkeypatch.setattr("omni_mcp.tools.health.get_client", lambda: _FakeClient(payload={"user": {}}))

    result = await omni_health_check(HealthCheckInput())

    assert "_No model roles reported._" in result


async def test_health_check_error_path(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/whoami")
    response = httpx.Response(401, json={"detail": "Unauthorized: Missing or invalid API key"}, request=request)
    error = httpx.HTTPStatusError("Unauthorized", request=request, response=response)
    monkeypatch.setattr("omni_mcp.tools.health.get_client", lambda: _FakeClient(exc=error))

    result = await omni_health_check(HealthCheckInput())

    assert result.startswith("Error (401):")
    assert "OMNI_API_KEY" in result


async def test_health_check_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    error = RuntimeError("No Omni API key configured. Set OMNI_API_KEY in the environment or .env")
    monkeypatch.setattr("omni_mcp.tools.health.get_client", lambda: _FakeClient(exc=error))

    result = await omni_health_check(HealthCheckInput())

    assert result.startswith("Error: No Omni API key configured.")


async def test_api_info_reports_configuration(configured: None) -> None:
    result = await omni_get_api_info(ApiInfoInput())

    assert "https://acme.omniapp.co/api" in result
    assert "API key: configured" in result
    assert "secret-key" not in result
    assert "60 requests/minute" in result
    assert f"## Tool modules ({len(registered_tool_modules())})" in result
    for module in registered_tool_modules():
        assert f"`{module}`" in result


async def test_api_info_reports_the_registered_set_when_it_is_narrowed(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    """With a filter active the report must not claim the whole package.

    `omni_get_api_info` is what a reader calls to find out which tools exist
    here; naming the modules that were left out keeps it useful without making
    it lie.
    """
    monkeypatch.setattr("omni_mcp.tools.health.registered_tool_modules", lambda: ("health", "queries"))

    result = await omni_get_api_info(ApiInfoInput())

    assert f"## Tool modules (2 of {len(available_tool_modules())} registered)" in result
    assert "`health`, `queries`" in result
    assert "Filtered by `OMNI_TOOL_MODULES`" in result
    assert "Not registered: `ai`" in result
    assert "`models`" in result


async def test_api_info_without_configuration() -> None:
    result = await omni_get_api_info(ApiInfoInput())

    assert "not configured (set OMNI_BASE_URL)" in result
    assert "NOT configured (set OMNI_API_KEY)" in result


@pytest.fixture
def misconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed instance URL — `Settings()` itself raises for this one."""
    monkeypatch.setenv("OMNI_BASE_URL", "acme.omniapp.co")
    monkeypatch.setenv("OMNI_API_KEY", "secret-key")
    get_settings.cache_clear()


async def test_api_info_survives_an_invalid_base_url(misconfigured: None) -> None:
    result = await omni_get_api_info(ApiInfoInput())

    assert not result.startswith("Error")
    assert "Invalid configuration" in result
    # The rejected value is echoed back so the typo is visible…
    assert "`acme.omniapp.co`" in result
    assert "must start with http:// or https://" in result
    # …the failing variable is named rather than assumed…
    assert "Rejected setting(s): `OMNI_BASE_URL`" in result
    # …and the tool still answers the rest of the question.
    for module in registered_tool_modules():
        assert f"`{module}`" in result


async def test_api_info_never_echoes_the_key_when_the_config_is_invalid(misconfigured: None) -> None:
    result = await omni_get_api_info(ApiInfoInput())

    assert "secret-key" not in result
    assert "API key: configured" in result


@pytest.fixture
def misconfigured_key_outside_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed URL, with no `OMNI_API_KEY` in the environment at all.

    A key that only exists in `.env` cannot be recovered from the validation
    error (pydantic records inputs for the failing fields only).
    """
    monkeypatch.setenv("OMNI_BASE_URL", "acme.omniapp.co")
    monkeypatch.delenv("OMNI_API_KEY", raising=False)
    get_settings.cache_clear()


async def test_api_info_reports_an_unknown_key_state_rather_than_guessing(
    misconfigured_key_outside_the_environment: None,
) -> None:
    result = await omni_get_api_info(ApiInfoInput())

    assert "API key: unknown" in result
    # Never claimed as missing: the key may well be configured in `.env`.
    assert "NOT configured (set OMNI_API_KEY)" not in result


async def test_api_info_reports_a_rejected_key_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-`OMNI_BASE_URL` failure is reported as itself, not as a URL problem."""
    monkeypatch.setenv("OMNI_BASE_URL", "https://acme.omniapp.co")
    monkeypatch.setenv("OMNI_MAX_RETRIES", "not-a-number")
    get_settings.cache_clear()

    result = await omni_get_api_info(ApiInfoInput())

    assert "Rejected setting(s): `OMNI_MAX_RETRIES`" in result
    assert "`OMNI_BASE_URL` as given: `https://acme.omniapp.co`" in result


async def test_health_check_reports_an_invalid_base_url(misconfigured: None) -> None:
    result = await omni_health_check(HealthCheckInput())

    assert result.startswith("Error: invalid configuration – ")
    assert "must start with http:// or https://" in result
    assert "secret-key" not in result


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        HealthCheckInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ApiInfoInput(unexpected="x")  # type: ignore[call-arg]
