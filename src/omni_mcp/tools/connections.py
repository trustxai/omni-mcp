"""Tools for the connection, connection environment, and schema refresh endpoints.

Covers all 13 operations across three tags: Connections, Connection environments,
Schema refresh schedules. Any output field whose name contains password/secret/
token/key is masked in both markdown and JSON output — the Omni API itself never
returns plaintext credentials, but this module never assumes that from upstream.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import quote

import httpx
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, iso_or_na, markdown_table, to_json, truncate_result
from omni_mcp.server import mcp

#: The `Dialect` enum, verbatim from the spec.
Dialect = Literal[
    "athena",
    "bigquery",
    "clickhouse",
    "databricks",
    "databricks_lakebase",
    "exasol",
    "mariadb",
    "motherduck",
    "mssql",
    "mysql",
    "oracle",
    "postgres",
    "redshift",
    "sap_hana",
    "snowflake",
    "starrocks",
    "trino",
]

SortField = Literal["database", "dialect", "name"]
SortDirection = Literal["asc", "desc"]

#: Credential-bearing string fields must not be whitespace-stripped: unlike
#: every other field on these models, leading/trailing whitespace in a
#: password or private key may be significant (or, for a private key, part of
#: its PEM structure). The model-level `str_strip_whitespace=True` config
#: would otherwise silently mangle these values.
CredentialStr = Annotated[str, StringConstraints(strip_whitespace=False)]

#: Schema refresh schedules use a 6-field AWS EventBridge cron expression
#: (`minute hour day-of-month month day-of-week year`) — confirmed by every
#: example in the spec, e.g. `"00 09 * * ? *"`.
_CRON_FIELD_COUNT = 6

_SENSITIVE_SUBSTRINGS = ("password", "secret", "token", "key")

#: `omni_create_connection`'s first-class request-body keys (camelCase, as
#: sent to the API). A `settings` passthrough key colliding with one of these
#: is rejected rather than silently overriding the dedicated parameter.
_CREATE_CONNECTION_FIRST_CLASS_KEYS = frozenset(
    {
        "dialect",
        "name",
        "passwordUnencrypted",
        "host",
        "port",
        "database",
        "username",
        "warehouse",
        "baseRole",
        "defaultSchema",
        "includeSchemas",
        "includeOtherCatalogs",
        "queryTimeoutSeconds",
        "region",
        "privateKey",
    }
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_SUBSTRINGS)


def _mask_sensitive(value: Any) -> Any:
    """Recursively replace any dict value whose key looks like a credential."""
    if isinstance(value, dict):
        return {
            key: "***MASKED***" if isinstance(key, str) and _is_sensitive_key(key) else _mask_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    return value


def _quote(value: str) -> str:
    return quote(value, safe="")


def _validate_cron(value: str) -> str:
    fields = value.split()
    if len(fields) != _CRON_FIELD_COUNT:
        raise ValueError(
            "schedule must be a 6-field AWS EventBridge cron expression "
            f'("minute hour day-of-month month day-of-week year"), got {len(fields)} field(s): {value!r}'
        )
    return value


def _connection_row(conn: dict[str, Any]) -> dict[str, Any]:
    return {
        "ID": conn.get("id", "unknown"),
        "Name": conn.get("name", "unnamed"),
        "Dialect": conn.get("dialect", "unknown"),
        "Database": conn.get("database", ""),
        "Base Role": conn.get("baseRole", ""),
        "Default Schema": conn.get("defaultSchema", ""),
        "Created": iso_or_na(conn.get("createdAt")),
        "Updated": iso_or_na(conn.get("updatedAt")),
        "Deleted": iso_or_na(conn.get("deletedAt")),
    }


def _schedule_row(schedule: dict[str, Any]) -> dict[str, Any]:
    return {
        "Schedule ID": schedule.get("scheduleId", "unknown"),
        "Cron": schedule.get("schedule", ""),
        "Timezone": schedule.get("timezone", ""),
        "Description": schedule.get("description", ""),
        "Hard Refresh": schedule.get("hardRefresh", False),
        "Disabled At": iso_or_na(schedule.get("disabledAt")),
        "Created": iso_or_na(schedule.get("createdAt")),
        "Updated": iso_or_na(schedule.get("updatedAt")),
    }


def _schedule_lines(schedule: dict[str, Any]) -> list[str]:
    return [
        f"- Schedule ID: `{schedule.get('scheduleId', 'unknown')}`",
        f"- Connection ID: `{schedule.get('connectionId', 'unknown')}`",
        f"- Cron: `{schedule.get('schedule', '')}`",
        f"- Timezone: {schedule.get('timezone', '')}",
        f"- Description: {schedule.get('description', 'N/A')}",
        f"- Hard refresh: {schedule.get('hardRefresh', False)}",
        f"- Disabled at: {iso_or_na(schedule.get('disabledAt'))}",
        f"- Created: {iso_or_na(schedule.get('createdAt'))}",
        f"- Updated: {iso_or_na(schedule.get('updatedAt'))}",
    ]


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


class ListConnectionsInput(BaseModel):
    """Input for `omni_list_connections`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(
        default=None, description="Filter connections by name (case-insensitive, partial matching)."
    )
    database: str | None = Field(
        default=None, description="Filter connections by database name (case-insensitive, partial matching)."
    )
    dialects: list[Dialect] | None = Field(
        default=None,
        description="Filter connections by dialect(s). Sent to the API as a comma-separated list.",
    )
    sort_field: SortField | None = Field(
        default=None, description="Field to sort by: `database`, `dialect`, or `name`."
    )
    sort_direction: SortDirection | None = Field(
        default=None, description="Sort direction: `asc` (A-Z, 0-9) or `desc` (Z-A, 9-0)."
    )
    include_deleted: bool | None = Field(
        default=None,
        description="When `true`, includes archived (soft-deleted) connections. Defaults to `false` on the API.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a table, `json` for the raw connection records."
    )


