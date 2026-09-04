"""Async HTTP client for the Omni REST API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from omni_mcp.config import Settings, get_settings

#: Transient server-side statuses retried with exponential backoff.
RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})

#: Backoff schedule (seconds) for the 5xx retries, indexed by attempt.
BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0, 2.0)

#: A single 429 wait is never longer than this, however large `Retry-After` is.
MAX_RETRY_AFTER_SECONDS = 60.0

#: Total time one request may spend sleeping between retries. Once the next
#: wait would cross this budget the client stops retrying and surfaces the
#: response, so a tool call cannot hang for minutes behind a rate limit.
MAX_TOTAL_RETRY_WAIT_SECONDS = 90.0

SleepFn = Callable[[float], Awaitable[None]]


def _retry_after_seconds(response: httpx.Response, fallback: float) -> float:
    """Read `Retry-After` (delta-seconds or HTTP-date), clamped and non-negative."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return fallback
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return fallback
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(tz=UTC)).total_seconds()
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


class OmniClient:
    """Thin async wrapper around httpx for the Omni REST API.

    Auth is a static bearer token (Organization API key or Personal Access
    Token), so there is no token-exchange lifecycle. Every path passed to
    :meth:`request` is relative to the instance's API root, so tools pass
    `/v1/users`, `/v1/query/run`, `/scim/v2/Users`, … and never a full URL.

    Retries: `429` honours `Retry-After` (capped at 30 s per wait) and
    `502/503/504` back off 0.5 s → 1 s → 2 s. Other 4xx are never retried.
    After the last attempt the response is `raise_for_status()`-ed so tools
    route the failure through :func:`omni_mcp.errors.handle_api_error`.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        # Injectable transport so tests can use httpx.MockTransport, and an
        # injectable sleep so retry tests do not actually wait.
        self._transport = transport
        self._sleep = sleep

    @property
    def settings(self) -> Settings:
        """The settings this client was built with."""
        return self._settings

    def _url(self, path: str) -> str:
        return f"{self._settings.api_root}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Perform an authenticated request and return the response.

        Raises `RuntimeError` when the instance URL or API key is missing, and
        `httpx.HTTPStatusError` on a non-2xx response that survived the retries.
        """
        settings = self._settings
        if not settings.omni_base_url.strip():
            raise RuntimeError(
                "No Omni instance URL configured. Set OMNI_BASE_URL in the environment or .env "
                "(for example https://your-instance.omniapp.co)."
            )
        if not settings.omni_api_key.strip():
            raise RuntimeError(
                "No Omni API key configured. Set OMNI_API_KEY in the environment or .env "
                "(Omni → Settings → API keys, or a Personal Access Token)."
            )

        request_headers: dict[str, str] = {
            "accept": "application/json",
            "authorization": f"Bearer {settings.omni_api_key.strip()}",
        }
        if headers:
            request_headers.update(headers)

        effective_timeout = timeout if timeout is not None else settings.omni_request_timeout_seconds
        attempts = 1 + max(settings.omni_max_retries, 0)
        response: httpx.Response | None = None
        total_wait = 0.0

        # One AsyncClient per request: tool calls are short and infrequent
        # (60 requests/minute per key), so a pooled client buys nothing and a
        # per-request client keeps the transport injectable for tests.
        # `follow_redirects` matters — without it a 3xx would come back as an
        # empty "successful" response and decode to None.
        async with httpx.AsyncClient(
            timeout=effective_timeout,
            transport=self._transport,
            follow_redirects=True,
        ) as client:
            for attempt in range(attempts):
                response = await client.request(
                    method,
                    self._url(path),
                    params=params,
                    json=json_body,
                    data=data,
                    files=files,
                    headers=request_headers,
                )
                last_attempt = attempt == attempts - 1
                if last_attempt:
                    break
                backoff = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                if response.status_code == 429:
                    delay = _retry_after_seconds(response, backoff)
                elif response.status_code in RETRYABLE_STATUS_CODES:
                    delay = backoff
                else:
                    break
                if total_wait + delay > MAX_TOTAL_RETRY_WAIT_SECONDS:
                    # Out of retry budget: surface the response instead of
                    # blocking the tool call any longer.
                    break
                total_wait += delay
                await self._sleep(delay)

        assert response is not None  # attempts >= 1, so the loop always ran
        response.raise_for_status()
        return response

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        """Perform a request and decode the JSON body (`None` for empty bodies)."""
        response = await self.request(method, path, **kwargs)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text


_client: OmniClient | None = None


def get_client() -> OmniClient:
    """Lazy module-level singleton (tools monkeypatch this in unit tests)."""
    global _client
    if _client is None:
        _client = OmniClient()
    return _client


def reset_client() -> None:
    """Drop the cached client so the next `get_client()` re-reads settings."""
    global _client
    _client = None
