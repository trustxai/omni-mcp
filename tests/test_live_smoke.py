"""Read-only live smoke suite: exercise one tool per API area against a real instance.

Every test here is `@pytest.mark.live` and is skipped unless `OMNI_API_KEY` is
present (see `tests/conftest.py`); run the suite with `uv run pytest -m live`.
See `docs/live-smoke.md` for what it covers and how to read its skips.

**Nothing in this file mutates.** Only GET-style tools are called — list, get,
whoami, validate, get-yaml and run-query. No create/update/delete/publish/
trigger/upload tool is imported, and none of the AI tools that spend credits
(`omni_ask_ai`, `omni_generate_query`, `omni_pick_topic`) is called.

Unlike the unit tests, these do **not** monkeypatch `get_client`: the real
client and the real credentials are used, which is why `conftest.py` exempts
live-marked tests from its environment isolation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any

import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.ai import ListAiConversationsInput, omni_list_ai_conversations
from omni_mcp.tools.ai_governance import GetAiCreditControlsInput, omni_get_ai_credit_controls
from omni_mcp.tools.connections import ListConnectionsInput, omni_list_connections
from omni_mcp.tools.content import (
    GetContentInput,
    SearchDashboardsInput,
    omni_get_content,
    omni_search_dashboards,
)
from omni_mcp.tools.dashboards import GetDashboardFiltersInput, omni_get_dashboard_filters
from omni_mcp.tools.documents import (
    GetDocumentInput,
    GetDocumentQueriesInput,
    ListDocumentsInput,
    omni_get_document,
    omni_get_document_queries,
    omni_list_documents,
)
from omni_mcp.tools.folders import ListFoldersInput, ListLabelsInput, omni_list_folders, omni_list_labels
from omni_mcp.tools.health import HealthCheckInput, omni_health_check
from omni_mcp.tools.identity import ListApiTokensInput, WhoamiInput, omni_list_api_tokens, omni_whoami
from omni_mcp.tools.models import (
    GetModelYamlInput,
    ListModelSchemasInput,
    ListModelsInput,
    ValidateModelInput,
    omni_get_model_yaml,
    omni_list_model_schemas,
    omni_list_models,
    omni_validate_model,
)
from omni_mcp.tools.queries import GetJobStatusInput, RunQueryInput, omni_get_job_status, omni_run_query
from omni_mcp.tools.schedules import ListSchedulesInput, omni_list_schedules
from omni_mcp.tools.uploads import ListUploadsInput, omni_list_uploads
from omni_mcp.tools.user_groups import ListUserGroupsInput, omni_list_user_groups
from omni_mcp.tools.users import ListUsersInput, omni_list_users

pytestmark = pytest.mark.live

#: Page size used everywhere: enough to prove pagination renders, small enough
#: that no single response approaches the result-size budget.
PAGE_SIZE = 5

#: Seconds slept before each live call. The API allows 60 requests/minute per
#: key and this suite makes roughly 25, so the pacing keeps a wide margin even
#: when the whole file runs back to back.
CALL_SPACING_SECONDS = 0.5

#: `truncate_result` appends one of these when a response outgrew the budget,
#: which turns a `json` response into unparseable text.
TRUNCATION_MARKERS = ("[truncated: result exceeded", "[truncated]")


# --- helpers ----------------------------------------------------------


async def paced(call: Awaitable[str]) -> str:
    """Await one live tool call, spaced out to stay under the rate limit."""
    await asyncio.sleep(CALL_SPACING_SECONDS)
    return await call


def live_call(call: Awaitable[str]) -> str:
    """Run one paced live call from a synchronous (session-scoped) fixture.

    The client builds and closes an `httpx.AsyncClient` per request, so running
    a fixture's calls in their own short-lived loop leaves nothing bound to it.
    """
    return asyncio.run(paced(call))


def check_ok(result: str, *, what: str, allow_missing: bool = False) -> str:
    """Fail on any `Error ...` result, except the two that are legitimate outcomes.

    A `403` means the key is a Personal Access Token or a non-admin membership
    hitting an admin-only endpoint — a property of the credentials, not a bug in
    the server, so the test skips. A `404` is only tolerated where the id came
    from a previous discovery call and may have gone away (or the endpoint does
    not apply to it); callers opt in with `allow_missing`.
    """
    first_line = result.splitlines()[0] if result else "<empty response>"
    if result.startswith("Error (403)"):
        pytest.skip(f"requires higher permission: {what} — {first_line}")
    if allow_missing and result.startswith("Error (404)"):
        pytest.skip(f"not available on this instance: {what} — {first_line}")
    assert not result.startswith("Error"), f"{what} failed: {result}"
    return result


def parse_json(result: str, *, what: str) -> Any:
    """Decode a `response_format="json"` result, skipping if it was truncated."""
    if result.endswith(TRUNCATION_MARKERS):
        pytest.skip(f"{what}: response exceeded the result budget and was truncated")
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        pytest.skip(f"{what}: response was not the expected JSON payload")


def records(payload: Any, *, what: str) -> list[Mapping[str, Any]]:
    """Pull the `items` list out of a paginated JSON payload."""
    assert isinstance(payload, dict), f"{what}: expected a JSON object, got {type(payload).__name__}"
    items = payload.get("items")
    assert isinstance(items, list), f"{what}: payload carried no `items` list"
    return [item for item in items if isinstance(item, dict)]


def sensitive_values(value: Any, *, key_part: str = "password") -> list[Any]:
    """Collect every value stored under a key containing `key_part`, recursively."""
    found: list[Any] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key_part in key.lower():
                found.append(item)
            else:
                found.extend(sensitive_values(item, key_part=key_part))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for item in value:
            found.extend(sensitive_values(item, key_part=key_part))
    return found


# --- discovery chain --------------------------------------------------


@pytest.fixture(scope="session")
def health() -> str:
    """Step 1: prove the base URL and key work before anything else runs."""
    return check_ok(live_call(omni_health_check(HealthCheckInput())), what="omni_health_check")


@pytest.fixture(scope="session")
def whoami(health: str) -> str:
    """Step 2: who the key acts as, and what it may see."""
    return check_ok(live_call(omni_whoami(WhoamiInput())), what="omni_whoami")


@pytest.fixture(scope="session")
def models(whoami: str) -> list[Mapping[str, Any]]:
    """Step 3: the model list every model-scoped test draws its id from."""
    result = check_ok(
        live_call(omni_list_models(ListModelsInput(page_size=PAGE_SIZE, response_format=ResponseFormat.JSON))),
        what="omni_list_models",
    )
    return records(parse_json(result, what="omni_list_models"), what="omni_list_models")


@pytest.fixture(scope="session")
def model_id(models: list[Mapping[str, Any]]) -> str:
    """Step 4: the first model id, or a skip when the instance has no models."""
    for model in models:
        identifier = model.get("id")
        if isinstance(identifier, str) and identifier:
            return identifier
    pytest.skip("no models visible to this API key")


@pytest.fixture(scope="session")
def documents(models: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Step 5: the document list the document/dashboard tests draw ids from."""
    result = check_ok(
        live_call(omni_list_documents(ListDocumentsInput(page_size=PAGE_SIZE, response_format=ResponseFormat.JSON))),
        what="omni_list_documents",
    )
    return records(parse_json(result, what="omni_list_documents"), what="omni_list_documents")


