"""Unit tests for settings loading and instance-URL normalisation."""

from __future__ import annotations

import pytest

from omni_mcp.config import Settings, get_settings


def test_defaults_are_safe_without_env() -> None:
    settings = Settings()
    assert settings.omni_base_url == ""
    assert settings.omni_api_key == ""
    assert settings.api_root == ""
    assert settings.has_credentials is False
    assert settings.omni_request_timeout_seconds == 60.0
    assert settings.omni_max_retries == 3
    assert settings.omni_max_result_chars == 900_000


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://acme.omniapp.co", "https://acme.omniapp.co/api"),
        ("https://acme.omniapp.co/", "https://acme.omniapp.co/api"),
        ("https://acme.omniapp.co/api", "https://acme.omniapp.co/api"),
        ("https://acme.omniapp.co/api/", "https://acme.omniapp.co/api"),
        ("  https://acme.omniapp.co  ", "https://acme.omniapp.co/api"),
        ("https://acme.playground.exploreomni.dev", "https://acme.playground.exploreomni.dev/api"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_api_root_normalisation(base_url: str, expected: str) -> None:
    assert Settings(omni_base_url=base_url).api_root == expected


def test_has_credentials_requires_both_url_and_key() -> None:
    assert Settings(omni_base_url="https://acme.omniapp.co").has_credentials is False
    assert Settings(omni_api_key="key").has_credentials is False
    assert Settings(omni_base_url="https://acme.omniapp.co", omni_api_key="key").has_credentials is True


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_BASE_URL", "https://env.omniapp.co")
    monkeypatch.setenv("OMNI_API_KEY", "env-key")
    monkeypatch.setenv("OMNI_MAX_RETRIES", "5")
    monkeypatch.setenv("OMNI_REQUEST_TIMEOUT_SECONDS", "12.5")

    settings = Settings()

    assert settings.api_root == "https://env.omniapp.co/api"
    assert settings.omni_api_key == "env-key"
    assert settings.omni_max_retries == 5
    assert settings.omni_request_timeout_seconds == 12.5


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_API_KEY", "first")
    first = get_settings()
    monkeypatch.setenv("OMNI_API_KEY", "second")

    assert get_settings() is first

    get_settings.cache_clear()
    assert get_settings().omni_api_key == "second"
