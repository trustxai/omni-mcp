"""Unit tests for the error-to-string mapping every tool routes failures through."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from omni_mcp.config import Settings
from omni_mcp.errors import handle_api_error, validation_error_detail


def _settings_validation_error(base_url: str) -> ValidationError:
    """The real `ValidationError` `Settings()` raises for a malformed URL."""
    try:
        Settings(omni_base_url=base_url)
    except ValidationError as exc:
        return exc
    raise AssertionError(f"Settings accepted {base_url!r}")


def _status_error(
    status: int,
    *,
    json_body: Any | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/users")
    if json_body is not None:
        response = httpx.Response(status, json=json_body, headers=headers, request=request)
    else:
        response = httpx.Response(status, text=text or "", headers=headers, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


@pytest.mark.parametrize(
    ("status", "expected_fragment"),
    [
        (400, "Bad request"),
        (401, "Unauthorized"),
        (403, "Forbidden"),
        (404, "Not found"),
        (409, "Conflict"),
        (429, "Rate limited"),
    ],
)
def test_mapped_statuses_use_detail_body_shape(status: int, expected_fragment: str) -> None:
    message = handle_api_error(_status_error(status, json_body={"detail": "something went wrong", "status": status}))

    assert message.startswith(f"Error ({status}):")
    assert expected_fragment in message
    assert "something went wrong" in message


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 429])
def test_mapped_statuses_use_error_message_body_shape(status: int) -> None:
    message = handle_api_error(_status_error(status, json_body={"error": str(status), "message": "legacy detail"}))

    assert message.startswith(f"Error ({status}):")
    assert "legacy detail" in message


def test_401_mentions_how_to_get_a_key() -> None:
    message = handle_api_error(_status_error(401, json_body={"detail": "Unauthorized: Missing or invalid API key"}))

    assert "OMNI_API_KEY" in message
    assert "API keys" in message


def test_403_explains_pat_limitations() -> None:
    message = handle_api_error(_status_error(403, json_body={"detail": "Forbidden"}))

    assert "Organization API key" in message
    assert "Personal Access Token" in message


def test_429_mentions_the_rate_limit() -> None:
    message = handle_api_error(_status_error(429, json_body={"detail": "Too many requests"}))

    assert "60 requests/minute" in message


def test_408_lists_remaining_job_ids() -> None:
    error = _status_error(
        408,
        json_body={"detail": "Query timed out", "remaining_job_ids": ["job-1", "job-2"], "timed_out": True},
    )

    message = handle_api_error(error)

    assert message.startswith("Error (408):")
    assert "job-1, job-2" in message
    assert "wait-for-query-results" in message


def test_408_without_job_ids_still_explains() -> None:
    message = handle_api_error(_status_error(408, json_body={"detail": "Query timed out"}))

    assert "none reported" in message


def test_410_shows_successor_link_and_sunset() -> None:
    error = _status_error(
        410,
        json_body={"detail": "This endpoint has been removed"},
        headers={
            "Link": '</api/documents-v2/create-document>; rel="successor-version"',
            "Sunset": "2026-07-31",
            "Deprecation": "true",
        },
    )

    message = handle_api_error(error)

    assert message.startswith("Error (410):")
    assert "documents-v2/create-document" in message
    assert "2026-07-31" in message
    assert "Deprecation: true" in message


def test_410_without_headers_still_explains() -> None:
    message = handle_api_error(_status_error(410, json_body={"detail": "This endpoint has been removed"}))

    assert message.startswith("Error (410):")
    assert "replacement endpoint" in message
    assert "Deprecation" not in message


def test_unmapped_status_falls_back_to_detail() -> None:
    message = handle_api_error(_status_error(500, json_body={"detail": "internal error"}))

    assert message == "Error (500): internal error"


def test_non_json_body_is_truncated_text() -> None:
    message = handle_api_error(_status_error(502, text="<html>bad gateway</html>"))

    assert "bad gateway" in message


def test_empty_body_reports_missing_detail() -> None:
    message = handle_api_error(_status_error(500, text=""))

    assert "no error detail" in message


def test_timeout_error() -> None:
    message = handle_api_error(httpx.ReadTimeout("timed out"))

    assert "timed out" in message
    assert "OMNI_REQUEST_TIMEOUT_SECONDS" in message


def test_connect_error() -> None:
    message = handle_api_error(httpx.ConnectError("nope"))

    assert "could not connect" in message
    assert "OMNI_BASE_URL" in message


def test_other_transport_error() -> None:
    message = handle_api_error(httpx.TooManyRedirects("looping"))

    assert "HTTP transport failure" in message


def test_runtime_error_is_passed_through() -> None:
    message = handle_api_error(RuntimeError("No Omni API key configured."))

    assert message == "Error: No Omni API key configured."


def test_unexpected_error_fallback() -> None:
    message = handle_api_error(ValueError("boom"))

    assert message == "Error: unexpected failure – ValueError: boom"


def test_settings_validation_error_is_reported_as_invalid_configuration() -> None:
    message = handle_api_error(_settings_validation_error("acme.omniapp.co"))

    assert message.startswith("Error: invalid configuration – ")
    assert "OMNI_BASE_URL must start with http:// or https://" in message
    assert "acme.omniapp.co" in message
    # None of pydantic's own noise: no multi-line report, no prefix, no docs URL.
    assert "\n" not in message
    assert "Value error" not in message
    assert "errors.pydantic.dev" not in message
    assert "unexpected failure" not in message


def test_a_non_settings_validation_error_is_not_reported_as_configuration() -> None:
    """Only `Settings` failures are the environment's fault."""

    class Payload(BaseModel):
        name: str

    try:
        Payload(name=1)  # type: ignore[arg-type]
    except ValidationError as exc:
        message = handle_api_error(exc)
    else:  # pragma: no cover - pydantic always rejects this
        raise AssertionError("Payload accepted an int name")

    assert message.startswith("Error: invalid input – ")
    assert "configuration" not in message


def test_validation_error_detail_falls_back_when_pydantic_reports_no_message() -> None:
    exc = ValidationError.from_exception_data("Settings", [])

    assert validation_error_detail(exc) == "no detail reported"