@mcp.tool(
    name="omni_list_connections",
    annotations=ToolAnnotations(
        title="List Connections",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_connections(params: ListConnectionsInput) -> str:
    """List database connections configured on this instance.

    Supports filtering by name/database (partial, case-insensitive) and dialect(s),
    and sorting by name/dialect/database. The API does not paginate this endpoint,
    so every matching connection is returned in one call.

    When to Use:
    - To find a connection's ID before calling `omni_get_connection` or a schema
      refresh schedule tool.
    - To audit which dialects/databases are connected, or to find archived
      connections with `include_deleted`.

    When NOT to Use:
    - To fetch full detail for one already-known connection — use
      `omni_get_connection`.

    Returns:
    A markdown table (ID, Name, Dialect, Database, Base Role, Default Schema,
    Created, Updated, Deleted) or the raw JSON connection records when
    `response_format` is `json`. Credential-shaped fields are masked in both
    formats. On failure, an `Error ...` string.

    Examples:
    - List everything: `{"params": {}}`
    - Only Postgres/MySQL, sorted by name: `{"params": {"dialects": ["postgres", "mysql"], "sort_field": "name"}}`
    - Include archived connections: `{"params": {"include_deleted": true}}`

    Error Handling:
    400 means an unrecognised query key, an invalid `sort_field`, or an invalid
    dialect name. 429 means the 60 requests/minute rate limit was hit.
    """
    try:
        query: dict[str, Any] = {}
        if params.name:
            query["name"] = params.name
        if params.database:
            query["database"] = params.database
        if params.dialects:
            query["dialect"] = ",".join(params.dialects)
        if params.sort_field:
            query["sortField"] = params.sort_field
        if params.sort_direction:
            query["sortDirection"] = params.sort_direction
        if params.include_deleted is not None:
            query["includeDeleted"] = params.include_deleted

        payload = await get_client().request_json("GET", "/v1/connections", params=query or None)
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        connections = _mask_sensitive(body.get("connections") or [])

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"count": len(connections), "connections": connections}))

        rows = [_connection_row(item) for item in connections]
        lines = [f"# Connections ({len(rows)})", "", markdown_table(rows)]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


