"""Tools for the dbt environment, configuration, and exposure endpoints.

Covers all 9 `dbt` operations in the Omni API: reading and updating the git-backed
dbt configuration on a connection, creating/listing/updating/deleting dbt
environments on that connection, reading computed dbt exposures for a model, and
setting which dbt environment a model branch should use (the CI/CD entry point
that lets a pipeline point a branch at a specific environment before triggering a
schema refresh).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, cursor_paginated_response, to_json, truncate_result
from omni_mcp.server import mcp

#: One line on what this module covers, for the generated README catalogue.
MODULE_SUMMARY = "dbt configuration, environments, exposures"

_DbtAuthMethod = Literal["ssh", "https_token", "github_app"]
_DbtVersion = Literal["Auto", "1.10", "1.11"]

#: `projectRootPath` must not start with `/`, must not contain `..`, and may only
#: contain word characters, spaces, `.`, `/`, and `-`. An empty string is also a
#: valid, distinct value (explicitly resets to the repository root).
_PROJECT_ROOT_PATH_PATTERN = re.compile(r"^(?!/)(?!.*\.\.)[\w ./-]+$")


def _quote(value: str) -> str:
    """Percent-encode one path segment, including `/` (branch names may contain it)."""
    return quote(value, safe="")


def _require_new_variable(var: Any, index: int) -> None:
    """Validate one *new* environment-variable entry (create, or update without `id`)."""
    if not isinstance(var, dict):
        raise ValueError(f"variables[{index}] must be an object")
    name = var.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"variables[{index}].name is required and must be a non-empty string")
    if not isinstance(var.get("value"), str):
        raise ValueError(f"variables[{index}].value is required and must be a string")
    if not isinstance(var.get("isSecret"), bool):
        raise ValueError(f"variables[{index}].isSecret is required and must be a boolean")


def _require_update_variable(var: Any, index: int) -> None:
    """Validate one environment-variable entry in an update: existing (`id`) or new (`name`)."""
    if not isinstance(var, dict):
        raise ValueError(f"variables[{index}] must be an object")
    if not isinstance(var.get("isSecret"), bool):
        raise ValueError(f"variables[{index}].isSecret is required and must be a boolean")
    has_id = isinstance(var.get("id"), str) and bool(var.get("id"))
    has_name = isinstance(var.get("name"), str) and bool(var.get("name"))
    if not has_id and not has_name:
        raise ValueError(f"variables[{index}] must include an existing `id` or a `name` for a new variable")


def _mask_variable(var: Mapping[str, Any]) -> dict[str, Any]:
    """Never echo a secret variable's value, even if the API ever included one."""
    is_secret = bool(var.get("isSecret"))
    return {
        "id": var.get("id"),
        "name": var.get("name"),
        "value": "***" if is_secret else var.get("value"),
        "isSecret": is_secret,
    }