@pytest.fixture(scope="session")
def document(documents: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Step 6: the first document, or a skip when the instance has none."""
    for record in documents:
        if isinstance(record.get("identifier"), str) and record["identifier"]:
            return record
    pytest.skip("no documents visible to this API key")


@pytest.fixture(scope="session")
def dashboard_document_id(documents: list[Mapping[str, Any]]) -> str:
    """The first document that actually carries a dashboard, for the filter tool."""
    for record in documents:
        identifier = record.get("identifier")
        if record.get("hasDashboard") and isinstance(identifier, str) and identifier:
            return identifier
    pytest.skip("none of the listed documents has a dashboard")


@pytest.fixture(scope="session")
def document_queries(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Step 7: the queries behind the first document, ready to re-run."""
    document_id = str(document["identifier"])
    result = check_ok(
        live_call(
            omni_get_document_queries(
                GetDocumentQueriesInput(document_id=document_id, response_format=ResponseFormat.JSON)
            )
        ),
        what="omni_get_document_queries",
        allow_missing=True,
    )
    payload = parse_json(result, what="omni_get_document_queries")
    assert isinstance(payload, dict), "omni_get_document_queries: expected a JSON object"
    queries = payload.get("queries")
    assert isinstance(queries, list), "omni_get_document_queries: payload carried no `queries` list"
    return [item for item in queries if isinstance(item, dict)]


@pytest.fixture(scope="session")
def query_result(document_queries: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Step 8: re-run the document's first query, capped at a handful of rows."""
    for item in document_queries:
        body = item.get("query")
        if not isinstance(body, dict):
            continue
        result = check_ok(
            live_call(
                omni_run_query(
                    RunQueryInput(
                        query={**body, "limit": PAGE_SIZE},
                        max_rows=PAGE_SIZE,
                        response_format=ResponseFormat.JSON,
                    )
                )
            ),
            what="omni_run_query",
            allow_missing=True,
        )
        if result.startswith("# Query still running"):
            pytest.skip("omni_run_query: the query outlived its request on this instance")
        payload = parse_json(result, what="omni_run_query")
        assert isinstance(payload, dict), "omni_run_query: expected a JSON object"
        return payload
    pytest.skip("the first document exposes no runnable query")


# --- tests ------------------------------------------------------------


async def test_health_check_reports_ok(health: str) -> None:
    """`omni_health_check` — the credentials reach the instance."""
    assert "**OK**" in health
    assert "Key scope:" in health


async def test_whoami_reports_an_identity(whoami: str) -> None:
    """`omni_whoami` — the key resolves to a caller with a scope."""
    assert whoami.startswith("# Who Am I")
    assert "Key scope:" in whoami
    assert "## Model roles" in whoami


async def test_list_models_returns_records(models: list[Mapping[str, Any]]) -> None:
    """`omni_list_models` — the model list paginates and carries ids."""
    assert models, "the instance reported no models"
    assert all(isinstance(model.get("id"), str) for model in models)


async def test_list_model_schemas(model_id: str) -> None:
    """`omni_list_model_schemas` — schema names for the first model."""
    result = check_ok(
        await paced(omni_list_model_schemas(ListModelSchemasInput(model_id=model_id))),
        what="omni_list_model_schemas",
        allow_missing=True,
    )
    assert result.startswith("# Schemas")


async def test_get_model_yaml_reports_checksums(model_id: str) -> None:
    """`omni_get_model_yaml` — file names and checksums only; no content is inspected."""
    result = check_ok(
        await paced(omni_get_model_yaml(GetModelYamlInput(model_id=model_id, response_format=ResponseFormat.JSON))),
        what="omni_get_model_yaml",
        allow_missing=True,
    )
    payload = parse_json(result, what="omni_get_model_yaml")
    assert isinstance(payload, dict), "omni_get_model_yaml: expected a JSON object"
    files = payload.get("files")
    checksums = payload.get("checksums")
    assert isinstance(files, dict), "omni_get_model_yaml: payload carried no `files` map"
    if not files:
        pytest.skip("the first model exposes no YAML files")
    # `include_checksums` defaults to true here, so the write path always has
    # the concurrency token it needs. Only the *names* are compared — the file
    # contents are deliberately never asserted on or reported.
    assert isinstance(checksums, dict) and checksums, "omni_get_model_yaml: no checksums returned"
    assert set(checksums) <= set(files), (
        f"checksums name files that were not returned: {sorted(set(checksums) - set(files))}"
    )


async def test_validate_model(model_id: str) -> None:
    """`omni_validate_model` — the validator answers for the first model.

    A model with validation issues is a fact about the instance, not a test
    failure; only a transport/permission error fails here.
    """
    result = check_ok(
        await paced(omni_validate_model(ValidateModelInput(model_id=model_id))),
        what="omni_validate_model",
        allow_missing=True,
    )
    assert result.startswith("# Validation")


async def test_list_documents_returns_records(documents: list[Mapping[str, Any]]) -> None:
    """`omni_list_documents` — the document list carries identifiers."""
    assert documents, "the instance reported no documents"
    assert all("identifier" in record for record in documents)


async def test_get_document(document: Mapping[str, Any]) -> None:
    """`omni_get_document` — the first document renders."""
    document_id = str(document["identifier"])
    result = check_ok(
        await paced(omni_get_document(GetDocumentInput(document_id=document_id))),
        what="omni_get_document",
        allow_missing=True,
    )
    assert document_id in result


async def test_get_document_queries(document_queries: list[Mapping[str, Any]]) -> None:
    """`omni_get_document_queries` — every entry is runnable through the query tool."""
    for item in document_queries:
        assert isinstance(item.get("query"), dict), f"query entry {item.get('id')} carries no `query` object"


async def test_run_query_returns_rows_or_an_empty_table(query_result: Mapping[str, Any]) -> None:
    """`omni_run_query` — a document's own query re-runs and decodes cleanly."""
    rows = query_result.get("rows")
    assert isinstance(rows, list), "omni_run_query: payload carried no `rows` list"
    assert len(rows) <= PAGE_SIZE, "omni_run_query returned more rows than the requested limit"
    # An empty result set is a clean outcome, not a failure — the row count just
    # has to agree with what was rendered.
    assert query_result.get("rowCount") == len(rows) or query_result.get("truncated") is True


async def test_get_job_status(query_result: Mapping[str, Any]) -> None:
    """`omni_get_job_status` — the job the query reported can be looked up."""
    job_id = query_result.get("jobId")
    if not isinstance(job_id, str) or not job_id:
        pytest.skip("omni_run_query reported no job id to look up")
    result = check_ok(
        await paced(omni_get_job_status(GetJobStatusInput(job_id=job_id))),
        what="omni_get_job_status",
        allow_missing=True,
    )
    assert result


async def test_list_folders(whoami: str) -> None:
    """`omni_list_folders` — the folder tree paginates."""
    result = check_ok(
        await paced(omni_list_folders(ListFoldersInput(page_size=PAGE_SIZE))),
        what="omni_list_folders",
    )
    assert result.startswith("# Folders")


async def test_list_labels(whoami: str) -> None:
    """`omni_list_labels` — organization labels render (an empty set is fine)."""
    result = check_ok(await paced(omni_list_labels(ListLabelsInput())), what="omni_list_labels")
    assert result.startswith("# ")


async def test_list_connections_masks_credentials(whoami: str) -> None:
    """`omni_list_connections` — connections render and no password leaks out."""
    result = check_ok(
        await paced(omni_list_connections(ListConnectionsInput(response_format=ResponseFormat.JSON))),
        what="omni_list_connections",
    )
    payload = parse_json(result, what="omni_list_connections")
    leaked = [value for value in sensitive_values(payload) if value not in (None, "", "***MASKED***")]
    assert not leaked, "omni_list_connections returned an unmasked password-like field"


async def test_list_schedules(whoami: str) -> None:
    """`omni_list_schedules` — schedules and alerts paginate."""
    result = check_ok(
        await paced(omni_list_schedules(ListSchedulesInput(page_size=PAGE_SIZE))),
        what="omni_list_schedules",
    )
    assert result.startswith("# ")


async def test_list_users(whoami: str) -> None:
    """`omni_list_users` — SCIM user listing (Organization API key only)."""
    result = check_ok(
        await paced(omni_list_users(ListUsersInput(count=PAGE_SIZE))),
        what="omni_list_users",
    )
    assert result.startswith("# ")


async def test_list_user_groups(whoami: str) -> None:
    """`omni_list_user_groups` — SCIM group listing (Organization API key only)."""
    result = check_ok(
        await paced(omni_list_user_groups(ListUserGroupsInput(count=PAGE_SIZE))),
        what="omni_list_user_groups",
    )
    assert result.startswith("# ")


async def test_list_uploads(whoami: str) -> None:
    """`omni_list_uploads` — CSV uploads paginate."""
    result = check_ok(
        await paced(omni_list_uploads(ListUploadsInput(page_size=PAGE_SIZE))),
        what="omni_list_uploads",
    )
    assert result.startswith("# ")


async def test_list_ai_conversations(whoami: str) -> None:
    """`omni_list_ai_conversations` — reads AI history without spending credits."""
    result = check_ok(
        await paced(omni_list_ai_conversations(ListAiConversationsInput(page_size=PAGE_SIZE))),
        what="omni_list_ai_conversations",
        allow_missing=True,
    )
    assert result.startswith("# ")


async def test_get_ai_credit_controls(whoami: str) -> None:
    """`omni_get_ai_credit_controls` — org-admin only, so a 403 is an expected outcome."""
    result = check_ok(
        await paced(omni_get_ai_credit_controls(GetAiCreditControlsInput())),
        what="omni_get_ai_credit_controls",
    )
    assert result.startswith("# AI Credit Controls")


async def test_list_api_tokens(whoami: str) -> None:
    """`omni_list_api_tokens` — admin-only, so a 403 is an expected outcome."""
    result = check_ok(
        await paced(omni_list_api_tokens(ListApiTokensInput(page_size=PAGE_SIZE))),
        what="omni_list_api_tokens",
    )
    assert result.startswith("# ")


async def test_get_dashboard_filters(dashboard_document_id: str) -> None:
    """`omni_get_dashboard_filters` — the filter bar of the first real dashboard."""
    result = check_ok(
        await paced(omni_get_dashboard_filters(GetDashboardFiltersInput(dashboard_id=dashboard_document_id))),
        what="omni_get_dashboard_filters",
        allow_missing=True,
    )
    assert result


async def test_get_content(whoami: str) -> None:
    """`omni_get_content` — the first page of the folder/document tree."""
    result = check_ok(
        await paced(omni_get_content(GetContentInput(page_size=PAGE_SIZE))),
        what="omni_get_content",
    )
    assert result.startswith("# ")


async def test_search_dashboards(document: Mapping[str, Any]) -> None:
    """`omni_search_dashboards` — free-text search using a name known to exist."""
    name = str(document.get("name") or "").strip()
    if not name:
        pytest.skip("the first document has no name to search for")
    result = check_ok(
        await paced(omni_search_dashboards(SearchDashboardsInput(q=name, limit=PAGE_SIZE))),
        what="omni_search_dashboards",
        allow_missing=True,
    )
    assert result.startswith("# ")
