"""Unit tests for settings loading and instance-URL normalisation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize(
    ("bad_url", "expected_message"),
    [
        ("acme.omniapp.co", "http:// or https://"),
        ("//acme.omniapp.co", "http:// or https://"),
        ("ftp://acme.omniapp.co", "http:// or https://"),
        ("https://", "must include a host"),
        ("https://acme.omniapp.co/api/v1", "instance root"),
        ("https://acme.omniapp.co/api/v1/users", "instance root"),
        ("https://acme.omniapp.co/bi", "instance root"),
        ("https://acme.omniapp.co?token=x", "query string or fragment"),
    ],
)
def test_base_url_validator_rejects_bad_values(bad_url: str, expected_message: str) -> None:
    with pytest.raises(ValidationError, match=expected_message):
        Settings(omni_base_url=bad_url)


@pytest.mark.parametrize(
    "good_url",
    [
        "",
        "   ",
        "https://acme.omniapp.co",
        "https://acme.omniapp.co/",
        "https://acme.omniapp.co/api",
        "https://acme.omniapp.co/api/",
        "http://localhost:3000",
        "https://acme.playground.exploreomni.dev/api",
    ],
)
def test_base_url_validator_accepts_instance_roots(good_url: str) -> None:
    Settings(omni_base_url=good_url)


def test_base_url_validator_applies_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_BASE_URL", "acme.omniapp.co")

    with pytest.raises(ValidationError, match="http:// or https://"):
        Settings()


def test_tool_modules_defaults_to_the_whole_package() -> None:
    """Unset means "register everything" — the empty tuple, not an empty server."""
    assert Settings().omni_tool_modules == ""
    assert Settings().tool_modules == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ()),
        ("   ", ()),
        ("models", ("models",)),
        ("models,queries", ("models", "queries")),
        ("  models ,  queries  ", ("models", "queries")),
        ("models,,queries,", ("models", "queries")),
        ("\tmodels\n,queries", ("models", "queries")),
        # Order is the caller's; a repeat is not an error.
        ("queries,models", ("queries", "models")),
        ("models,models,queries", ("models", "queries")),
    ],
)
def test_tool_modules_parsing(raw: str, expected: tuple[str, ...]) -> None:
    assert Settings(omni_tool_modules=raw).tool_modules == expected


def test_tool_modules_rejects_an_unknown_name() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(omni_tool_modules="models,modles,queries")

    message = str(excinfo.value)
    assert "modles" in message
    assert "OMNI_TOOL_MODULES" in message
    # The valid names are listed, so the typo is fixable without reading the source.
    for module in ("ai", "health", "models", "queries", "user_groups"):
        assert module in message


def test_tool_modules_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_TOOL_MODULES", "queries, models")

    assert Settings().tool_modules == ("queries", "models")


def test_tool_modules_validator_applies_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_TOOL_MODULES", "not_a_module")

    with pytest.raises(ValidationError, match="not_a_module"):
        Settings()
