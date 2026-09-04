"""Settings for the Omni MCP server, loaded from env vars / .env."""

from __future__ import annotations

from functools import lru_cache

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

    # Tool results are truncated below this many characters, keeping responses
    # under the MCP 1 MB tool-result ceiling.
    omni_max_result_chars: int = 900_000

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