class GetConnectionInput(BaseModel):
    """Input for `omni_get_connection`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="The connection's UUID.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a summary, `json` for the raw connection record.",
    )


@mcp.tool(
    name="omni_get_connection",
    annotations=ToolAnnotations(
        title="Get Connection",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_connection(params: GetConnectionInput) -> str:
    """Fetch a single database connection by ID.

    Requires Restricted Querier permissions or higher on the connection.

    When to Use:
    - To inspect a connection's dialect, database, base role, and environment
      user-attribute configuration before updating it or creating a schedule.

    When NOT to Use:
    - To browse all connections — use `omni_list_connections`.

    Returns:
    A markdown summary (id, name, dialect, database, base role, default schema,
    environment user-attribute settings, timestamps) or the raw JSON record when
    `response_format` is `json`. Credential-shaped fields are masked in both
    formats (the API never returns plaintext credentials in the first place). On
    failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    400 means `connection_id` is not a valid UUID; 401 means the API key is
    missing or invalid; 403 means the caller lacks `READ` permission on the
    connection; 404 means no connection with that ID exists on this instance.
    """
    try:
        payload = await get_client().request_json("GET", f"/v1/connections/{_quote(params.connection_id)}")
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        conn = _mask_sensitive(body.get("connection") or {})

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"connection": conn}))

        lines = [
            "# Connection",
            "",
            f"- ID: `{conn.get('id', 'unknown')}`",
            f"- Name: **{conn.get('name', 'unnamed')}**",
            f"- Dialect: {conn.get('dialect', 'unknown')}",
            f"- Database: {conn.get('database', 'N/A')}",
            f"- Default schema: {conn.get('defaultSchema', 'N/A')}",
            f"- Base role: {conn.get('baseRole', 'N/A')}",
            "- Environment user attribute: "
            f"{conn.get('userAttributeNameForConnectionEnvironments') or 'not configured'}",
            "- Default environment user attribute values: "
            f"{conn.get('userAttributeValuesForDefaultEnvironment') or 'N/A'}",
            f"- Branch overrides user attribute: {conn.get('branchConnectionEnvironmentOverridesUserAttr', False)}",
            f"- Environment switches schema model: {conn.get('environmentConnectionSwitchesSchemaModel', False)}",
            f"- Created: {iso_or_na(conn.get('createdAt'))}",
            f"- Updated: {iso_or_na(conn.get('updatedAt'))}",
            f"- Deleted: {iso_or_na(conn.get('deletedAt'))}",
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


class CreateConnectionInput(BaseModel):
    """Input for `omni_create_connection`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dialect: Dialect = Field(..., description="The database dialect.")
    name: str = Field(..., min_length=1, description="A descriptive name for the connection.")
    password_unencrypted: CredentialStr = Field(
        ...,
        description=(
            "The password to authenticate with. Sent verbatim, without whitespace-stripping — leading/"
            "trailing whitespace may be a meaningful part of the credential. BigQuery: the full service "
            "account JSON file content. Snowflake with keypair authentication: may be an empty string "
            "since `private_key` supplies the credential instead."
        ),
    )
    host: str | None = Field(
        default=None,
        description=(
            "The hostname or IP address of the database server. Not required for MotherDuck. Snowflake: "
            "the account identifier only (e.g. `myaccount`, not `myaccount.snowflakecomputing.com`). "
            "BigQuery: automatically determined from the service account."
        ),
    )
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description=(
            "The port number for the database connection. Not required for Snowflake, MotherDuck, "
            "BigQuery, Databricks, and Athena."
        ),
    )
    database: str | None = Field(
        default=None,
        description="The default database/catalog to connect to. BigQuery: the project ID. Athena: the data catalog.",
    )
    username: str | None = Field(
        default=None,
        description="The username to authenticate with. Not required for MotherDuck. BigQuery: the service account client email.",
    )
    warehouse: str | None = Field(
        default=None,
        description="Required for Snowflake (specify the warehouse) and Databricks (specify the HTTP path).",
    )
    base_role: str | None = Field(
        default=None,
        description=(
            "The default role for users accessing the connection: `VIEWER`, `QUERIER`, `QUERY_TOPICS` "
            "(Restricted Querier), `MODELER`, `CONNECTION_ADMIN`, `NO_ACCESS`, or a custom role name defined "
            "for your organization."
        ),
    )
    default_schema: str | None = Field(default=None, description="Required for MSSQL. The default schema to use.")
    include_schemas: str | None = Field(
        default=None, description="Comma-separated list of schemas to include. Leave unset to include all schemas."
    )
    include_other_catalogs: str | None = Field(
        default=None,
        description=(
            "Comma-separated list of other catalogs/databases to include. Only applicable for databases that "
            "support multi-catalog queries: BigQuery, Snowflake, MotherDuck, Databricks, Trino, Athena."
        ),
    )
    query_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=3600,
        description="The timeout in seconds for queries. Maximum 3600 (1 hour). API default is 900.",
    )
    region: str | None = Field(
        default=None,
        description="Required for BigQuery and Athena connections. BigQuery: a region like `us`. Athena: an AWS region like `us-east-1`.",
    )
    private_key: CredentialStr | None = Field(
        default=None,
        description=(
            "Applicable for Snowflake only. An RSA key for keypair authentication (PEM headers are added "
            "automatically if none are provided). Sent verbatim, without whitespace-stripping, since PEM "
            "formatting may depend on exact whitespace."
        ),
    )
    settings: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Additional dialect-specific settings passed through verbatim as camelCase keys, merged into "
            "the request body *before* the first-class fields above (which always win on key collision). "
            "Must not repeat a first-class field's API key (e.g. `passwordUnencrypted`, `name`) — set that "
            "field directly instead; doing so is rejected as a validation error. Recognised keys (all "
            "optional): `maxBillingBytes` (str, BigQuery max billed "
            "bytes), `scratchSchema` (str, schema for data-upload tables), `systemTimezone` (str, default "
            "`UTC`), `queryTimezone` (str, default `NONE`), `allowsUserSpecificTimezones` (bool, default "
            "false), `alwaysScopeViewNames` (bool), `trustServerCertificate` (bool, default false; MSSQL/"
            "Exasol/ClickHouse), `acceptsLicense` (bool, Oracle license acceptance), `authenticationType` "
            "(str; BigQuery/MSSQL/Snowflake/Databricks/Athena), `awsRoleArn` (str, Athena role to assume), "
            "`enableDbSemanticLayerIntegration` (bool, Snowflake/Databricks), `enableDbSemanticLayerTopics` "
            "(bool, Snowflake/Databricks), `externalOauthAudience` / `externalOauthAuthorizationUrl` / "
            "`externalOauthTokenUrl` (str, Snowflake external OAuth), `hostOverride` (str, Snowflake), "
            "`inferRelationshipsFromColumnNames` (bool, default true), `inferRelationshipsFromForeignKeys` "
            "(bool, Postgres/Snowflake), `oauthClientId` (str, Snowflake/Databricks native OAuth), "
            "`oauthClientSecretUnencrypted` (str, Snowflake/Databricks native OAuth), `offloadedSchemas` "
            "(str), `useMachineAuth` (bool, Athena/Databricks)."
        ),
    )

    @model_validator(mode="after")
    def _settings_must_not_shadow_first_class_fields(self) -> CreateConnectionInput:
        if self.settings:
            collisions = sorted(set(self.settings) & _CREATE_CONNECTION_FIRST_CLASS_KEYS)
            if collisions:
                raise ValueError(
                    f"settings must not repeat first-class field keys: {', '.join(collisions)}. "
                    "Set these directly on the tool's own parameters instead of via settings."
                )
        return self


