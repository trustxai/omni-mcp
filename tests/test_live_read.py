"""Live read-only smoke test against a real instance.

Skipped unless `OMNI_API_KEY` (and `OMNI_BASE_URL`) are present in the
environment or `.env`; run with `uv run pytest -m live`.
"""

from __future__ import annotations

import pytest

from omni_mcp.tools.health import ApiInfoInput, HealthCheckInput, omni_get_api_info, omni_health_check

pytestmark = pytest.mark.live


async def test_live_health_check_authenticates() -> None:
    result = await omni_health_check(HealthCheckInput())

    assert not result.startswith("Error"), result
    assert "**OK**" in result
    assert "Key scope:" in result


async def test_live_api_info_matches_configuration() -> None:
    result = await omni_get_api_info(ApiInfoInput())

    assert "API key: configured" in result
    assert "/api" in result
