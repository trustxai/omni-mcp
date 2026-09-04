"""Health and configuration tools — the in-repo exemplar of the house tool style.

Lane workers: copy this module's shape (input model, decorator + annotations,
`params: <Input>` signature, docstring sections, try/except →
`handle_api_error`, `-> str` return). See CONTRIBUTING.md for the full contract.
"""

from __future__ import annotations

import pkgutil
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

import omni_mcp.tools
from omni_mcp.client import get_client
from omni_mcp.config import get_settings
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, to_json, truncate_result
from omni_mcp.server import mcp


def _discover_tool_modules() -> tuple[str, ...]:
    """Every module in `omni_mcp.tools` — the same set `register_all` imports.

    Discovered rather than hand-listed so this never drifts as modules land.
    """
    return tuple(
        sorted(name for _, name, _ in pkgutil.iter_modules(omni_mcp.tools.__path__) if not name.startswith("_"))
    )


#: Every tool module in this server, reported by `omni_get_api_info`.
TOOL_MODULES: tuple[str, ...] = _discover_tool_modules()


class HealthCheckInput(BaseModel):
    """Input for `omni_health_check`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str | None = Field(
        default=None,
        description="Optional model filter: one model ID or a comma-separated list. Scopes the reported model roles.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw identity payload.",
    )


class ApiInfoInput(BaseModel):
    """Input for `omni_get_api_info` (no parameters)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


def _summarize_roles(roles_by_model: dict[str, Any], limit: int = 10) -> list[str]:
    lines: list[str] = []
    for model_id, role in list(roles_by_model.items())[:limit]:
        if not isinstance(role, dict):
            continue
        role_name = role.get("roleName", "unknown")
        base_role = role.get("baseRole", "unknown")
        permissions = role.get("permissions") or []
        permission_text = ", ".join(str(item) for item in permissions) if permissions else "none reported"
        lines.append(f"- `{model_id}` — role **{role_name}** (base {base_role}); permissions: {permission_text}")
    remaining = len(roles_by_model) - len(lines)
    if remaining > 0:
        lines.append(f"- _… {remaining:,} more model(s) not shown; pass `model_id` to scope the result._")
    return lines


@mcp.tool(
    name="omni_health_check",
    annotations=ToolAnnotations(
        title="Omni Health Check",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_health_check(params: HealthCheckInput) -> str:
    """Verify connectivity and authentication against the Omni API.

    Calls `GET /v1/whoami` with the configured API key and reports who the key
    belongs to, its scope (Organization API key vs Personal Access Token), the
    organization role, and the resolved per-model permissions.

    When to Use:
    - As the first call after configuring the server, to confirm the key works.
    - To debug 401/403 responses from other tools.
    - To check whether the caller may run an action before attempting it.

    When NOT to Use:
    - To look up other people (use the user tools).
    - To list models in detail (use the model tools).

    Returns:
    A markdown summary — instance URL, whether a key is configured, the caller's
    user and membership IDs, key scope, org role and model roles — or the raw
    identity payload when `response_format` is `json`. On failure, an
    `Error ...` string.

    Examples:
    - Check the connection: `{"params": {}}`
    - Scope roles to one model: `{"params": {"model_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    401 means the API key is missing or invalid; 404 means one of the requested
    model IDs does not exist or is not visible to the caller; connection errors
    point at the network or an incorrect `OMNI_BASE_URL`.
    """
    try:
        settings = get_settings()
        client = get_client()
        query: dict[str, Any] = {}
        if params.model_id:
            query["modelId"] = params.model_id
        payload = await client.request_json("GET", "/v1/whoami", params=query or None)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(
                to_json(
                    {
                        "status": "ok",
                        "apiRoot": settings.api_root,
                        "apiKeyConfigured": bool(settings.omni_api_key.strip()),
                        "identity": body,
                    }
                )
            )

        raw_user = body.get("user")
        user: dict[str, Any] = raw_user if isinstance(raw_user, dict) else {}
        raw_roles = body.get("rolesByModel")
        roles: dict[str, Any] = raw_roles if isinstance(raw_roles, dict) else {}
        lines = [
            "# Omni Health Check",
            "",
            "**OK** — the API key authenticated successfully.",
            "",
            f"- API root: `{settings.api_root}`",
            "- API key: configured",
            f"- User ID: `{user.get('id', 'unknown')}`",
            f"- Membership ID: `{user.get('membershipId', 'unknown')}`",
            f"- Key scope: **{body.get('keyScope', 'unknown')}**",
            f"- Organization role: **{body.get('orgRole', 'unknown')}**",
            "",
            f"## Model roles ({len(roles):,})",
        ]
        if body.get("rolesByModelTruncated"):
            lines.append("_The API truncated this list; pass `model_id` to scope it._")
        lines.extend(_summarize_roles(roles) or ["_No model roles reported._"])
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_api_info",
    annotations=ToolAnnotations(
        title="Omni API Info",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def omni_get_api_info(params: ApiInfoInput) -> str:
    """Report how this server is configured, without calling the API.

    When to Use:
    - To confirm which instance the server points at and whether a key is set.
    - To find out which tool areas this server exposes.
    - When `omni_health_check` fails and you need to see the configuration.

    When NOT to Use:
    - To verify that the key actually works — that needs `omni_health_check`.

    Returns:
    A markdown summary of the instance URL, credential presence (never the key
    itself), timeout/retry settings, the rate limit, and the tool modules.

    Examples:
    - `{"params": {}}`

    Error Handling:
    Makes no network call, so it only fails if the environment cannot be read;
    any failure comes back as an `Error ...` string.
    """
    try:
        settings = get_settings()
        api_root = settings.api_root or "not configured (set OMNI_BASE_URL)"
        key_state = "configured" if settings.omni_api_key.strip() else "NOT configured (set OMNI_API_KEY)"
        lines = [
            "# Omni MCP configuration",
            "",
            f"- API root: `{api_root}`",
            f"- API key: {key_state}",
            f"- Request timeout: {settings.omni_request_timeout_seconds:g}s",
            f"- Retries per request: {settings.omni_max_retries}",
            f"- Max result characters: {settings.omni_max_result_chars:,}",
            "",
            "Rate limit: **60 requests/minute per API key**. On `429` the client honours `Retry-After` "
            "and retries automatically; prefer fewer, larger pages over many small calls.",
            "",
            f"## Tool modules ({len(TOOL_MODULES)})",
            "",
            ", ".join(f"`{module}`" for module in TOOL_MODULES),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)