def _mask_environment(env: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a `DbtEnvironment` record with secret variable values masked."""
    masked = dict(env)
    variables = masked.get("variables")
    if isinstance(variables, list):
        masked["variables"] = [_mask_variable(item) if isinstance(item, dict) else item for item in variables]
    return masked


def _environment_kind(env: Mapping[str, Any]) -> str:
    owner_id = env.get("ownerId")
    return "shared" if owner_id is None else f"personal (owner `{owner_id}`)"


def _format_variables_inline(variables: Any) -> str:
    """Render `name`=`value` pairs for markdown. Callers must pass already-masked variables."""
    if not isinstance(variables, list) or not variables:
        return "none"
    parts: list[str] = []
    for var in variables:
        if not isinstance(var, dict):
            continue
        name = var.get("name", "unnamed")
        value = var.get("value")
        parts.append(f"`{name}`=`{value}`" if value is not None else f"`{name}`=`(unset)`")
    return ", ".join(parts) if parts else "none"


def _format_environment_item(env: Mapping[str, Any]) -> str:
    """Render one environment. `env` must already be masked (see `_mask_environment`)."""
    tags = []
    if env.get("isDefaultEnvironment"):
        tags.append("default")
    if env.get("isDeferralEnabled"):
        tags.append("deferral enabled")
    tag_text = f" — {', '.join(tags)}" if tags else ""
    variables_text = _format_variables_inline(env.get("variables"))
    return (
        f"- **{env.get('name', 'unnamed')}** (`{env.get('id')}`) — target schema `{env.get('targetSchema')}`, "
        f"{_environment_kind(env)}{tag_text} — variables: {variables_text}"
    )


def _format_dbt_configuration_markdown(connection_id: str, body: Mapping[str, Any]) -> str:
    lines = [f"# dbt Configuration — Connection `{connection_id}`", ""]
    lines.append(f"- Connection dialect supports dbt: **{bool(body.get('supportsDbt'))}**")
    message = body.get("message")
    if message:
        lines.append(f"- Status: {message}")
        return "\n".join(lines)
    lines.extend(
        [
            f"- Auth method: `{body.get('authMethod', 'unknown')}`",
            f"- Git branch: `{body.get('branch', 'unknown')}`",
            f"- Repository URL: `{body.get('sshUrl', 'unknown')}`",
            f"- Project root path: `{body.get('projectRootPath') or '(repository root)'}`",
            f"- dbt version: `{body.get('dbtVersion', 'unknown')}`",
            f"- Autogenerate relationships: **{bool(body.get('autogenRelationships'))}**",
            f"- Semantic layer enabled: **{bool(body.get('enableSemanticLayer'))}**",
            f"- Virtual schemas enabled: **{bool(body.get('enableVirtualSchemas'))}**",
        ]
    )
    if body.get("githubAppInstallationId"):
        lines.append(f"- GitHub App installation ID: `{body.get('githubAppInstallationId')}`")
    if body.get("committerName") or body.get("committerEmail"):
        lines.append(
            f"- Commit signer: {body.get('committerName', 'unknown')} <{body.get('committerEmail', 'unknown')}>"
        )
    if body.get("commitSigningPublicKey"):
        lines.append("- Commit signing public key: configured")
    return "\n".join(lines)


def _format_exposure_item(record: Mapping[str, Any]) -> str:
    dashboard_id = record.get("dashboard_identifier", "unknown")
    dedup_name = record.get("deduplication_name", "unknown")
    exposure = record.get("exposure")
    if not isinstance(exposure, dict):
        return f"- Dashboard `{dashboard_id}` (dedup `{dedup_name}`) — no dbt models referenced (exposure is null)."
    name = exposure.get("name", "unnamed")
    label = exposure.get("label", name)
    url = exposure.get("url", "")
    depends_on = exposure.get("depends_on") or []
    owner = exposure.get("owner") or {}
    owner_text = f"{owner.get('name', 'unknown')} <{owner.get('email', 'unknown')}>"
    depends_text = ", ".join(str(item) for item in depends_on) if depends_on else "none"
    return (
        f"- **{label}** (`{name}`) — dashboard `{dashboard_id}` (dedup `{dedup_name}`), owner {owner_text}, "
        f"depends on: {depends_text} ({url})"
    )


# --------------------------------------------------------------------------- #
# Get dbt configuration
# --------------------------------------------------------------------------- #


class GetDbtConfigurationInput(BaseModel):
    """Input for `omni_get_dbt_configuration`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="ID (UUID) of the connection where dbt is configured.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw configuration payload.",
    )