@mcp.tool(
    name="omni_create_connection",
    annotations=ToolAnnotations(
        title="Create Connection",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_connection(params: CreateConnectionInput) -> str:
    """Create a new database connection.

    Required fields depend on the dialect (see each field's description); common
    scalar fields (name, dialect, host, port, database, username, password,
    warehouse, schema, role) are first-class parameters, and less common
    dialect-specific options are passed through the `settings` dict.

    When to Use:
    - To register a new Snowflake/Postgres/BigQuery/etc. connection.

    When NOT to Use:
    - To change credentials on an existing connection — use `omni_update_connection`.
    - To scope environment-specific connections — use
      `omni_create_connection_environments`.

    Returns:
    A short confirmation with the new connection's ID. On failure, an
    `Error ...` string.

    Examples:
    - Postgres: `{"params": {"dialect": "postgres", "name": "My Postgres Connection", "host": "postgres.example.com", "port": 5432, "database": "mydb", "username": "dbuser", "password_unencrypted": "mypassword"}}`
    - Snowflake: `{"params": {"dialect": "snowflake", "name": "My Snowflake Connection", "host": "myaccount", "database": "MYDB", "username": "dbuser", "password_unencrypted": "mypassword", "warehouse": "COMPUTE_WH"}}`
    - BigQuery: `{"params": {"dialect": "bigquery", "name": "My BigQuery Connection", "region": "us", "password_unencrypted": "<SERVICE_ACCOUNT_JSON>"}}`

    Error Handling:
    400 means a missing/invalid required field for the chosen dialect; 403 means
    the caller lacks permission to create connections; 429 means the rate limit
    was hit.
    """
    try:
        # `settings` is merged in FIRST so the first-class fields below always
        # win on key collision; the model validator additionally rejects a
        # `settings` key that repeats a first-class field outright.
        body: dict[str, Any] = dict(params.settings) if params.settings else {}
        body["dialect"] = params.dialect
        body["name"] = params.name
        body["passwordUnencrypted"] = params.password_unencrypted
        optional: dict[str, Any] = {
            "host": params.host,
            "port": params.port,
            "database": params.database,
            "username": params.username,
            "warehouse": params.warehouse,
            "baseRole": params.base_role,
            "defaultSchema": params.default_schema,
            "includeSchemas": params.include_schemas,
            "includeOtherCatalogs": params.include_other_catalogs,
            "queryTimeoutSeconds": params.query_timeout_seconds,
            "region": params.region,
            "privateKey": params.private_key,
        }
        for key, value in optional.items():
            if value is not None:
                body[key] = value

        payload = await get_client().request_json("POST", "/v1/connections", json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        connection_id = result.get("data", "unknown")
        return f"Created connection **{params.name}** (`{params.dialect}`), id `{connection_id}`."
    except Exception as exc:
        return handle_api_error(exc)


class EnvironmentUserAttributeInput(BaseModel):
    """A user-attribute name and its default values, for `omni_update_connection`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    attribute_name: str = Field(
        ..., min_length=1, description="The name of the user attribute to use for environments."
    )
    default_values: list[str] = Field(
        ..., description="Array of default values for the user attribute (selects the base connection)."
    )


class UpdateConnectionInput(BaseModel):
    """Input for `omni_update_connection`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="The connection's UUID.")
    password_unencrypted: CredentialStr | None = Field(
        default=None,
        description=(
            "New password for credential rotation. Sent verbatim, without whitespace-stripping. Postgres/"
            "MySQL/etc.: the new password. BigQuery: the entire new service account JSON file content as a "
            "string."
        ),
    )
    private_key: CredentialStr | None = Field(
        default=None,
        description=(
            "Applicable only to Snowflake connections. New RSA private key in PEM format, for keypair "
            "rotation. Must be at least 2048 bits. Sent verbatim, without whitespace-stripping, since PEM "
            "formatting may depend on exact whitespace."
        ),
    )
    base_role: str | None = Field(
        default=None,
        description=(
            "The default role for users accessing the connection: `VIEWER`, `QUERIER`, `QUERY_TOPICS`, "
            "`MODELER`, `CONNECTION_ADMIN`, `NO_ACCESS`, or a custom role name."
        ),
    )
    environment_user_attribute: EnvironmentUserAttributeInput | None = Field(
        default=None,
        description=(
            "Configuration for environment user attributes. Ignored when `clear_environment_user_attribute` is true."
        ),
    )
    clear_environment_user_attribute: bool = Field(
        default=False,
        description="When true, sends `environmentUserAttribute: null` to remove environment user attribute settings.",
    )

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> UpdateConnectionInput:
        if (
            self.password_unencrypted is None
            and self.private_key is None
            and self.base_role is None
            and self.environment_user_attribute is None
            and not self.clear_environment_user_attribute
        ):
            raise ValueError(
                "At least one field must be provided: password_unencrypted, private_key, base_role, "
                "environment_user_attribute, or clear_environment_user_attribute."
            )
        return self


@mcp.tool(
    name="omni_update_connection",
    annotations=ToolAnnotations(
        title="Update Connection",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_update_connection(params: UpdateConnectionInput) -> str:
    """Update connection credentials, base role, or environment user-attribute settings.

    Credentials are encrypted at rest and never returned in API responses. Supports
    credential rotation for infrastructure-as-code workflows: password-based
    connections (Postgres, MySQL, etc.) and BigQuery rotate via
    `password_unencrypted`; Snowflake rotates via `private_key` (PEM, minimum 2048
    bits). At least one field must be provided.

    When to Use:
    - To rotate a connection's password/service-account key or Snowflake private key.
    - To change the connection's default base role.
    - To set or clear which user attribute (and default values) selects a
      connection environment.

    When NOT to Use:
    - To change host/port/database/dialect — not supported by this endpoint;
      recreate the connection instead.
    - To associate specific environment connections — use
      `omni_create_connection_environments` / `omni_update_connection_environment`.

    Returns:
    A short confirmation naming the connection ID and which fields were changed.
    On failure, an `Error ...` string.

    Examples:
    - Rotate a password: `{"params": {"connection_id": "...", "password_unencrypted": "new-password-here"}}`
    - Rotate a Snowflake key: `{"params": {"connection_id": "...", "private_key": "-----BEGIN PRIVATE KEY-----..."}}`
    - Update role: `{"params": {"connection_id": "...", "base_role": "QUERIER"}}`
    - Clear environment user attribute: `{"params": {"connection_id": "...", "clear_environment_user_attribute": true}}`

    Error Handling:
    400 means an empty/invalid body, a `private_key` on a non-Snowflake connection,
    or an invalid/too-short private key; 401 means missing/invalid authentication;
    403 means the caller lacks Connection Admin permissions; 404 means the
    connection does not exist.
    """
    try:
        body: dict[str, Any] = {}
        changed: list[str] = []
        if params.password_unencrypted is not None:
            body["passwordUnencrypted"] = params.password_unencrypted
            changed.append("passwordUnencrypted")
        if params.private_key is not None:
            body["privateKey"] = params.private_key
            changed.append("privateKey")
        if params.base_role is not None:
            body["baseRole"] = params.base_role
            changed.append("baseRole")
        if params.clear_environment_user_attribute:
            body["environmentUserAttribute"] = None
            changed.append("environmentUserAttribute (cleared)")
        elif params.environment_user_attribute is not None:
            body["environmentUserAttribute"] = {
                "attributeName": params.environment_user_attribute.attribute_name,
                "defaultValues": params.environment_user_attribute.default_values,
            }
            changed.append("environmentUserAttribute")

        await get_client().request_json("PATCH", f"/v1/connections/{_quote(params.connection_id)}", json_body=body)
        return f"Updated connection `{params.connection_id}` — changed: {', '.join(changed)}."
    except Exception as exc:
        return handle_api_error(exc)


class DeleteConnectionInput(BaseModel):
    """Input for `omni_delete_connection`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="The connection's UUID.")


@mcp.tool(
    name="omni_delete_connection",
    annotations=ToolAnnotations(
        title="Delete Connection",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_connection(params: DeleteConnectionInput) -> str:
    """Archive a connection (soft delete — moves it to trash).

    Requires Connection Admin permissions. The connection and its associated
    models and content move to trash and can be restored from the Omni app;
    sensitive credentials (passwords, keys) are permanently deleted immediately.

    When to Use:
    - To decommission a connection that is no longer needed.

    When NOT to Use:
    - When you are not sure the connection is unused — check dependent models
      and schedules first with `omni_get_connection` / the schedule tools.

    Returns:
    A short confirmation that the connection was archived. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "550e8400-e29b-41d4-a716-446655440000"}}`

    Error Handling:
    400 means `connection_id` is not a valid UUID; 401 means missing/invalid
    authentication; 403 means the caller lacks Connection Admin permissions on
    the connection; 404 means the connection does not exist or belongs to
    another organization; **410 means the connection was already archived** —
    this tool detects that case itself and returns a plain confirmation
    instead of an error, since the connection is already in the desired
    (archived) state and there is nothing to retry.
    """
    try:
        await get_client().request_json("DELETE", f"/v1/connections/{_quote(params.connection_id)}")
        return f"Archived connection `{params.connection_id}` (moved to trash)."
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 410:
            return f"Connection `{params.connection_id}` is already archived (no action taken)."
        return handle_api_error(exc)
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Connection environments
# --------------------------------------------------------------------------- #


class CreateConnectionEnvironmentsInput(BaseModel):
    """Input for `omni_create_connection_environments`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    base_connection_id: str = Field(..., min_length=1, description="The UUID of the base connection.")
    environment_connection_ids: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Connection UUIDs to use as environment connections. Each must exist in your organization, "
            "share the base connection's dialect, not already be used as an environment connection for "
            "this base connection, and not be the base connection itself. Sent to the API as a "
            "comma-separated list."
        ),
    )

    @field_validator("environment_connection_ids")
    @classmethod
    def _no_blank_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("environment_connection_ids must not contain empty strings")
        return value


@mcp.tool(
    name="omni_create_connection_environments",
    annotations=ToolAnnotations(
        title="Create Connection Environments",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_connection_environments(params: CreateConnectionEnvironmentsInput) -> str:
    """Associate one or more environment-specific connections with a base connection.

    Connection environments let a base connection's user-attribute value route
    different users to different underlying connections (e.g. dev/staging/prod)
    while sharing the same model.

    When to Use:
    - To add dev/staging/etc. connections that share dialect with an existing
      base connection, before wiring up which user-attribute values select each
      one with `omni_update_connection_environment`.

    When NOT to Use:
    - To change which user-attribute values select an existing environment
      connection — use `omni_update_connection_environment`.
    - To set the base connection's own user-attribute name — use
      `omni_update_connection`.

    Returns:
    A short confirmation listing the created connection-environment IDs. On
    failure, an `Error ...` string.

    Examples:
    - `{"params": {"base_connection_id": "123e4567-e89b-12d3-a456-426614174000", "environment_connection_ids": ["223e4567-e89b-12d3-a456-426614174001", "323e4567-e89b-12d3-a456-426614174002"]}}`

    Error Handling:
    400 means an invalid UUID, or one of the environment connection IDs is not
    valid for this base connection (wrong dialect, already used, or is the base
    connection itself); 404 means the base connection does not exist.
    """
    try:
        body = {
            "baseConnectionId": params.base_connection_id,
            "environmentConnectionIds": ",".join(params.environment_connection_ids),
        }
        payload = await get_client().request_json("POST", "/v1/connection-environments", json_body=body)
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        data = result.get("data") or []
        ids = ", ".join(f"`{item.get('id', 'unknown')}`" for item in data if isinstance(item, dict))
        return (
            f"Created {len(data)} connection environment(s) for base connection "
            f"`{params.base_connection_id}`: {ids or 'none reported'}."
        )
    except Exception as exc:
        return handle_api_error(exc)


class UpdateConnectionEnvironmentInput(BaseModel):
    """Input for `omni_update_connection_environment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_environment_id: str = Field(..., min_length=1, description="The connection environment's UUID.")
    user_attribute_values: list[str] = Field(
        ...,
        min_length=1,
        description=(
            'User attribute values that select this connection environment (e.g. ["dev", "staging"]). '
            "Must be unique across all environments for the same base connection. Sent to the API as a "
            "comma-separated list."
        ),
    )

    @field_validator("user_attribute_values")
    @classmethod
    def _no_blank_values(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("user_attribute_values must not contain empty strings")
        return value


@mcp.tool(
    name="omni_update_connection_environment",
    annotations=ToolAnnotations(
        title="Update Connection Environment",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_connection_environment(params: UpdateConnectionEnvironmentInput) -> str:
    """Set which user-attribute values route to a specific connection environment.

    Works together with the base connection's own user-attribute name and default
    values (set via `omni_update_connection`): the base connection specifies which
    attribute to evaluate, and each environment connection specifies which values
    of that attribute trigger its use.

    When to Use:
    - After creating environment connections, to wire up which attribute values
      (e.g. `dev`, `staging`) select each one.

    When NOT to Use:
    - To create the environment connections themselves — use
      `omni_create_connection_environments` first.

    Returns:
    A short confirmation of the connection environment ID and the values now
    associated with it. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_environment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "user_attribute_values": ["dev", "staging"]}}`

    Error Handling:
    400 means invalid JSON/UUID, or one of the values is already in use by
    another environment on the same base connection; 404 means the connection
    environment does not exist.
    """
    try:
        body = {"userAttributeValues": ",".join(params.user_attribute_values)}
        await get_client().request_json(
            "PUT",
            f"/v1/connection-environments/{_quote(params.connection_environment_id)}",
            json_body=body,
        )
        values = ", ".join(params.user_attribute_values)
        return f"Updated connection environment `{params.connection_environment_id}` — values: {values}."
    except Exception as exc:
        return handle_api_error(exc)


class DeleteConnectionEnvironmentInput(BaseModel):
    """Input for `omni_delete_connection_environment`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_environment_id: str = Field(..., min_length=1, description="The connection environment's UUID.")


@mcp.tool(
    name="omni_delete_connection_environment",
    annotations=ToolAnnotations(
        title="Delete Connection Environment",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_connection_environment(params: DeleteConnectionEnvironmentInput) -> str:
    """Delete a connection environment, removing its base-connection association.

    Requires Connection Admin or Organization Admin permissions. This does not
    delete the underlying environment connection itself, only its role as an
    environment for the base connection.

    When to Use:
    - To retire a dev/staging/etc. environment mapping that is no longer needed.

    When NOT to Use:
    - To delete the underlying connection — use `omni_delete_connection`.

    Returns:
    A short confirmation that the connection environment was deleted. On
    failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_environment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    403 means the caller lacks sufficient permissions; 404 means the connection
    environment does not exist.
    """
    try:
        await get_client().request_json(
            "DELETE", f"/v1/connection-environments/{_quote(params.connection_environment_id)}"
        )
        return f"Deleted connection environment `{params.connection_environment_id}`."
    except Exception as exc:
        return handle_api_error(exc)


# --------------------------------------------------------------------------- #
# Schema refresh schedules
# --------------------------------------------------------------------------- #


class ListSchemaRefreshSchedulesInput(BaseModel):
    """Input for `omni_list_schema_refresh_schedules`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="The connection's UUID.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="`markdown` for a table, `json` for the raw schedule records."
    )


@mcp.tool(
    name="omni_list_schema_refresh_schedules",
    annotations=ToolAnnotations(
        title="List Schema Refresh Schedules",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_list_schema_refresh_schedules(params: ListSchemaRefreshSchedulesInput) -> str:
    """List the schema refresh schedules configured for a connection.

    Requires Connection Admin permissions for the connection. Each schedule uses
    a 6-field AWS EventBridge cron expression (`minute hour day-of-month month
    day-of-week year`) plus an IANA timezone to define when the connection's
    schema is refreshed. The API does not paginate this endpoint.

    When to Use:
    - To audit or find the ID of an existing schedule before updating/deleting
      it.

    When NOT to Use:
    - To create a new schedule — use `omni_create_schema_refresh_schedule`.

    Returns:
    A markdown table (Schedule ID, Cron, Timezone, Description, Hard Refresh,
    Disabled At, Created, Updated) or the raw JSON schedule records when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"}}`

    Error Handling:
    401 means missing/invalid authentication; 403 means the caller lacks
    Connection Admin permissions for the connection.
    """
    try:
        payload = await get_client().request_json("GET", f"/v1/connections/{_quote(params.connection_id)}/schedules")
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        schedules = _mask_sensitive(body.get("schedules") or [])

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"count": len(schedules), "schedules": schedules}))

        rows = [_schedule_row(item) for item in schedules]
        lines = [
            f"# Schema Refresh Schedules for `{params.connection_id}` ({len(rows)})",
            "",
            markdown_table(rows),
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


class GetSchemaRefreshScheduleInput(BaseModel):
    """Input for `omni_get_schema_refresh_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="The connection's UUID.")
    schedule_id: str = Field(..., min_length=1, description="The schema refresh schedule's UUID.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a summary, `json` for the raw schedule record.",
    )


@mcp.tool(
    name="omni_get_schema_refresh_schedule",
    annotations=ToolAnnotations(
        title="Get Schema Refresh Schedule",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_schema_refresh_schedule(params: GetSchemaRefreshScheduleInput) -> str:
    """Fetch one schema refresh schedule's details.

    Requires Connection Admin permissions for the connection.

    When to Use:
    - To check a schedule's cron expression, timezone, or disabled status before
      editing it.

    When NOT to Use:
    - To list all schedules for a connection — use
      `omni_list_schema_refresh_schedules`.

    Returns:
    A markdown summary or the raw JSON record when `response_format` is `json`.
    On failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "schedule_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    403 means the caller lacks Connection Admin permissions; 404 means the
    schedule does not exist, or belongs to a different connection than
    `connection_id`.
    """
    try:
        payload = await get_client().request_json(
            "GET",
            f"/v1/connections/{_quote(params.connection_id)}/schedules/{_quote(params.schedule_id)}",
        )
        schedule: dict[str, Any] = _mask_sensitive(payload if isinstance(payload, dict) else {})

        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json(schedule))

        lines = ["# Schema Refresh Schedule", "", *_schedule_lines(schedule)]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


class CreateSchemaRefreshScheduleInput(BaseModel):
    """Input for `omni_create_schema_refresh_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="The connection's UUID.")
    schedule: str = Field(
        ...,
        description=(
            "A 6-field AWS EventBridge cron expression: `minute hour day-of-month month day-of-week year`. "
            'Examples: "00 09 * * ? *" (every day at 9:00 AM), "00 18 ? * MON-FRI *" (every weekday at 6:00 '
            'PM), "30 08 1 * ? *" (first day of every month at 8:30 AM).'
        ),
    )
    timezone: str = Field(
        ...,
        min_length=1,
        description="An IANA timezone identifier for when the schedule should run, e.g. `America/New_York`, `Europe/London`, `Asia/Tokyo`.",
    )
    hard_refresh: bool | None = Field(
        default=None,
        description=(
            "Whether the scheduled refresh performs a hard refresh (removes dropped objects) instead of a "
            "soft refresh (additive only). Defaults to `false` on the API."
        ),
    )

    @field_validator("schedule")
    @classmethod
    def _validate_schedule(cls, value: str) -> str:
        return _validate_cron(value)


@mcp.tool(
    name="omni_create_schema_refresh_schedule",
    annotations=ToolAnnotations(
        title="Create Schema Refresh Schedule",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_schema_refresh_schedule(params: CreateSchemaRefreshScheduleInput) -> str:
    """Create a new schema refresh schedule for a connection.

    Requires Connection Admin permissions for the connection. Multiple schedules
    can be created for the same connection.

    When to Use:
    - To schedule automatic schema refreshes (e.g. nightly, weekdays at 6 PM).

    When NOT to Use:
    - To change an existing schedule — use `omni_update_schema_refresh_schedule`.

    Returns:
    A short confirmation with the new schedule's ID and its human-readable
    description. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "schedule": "00 09 * * ? *", "timezone": "America/New_York"}}`
    - `{"params": {"connection_id": "...", "schedule": "00 18 ? * MON-FRI *", "timezone": "Europe/London", "hard_refresh": true}}`

    Error Handling:
    400 means an invalid cron expression or timezone; 401 means missing/invalid
    authentication; 403 means the caller lacks Connection Admin permissions for
    the connection.
    """
    try:
        body: dict[str, Any] = {"schedule": params.schedule, "timezone": params.timezone}
        if params.hard_refresh is not None:
            body["hardRefresh"] = params.hard_refresh
        payload = await get_client().request_json(
            "POST", f"/v1/connections/{_quote(params.connection_id)}/schedules", json_body=body
        )
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        schedule_id = result.get("scheduleId", "unknown")
        description = result.get("description", "")
        return (
            f"Created schema refresh schedule `{schedule_id}` for connection "
            f"`{params.connection_id}` — {description or params.schedule}."
        )
    except Exception as exc:
        return handle_api_error(exc)


class UpdateSchemaRefreshScheduleInput(BaseModel):
    """Input for `omni_update_schema_refresh_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="The connection's UUID.")
    schedule_id: str = Field(..., min_length=1, description="The schema refresh schedule's UUID.")
    schedule: str = Field(
        ...,
        description=(
            "A 6-field AWS EventBridge cron expression: `minute hour day-of-month month day-of-week year`. "
            'Examples: "00 09 * * ? *", "00 18 ? * MON-FRI *", "30 08 1 * ? *".'
        ),
    )
    timezone: str = Field(
        ..., min_length=1, description="An IANA timezone identifier for when the schedule should run."
    )
    hard_refresh: bool | None = Field(
        default=None,
        description=(
            "Whether the scheduled refresh performs a hard refresh (removes dropped objects) instead of a "
            "soft refresh (additive only). Defaults to `false` on the API."
        ),
    )

    @field_validator("schedule")
    @classmethod
    def _validate_schedule(cls, value: str) -> str:
        return _validate_cron(value)


@mcp.tool(
    name="omni_update_schema_refresh_schedule",
    annotations=ToolAnnotations(
        title="Update Schema Refresh Schedule",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_update_schema_refresh_schedule(params: UpdateSchemaRefreshScheduleInput) -> str:
    """Replace the cron schedule and/or timezone for an existing schema refresh schedule.

    Requires Connection Admin permissions for the connection.

    When to Use:
    - To change when an existing schedule runs, or to toggle hard vs. soft
      refresh.

    When NOT to Use:
    - To create a new schedule — use `omni_create_schema_refresh_schedule`.

    Returns:
    A short confirmation with the schedule's ID and new description. On
    failure, an `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "schedule_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "schedule": "00 18 ? * * *", "timezone": "Europe/London"}}`

    Error Handling:
    400 means an invalid cron expression or timezone; 401 means missing/invalid
    authentication; 403 means the caller lacks Connection Admin permissions; 404
    means the schedule or connection does not exist.
    """
    try:
        body: dict[str, Any] = {"schedule": params.schedule, "timezone": params.timezone}
        if params.hard_refresh is not None:
            body["hardRefresh"] = params.hard_refresh
        payload = await get_client().request_json(
            "PUT",
            f"/v1/connections/{_quote(params.connection_id)}/schedules/{_quote(params.schedule_id)}",
            json_body=body,
        )
        result: dict[str, Any] = payload if isinstance(payload, dict) else {}
        description = result.get("description", "")
        return f"Updated schema refresh schedule `{params.schedule_id}` — {description or params.schedule}."
    except Exception as exc:
        return handle_api_error(exc)


class DeleteSchemaRefreshScheduleInput(BaseModel):
    """Input for `omni_delete_schema_refresh_schedule`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, description="The connection's UUID.")
    schedule_id: str = Field(..., min_length=1, description="The schema refresh schedule's UUID.")


@mcp.tool(
    name="omni_delete_schema_refresh_schedule",
    annotations=ToolAnnotations(
        title="Delete Schema Refresh Schedule",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_schema_refresh_schedule(params: DeleteSchemaRefreshScheduleInput) -> str:
    """Delete a schema refresh schedule.

    Requires Connection Admin permissions for the connection. Deleting one
    schedule does not affect other schedules on the same connection.

    When to Use:
    - To remove a schedule that is no longer needed.

    When NOT to Use:
    - To temporarily pause a schedule without deleting it — this API has no
      disable/enable endpoint exposed here; delete and recreate it when needed.

    Returns:
    A short confirmation that the schedule was deleted. On failure, an
    `Error ...` string.

    Examples:
    - `{"params": {"connection_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "schedule_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    401 means missing/invalid authentication; 403 means the caller lacks
    Connection Admin permissions; 404 means the schedule or connection does not
    exist.
    """
    try:
        await get_client().request_json(
            "DELETE",
            f"/v1/connections/{_quote(params.connection_id)}/schedules/{_quote(params.schedule_id)}",
        )
        return f"Deleted schema refresh schedule `{params.schedule_id}` for connection `{params.connection_id}`."
    except Exception as exc:
        return handle_api_error(exc)
