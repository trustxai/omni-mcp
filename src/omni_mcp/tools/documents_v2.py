"""Tools for the documents v2 endpoints — the create → draft → patch → publish lifecycle.

The v2 API replaces v1's one-shot `PUT /v1/documents/{id}` with an explicit
lifecycle: `omni_create_document_v2` (create + publish in one call),
`omni_get_document_state` (read the published state),
`omni_create_draft_and_patch_document` (open a draft on a published document and
apply the first patch), `omni_patch_document_draft` (further edits),
`omni_get_document_draft_state` (read the draft back),
`omni_publish_document_draft` (promote the main draft live) and
`omni_rename_document_identifier` (rename the URL slug, outside the draft flow).

Tiles are addressed by their record key in `queryPresentations.data` (`"1"`,
`"2"`, …). The server owns internal tile identity, so callers never send
`miniUuid` — this module drops it from outgoing payloads so a verbatim
round-trip of a read is always safe.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, to_json, truncate_result
from omni_mcp.server import mcp

#: One line on what this module covers, for the generated README catalogue.
MODULE_SUMMARY = "Documents v2 — create, draft, patch, publish, read state"

#: A single patch may carry at most this many non-null query presentations.
MAX_QUERY_PRESENTATIONS_PER_PATCH = 48

#: Tile fields the server owns. Dropped from outgoing payloads so that feeding a
#: read response straight back as a patch body never fights the server for tile
#: identity.
SERVER_OWNED_TILE_FIELDS = frozenset({"miniUuid"})

#: Record keys (`queryPresentations.data` keys and `order` entries).
RECORD_KEY_PATTERN = re.compile(r"^[1-9][0-9]*$")

#: Values accepted by `settings.runQueriesOn` (plus `null`).
RUN_QUERIES_ON_VALUES = ("current-page", "all-pages")

#: Keys accepted inside the `settings` object.
SETTINGS_KEYS = frozenset({"crossfilterEnabled", "customText", "facetFilters", "refreshInterval", "runQueriesOn"})

#: Values accepted by a tile's required `type` field.
QUERY_PRESENTATION_TYPES = (
    "blank",
    "csv",
    "query",
    "dataset",
    "spreadsheet",
    "sql",
    "dbt",
    "query-view",
    "linked",
    "app",
)

#: How many tiles / controls a markdown summary lists before it stops.
SUMMARY_ITEM_LIMIT = 30


def _validate_query_presentations(value: dict[str, Any]) -> dict[str, Any]:
    """Enforce the record-key grammar, the tile `type` enum and the 48-tile cap."""
    unknown = set(value) - {"data", "order"}
    if unknown:
        raise ValueError(f"queryPresentations accepts only `data` and `order`; got: {', '.join(sorted(unknown))}.")

    data = value.get("data")
    if data is not None:
        if not isinstance(data, Mapping):
            raise ValueError('queryPresentations.data must be an object keyed by record key ("1", "2", …).')
        non_null = sum(1 for tile in data.values() if tile is not None)
        if non_null > MAX_QUERY_PRESENTATIONS_PER_PATCH:
            raise ValueError(
                f"A single patch may carry at most {MAX_QUERY_PRESENTATIONS_PER_PATCH} query presentations "
                f"(got {non_null}). Split the change across several patches on the same draft."
            )
        for key, tile in data.items():
            if not RECORD_KEY_PATTERN.match(str(key)):
                raise ValueError(
                    f'queryPresentations.data key "{key}" is not a record key: use a positive-integer string '
                    'such as "1" or "2".'
                )
            if tile is None:
                continue
            if not isinstance(tile, Mapping):
                raise ValueError(f'queryPresentations.data["{key}"] must be an object or null (null deletes the tab).')
            tile_type = tile.get("type")
            if tile_type is None:
                raise ValueError(
                    f'queryPresentations.data["{key}"] is missing the required `type` field '
                    f"(one of: {', '.join(QUERY_PRESENTATION_TYPES)})."
                )
            if tile_type not in QUERY_PRESENTATION_TYPES:
                raise ValueError(
                    f'queryPresentations.data["{key}"].type must be one of: {", ".join(QUERY_PRESENTATION_TYPES)}; '
                    f"got {tile_type!r}."
                )

    order = value.get("order")
    if order is not None:
        if not isinstance(order, list):
            raise ValueError('queryPresentations.order must be a list of record keys, e.g. ["1", "2"].')
        for key in order:
            if not RECORD_KEY_PATTERN.match(str(key)):
                raise ValueError(
                    f'queryPresentations.order entry "{key}" is not a record key: use a positive-integer string. '
                    "It is a tile record key, not a positional index."
                )
    return value


def _validate_controls(value: dict[str, Any]) -> dict[str, Any]:
    """Check the `controls` envelope; the control grammar itself is server-validated."""
    unknown = set(value) - {"data", "order"}
    if unknown:
        raise ValueError(f"controls accepts only `data` and `order`; got: {', '.join(sorted(unknown))}.")
    data = value.get("data")
    if data is not None and not isinstance(data, Mapping):
        raise ValueError("controls.data must be an object keyed by control ID (null on a key deletes that control).")
    if isinstance(data, Mapping):
        for key, control in data.items():
            if control is not None and not isinstance(control, Mapping):
                raise ValueError(f'controls.data["{key}"] must be an object or null (null deletes the control).')
    order = value.get("order")
    if order is not None and not isinstance(order, list):
        raise ValueError("controls.order must be a list of control IDs.")
    return value


def _validate_settings(value: dict[str, Any]) -> dict[str, Any]:
    """Reject unknown settings keys and a bad `runQueriesOn` before the API does."""
    unknown = set(value) - SETTINGS_KEYS
    if unknown:
        raise ValueError(
            f"settings accepts only {', '.join(sorted(SETTINGS_KEYS))}; got: {', '.join(sorted(unknown))}."
        )
    run_queries_on = value.get("runQueriesOn")
    if run_queries_on is not None and run_queries_on not in RUN_QUERIES_ON_VALUES:
        raise ValueError(
            f"settings.runQueriesOn must be one of {', '.join(RUN_QUERIES_ON_VALUES)} or null; got {run_queries_on!r}."
        )
    custom_text = value.get("customText")
    if custom_text is not None and not isinstance(custom_text, Mapping):
        raise ValueError("settings.customText must be an object with `queryError` / `queryNoResults`, or null.")
    return value


QueryPresentationsPatch = Annotated[dict[str, Any], AfterValidator(_validate_query_presentations)]
ControlsPatch = Annotated[dict[str, Any], AfterValidator(_validate_controls)]
SettingsPatch = Annotated[dict[str, Any], AfterValidator(_validate_settings)]

QUERY_PRESENTATIONS_DESCRIPTION = (
    'Tabs/tiles keyed by record key: `{"data": {"1": {…tile…}}, "order": ["1", "2"]}`. `data` keys are '
    'positive-integer strings ("1", "2", …) and are shallow-merged — omitted keys are untouched and '
    '`"<key>": null` deletes that tab; at most 48 non-null entries per patch. A tile requires `type` '
    "(blank | csv | query | dataset | spreadsheet | sql | dbt | query-view | linked | app) and accepts "
    "`name`, `subTitle`, `description`, `sourceQueryPresentationKey` (the source tile's record key, "
    "`linked` tabs only), `topicName`, `isSql`, `prefersChart`, `automaticVis`, `filterOrder`, "
    "`editingModelObjectName` / `editingModelObjectNameChange` (dataset / query-view tabs only), "
    "`fileUploadId` (csv / spreadsheet tabs only), `query`, `visConfig`, `resultConfig` and `aiConfig`. "
    "`order`, when present, replaces the tab order. Never send `miniUuid` — the server owns tile identity "
    "and this tool strips it."
)

CONTROLS_DESCRIPTION = (
    'Dashboard filters/controls: `{"data": {"<controlId>": {…control…}}, "order": ["<controlId>"]}`. '
    '`data` is shallow-merged by control ID — omitted IDs are untouched and `"<id>": null` deletes that '
    "control. The control grammar (control type, field bindings, filter values, and the recursive "
    "composite-filter grammar for multi-field filters) is validated by the API on apply. `order`, when "
    "present, replaces the control order."
)

SETTINGS_DESCRIPTION = (
    "Document settings, shallow-merged per key: `crossfilterEnabled` (bool), `customText` "
    "(`{queryError, queryNoResults}` or null), `facetFilters` (bool), `refreshInterval` (seconds; null "
    "disables auto-refresh) and `runQueriesOn` (`current-page`, `all-pages` or null)."
)

CONTAINERS_DESCRIPTION = (
    "Container layout array (grid / stack / page / reference containers, recursively nested). When present "
    "it FULLY REPLACES the existing layout; `instanceKey` / `referenceKey` values round-trip unchanged and "
    "the API validates the full structure on apply. A workbook-only document rejects a patch that carries "
    "no containers (422)."
)

BODY_OVERRIDE_DESCRIPTION = (
    "Escape hatch: the complete request body, sent verbatim instead of the one assembled from the other "
    "fields. Use it to replay a `omni_get_document_state` / `omni_get_document_draft_state` response "
    "unchanged, or to send an explicit null (for example `description: null` or "
    "`settings.refreshInterval: null`), which the per-field arguments cannot express because a null field "
    "is omitted from the payload."
)


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drop `None` values — an omitted field leaves that part of the document untouched."""
    return {key: value for key, value in values.items() if value is not None}