@mcp.tool(
    name="omni_get_dbt_configuration",
    annotations=ToolAnnotations(
        title="Get dbt Configuration",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_dbt_configuration(params: GetDbtConfigurationInput) -> str:
    """Retrieve the dbt configuration for a connection.

    Reports whether the connection's dialect supports dbt, and — when dbt is
    configured — the git auth method, branch, repository URL, project root path,
    dbt version, and the autogen-relationships / semantic-layer / virtual-schemas
    toggles. The write-only `token` field (HTTPS auth) is never returned by the
    API and never echoed here.

    When to Use:
    - Before calling `omni_update_dbt_configuration`, to see the current state.
    - To check whether dbt is linked to a connection and which branch it tracks.
    - To debug why dbt environments or exposures are unavailable for a connection.

    When NOT to Use:
    - To list the dbt environments themselves — use `omni_list_dbt_environments`.
    - To read exposures for a model — use `omni_get_dbt_exposures`.

    Returns:
    A markdown summary of the configuration, or the raw JSON payload when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", "response_format": "json"}}`

    Error Handling:
    401 means the API key is missing or invalid; 403 means the caller lacks
    Connection Admin permissions on this connection; 404 means the connection
    does not exist (or is not visible to the caller).
    """
    try:
        path = f"/v1/connections/{_quote(params.connection_id)}/dbt"
        payload = await get_client().request_json("GET", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(body))
        return truncate_result(_format_dbt_configuration_markdown(params.connection_id, body))
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Update dbt configuration
# --------------------------------------------------------------------------- #


class UpdateDbtConfigurationInput(BaseModel):
    """Input for `omni_update_dbt_configuration`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="ID (UUID) of the connection where dbt is configured.")
    branch: str = Field(..., min_length=1, description="Git branch name.")
    autogen_relationships: bool = Field(..., description="Whether relationships should be auto-generated from dbt.")
    enable_virtual_schemas: bool = Field(..., description="Whether virtual schemas from dbt should be enabled.")
    ssh_url: str = Field(
        ...,
        min_length=1,
        description=(
            "Git repository URL. Its expected form depends on the effective `auth_method`: `ssh` — a string "
            "starting with `git@...`; `https_token` — a string starting with `https://...`; `github_app` — a "
            "string starting with `https://github.com/...`."
        ),
    )
    auth_method: _DbtAuthMethod | None = Field(
        default=None,
        description=(
            "Git auth method: `ssh` (deploy key), `https_token` (deploy token/PAT), or `github_app` (GitHub App "
            "installation, github.com only). Omit to keep the existing auth method."
        ),
    )
    committer_email: str | None = Field(
        default=None,
        description=(
            "Required when `auth_method` is `github_app` and commit signing is enabled; must match the email the "
            "signing key is registered against in GitHub. Blank/omitted preserves the existing value."
        ),
    )
    committer_name: str | None = Field(
        default=None,
        description=(
            "Required when `auth_method` is `github_app` and commit signing is enabled. "
            "Blank/omitted preserves the existing value."
        ),
    )
    dbt_version: _DbtVersion | None = Field(default=None, description="dbt version to use: `Auto`, `1.10`, or `1.11`.")
    enable_semantic_layer: bool | None = Field(
        default=None, description="Enable the dbt semantic layer integration. Server default is `false`."
    )
    github_app_installation_id: str | None = Field(
        default=None,
        pattern=r"^[0-9]+$",
        description="Required when `auth_method` is `github_app`. Numeric GitHub App installation ID.",
    )
    project_root_path: str | None = Field(
        default=None,
        description=(
            "Path to the dbt project root within the repository. Must not start with `/` or contain `..`; may "
            "contain word characters, spaces, `.`, `/`, and `-`. Pass an empty string to explicitly reset to the "
            "repository root; omit to leave unchanged."
        ),
    )
    rotate_keys: bool | None = Field(
        default=None, description="Rotate SSH deploy keys. Only applicable when `auth_method` is `ssh`."
    )
    token: str | None = Field(
        default=None,
        max_length=1000,
        pattern=r"^[a-zA-Z0-9_\-.]+$",
        description=(
            "Required when `auth_method` is `https_token`. HTTPS token for authentication (deploy token, PAT, "
            "etc.). Write-only — never returned in GET responses, and never echoed by this tool."
        ),
    )

    @field_validator("project_root_path")
    @classmethod
    def _validate_project_root_path(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if not _PROJECT_ROOT_PATH_PATTERN.match(value):
            raise ValueError(
                "project_root_path must not start with '/', must not contain '..', and may contain only "
                "word characters, spaces, '.', '/', and '-'."
            )
        return value


def _build_update_configuration_body(params: UpdateDbtConfigurationInput) -> dict[str, Any]:
    body: dict[str, Any] = {
        "autogenRelationships": params.autogen_relationships,
        "branch": params.branch,
        "enableVirtualSchemas": params.enable_virtual_schemas,
        "sshUrl": params.ssh_url,
    }
    if params.auth_method is not None:
        body["authMethod"] = params.auth_method
    if params.committer_email is not None:
        body["committerEmail"] = params.committer_email
    if params.committer_name is not None:
        body["committerName"] = params.committer_name
    if params.dbt_version is not None:
        body["dbtVersion"] = params.dbt_version
    if params.enable_semantic_layer is not None:
        body["enableSemanticLayer"] = params.enable_semantic_layer
    if params.github_app_installation_id is not None:
        body["githubAppInstallationId"] = params.github_app_installation_id
    if params.project_root_path is not None:
        body["projectRootPath"] = params.project_root_path
    if params.rotate_keys is not None:
        body["rotateKeys"] = params.rotate_keys
    if params.token is not None:
        body["token"] = params.token
    return body


@mcp.tool(
    name="omni_update_dbt_configuration",
    annotations=ToolAnnotations(
        title="Update dbt Configuration",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_dbt_configuration(params: UpdateDbtConfigurationInput) -> str:
    """Update the dbt configuration for a connection.

    `branch`, `autogen_relationships`, `enable_virtual_schemas`, and `ssh_url` are
    always required by the API; every other field is optional and, when omitted,
    the existing value is retained. Field/method compatibility (e.g. `token` with
    `https_token`, `github_app_installation_id` with `github_app`) is validated
    against the *effective* auth method — the request's `auth_method`, or the
    connected repo's if omitted.

    When to Use:
    - To link or relink a connection's dbt project (branch, repo URL, auth).
    - To switch auth method (ssh / https_token / github_app) or rotate SSH keys.
    - To toggle autogen relationships, the semantic layer, or virtual schemas.

    When NOT to Use:
    - To unlink dbt entirely — use `omni_delete_dbt_configuration`.
    - To manage dbt environments — use the environment tools.

    Returns:
    A short confirmation of the updated branch, auth method, and dbt version. On
    failure, an `Error ...` string. Secrets (`token`) are never echoed back.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", "branch": "main", \
"autogen_relationships": true, "enable_virtual_schemas": false, "ssh_url": "git@github.com:org/repo.git"}}`
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", "branch": "main", \
"autogen_relationships": true, "enable_virtual_schemas": false, "ssh_url": "https://github.com/org/repo.git", \
"auth_method": "github_app", "github_app_installation_id": "12345678"}}`

    Error Handling:
    400 means an invalid body (check enum values, patterns, and required-when
    conditions for the chosen `auth_method`); 401 means missing/invalid
    authentication; 403 means the caller lacks Connection Admin permissions;
    404 means the connection does not exist.
    """
    try:
        path = f"/v1/connections/{_quote(params.connection_id)}/dbt"
        body = _build_update_configuration_body(params)
        payload = await get_client().request_json("PUT", path, json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return (
            f"Updated dbt configuration for connection `{params.connection_id}` — "
            f"branch `{result.get('branch', params.branch)}`, "
            f"auth method `{result.get('authMethod', params.auth_method or 'unchanged')}`, "
            f"dbt version `{result.get('dbtVersion', 'unknown')}`."
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Delete dbt configuration
# --------------------------------------------------------------------------- #


class DeleteDbtConfigurationInput(BaseModel):
    """Input for `omni_delete_dbt_configuration`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="ID (UUID) of the connection where dbt is configured.")


@mcp.tool(
    name="omni_delete_dbt_configuration",
    annotations=ToolAnnotations(
        title="Delete dbt Configuration",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_dbt_configuration(params: DeleteDbtConfigurationInput) -> str:
    """Remove the dbt configuration for a connection by unlinking its dbt repository.

    This does not delete the git repository itself — only the connection's link
    to it — but it does remove Omni's dbt integration for this connection,
    including its dbt environments' usability. This action cannot be undone from
    this tool (the connection would need to be reconfigured from scratch).

    When to Use:
    - To fully unlink dbt from a connection (e.g. decommissioning a project).
    - Before relinking a connection to an unrelated dbt repository from scratch.

    When NOT to Use:
    - To temporarily disable one dbt environment — use `omni_delete_dbt_environment`.
    - To change the repo URL or branch while keeping dbt linked — use
      `omni_update_dbt_configuration`.

    Returns:
    A short confirmation string. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    401 means missing/invalid authentication; 403 means the caller lacks
    Connection Admin permissions; 404 means the connection does not exist or dbt
    was not configured for it.
    """
    try:
        path = f"/v1/connections/{_quote(params.connection_id)}/dbt"
        payload = await get_client().request_json("DELETE", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        success = bool(body.get("success"))
        message = body.get("message") or ("dbt configuration removed" if success else "no confirmation returned")
        verb = "Deleted" if success else "Attempted to delete"
        return f"{verb} dbt configuration for connection `{params.connection_id}`: {message}"
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Create dbt environment
# --------------------------------------------------------------------------- #


class CreateDbtEnvironmentInput(BaseModel):
    """Input for `omni_create_dbt_environment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(
        ..., min_length=1, description="ID (UUID) of the connection to create the environment on."
    )
    name: str = Field(..., min_length=1, description="The name of the dbt environment.")
    target_schema: str = Field(..., min_length=1, description="The target schema for the environment.")
    is_deferral_enabled: bool | None = Field(
        default=None,
        description=(
            "Whether deferral is enabled for this environment. Server default is `false`. Ignored (forced to "
            "`false`) for the default (production) environment."
        ),
    )
    owner_id: str | None = Field(
        default=None,
        description=(
            "Set to the ID of a user to create a **personal** environment owned by that user (Organization Admin "
            "permissions are required to set an ID other than the caller's own). Mutually exclusive with `shared`."
        ),
    )
    shared: bool = Field(
        default=False,
        description=(
            "Set true to explicitly create a **shared** environment — per the API, this sends `ownerId: null` "
            "(requires Connection Admin or Organization Admin permissions). Mutually exclusive with `owner_id`. "
            "If neither `owner_id` nor `shared` is set, `ownerId` is omitted from the request entirely and the "
            "API decides; pass `shared=true` explicitly when a shared environment is what you want."
        ),
    )
    target_database: str | None = Field(default=None, description="Target database override.")
    target_name: str | None = Field(default=None, description="Target name override.")
    target_role: str | None = Field(default=None, description="Target role override.")
    variables: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            'Environment variables, each shaped `{"name": str, "value": str, "isSecret": bool}`. All three '
            "keys are required per entry when `variables` is included, and at least one entry is required."
        ),
    )

    @field_validator("variables")
    @classmethod
    def _validate_variables(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return value
        if not value:
            raise ValueError("variables must contain at least one entry when included")
        for index, var in enumerate(value):
            _require_new_variable(var, index)
        return value

    @model_validator(mode="after")
    def _validate_owner_exclusivity(self) -> CreateDbtEnvironmentInput:
        if self.owner_id is not None and self.shared:
            raise ValueError("owner_id and shared are mutually exclusive — set at most one")
        return self


@mcp.tool(
    name="omni_create_dbt_environment",
    annotations=ToolAnnotations(
        title="Create dbt Environment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_dbt_environment(params: CreateDbtEnvironmentInput) -> str:
    """Create a new dbt environment on a connection.

    A dbt environment connects Omni to a specific dbt project/target within the
    connection. Environments can be personal (owned by one user, via `owner_id`)
    or shared (`shared=true`, which sends `ownerId: null` as the API's documented
    shared-environment convention). `owner_id` and `shared` are mutually
    exclusive. Permission requirements differ by kind: creating a personal
    environment for yourself needs Restricted Querier or higher on the
    associated model; a personal environment for someone else needs
    Organization Admin; a shared environment needs Connection Admin or
    Organization Admin.

    When to Use:
    - To add a new dbt target (e.g. a personal dev environment, or a new shared
      production/staging environment) to a connection.
    - As part of provisioning a new dbt project integration.

    When NOT to Use:
    - To change an existing environment's schema/variables — use
      `omni_update_dbt_environment`.
    - Before dbt itself is configured on the connection — configure it first with
      `omni_update_dbt_configuration`.

    Returns:
    A short confirmation naming the environment, its id, target schema, and
    whether it is shared or personal. On failure, an `Error ...` string. Secret
    variable values are never echoed back.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", "name": "Production dbt", \
"target_schema": "analytics", "shared": true}}`
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", "name": "My dev env", \
"target_schema": "analytics_dev", "owner_id": "550e8400-e29b-41d4-a716-446655440000", \
"variables": [{"name": "DBT_TARGET", "value": "dev", "isSecret": false}]}}`

    Error Handling:
    400 means an invalid body, or that `owner_id` names a user who is not an
    organization member; 401 means missing/invalid authentication; 403 means
    insufficient permissions (Connection Admin required for a shared environment,
    or a non-admin tried to set `owner_id` for another user); 404 means the
    connection does not exist.
    """
    try:
        path = f"/v1/connections/{_quote(params.connection_id)}/dbt/environments"
        body: dict[str, Any] = {"name": params.name, "targetSchema": params.target_schema}
        if params.is_deferral_enabled is not None:
            body["isDeferralEnabled"] = params.is_deferral_enabled
        if params.owner_id is not None:
            body["ownerId"] = params.owner_id
        elif params.shared:
            body["ownerId"] = None
        if params.target_database is not None:
            body["targetDatabase"] = params.target_database
        if params.target_name is not None:
            body["targetName"] = params.target_name
        if params.target_role is not None:
            body["targetRole"] = params.target_role
        if params.variables is not None:
            body["variables"] = params.variables

        payload = await get_client().request_json("POST", path, json_body=body)
        env: dict[str, Any] = payload if isinstance(payload, dict) else {}
        env_id = env.get("id", "unknown")
        name = env.get("name", params.name)
        return (
            f"Created dbt environment **{name}** (id `{env_id}`) on connection `{params.connection_id}` — "
            f"target schema `{env.get('targetSchema', params.target_schema)}`, {_environment_kind(env)}."
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# List dbt environments
# --------------------------------------------------------------------------- #


class ListDbtEnvironmentsInput(BaseModel):
    """Input for `omni_list_dbt_environments`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="ID (UUID) of the connection to list environments for.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw environment records.",
    )


@mcp.tool(
    name="omni_list_dbt_environments",
    annotations=ToolAnnotations(
        title="List dbt Environments",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_dbt_environments(params: ListDbtEnvironmentsInput) -> str:
    """List every dbt environment configured on a connection.

    The API returns the full list in one response (it is not paginated), so
    there is no `page_size`/`cursor` for this tool.

    When to Use:
    - To find an environment's id before calling `omni_update_dbt_environment`,
      `omni_delete_dbt_environment`, or `omni_update_model_branch_dbt_environment`.
    - To audit which environments exist, which is the default, and who owns each.

    When NOT to Use:
    - To read a single environment's full variable list only — this already
      returns every environment; filter the result yourself if you only need one.

    Returns:
    A markdown list (name, id, target schema, shared/personal, default/deferral
    tags) or the raw JSON records when `response_format` is `json`. Secret
    variable values are masked as `***` in both formats. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", "response_format": "json"}}`

    Error Handling:
    401 means missing/invalid authentication; 403 means insufficient permissions
    (Connection Admin required); 404 means the connection does not exist.
    """
    try:
        path = f"/v1/connections/{_quote(params.connection_id)}/dbt/environments"
        payload = await get_client().request_json("GET", path)
        records: list[Any] = payload if isinstance(payload, list) else []
        items = [_mask_environment(env) for env in records if isinstance(env, dict)]
        return cursor_paginated_response(
            items=items,
            page_info=None,
            fmt=params.response_format,
            item_formatter=_format_environment_item,
            title=f"dbt Environments — Connection {params.connection_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Update dbt environment
# --------------------------------------------------------------------------- #


class UpdateDbtEnvironmentInput(BaseModel):
    """Input for `omni_update_dbt_environment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="ID (UUID) of the connection the environment belongs to.")
    environment_id: str = Field(..., min_length=1, description="ID (UUID) of the dbt environment to update.")
    name: str | None = Field(default=None, min_length=1, description="New name for the dbt environment.")
    target_schema: str | None = Field(default=None, min_length=1, description="New target schema for the environment.")
    is_deferral_enabled: bool | None = Field(
        default=None,
        description=(
            "Whether deferral is enabled. Ignored (forced to `false`) for the default (production) environment."
        ),
    )
    owner_id: str | None = Field(
        default=None,
        description=(
            "Set to a user ID to make this a personal environment owned by that user (Organization Admin "
            "permissions required to name a user other than the caller). Omit to leave ownership unchanged; set "
            "`clear_owner=true` instead to convert this to a shared environment."
        ),
    )
    clear_owner: bool = Field(
        default=False,
        description=(
            "Set true to explicitly clear ownership (send `ownerId: null`), converting this to a shared "
            "environment. Ignored if `owner_id` is also set. Requires Connection Admin or Organization Admin "
            "permissions."
        ),
    )
    target_database: str | None = Field(default=None, description="New target database override.")
    clear_target_database: bool = Field(
        default=False,
        description=(
            "Set true to explicitly clear the target database override (send `targetDatabase: null`), reverting "
            "to the connection's default. Ignored if `target_database` is also set."
        ),
    )
    target_name: str | None = Field(default=None, description="New target name override.")
    clear_target_name: bool = Field(
        default=False,
        description=(
            "Set true to explicitly clear the target name override (send `targetName: null`), reverting to the "
            "connection's default. Ignored if `target_name` is also set."
        ),
    )
    target_role: str | None = Field(default=None, description="New target role override.")
    clear_target_role: bool = Field(
        default=False,
        description=(
            "Set true to explicitly clear the target role override (send `targetRole: null`), reverting to the "
            "connection's default. Ignored if `target_role` is also set."
        ),
    )
    variables: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Environment variables to add or update, each shaped "
            '`{"id": str (existing var, omit for new), "name": str (required for new), '
            '"value": str (required for new; omit/null to keep an existing secret\'s value), '
            '"isSecret": bool (always required)}`. At least one entry is required when `variables` is included.'
        ),
    )

    @field_validator("variables")
    @classmethod
    def _validate_variables(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return value
        if not value:
            raise ValueError("variables must contain at least one entry when included")
        for index, var in enumerate(value):
            _require_update_variable(var, index)
        return value


def _build_update_environment_body(params: UpdateDbtEnvironmentInput) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if params.name is not None:
        body["name"] = params.name
    if params.target_schema is not None:
        body["targetSchema"] = params.target_schema
    if params.is_deferral_enabled is not None:
        body["isDeferralEnabled"] = params.is_deferral_enabled
    if params.owner_id is not None:
        body["ownerId"] = params.owner_id
    elif params.clear_owner:
        body["ownerId"] = None
    if params.target_database is not None:
        body["targetDatabase"] = params.target_database
    elif params.clear_target_database:
        body["targetDatabase"] = None
    if params.target_name is not None:
        body["targetName"] = params.target_name
    elif params.clear_target_name:
        body["targetName"] = None
    if params.target_role is not None:
        body["targetRole"] = params.target_role
    elif params.clear_target_role:
        body["targetRole"] = None
    if params.variables is not None:
        body["variables"] = params.variables
    return body


@mcp.tool(
    name="omni_update_dbt_environment",
    annotations=ToolAnnotations(
        title="Update dbt Environment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_dbt_environment(params: UpdateDbtEnvironmentInput) -> str:
    """Update an existing dbt environment on a connection.

    Every field is optional — only the fields you provide are changed. To
    convert a personal environment to shared (or vice versa), see `owner_id` and
    `clear_owner`. The `target_database`/`target_name`/`target_role` overrides
    are nullable in the API; use `clear_target_database`/`clear_target_name`/
    `clear_target_role` to explicitly reset one to the connection's default
    instead of leaving it unchanged. Variables can be added (omit `id`) or
    updated in place (include the existing `id`).

    When to Use:
    - To rename an environment, change its target schema, or toggle deferral.
    - To reassign ownership (personal ↔ shared) via `owner_id` / `clear_owner`.
    - To add, update, or rotate environment variables.

    When NOT to Use:
    - To create a brand-new environment — use `omni_create_dbt_environment`.
    - To permanently remove one — use `omni_delete_dbt_environment`.

    Returns:
    A short confirmation naming the updated environment and connection. On
    failure, an `Error ...` string, including when no fields were provided to
    change. Secret variable values are never echoed back.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", \
"environment_id": "247dc6dc-2a58-4688-9521-c5ed3e99c1e8", "name": "Staging", "target_schema": "dev_schema"}}`
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", \
"environment_id": "247dc6dc-2a58-4688-9521-c5ed3e99c1e8", "clear_owner": true}}`

    Error Handling:
    400 means an invalid body, or that `owner_id` names a user who is not an
    organization member; 401 means missing/invalid authentication; 403 means
    insufficient permissions (Connection Admin required, or a non-admin tried to
    set `owner_id` for another user); 404 means the connection or environment
    does not exist.
    """
    try:
        body = _build_update_environment_body(params)
        if not body:
            return (
                "Error: provide at least one field to update (name, target_schema, ownership, overrides, or variables)."
            )
        path = f"/v1/connections/{_quote(params.connection_id)}/dbt/environments/{_quote(params.environment_id)}"
        payload = await get_client().request_json("PUT", path, json_body=body)
        env: dict[str, Any] = payload if isinstance(payload, dict) else {}
        name = env.get("name", params.name or params.environment_id)
        return (
            f"Updated dbt environment **{name}** (id `{params.environment_id}`) on connection `{params.connection_id}`."
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Delete dbt environment
# --------------------------------------------------------------------------- #


class DeleteDbtEnvironmentInput(BaseModel):
    """Input for `omni_delete_dbt_environment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="ID (UUID) of the connection the environment belongs to.")
    environment_id: str = Field(..., min_length=1, description="ID (UUID) of the dbt environment to delete.")


@mcp.tool(
    name="omni_delete_dbt_environment",
    annotations=ToolAnnotations(
        title="Delete dbt Environment",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_dbt_environment(params: DeleteDbtEnvironmentInput) -> str:
    """Delete a dbt environment from a connection. This action cannot be undone.

    Any model branch currently set to use this environment (via
    `omni_update_model_branch_dbt_environment`) will need to be repointed at a
    different environment afterward.

    When to Use:
    - To remove a personal dev environment that is no longer needed.
    - To decommission a shared environment being replaced by another.

    When NOT to Use:
    - To temporarily disable dbt for the whole connection — use
      `omni_delete_dbt_configuration` instead.
    - When you are unsure whether a branch still depends on it — check first
      with `omni_list_dbt_environments`, and repoint the branch if needed.

    Returns:
    A short confirmation string. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000", \
"environment_id": "247dc6dc-2a58-4688-9521-c5ed3e99c1e8"}}`

    Error Handling:
    401 means missing/invalid authentication; 403 means insufficient permissions
    (Connection Admin required), the connection's dialect does not support dbt
    environments, or the environment belongs to a different organization; 404
    means the connection or environment does not exist.
    """
    try:
        path = f"/v1/connections/{_quote(params.connection_id)}/dbt/environments/{_quote(params.environment_id)}"
        payload = await get_client().request_json("DELETE", path)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        success = bool(body.get("success"))
        verb = "Deleted" if success else "Attempted to delete"
        return f"{verb} dbt environment `{params.environment_id}` on connection `{params.connection_id}`."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Get dbt exposures
# --------------------------------------------------------------------------- #


class GetDbtExposuresInput(BaseModel):
    """Input for `omni_get_dbt_exposures`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="ID (UUID) of the model to compute dbt exposures for.")
    page_size: int = Field(default=20, ge=1, le=100, description="Records per page (1-100).")
    cursor: str | None = Field(
        default=None, description="Pagination cursor from a previous response's `pageInfo.nextCursor`."
    )
    branch_id: str | None = Field(default=None, description="Branch ID (UUID) for branch-aware model operations.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw exposure records.",
    )


@mcp.tool(
    name="omni_get_dbt_exposures",
    annotations=ToolAnnotations(
        title="Get dbt Exposures",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_dbt_exposures(params: GetDbtExposuresInput) -> str:
    """Compute and retrieve dbt exposures for a model, on demand.

    Exposures are computed by analyzing which dbt models are referenced by
    dashboards that use the specified Omni model — the same data the manual
    **Sync exposures** flow produces, but retrievable programmatically. This
    makes it usable from CI/CD pipelines and other automated workflows, without
    a person triggering a sync in the UI. A dashboard that references no dbt
    models yields a record with a `null` exposure.

    The result set can be large for models with many dashboards; it is paginated
    and the markdown/JSON output is truncated at the tool-result size limit.

    When to Use:
    - In a CI/CD pipeline, to publish dbt exposure metadata after a deploy.
    - To audit which dashboards depend on which dbt models.

    When NOT to Use:
    - To trigger the manual **Sync exposures** UI flow — this only reads,
      it does not write exposures back to dbt.
    - To list dbt environments or configuration — use the environment/config
      tools.

    Returns:
    A markdown page of exposures (dashboard id, exposure label/name, owner,
    `depends_on` refs, dashboard URL) with a next cursor when more remain, or
    the raw JSON records when `response_format` is `json`. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"model_id": "550e8400-e29b-41d4-a716-446655440000"}}`
    - `{"params": {"model_id": "550e8400-e29b-41d4-a716-446655440000", "page_size": 100, \
"branch_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    400 means an invalid `modelId`/`cursor`/`branch_id` format, or a `pageSize`
    outside 1-100; 403 means insufficient permissions (Connection Admin
    required); 404 means the model does not exist.
    """
    try:
        query: dict[str, Any] = {"pageSize": params.page_size}
        if params.cursor:
            query["cursor"] = params.cursor
        if params.branch_id:
            query["branch_id"] = params.branch_id
        path = f"/v1/models/{_quote(params.model_id)}/dbt-exposures"
        payload = await get_client().request_json("GET", path, params=query)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        return cursor_paginated_response(
            items=body.get("records") or [],
            page_info=body.get("pageInfo"),
            fmt=params.response_format,
            item_formatter=_format_exposure_item,
            title=f"dbt Exposures — Model {params.model_id}",
        )
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Set dbt environment on model branch
# --------------------------------------------------------------------------- #


class UpdateModelBranchDbtEnvironmentInput(BaseModel):
    """Input for `omni_update_model_branch_dbt_environment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="ID (UUID) of the model.")
    branch_name: str = Field(
        ..., min_length=1, description="Name of the model branch to configure (e.g. `feature/new-models`)."
    )
    dbt_environment_id: str = Field(
        ..., min_length=1, description="ID (UUID) of the dbt environment to set as active for this branch."
    )
    dbt_git_branch: str | None = Field(
        default=None, description="Optional git branch name to associate with the dbt environment on this model branch."
    )


@mcp.tool(
    name="omni_update_model_branch_dbt_environment",
    annotations=ToolAnnotations(
        title="Update Model Branch dbt Environment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_model_branch_dbt_environment(params: UpdateModelBranchDbtEnvironmentInput) -> str:
    """Update the active dbt environment on a model branch.

    Model branches are Omni's working-copy mechanism for iterating on a model
    (e.g. a developer's feature branch, or a branch a CI pipeline checks out)
    before merging changes into the shared model. Each branch can point at a
    different dbt environment — for example, a feature branch might target a
    personal dev environment while the shared model targets production. This
    endpoint lets a CI/CD pipeline configure that mapping programmatically,
    typically right before triggering a schema refresh on the branch, so the
    refresh compiles dbt against the intended target rather than whatever
    environment the branch last happened to use.

    When to Use:
    - In CI/CD, before refreshing a branch's schema, to point it at the correct
      dbt environment (e.g. a per-branch or per-PR environment).
    - To repoint a branch after its previous dbt environment was deleted.

    When NOT to Use:
    - To create the dbt environment itself — use `omni_create_dbt_environment`
      first, then set it here.
    - To change the connection-level dbt configuration — use
      `omni_update_dbt_configuration`.

    Returns:
    A short confirmation string. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "branch_name": "feature/new-models", \
"dbt_environment_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901"}}`
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "branch_name": "feature/new-models", \
"dbt_environment_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901", "dbt_git_branch": "feature/branch"}}`

    Error Handling:
    400 means an invalid `modelId`/`dbt_environment_id` format or a missing
    `dbt_environment_id`; 404 means the shared model or branch model does not
    exist, the named branch model id does not exist, or the dbt environment does
    not exist; 405 means the HTTP method is not allowed on this route (check the
    URL); 403/401 indicate a permissions or authentication problem.
    """
    try:
        path = f"/v1/models/{_quote(params.model_id)}/branch/{_quote(params.branch_name)}/dbt"
        body: dict[str, Any] = {"dbt_environment_id": params.dbt_environment_id}
        if params.dbt_git_branch is not None:
            body["dbt_git_branch"] = params.dbt_git_branch

        payload = await get_client().request_json("POST", path, json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        success = bool(result.get("success", True))
        branch_note = f" (git branch `{params.dbt_git_branch}`)" if params.dbt_git_branch else ""
        if not success:
            return (
                f"Request completed but the API did not confirm success for model `{params.model_id}` "
                f"branch `{params.branch_name}`."
            )
        return (
            f"Set dbt environment `{params.dbt_environment_id}` as active for model `{params.model_id}` "
            f"branch `{params.branch_name}`{branch_note}."
        )
    except Exception as exc:
        return handle_api_error(exc)
