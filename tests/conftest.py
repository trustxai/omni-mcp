"""Shared pytest configuration: marker gating + settings isolation."""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from omni_mcp.config import get_settings

# Load .env so live-marked tests can pick up real credentials; non-live tests
# are isolated from it by the autouse fixture below.
load_dotenv()


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
) -> None:
    """Keep the developer's real OMNI_* env / .env out of non-live tests.

    Strips ambient OMNI_* vars, chdirs away from the repo (so
    `Settings(env_file=".env")` finds nothing), and clears the settings cache
    before and after each non-live test. Live tests are left untouched.
    """
    if "live" in request.keywords:
        return
    for key in list(os.environ):
        if key.startswith("OMNI_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("isolated-cwd"))
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)
