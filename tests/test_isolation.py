"""Proof that test isolation actually holds between tests.

The two ordered tests below are a pair: the first builds a client from real
credentials, the second asserts the next test cannot see it. Keep them in this
order and in this file.
"""

from __future__ import annotations

import pytest

from omni_mcp.client import get_client, reset_client
from omni_mcp.config import get_settings

LEAKED_KEY = "super-secret-key-from-the-previous-test"


def test_a_client_singleton_built_with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_BASE_URL", "https://leaky.omniapp.co")
    monkeypatch.setenv("OMNI_API_KEY", LEAKED_KEY)
    get_settings.cache_clear()
    reset_client()

    built = get_client()

    assert built.settings.omni_api_key == LEAKED_KEY
    assert built.settings.api_root == "https://leaky.omniapp.co/api"


def test_b_next_test_sees_a_fresh_blocked_client() -> None:
    fresh = get_client()

    # Values seeded by the autouse fixture in conftest.py.
    assert fresh.settings.omni_api_key == "blocked-test-key"
    assert fresh.settings.omni_base_url == "https://blocked.invalid"
    assert fresh.settings.omni_api_key != LEAKED_KEY


def test_settings_singleton_is_also_reset() -> None:
    assert get_settings().omni_api_key == ""
    assert get_settings().omni_base_url == ""


async def test_default_client_refuses_network_calls() -> None:
    with pytest.raises(RuntimeError, match="Unpatched network call"):
        await get_client().request("GET", "/v1/whoami")
