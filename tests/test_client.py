"""Unit tests for the HTTP client: auth, URL joining, retries, timeouts."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from omni_mcp.client import MAX_RETRY_AFTER_SECONDS, MAX_TOTAL_RETRY_WAIT_SECONDS, OmniClient
from omni_mcp.config import Settings


class _Sleeper:
    """Records the delays the client would have slept for."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "omni_base_url": "https://acme.omniapp.co",
        "omni_api_key": "secret-key",
        "omni_max_retries": 3,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    settings: Settings | None = None,
    sleeper: _Sleeper | None = None,
) -> OmniClient:
    return OmniClient(
        settings=settings or _settings(),
        transport=httpx.MockTransport(handler),
        sleep=sleeper or _Sleeper(),
    )


async def test_sends_bearer_auth_and_accept_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    response = await _client(handler).request("GET", "/v1/whoami")

    assert response.status_code == 200
    assert seen[0].headers["authorization"] == "Bearer secret-key"
    assert seen[0].headers["accept"] == "application/json"


async def test_extra_headers_are_merged() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await _client(handler).request("POST", "/v1/query/run", headers={"x-trace": "abc"})

    assert seen[0].headers["x-trace"] == "abc"
    assert seen[0].headers["authorization"] == "Bearer secret-key"


@pytest.mark.parametrize(
    ("base_url", "path", "expected"),
    [
        ("https://acme.omniapp.co", "/v1/users", "https://acme.omniapp.co/api/v1/users"),
        ("https://acme.omniapp.co/api", "/v1/users", "https://acme.omniapp.co/api/v1/users"),
        ("https://acme.omniapp.co/api/", "v1/users", "https://acme.omniapp.co/api/v1/users"),
        ("https://acme.omniapp.co", "/scim/v2/Users", "https://acme.omniapp.co/api/scim/v2/Users"),
        ("https://acme.omniapp.co/api", "/scim/v2/Groups", "https://acme.omniapp.co/api/scim/v2/Groups"),
        ("https://acme.omniapp.co", "/v1/query/run", "https://acme.omniapp.co/api/v1/query/run"),
        ("https://acme.omniapp.co", "/unstable/ai/generate", "https://acme.omniapp.co/api/unstable/ai/generate"),
        ("https://acme.omniapp.co/api", "/unstable/ai/chat", "https://acme.omniapp.co/api/unstable/ai/chat"),
    ],
)
async def test_base_url_joining(base_url: str, path: str, expected: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await _client(handler, settings=_settings(omni_base_url=base_url)).request("GET", path)

    assert str(seen[0].url) == expected


async def test_query_params_are_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await _client(handler).request("GET", "/v1/users", params={"pageSize": 50, "cursor": "abc"})

    assert seen[0].url.params["pageSize"] == "50"
    assert seen[0].url.params["cursor"] == "abc"


async def test_runtime_error_without_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("no request should be made without a key")

    client = _client(handler, settings=_settings(omni_api_key=""))

    with pytest.raises(RuntimeError, match="OMNI_API_KEY"):
        await client.request("GET", "/v1/whoami")


async def test_runtime_error_without_base_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("no request should be made without an instance URL")

    client = _client(handler, settings=_settings(omni_base_url=""))

    with pytest.raises(RuntimeError, match="OMNI_BASE_URL"):
        await client.request("GET", "/v1/whoami")


async def test_retries_429_honouring_retry_after_then_succeeds() -> None:
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json={"detail": "slow down", "status": 429})
        return httpx.Response(200, json={"ok": True})

    response = await _client(handler, sleeper=sleeper).request("GET", "/v1/users")

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeper.delays == [2.0]


async def test_retry_after_is_capped() -> None:
    sleeper = _Sleeper()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3600"}, json={})
        return httpx.Response(200, json={})

    await _client(handler, sleeper=sleeper).request("GET", "/v1/users")

    assert sleeper.delays == [MAX_RETRY_AFTER_SECONDS]
    assert MAX_RETRY_AFTER_SECONDS == 60.0


async def test_retry_after_http_date_is_parsed() -> None:
    sleeper = _Sleeper()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}, json={})
        return httpx.Response(200, json={})

    await _client(handler, sleeper=sleeper).request("GET", "/v1/users")

    # A date in the past clamps to zero rather than going negative.
    assert sleeper.delays == [0.0]


