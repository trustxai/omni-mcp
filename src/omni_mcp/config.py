"""Settings for the Omni MCP server, loaded from env vars / .env."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration, derived from `OMNI_*` environment variables.

    Every field has a default so importing the package never fails; missing
    credentials surface as a descriptive error on the first API request.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Instance URL, e.g. `https://acme.omniapp.co` (or the playground host,
    # `https://acme.playground.exploreomni.dev`). A trailing `/api` is accepted
    # and normalised away — `api_root` always appends exactly one.
    omni_base_url: str = ""

    # Organization API key or Personal Access Token (PAT). Sent as
    # `Authorization: Bearer <key>`. Some endpoints reject PATs and require an
    # Organization API key; those answer 403.
    omni_api_key: str = ""

    omni_request_timeout_seconds: float = 60.0

    # Extra attempts after the first one, for 429 and 502/503/504 responses.
    omni_max_retries: int = 3

    # Tool results are truncated above this many UTF-8 *bytes*, keeping responses
    # under the MCP 1 MB tool-result ceiling. (The name says `chars` for
    # backwards compatibility; the budget has always been about payload size.)
    omni_max_result_chars: int = 900_000

    @field_validator("omni_base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        """Reject instance URLs that would silently build wrong request URLs.

        An empty value stays empty so importing the package never fails; the
        missing-credentials error surfaces on the first request instead.
        """
        raw = value.strip()
        if not raw:
            return ""
        parsed = urlsplit(raw)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                "OMNI_BASE_URL must start with http:// or https:// "
                "(for example https://your-instance.omniapp.co), got: " + raw
            )
        if not parsed.netloc:
            raise ValueError("OMNI_BASE_URL must include a host, for example https://your-instance.omniapp.co")
        if parsed.query or parsed.fragment:
            raise ValueError("OMNI_BASE_URL must not contain a query string or fragment, got: " + raw)
        path = parsed.path.rstrip("/")
        if path not in ("", "/api"):
            raise ValueError(
                "OMNI_BASE_URL must be the instance root, optionally ending in /api "
                "(tools supply the rest of the path, e.g. /v1/users), got: " + raw
            )
        return raw

    @property
    def api_root(self) -> str:
        """The API root — the instance URL with exactly one `/api` suffix.

        Returns an empty string when no instance URL is configured.
        """
        base = self.omni_base_url.strip().rstrip("/")
        if not base:
            return ""
        if base.endswith("/api"):
            return base
        return f"{base}/api"

    @property
    def has_credentials(self) -> bool:
        """True when both an instance URL and an API key are configured."""
        return bool(self.omni_base_url.strip() and self.omni_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton (tests call `get_settings.cache_clear()`)."""
    return Settings()