def _clean_query_presentations(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip server-owned tile fields so a verbatim read round-trip is safe to send."""
    if value is None:
        return None
    payload = dict(value)
    data = payload.get("data")
    if isinstance(data, Mapping):
        cleaned: dict[str, Any] = {}
        for key, tile in data.items():
            if isinstance(tile, Mapping):
                cleaned[str(key)] = {name: item for name, item in tile.items() if name not in SERVER_OWNED_TILE_FIELDS}
            else:
                cleaned[str(key)] = tile
        payload["data"] = cleaned
    return payload


def _document_path(document_id: str) -> str:
    return f"/v2/documents/{quote(document_id, safe='')}"


def _draft_path(document_id: str, draft_id: str) -> str:
    return f"{_document_path(document_id)}/draft/{quote(draft_id, safe='')}"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ordered_keys(data: Mapping[str, Any], order: Any) -> list[str]:
    """Keys in display order, with any key missing from `order` appended."""
    ordered = [str(key) for key in order if str(key) in data] if isinstance(order, list) else []
    return ordered + [str(key) for key in data if str(key) not in ordered]


def _tile_lines(query_presentations: Mapping[str, Any]) -> list[str]:
    data = _as_dict(query_presentations.get("data"))
    keys = _ordered_keys(data, query_presentations.get("order"))
    lines: list[str] = []
    for key in keys[:SUMMARY_ITEM_LIMIT]:
        tile = data.get(key)
        if not isinstance(tile, Mapping):
            lines.append(f"- `{key}` — _empty slot_")
            continue
        name = tile.get("name") or "_unnamed_"
        details = [str(tile.get("type", "unknown"))]
        topic = tile.get("topicName")
        if topic:
            details.append(f"topic `{topic}`")
        source = tile.get("sourceQueryPresentationKey")
        if source:
            details.append(f"linked to tile `{source}`")
        upload = tile.get("fileUploadId")
        if upload:
            details.append(f"upload `{upload}`")
        lines.append(f"- `{key}` — **{name}** ({'; '.join(details)})")
    remaining = len(keys) - len(lines)
    if remaining > 0:
        lines.append(f'- _… {remaining:,} more tile(s) not shown; use `response_format="json"` for the full state._')
    return lines or ["_No tiles._"]


def _control_lines(controls: Mapping[str, Any]) -> list[str]:
    data = _as_dict(controls.get("data"))
    keys = _ordered_keys(data, controls.get("order"))
    lines: list[str] = []
    for key in keys[:SUMMARY_ITEM_LIMIT]:
        control = data.get(key)
        label: Any = None
        if isinstance(control, Mapping):
            label = control.get("label") or control.get("field") or control.get("type")
        lines.append(f"- `{key}`" + (f" — {label}" if label else ""))
    remaining = len(keys) - len(lines)
    if remaining > 0:
        lines.append(f"- _… {remaining:,} more control(s) not shown._")
    return lines or ["_No controls._"]


def _settings_lines(settings: Mapping[str, Any]) -> list[str]:
    refresh = settings.get("refreshInterval")
    refresh_text = f"{refresh:g}s" if isinstance(refresh, int | float) and not isinstance(refresh, bool) else "disabled"
    lines = [
        f"- Crossfilter: {'on' if settings.get('crossfilterEnabled') else 'off'}",
        f"- Facet filters: {'on' if settings.get('facetFilters') else 'off'}",
        f"- Refresh interval: {refresh_text}",
        f"- Run queries on: {settings.get('runQueriesOn') or 'default'}",
    ]
    custom_text = settings.get("customText")
    if isinstance(custom_text, Mapping) and custom_text:
        lines.append(f"- Custom text overrides: {', '.join(sorted(str(key) for key in custom_text))}")
    return lines


def _render_state(*, state: dict[str, Any], fmt: ResponseFormat, document_id: str, draft_id: str | None) -> str:
    """Render a `DocumentsV2ReadResponse` as markdown, or the raw state as JSON."""
    if fmt is ResponseFormat.JSON:
        return truncate_result(to_json({"documentId": document_id, "draftId": draft_id, "state": state}))

    query_presentations = _as_dict(state.get("queryPresentations"))
    controls = _as_dict(state.get("controls"))
    settings = _as_dict(state.get("settings"))
    containers = state.get("containers")
    tile_count = len(_as_dict(query_presentations.get("data")))
    control_count = len(_as_dict(controls.get("data")))

    lines = [
        f"# {state.get('name') or 'Untitled document'}",
        "",
        f"- Document: `{document_id}`",
        f"- State: **draft** (`{draft_id}`)" if draft_id else "- State: **published**",
        f"- Description: {state.get('description') or '_none_'}",
        f"- Base model ID: `{state.get('modelId', 'N/A')}`",
        f"- Workbook model ID: `{state.get('workbookModelId', 'N/A')}`",
        "",
        f"## Tiles ({tile_count:,})",
        "",
    ]
    lines.extend(_tile_lines(query_presentations))
    lines.extend(["", f"## Controls ({control_count:,})", ""])
    lines.extend(_control_lines(controls))
    lines.extend(["", "## Settings", ""])
    if settings:
        lines.extend(_settings_lines(settings))
    else:
        lines.append("_Workbook-only document — no dashboard settings yet._")
    lines.extend(["", "## Layout", ""])
    if isinstance(containers, list):
        lines.append(f"{len(containers):,} top-level container(s).")
    else:
        lines.append("_Workbook-only document — no dashboard layout yet (patching one requires `containers`)._")
    lines.extend(
        [
            "",
            "## Full state",
            "",
            "Round-trippable: submit this verbatim as a draft patch `body`.",
            "",
            "```json",
            to_json(state),
            "```",
        ]
    )
    return truncate_result("\n".join(lines))


class CreateDocumentV2Input(BaseModel):
    """Input for `omni_create_document_v2`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(
        description=(
            "Base workbook model the document is built on (UUID) — a SHARED model, or a SHARED_EXTENSION with "
            "`allowAsWorkbookBase = true`."
        )
    )
    name: str = Field(min_length=1, max_length=254, description="Document name (1-254 characters).")
    identifier: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$",
        description=(
            "Identifier (URL slug) for the new document. Must be unique within the organization; auto-generated "
            "when omitted. Lowercase letters, numbers, hyphens and underscores only; cannot start or end with a "
            "hyphen or underscore."
        ),
    )
    description: str | None = Field(default=None, description="Document description.")
    folder_id: str | None = Field(
        default=None,
        description=(
            "Folder to create the document in (UUID). When omitted, defaults to the caller's personal "
            '"My documents", which requires permission to save personal content — otherwise the request is rejected.'
        ),
    )
    summary: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            'Note describing the create, written to the history audit trail. Omni auto-fills "Created document" '
            "when omitted."
        ),
    )
    query_presentations: QueryPresentationsPatch | None = Field(
        default=None,
        description=(
            QUERY_PRESENTATIONS_DESCRIPTION
            + ' A new document starts with one empty seed tile at key "1" — write to "1" (or send it as null) '
            "to replace the seed."
        ),
    )
    controls: ControlsPatch | None = Field(default=None, description=CONTROLS_DESCRIPTION)
    settings: SettingsPatch | None = Field(default=None, description=SETTINGS_DESCRIPTION)
    containers: list[dict[str, Any]] | None = Field(default=None, description=CONTAINERS_DESCRIPTION)
    body: dict[str, Any] | None = Field(default=None, description=BODY_OVERRIDE_DESCRIPTION)