async def test_retries_5xx_with_backoff_then_fails() -> None:
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={"error": "503", "message": "upstream unavailable"})

    client = _client(handler, settings=_settings(omni_max_retries=2), sleeper=sleeper)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await client.request("GET", "/v1/models")

    assert excinfo.value.response.status_code == 503
    assert len(calls) == 3
    assert sleeper.delays == [0.5, 1.0]


async def test_backoff_schedule_is_capped_at_two_seconds() -> None:
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={})

    client = _client(handler, settings=_settings(omni_max_retries=5), sleeper=sleeper)

    with pytest.raises(httpx.HTTPStatusError):
        await client.request("GET", "/v1/models")

    assert sleeper.delays == [0.5, 1.0, 2.0, 2.0, 2.0]


async def test_no_retry_on_other_4xx() -> None:
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, json={"detail": "Model not found", "status": 404})

    client = _client(handler, sleeper=sleeper)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await client.request("GET", "/v1/models/missing")

    assert excinfo.value.response.status_code == 404
    assert len(calls) == 1
    assert sleeper.delays == []


async def test_no_retries_when_disabled() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={})

    client = _client(handler, settings=_settings(omni_max_retries=0))

    with pytest.raises(httpx.HTTPStatusError):
        await client.request("GET", "/v1/models")

    assert len(calls) == 1


async def test_timeout_override_is_applied() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client = _client(handler, settings=_settings(omni_request_timeout_seconds=60.0))

    await client.request("GET", "/v1/users")
    await client.request("GET", "/v1/users", timeout=5.0)

    assert seen[0].extensions["timeout"]["read"] == 60.0
    assert seen[1].extensions["timeout"]["read"] == 5.0


async def test_request_json_decodes_and_handles_empty_bodies() -> None:
    def json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"records": [1, 2]})

    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    assert await _client(json_handler).request_json("GET", "/v1/users") == {"records": [1, 2]}
    assert await _client(empty_handler).request_json("DELETE", "/v1/users/1") is None


async def test_no_retry_on_500() -> None:
    """Only 502/503/504 are retried — a 500 is a real failure, not a blip."""
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"detail": "internal error", "status": 500})

    client = _client(handler, sleeper=sleeper)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await client.request("GET", "/v1/models")

    assert excinfo.value.response.status_code == 500
    assert len(calls) == 1
    assert sleeper.delays == []


async def test_total_retry_wait_budget_stops_retrying() -> None:
    """A long Retry-After twice over would exceed the budget, so we stop."""
    calls: list[int] = []
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "60"}, json={"detail": "slow down", "status": 429})

    client = _client(handler, settings=_settings(omni_max_retries=3), sleeper=sleeper)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await client.request("GET", "/v1/users")

    assert excinfo.value.response.status_code == 429
    # First wait of 60s fits the 90s budget; a second would not, so only two
    # requests are made instead of the configured four.
    assert sleeper.delays == [60.0]
    assert sum(sleeper.delays) <= MAX_TOTAL_RETRY_WAIT_SECONDS
    assert len(calls) == 2


async def test_retry_budget_allows_several_short_waits() -> None:
    sleeper = _Sleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    client = _client(handler, settings=_settings(omni_max_retries=4), sleeper=sleeper)

    with pytest.raises(httpx.HTTPStatusError):
        await client.request("GET", "/v1/models")

    assert sleeper.delays == [0.5, 1.0, 2.0, 2.0]
    assert sum(sleeper.delays) <= MAX_TOTAL_RETRY_WAIT_SECONDS


async def test_redirects_are_followed() -> None:
    """Without follow_redirects a 3xx decodes to an empty, silently wrong result."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/api/v1/documents":
            return httpx.Response(302, headers={"location": "/api/v1/documents/final"})
        return httpx.Response(200, json={"id": "doc-1"})

    payload = await _client(handler).request_json("GET", "/v1/documents")

    assert payload == {"id": "doc-1"}
    assert seen == [
        "https://acme.omniapp.co/api/v1/documents",
        "https://acme.omniapp.co/api/v1/documents/final",
    ]
