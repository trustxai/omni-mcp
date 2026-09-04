"""Shared pytest configuration: marker gating + settings/client isolation."""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest
from dotenv import load_dotenv

from omni_mcp import client as client_module
from omni_mcp.client import OmniClient, reset_client
from omni_mcp.config import Settings, get_settings

# Load .env so live-marked tests can pick up real credentials; non-live tests
# are isolated from it by the autouse fixture below.
load_dotenv()

#: Placeholder credentials handed to the default client during non-live tests.
BLOCKED_BASE_URL = "https://blocked.invalid"
BLOCKED_API_KEY = "blocked-test-key"


def _refuse(request: httpx.Request) -> httpx.Response:
    raise RuntimeError(
        f"Unpatched network call to {request.url}. Unit tests must monkeypatch `get_client` "
        "in the module under test with a fake client."
    )


#: Any request that escapes a test's fakes hits this and fails loudly.
BLOCKED_TRANSPORT = httpx.MockTransport(_refuse)


def _blocked_client() -> OmniClient:
    return OmniClient(
        settings=Settings(omni_base_url=BLOCKED_BASE_URL, omni_api_key=BLOCKED_API_KEY),
        transport=BLOCKED_TRANSPORT,
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    has_creds = bool(os.environ.get("OMNI_API_KEY"))
    allow_destructive = os.environ.get("OMNI_TEST_ALLOW_DESTRUCTIVE") == "1"
    skip_live = pytest.mark.skip(reason="live tests require OMNI_API_KEY in the environment / .env")
    skip_destructive = pytest.mark.skip(reason="destructive tests require OMNI_TEST_ALLOW_DESTRUCTIVE=1")
    for item in items:
        if "live" in item.keywords and not has_creds:
            item.add_marker(skip_live)
        if "destructive" in item.keywords and not allow_destructive:
            item.add_marker(skip_destructive)


@pytest.fixture(autouse=True)
def _isolate_settings_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Keep the developer's real OMNI_* env / .env out of non-live tests.

    Both caches are reset around **every** test — the settings cache and the
    client singleton. `OmniClient` captures its settings at construction, so a
    client built by a live test (or by a test that sets credentials) would
    otherwise survive into the next test and talk to the real instance with the
    real key.

    For non-live tests the fixture additionally strips ambient `OMNI_*` vars,
    chdirs away from the repo (so `Settings(env_file=".env")` finds nothing),
    and seeds the singleton with a client whose transport refuses every
    request, so a tool that forgets to monkeypatch `get_client` fails loudly
    instead of reaching the network.
    """
    get_settings.cache_clear()
    reset_client()
    if "live" in request.keywords:
        try:
            yield
        finally:
            get_settings.cache_clear()
            reset_client()
        return

    for key in list(os.environ):
        if key.startswith("OMNI_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("isolated-cwd"))
    get_settings.cache_clear()
    client_module._client = _blocked_client()
    try:
        yield
    finally:
        get_settings.cache_clear()
        reset_client()