class GetDocumentStateInput(BaseModel):
    """Input for `omni_get_document_state`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        min_length=1,
        description="The document's URL slug (e.g. `abc123`) or its canonical workbook UUID.",
    )
    pretty: bool | None = Field(
        default=None,
        description=(
            "Ask the API to indent the raw JSON it returns. Cosmetic only — this tool re-formats the payload "
            "either way."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a summary plus the full state, `json` for the raw state payload.",
    )


class CreateDraftAndPatchDocumentInput(BaseModel):
    """Input for `omni_create_draft_and_patch_document`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        min_length=1,
        description="The published document's URL slug (e.g. `abc123`) or its canonical workbook UUID.",
    )
    branch_id: str | None = Field(
        default=None,
        description=(
            "Branch the draft is created on (UUID). Omit for a draft on the main (unpublished) workspace. A "
            "branch-attached draft is published by merging its branch, not by `omni_publish_document_draft`."
        ),
    )
    model_id: str | None = Field(
        default=None,
        description=(
            "Immutable base model identifier (UUID). Must match the document's current base model or the API "
            "returns 400."
        ),
    )
    workbook_model_id: str | None = Field(
        default=None,
        description=(
            "Immutable workbook model identifier (UUID). Must match the document's current workbook model or the "
            "API returns 400."
        ),
    )
    name: str | None = Field(
        default=None, min_length=1, max_length=254, description="Document name (1-254 characters)."
    )
    description: str | None = Field(default=None, description="Document description.")
    summary: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Note describing what this patch changes, written to the history audit trail. Omni auto-generates one "
            "from the updated sections when omitted."
        ),
    )
    query_presentations: QueryPresentationsPatch | None = Field(
        default=None, description=QUERY_PRESENTATIONS_DESCRIPTION
    )
    controls: ControlsPatch | None = Field(default=None, description=CONTROLS_DESCRIPTION)
    settings: SettingsPatch | None = Field(default=None, description=SETTINGS_DESCRIPTION)
    containers: list[dict[str, Any]] | None = Field(default=None, description=CONTAINERS_DESCRIPTION)
    body: dict[str, Any] | None = Field(default=None, description=BODY_OVERRIDE_DESCRIPTION)


class PublishDocumentDraftInput(BaseModel):
    """Input for `omni_publish_document_draft`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        min_length=1,
        description="The published document's URL slug (e.g. `abc123`) or its canonical workbook UUID.",
    )


class GetDocumentDraftStateInput(BaseModel):
    """Input for `omni_get_document_draft_state`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        min_length=1,
        description="The published document's URL slug (e.g. `abc123`) or its canonical workbook UUID.",
    )
    draft_id: str = Field(
        min_length=1,
        description="The draft workbook identifier returned by `omni_create_draft_and_patch_document`.",
    )
    pretty: bool | None = Field(
        default=None,
        description=(
            "Ask the API to indent the raw JSON it returns. Cosmetic only — this tool re-formats the payload "
            "either way."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a summary plus the full state, `json` for the raw state payload.",
    )


class PatchDocumentDraftInput(BaseModel):
    """Input for `omni_patch_document_draft`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        min_length=1,
        description="The published document's URL slug (e.g. `abc123`) or its canonical workbook UUID.",
    )
    draft_id: str = Field(min_length=1, description="The draft workbook identifier the patch is applied to.")
    model_id: str | None = Field(
        default=None,
        description=(
            "Immutable base model identifier (UUID). Must match the document's current base model or the API "
            "returns 400."
        ),
    )
    workbook_model_id: str | None = Field(
        default=None,
        description=(
            "Immutable workbook model identifier (UUID). Must match the document's current workbook model or the "
            "API returns 400."
        ),
    )
    name: str | None = Field(
        default=None, min_length=1, max_length=254, description="Document name (1-254 characters)."
    )
    description: str | None = Field(default=None, description="Document description.")
    summary: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Note describing what this patch changes, written to the history audit trail. Omni auto-generates one "
            "from the updated sections when omitted."
        ),
    )
    query_presentations: QueryPresentationsPatch | None = Field(
        default=None, description=QUERY_PRESENTATIONS_DESCRIPTION
    )
    controls: ControlsPatch | None = Field(default=None, description=CONTROLS_DESCRIPTION)
    settings: SettingsPatch | None = Field(default=None, description=SETTINGS_DESCRIPTION)
    containers: list[dict[str, Any]] | None = Field(default=None, description=CONTAINERS_DESCRIPTION)
    body: dict[str, Any] | None = Field(default=None, description=BODY_OVERRIDE_DESCRIPTION)


class RenameDocumentIdentifierInput(BaseModel):
    """Input for `omni_rename_document_identifier`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(
        min_length=1,
        description="The document to rename — its current URL slug (e.g. `abc123`) or its canonical workbook UUID.",
    )
    identifier: str = Field(
        min_length=2,
        max_length=48,
        pattern=r"^[a-z0-9_-]+$",
        description=(
            "The new identifier slug: 2-48 characters, lowercase letters (a-z), digits (0-9), hyphens (-) and "
            "underscores (_) only, not a reserved value, and unused by any other document in the organization."
        ),
    )


@mcp.tool(
    name="omni_create_document_v2",
    annotations=ToolAnnotations(
        title="Create Document (v2)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_document_v2(params: CreateDocumentV2Input) -> str:
    """Create a brand-new document and publish it in one call.

    `POST /v2/documents`. Takes creation metadata (`model_id`, `name`, and
    optional `identifier` / `description` / `folder_id`) plus the same content
    slice as a draft patch: `query_presentations`, `controls`, `settings` and
    `containers`. A new document starts with a single empty seed tile at record
    key "1" — write to "1" (or send it as null) to replace the seed. Because
    this is the first publish of brand-new content, it is not subject to the
    organization's `requirePullRequestToPublish` policy, which gates edits to
    existing content.

    This is step 1 of the v2 lifecycle: create → `omni_get_document_state` →
    `omni_create_draft_and_patch_document` → `omni_patch_document_draft` →
    `omni_publish_document_draft` → `omni_rename_document_identifier`. It
    replaces the v1 one-shot `PUT` behind the v1 `documents` tools; the v1
    tools still own listing, searching and deleting documents.

    When to Use:
    - To create a new dashboard/workbook from scratch, optionally with tiles,
      controls, settings and a layout in the same request.
    - When you want the content live immediately without a draft round-trip.

    When NOT to Use:
    - To edit an existing document — use `omni_create_draft_and_patch_document`
      then `omni_publish_document_draft`.
    - To change a published document's slug — use
      `omni_rename_document_identifier`.
    - To copy or delete documents, or to list/search them — those live in the
      v1 `documents` tools.

    Returns:
    A confirmation naming the new document, its identifier and description, or
    an `Error ...` string on failure.

    Examples:
    - Minimal: `{"params": {"model_id": "b81e4679-1234-4abc-9def-0123456789ab", "name": "Q2 Revenue"}}`
    - With one tile and a custom slug: `{"params": {"model_id": "b81e4679-1234-4abc-9def-0123456789ab",
      "identifier": "q2-revenue", "name": "Q2 Revenue", "description": "Quarterly revenue review",
      "query_presentations": {"data": {"1": {"type": "query", "name": "Revenue by month",
      "topicName": "order_items", "prefersChart": true, "query": {"fields": ["order_items.created_at[month]",
      "order_items.sale_price_sum"]}}}}, "settings": {"crossfilterEnabled": true, "refreshInterval": 3600}}}`
    - Verbatim body: `{"params": {"model_id": "ignored-when-body-is-set", "name": "ignored",
      "body": {"modelId": "b81e4679-1234-4abc-9def-0123456789ab", "name": "Q2 Revenue"}}}`

    Error Handling:
    400 — schema validation failed: an unknown top-level field, a length cap
    exceeded (`name must be 254 characters or fewer`), more than 48 query
    presentations in one patch, an `identifier` already in use, or a
    `fileUploadId` on a tab that is not `csv`/`spreadsheet` (or an upload from
    another organization). 401 — the API key is missing or invalid. 403 — the
    caller lacks permission to create a document on this model (needs at least
    the model's editor-level access, and permission to save personal content
    when `folder_id` is omitted). 404 — the base model or branch does not exist.
    """
    try:
        payload_body = params.body
        if payload_body is None:
            payload_body = _compact(
                {
                    "modelId": params.model_id,
                    "name": params.name,
                    "identifier": params.identifier,
                    "description": params.description,
                    "folderId": params.folder_id,
                    "summary": params.summary,
                    "queryPresentations": _clean_query_presentations(params.query_presentations),
                    "controls": params.controls,
                    "settings": params.settings,
                    "containers": params.containers,
                }
            )
        payload = await get_client().request_json("POST", "/v2/documents", json_body=payload_body)
        created = _as_dict(payload)
        identifier = created.get("identifier", "unknown")
        name = created.get("name", params.name)
        description = created.get("description")
        suffix = f" — {description}" if description else ""
        return truncate_result(
            f"Created and published document **{name}** (identifier `{identifier}`){suffix}. "
            f'Read it back with `omni_get_document_state` (`document_id="{identifier}"`); edit it with '
            "`omni_create_draft_and_patch_document`."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_document_state",
    annotations=ToolAnnotations(
        title="Get Document State (v2)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_document_state(params: GetDocumentStateInput) -> str:
    """Read a document's published state (tiles, controls, settings, layout).

    `GET /v2/documents/{documentId}`. The response is round-trippable: submit it
    verbatim as the `body` of `omni_create_draft_and_patch_document` or
    `omni_patch_document_draft`. Tiles come back keyed by a stable record key
    ("1", "2", …) which the server uses to identify existing tiles on a later
    patch, so no other identifier needs tracking; control IDs and container
    `instanceKey` / `referenceKey` values also round-trip unchanged.

    A workbook-only document (no dashboard layout yet) returns only the
    workbook-scoped fields (`name`, `description`, `queryPresentations`); the
    dashboard-scoped `containers`, `controls` and `settings` are omitted until a
    layout exists.

    When to Use:
    - Before editing, to learn the current record keys, control IDs and layout.
    - To capture a body you can edit and send back through the draft flow.
    - To confirm what a publish actually made live.

    When NOT to Use:
    - To read unpublished work — use `omni_get_document_draft_state` with the
      `draftIdentifier` returned by `omni_create_draft_and_patch_document`.
    - To list or search documents, or to read document metadata such as owner,
      folder and labels — those are the v1 `documents` tools.

    Returns:
    A markdown summary (name, document ID, published/draft status, base and
    workbook model IDs, tile keys with names and types, control IDs, settings
    highlights and container count) followed by the full JSON state; the raw
    state alone when `response_format` is `json`. On failure, an `Error ...`
    string.

    Examples:
    - By slug: `{"params": {"document_id": "q2-revenue"}}`
    - Raw state to edit and replay: `{"params": {"document_id": "q2-revenue", "response_format": "json"}}`

    Error Handling:
    401 — the API key is missing or invalid. 403 — the caller cannot read this
    document (needs at least viewer access). 404 — no such document in this
    instance. 422 — the document cannot be read as a dashboard: a classic-layout
    dashboard (upgrade it to the advanced layout first) or an app document.
    """
    try:
        query: dict[str, Any] = {}
        if params.pretty is not None:
            query["pretty"] = params.pretty
        payload = await get_client().request_json("GET", _document_path(params.document_id), params=query or None)
        return _render_state(
            state=_as_dict(payload), fmt=params.response_format, document_id=params.document_id, draft_id=None
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_draft_and_patch_document",
    annotations=ToolAnnotations(
        title="Create Draft and Patch Document (v2)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_draft_and_patch_document(params: CreateDraftAndPatchDocumentInput) -> str:
    """Open a draft on a published document and apply the first patch.

    `PATCH /v2/documents/{documentId}/draft`. Creates a new draft on the
    published document and applies the patch — nothing goes live here. The
    response carries the new `draftIdentifier` for the follow-up calls. Pass
    `branch_id` to attach the draft to a branch; omit it for a draft on the main
    (unpublished) workspace.

    Step 3 of the lifecycle: create → get state → **create draft and patch** →
    `omni_patch_document_draft` (optional, repeatable) →
    `omni_publish_document_draft`. A document may have only one main draft at a
    time: if one already exists the API returns 409 — patch that draft or
    publish it instead. This replaces v1's `clearExistingDraft` flag, which does
    not exist in v2, and the v1 one-shot `PUT` in the `documents` tools maps onto
    this create-draft → patch → publish sequence. There is also no `narrative`
    field: attach an AI-style summary as a `linked` tile whose
    `sourceQueryPresentationKey` is the source tile's record key.

    Tiles are addressed by record key: a new key creates a tile, an existing key
    with identical content is a no-op, an existing key with changed content
    updates the tile, and `null` deletes it. `linked` tiles always re-resolve.
    `queryPresentations.data`, `controls.data` and `settings` are shallow-merged;
    `containers`, when present, fully replaces the layout.

    When to Use:
    - To start editing an existing published document.
    - To bind an upload as a Source tab: read `workbookModelId` with
      `omni_get_document_state`, create the upload against it, then patch a
      `csv` / `spreadsheet` tile carrying that `fileUploadId`, then publish.
    - To edit on a branch (pass `branch_id`), for organizations that require a
      pull request to publish.

    When NOT to Use:
    - When a draft already exists — patch it with `omni_patch_document_draft`
      (this endpoint would return 409).
    - To create a document (`omni_create_document_v2`) or to publish
      (`omni_publish_document_draft`).
    - To rename the document's slug — that is live and outside the draft flow
      (`omni_rename_document_identifier`).

    Returns:
    A confirmation with the draft identifier and the document it targets, or an
    `Error ...` string on failure.

    Examples:
    - Rename and tweak a setting: `{"params": {"document_id": "q2-revenue", "name": "Q2 Revenue (final)",
      "summary": "Rename and enable cross-filtering", "settings": {"crossfilterEnabled": true}}}`
    - Add a tile and delete another: `{"params": {"document_id": "q2-revenue", "query_presentations":
      {"data": {"3": {"type": "query", "name": "Orders by region", "topicName": "order_items",
      "query": {"fields": ["users.state", "order_items.count"]}}, "4": null}}}}`
    - Attach an AI summary linked to tile "2": `{"params": {"document_id": "q2-revenue",
      "query_presentations": {"data": {"5": {"type": "linked", "name": "Summary",
      "sourceQueryPresentationKey": "2"}}}}}`
    - On a branch: `{"params": {"document_id": "q2-revenue", "branch_id": "8b1f0a2c-1234-4abc-9def-0123456789ab",
      "name": "Q2 Revenue (branch edit)"}}`

    Error Handling:
    400 — schema validation failed: an unknown top-level field, a length cap
    exceeded, more than 48 query presentations in one patch, a `model_id` /
    `workbook_model_id` that does not match the document's current models, or a
    `fileUploadId` on a tab that is not `csv`/`spreadsheet`. 401 — the API key is
    missing or invalid. 403 — the caller lacks permission to update this
    document. 404 — the document or branch does not exist. 409 — the target is
    not a published document, or a main draft already exists (patch or publish
    it first). 422 — the document cannot satisfy the patch: a classic-layout
    dashboard, an app document, or a workbook-only document patched without a
    non-empty `containers` payload.
    """
    try:
        payload_body = params.body
        if payload_body is None:
            payload_body = _compact(
                {
                    "branchId": params.branch_id,
                    "modelId": params.model_id,
                    "workbookModelId": params.workbook_model_id,
                    "name": params.name,
                    "description": params.description,
                    "summary": params.summary,
                    "queryPresentations": _clean_query_presentations(params.query_presentations),
                    "controls": params.controls,
                    "settings": params.settings,
                    "containers": params.containers,
                }
            )
        payload = await get_client().request_json(
            "PATCH", f"{_document_path(params.document_id)}/draft", json_body=payload_body
        )
        result = _as_dict(payload)
        draft_identifier = result.get("draftIdentifier", "unknown")
        identifier = result.get("identifier", params.document_id)
        name = result.get("name", "the document")
        return truncate_result(
            f"Created draft `{draft_identifier}` on document `{identifier}` (**{name}**) and applied the patch. "
            "Nothing is live yet — keep editing with `omni_patch_document_draft` "
            f'(`draft_id="{draft_identifier}"`), inspect it with `omni_get_document_draft_state`, then make it '
            "live with `omni_publish_document_draft`"
            + (" (a branch-attached draft goes live by merging its branch)." if params.branch_id else ".")
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_publish_document_draft",
    annotations=ToolAnnotations(
        title="Publish Document Draft (v2)",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_publish_document_draft(params: PublishDocumentDraftInput) -> str:
    """Publish the document's main draft, making it live (effectively irreversible).

    `POST /v2/documents/{documentId}/draft/publish`. No request body — the draft
    is consumed by the publish, so there is no undo: the previous published
    version is replaced for every viewer and recovering it means editing the
    document back. Verify the draft with `omni_get_document_draft_state` before
    calling this.

    Step 5 of the lifecycle: create → get state → create draft and patch → patch
    draft → **publish** → rename identifier. Only the main draft is publishable
    here; a branch-attached draft (one created with `branch_id`) goes live by
    merging its branch instead.

    When to Use:
    - After `omni_create_draft_and_patch_document` (and any
      `omni_patch_document_draft` calls) once the draft looks right.

    When NOT to Use:
    - Before reviewing the draft — this cannot be undone.
    - For a branch-attached draft — merge the branch instead.
    - When the organization requires a pull request to publish this document —
      the API returns 400 and the change must go through a branch.

    Returns:
    A confirmation with the published identifier and name, or an `Error ...`
    string on failure.

    Examples:
    - `{"params": {"document_id": "q2-revenue"}}`

    Error Handling:
    400 — the document can only be edited through a branch ("Can't publish
    because this document can only be edited through a branch"): create the
    draft with `branch_id` and merge the branch. 401 — the API key is missing or
    invalid. 403 — the caller lacks permission to publish this document. 404 —
    the document does not exist, or it has no main draft to publish (a
    branch-attached draft is published by merging its branch). 409 — the target
    is not a published document.
    """
    try:
        payload = await get_client().request_json("POST", f"{_document_path(params.document_id)}/draft/publish")
        result = _as_dict(payload)
        identifier = result.get("identifier", params.document_id)
        name = result.get("name", "the document")
        return truncate_result(
            f"Published document `{identifier}` (**{name}**) — the draft was consumed and the change is live. "
            "Confirm with `omni_get_document_state`."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_get_document_draft_state",
    annotations=ToolAnnotations(
        title="Get Document Draft State (v2)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_document_draft_state(params: GetDocumentDraftStateInput) -> str:
    """Read a named draft's state — the unpublished counterpart of the document state.

    `GET /v2/documents/{documentId}/draft/{draftId}`. Returns the same shape as
    `omni_get_document_state` and is equally round-trippable as a patch `body`.
    `workbookModelId` here is the draft's workbook model, not the published
    document's — that is the model an upload must be created against before a
    `csv` / `spreadsheet` tile can reference it.

    Step 4b of the lifecycle: create → get state → create draft and patch →
    **read the draft** / patch it → publish. The `draft_id` is the
    `draftIdentifier` returned by `omni_create_draft_and_patch_document`.

    When to Use:
    - To review pending changes before `omni_publish_document_draft`.
    - To fetch the draft's current record keys and control IDs before another
      `omni_patch_document_draft`.
    - To read the draft's `workbookModelId` when binding an upload.

    When NOT to Use:
    - To read what viewers currently see — that is `omni_get_document_state`.
    - To discover whether a draft exists at all: this needs an identifier; a
      `omni_create_draft_and_patch_document` call returning 409 is what tells
      you a draft already exists.

    Returns:
    A markdown summary (name, document ID, the draft ID, base and workbook model
    IDs, tile keys with names and types, control IDs, settings highlights and
    container count) followed by the full JSON state; the raw state alone when
    `response_format` is `json`. On failure, an `Error ...` string.

    Examples:
    - `{"params": {"document_id": "q2-revenue", "draft_id": "q2-revenue-draft-7f3a"}}`
    - Raw state: `{"params": {"document_id": "q2-revenue", "draft_id": "q2-revenue-draft-7f3a",
      "response_format": "json"}}`

    Error Handling:
    401 — the API key is missing or invalid. 403 — the caller cannot read this
    draft. 404 — the document or the draft does not exist (drafts are consumed
    by a publish, so a published draft ID stops resolving). 422 — the draft
    cannot be read as a dashboard: a classic-layout dashboard (upgrade to the
    advanced layout first) or an app document.
    """
    try:
        query: dict[str, Any] = {}
        if params.pretty is not None:
            query["pretty"] = params.pretty
        payload = await get_client().request_json(
            "GET", _draft_path(params.document_id, params.draft_id), params=query or None
        )
        return _render_state(
            state=_as_dict(payload),
            fmt=params.response_format,
            document_id=params.document_id,
            draft_id=params.draft_id,
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_patch_document_draft",
    annotations=ToolAnnotations(
        title="Patch Document Draft (v2)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_patch_document_draft(params: PatchDocumentDraftInput) -> str:
    """Apply a further patch to an existing draft — no draft creation, no publish.

    `PATCH /v2/documents/{documentId}/draft/{draftId}`. A pure apply: the body,
    content sections, field caps and tile-addressing rules are identical to
    `omni_create_draft_and_patch_document`, except that this route does not
    accept `branch_id` (the draft's branch was fixed when it was created).

    Step 4 of the lifecycle: create → get state → create draft and patch →
    **patch draft** (repeatable) → publish. Use it for the second and later
    edits to the same draft, and when
    `omni_create_draft_and_patch_document` returned 409 because a draft already
    exists. `queryPresentations.data`, `controls.data` and `settings` are
    shallow-merged by key; a key set to `null` deletes that tile or control; and
    `containers`, when present, fully replaces the layout.

    When to Use:
    - To keep editing a draft you already opened.
    - To fix up a draft that a 409 revealed was already open.
    - To replay an edited `omni_get_document_draft_state` payload as `body`.

    When NOT to Use:
    - To open the first draft on a published document — use
      `omni_create_draft_and_patch_document`.
    - To make the change live — that is `omni_publish_document_draft`.
    - To move the draft to another branch: pick the branch at draft creation.

    Returns:
    A confirmation with the draft identifier and the document it targets, or an
    `Error ...` string on failure.

    Examples:
    - Update a tile's subtitle: `{"params": {"document_id": "q2-revenue", "draft_id": "q2-revenue-draft-7f3a",
      "query_presentations": {"data": {"2": {"type": "query", "name": "Revenue by month",
      "subTitle": "Trailing 12 months", "topicName": "order_items",
      "query": {"fields": ["order_items.created_at[month]", "order_items.sale_price_sum"]}}}}}}`
    - Delete a control and reorder the rest: `{"params": {"document_id": "q2-revenue",
      "draft_id": "q2-revenue-draft-7f3a", "controls": {"data": {"state_filter": null},
      "order": ["created_at_filter"]}}}`
    - Replay an edited read verbatim: `{"params": {"document_id": "q2-revenue",
      "draft_id": "q2-revenue-draft-7f3a", "body": {"name": "Q2 Revenue", "description": null,
      "queryPresentations": {"data": {}, "order": ["1"]}}}}`

    Error Handling:
    400 — schema validation failed: an unknown top-level field, a length cap
    exceeded, more than 48 query presentations in one patch, a `model_id` /
    `workbook_model_id` that does not match the document's current models, or a
    `fileUploadId` on a tab that is not `csv`/`spreadsheet`. 401 — the API key is
    missing or invalid. 403 — the caller lacks permission to update this draft.
    404 — the document or the draft does not exist (a publish consumes the
    draft). 409 — the target is not a published document, or a concurrent
    request just created its layout; retry. 422 — the draft cannot satisfy the
    patch: a classic-layout dashboard, an app document, or a workbook-only draft
    patched without a non-empty `containers` payload.
    """
    try:
        payload_body = params.body
        if payload_body is None:
            payload_body = _compact(
                {
                    "modelId": params.model_id,
                    "workbookModelId": params.workbook_model_id,
                    "name": params.name,
                    "description": params.description,
                    "summary": params.summary,
                    "queryPresentations": _clean_query_presentations(params.query_presentations),
                    "controls": params.controls,
                    "settings": params.settings,
                    "containers": params.containers,
                }
            )
        payload = await get_client().request_json(
            "PATCH", _draft_path(params.document_id, params.draft_id), json_body=payload_body
        )
        result = _as_dict(payload)
        draft_identifier = result.get("draftIdentifier", params.draft_id)
        identifier = result.get("identifier", params.document_id)
        name = result.get("name", "the document")
        return truncate_result(
            f"Patched draft `{draft_identifier}` on document `{identifier}` (**{name}**). Still unpublished — "
            "review it with `omni_get_document_draft_state`, then publish with `omni_publish_document_draft`."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_rename_document_identifier",
    annotations=ToolAnnotations(
        title="Rename Document Identifier (v2)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_rename_document_identifier(params: RenameDocumentIdentifierInput) -> str:
    """Rename a published document's identifier (URL slug), live and immediately.

    `PUT /v2/documents/{documentId}/identifier`. The rename does not go through
    the draft/publish workflow: it applies at once. The former identifier is
    recorded in the document's rename history so old links redirect
    automatically. Only published documents can be renamed, and the new
    identifier must be a valid slug that no other document in the organization
    uses. Sending the identifier the document already has (case-sensitive)
    returns 200 without writing a history record.

    The last step of the lifecycle: create → get state → create draft and patch →
    patch draft → publish → **rename identifier**. There is deliberately no
    one-shot v2 endpoint that changes content and identifier together, and the
    v1 `documents` tools have no equivalent rename.

    When to Use:
    - To give a document a readable slug after creating it without `identifier`.
    - To move a document to a new URL while keeping old links working.

    When NOT to Use:
    - To change the document's display name — that is `name` on
      `omni_create_draft_and_patch_document` / `omni_patch_document_draft`.
    - On a document whose draft is still unpublished — publish first; renaming a
      draft returns 409.

    Returns:
    A confirmation with the new identifier and the document name, or an
    `Error ...` string on failure.

    Examples:
    - Rename: `{"params": {"document_id": "q2-revenue", "identifier": "new-q2-revenue"}}`
    - Rename by UUID: `{"params": {"document_id": "b81e4679-1234-4abc-9def-0123456789ab",
      "identifier": "q2-revenue-2024"}}`

    Error Handling:
    400 — invalid identifier format: it must be 2-48 characters of lowercase
    letters, digits, hyphens and underscores, and not a reserved value. 401 —
    the API key is missing or invalid. 403 — the caller lacks permission to
    rename this document. 404 — the document does not exist or is archived. 409 —
    the target is a draft rather than a published document, or the identifier is
    already used by another document (the collision check is
    case-insensitive). 429 — the rate limit (60 requests/minute per key) was hit.
    """
    try:
        payload = await get_client().request_json(
            "PUT",
            f"{_document_path(params.document_id)}/identifier",
            json_body={"identifier": params.identifier},
        )
        result = _as_dict(payload)
        identifier = result.get("identifier", params.identifier)
        name = result.get("name", "the document")
        return truncate_result(
            f"Renamed document `{params.document_id}` to identifier `{identifier}` (**{name}**). "
            "The former identifier is kept in the rename history, so existing links redirect automatically."
        )
    except Exception as exc:
        return handle_api_error(exc)
