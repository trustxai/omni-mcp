"""Unit tests for the documents v2 tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.documents_v2 import (
    MAX_QUERY_PRESENTATIONS_PER_PATCH,
    CreateDocumentV2Input,
    CreateDraftAndPatchDocumentInput,
    GetDocumentDraftStateInput,
    GetDocumentStateInput,
    PatchDocumentDraftInput,
    PublishDocumentDraftInput,
    RenameDocumentIdentifierInput,
    omni_create_document_v2,
    omni_create_draft_and_patch_document,
    omni_get_document_draft_state,
    omni_get_document_state,
    omni_patch_document_draft,
    omni_publish_document_draft,
    omni_rename_document_identifier,
)

MODULE = "omni_mcp.tools.documents_v2"
MODEL_ID = "b81e4679-1234-4abc-9def-0123456789ab"

READ_STATE: dict[str, Any] = {
    "name": "Q2 Revenue",
    "description": "Quarterly revenue review",
    "modelId": MODEL_ID,
    "workbookModelId": "0f1e2d3c-4b5a-4697-8899-aabbccddeeff",
    "queryPresentations": {
        "data": {
            "1": {
                "type": "query",
                "name": "Revenue by month",
                "topicName": "order_items",
                "prefersChart": True,
                "query": {"fields": ["order_items.created_at[month]", "order_items.sale_price_sum"]},
            },
            "2": {"type": "linked", "name": "Summary", "sourceQueryPresentationKey": "1"},
        },
        "order": ["2", "1"],
    },
    "controls": {
        "data": {"state_filter": {"type": "dropdown", "label": "State"}},
        "order": ["state_filter"],
    },
    "settings": {
        "crossfilterEnabled": True,
        "customText": {"queryError": "Something broke"},
        "facetFilters": False,
        "refreshInterval": 3600,
        "runQueriesOn": "current-page",
    },
    "containers": [{"type": "grid", "instanceKey": "root"}],
}

CREATE_RESPONSE: dict[str, Any] = {
    "identifier": "q2-revenue",
    "name": "Q2 Revenue",
    "description": "Quarterly revenue review",
}

PATCH_DRAFT_RESPONSE: dict[str, Any] = {
    "identifier": "q2-revenue",
    "draftIdentifier": "q2-revenue-draft-7f3a",
    "name": "Q2 Revenue (final)",
    "description": "Quarterly revenue review",
}


class _FakeClient:
    """Records `(method, path, kwargs)` calls; returns a canned payload."""

    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {}
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return self._payload


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> _FakeClient:
    monkeypatch.setattr(f"{MODULE}.get_client", lambda: fake)
    return fake


def _http_error(
    status: int, detail: str, method: str = "GET", url: str = "https://acme.omniapp.co/api/v2/documents/x"
) -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# --------------------------------------------------------------------------
# omni_create_document_v2
# --------------------------------------------------------------------------


async def test_create_document_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=CREATE_RESPONSE))

    result = await omni_create_document_v2(CreateDocumentV2Input(model_id=MODEL_ID, name="Q2 Revenue"))

    assert "Created and published document **Q2 Revenue**" in result
    assert "`q2-revenue`" in result
    assert fake.calls == [
        ("POST", "/v2/documents", {"json_body": {"modelId": MODEL_ID, "name": "Q2 Revenue"}}),
    ]


async def test_create_document_with_content(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=CREATE_RESPONSE))

    await omni_create_document_v2(
        CreateDocumentV2Input(
            model_id=MODEL_ID,
            name="Q2 Revenue",
            identifier="q2-revenue",
            description="Quarterly revenue review",
            folder_id="4c9c2f1e-1111-4222-8333-444455556666",
            summary="Created from the API",
            query_presentations={
                "data": {
                    "1": {
                        "type": "query",
                        "name": "Revenue by month",
                        "topicName": "order_items",
                        "prefersChart": True,
                        "query": {"fields": ["order_items.created_at[month]", "order_items.sale_price_sum"]},
                    }
                }
            },
            controls={"data": {"state_filter": {"type": "dropdown"}}, "order": ["state_filter"]},
            settings={"crossfilterEnabled": True, "refreshInterval": 3600},
            containers=[{"type": "grid", "instanceKey": "root"}],
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v2/documents",
            {
                "json_body": {
                    "modelId": MODEL_ID,
                    "name": "Q2 Revenue",
                    "identifier": "q2-revenue",
                    "description": "Quarterly revenue review",
                    "folderId": "4c9c2f1e-1111-4222-8333-444455556666",
                    "summary": "Created from the API",
                    "queryPresentations": {
                        "data": {
                            "1": {
                                "type": "query",
                                "name": "Revenue by month",
                                "topicName": "order_items",
                                "prefersChart": True,
                                "query": {"fields": ["order_items.created_at[month]", "order_items.sale_price_sum"]},
                            }
                        }
                    },
                    "controls": {"data": {"state_filter": {"type": "dropdown"}}, "order": ["state_filter"]},
                    "settings": {"crossfilterEnabled": True, "refreshInterval": 3600},
                    "containers": [{"type": "grid", "instanceKey": "root"}],
                }
            },
        )
    ]


async def test_create_document_strips_server_owned_tile_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=CREATE_RESPONSE))

    await omni_create_document_v2(
        CreateDocumentV2Input(
            model_id=MODEL_ID,
            name="Q2 Revenue",
            query_presentations={"data": {"1": {"type": "query", "name": "Tile", "miniUuid": "abc"}, "2": None}},
        )
    )

    body = fake.calls[0][2]["json_body"]
    assert body["queryPresentations"]["data"]["1"] == {"type": "query", "name": "Tile"}
    assert body["queryPresentations"]["data"]["2"] is None


async def test_create_document_body_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=CREATE_RESPONSE))

    await omni_create_document_v2(
        CreateDocumentV2Input(
            model_id=MODEL_ID,
            name="ignored",
            settings={"crossfilterEnabled": True},
            body={"modelId": MODEL_ID, "name": "Q2 Revenue", "description": None},
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v2/documents",
            {"json_body": {"modelId": MODEL_ID, "name": "Q2 Revenue", "description": None}},
        )
    ]


async def test_create_document_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeClient(exc=_http_error(400, "identifier is already in use", method="POST")))

    result = await omni_create_document_v2(CreateDocumentV2Input(model_id=MODEL_ID, name="Q2 Revenue"))

    assert result.startswith("Error (400):")
    assert "identifier is already in use" in result


# --------------------------------------------------------------------------
# omni_get_document_state
# --------------------------------------------------------------------------


async def test_get_document_state_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=READ_STATE))

    result = await omni_get_document_state(GetDocumentStateInput(document_id="q2-revenue"))

    assert "# Q2 Revenue" in result
    assert "- Document: `q2-revenue`" in result
    assert "- State: **published**" in result
    assert "## Tiles (2)" in result
    assert "**Revenue by month**" in result
    assert "linked to tile `1`" in result
    assert "## Controls (1)" in result
    assert "`state_filter`" in result
    assert "Crossfilter: on" in result
    assert "Refresh interval: 3600s" in result
    assert "Run queries on: current-page" in result
    assert "1 top-level container(s)." in result
    assert '"workbookModelId": "0f1e2d3c-4b5a-4697-8899-aabbccddeeff"' in result
    assert fake.calls == [("GET", "/v2/documents/q2-revenue", {"params": None})]


async def test_get_document_state_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=READ_STATE))

    payload = json.loads(
        await omni_get_document_state(
            GetDocumentStateInput(document_id="q2-revenue", pretty=True, response_format=ResponseFormat.JSON)
        )
    )

    assert payload["documentId"] == "q2-revenue"
    assert payload["draftId"] is None
    assert payload["state"]["queryPresentations"]["order"] == ["2", "1"]
    assert fake.calls == [("GET", "/v2/documents/q2-revenue", {"params": {"pretty": True}})]


async def test_get_document_state_quotes_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=READ_STATE))

    await omni_get_document_state(GetDocumentStateInput(document_id="a b/c"))

    assert fake.calls[0][1] == "/v2/documents/a%20b%2Fc"


async def test_get_document_state_workbook_only(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"name": "Workbook", "description": None, "queryPresentations": {"data": {}, "order": []}}
    _install(monkeypatch, _FakeClient(payload=state))

    result = await omni_get_document_state(GetDocumentStateInput(document_id="wb"))

    assert "_No tiles._" in result
    assert "_No controls._" in result
    assert "no dashboard settings yet" in result
    assert "no dashboard layout yet" in result


async def test_get_document_state_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeClient(exc=_http_error(404, "Document not found")))

    result = await omni_get_document_state(GetDocumentStateInput(document_id="missing"))

    assert result.startswith("Error (404):")
    assert "Document not found" in result


# --------------------------------------------------------------------------
# omni_create_draft_and_patch_document
# --------------------------------------------------------------------------


async def test_create_draft_and_patch_document(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=PATCH_DRAFT_RESPONSE))

    result = await omni_create_draft_and_patch_document(
        CreateDraftAndPatchDocumentInput(
            document_id="q2-revenue",
            name="Q2 Revenue (final)",
            summary="Rename and enable cross-filtering",
            settings={"crossfilterEnabled": True},
        )
    )

    assert "Created draft `q2-revenue-draft-7f3a` on document `q2-revenue`" in result
    assert "omni_publish_document_draft" in result
    assert fake.calls == [
        (
            "PATCH",
            "/v2/documents/q2-revenue/draft",
            {
                "json_body": {
                    "name": "Q2 Revenue (final)",
                    "summary": "Rename and enable cross-filtering",
                    "settings": {"crossfilterEnabled": True},
                }
            },
        )
    ]


async def test_create_draft_and_patch_document_on_a_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=PATCH_DRAFT_RESPONSE))

    result = await omni_create_draft_and_patch_document(
        CreateDraftAndPatchDocumentInput(
            document_id="q2-revenue",
            branch_id="8b1f0a2c-1234-4abc-9def-0123456789ab",
            model_id=MODEL_ID,
            workbook_model_id="0f1e2d3c-4b5a-4697-8899-aabbccddeeff",
            description="Quarterly revenue review",
            query_presentations={
                "data": {
                    "3": {
                        "type": "query",
                        "name": "Orders by region",
                        "topicName": "order_items",
                        "query": {"fields": ["users.state", "order_items.count"]},
                    },
                    "4": None,
                },
                "order": ["3"],
            },
            controls={"data": {"state_filter": None}},
            containers=[{"type": "grid"}],
        )
    )

    assert "merging its branch" in result
    assert fake.calls == [
        (
            "PATCH",
            "/v2/documents/q2-revenue/draft",
            {
                "json_body": {
                    "branchId": "8b1f0a2c-1234-4abc-9def-0123456789ab",
                    "modelId": MODEL_ID,
                    "workbookModelId": "0f1e2d3c-4b5a-4697-8899-aabbccddeeff",
                    "description": "Quarterly revenue review",
                    "queryPresentations": {
                        "data": {
                            "3": {
                                "type": "query",
                                "name": "Orders by region",
                                "topicName": "order_items",
                                "query": {"fields": ["users.state", "order_items.count"]},
                            },
                            "4": None,
                        },
                        "order": ["3"],
                    },
                    "controls": {"data": {"state_filter": None}},
                    "containers": [{"type": "grid"}],
                }
            },
        )
    ]


async def test_create_draft_and_patch_document_body_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=PATCH_DRAFT_RESPONSE))

    await omni_create_draft_and_patch_document(
        CreateDraftAndPatchDocumentInput(
            document_id="q2-revenue",
            name="ignored",
            body={"name": "Q2 Revenue (final)", "description": None},
        )
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v2/documents/q2-revenue/draft",
            {"json_body": {"name": "Q2 Revenue (final)", "description": None}},
        )
    ]


async def test_create_draft_and_patch_document_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        _FakeClient(exc=_http_error(409, "A draft already exists for this document", method="PATCH")),
    )

    result = await omni_create_draft_and_patch_document(
        CreateDraftAndPatchDocumentInput(document_id="q2-revenue", name="Q2 Revenue (final)")
    )

    assert result.startswith("Error (409):")
    assert "A draft already exists for this document" in result


# --------------------------------------------------------------------------
# omni_publish_document_draft
# --------------------------------------------------------------------------


async def test_publish_document_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(
        monkeypatch,
        _FakeClient(payload={"identifier": "q2-revenue", "name": "Q2 Revenue (final)", "description": None}),
    )

    result = await omni_publish_document_draft(PublishDocumentDraftInput(document_id="q2 revenue"))

    assert "Published document `q2-revenue` (**Q2 Revenue (final)**)" in result
    assert fake.calls == [("POST", "/v2/documents/q2%20revenue/draft/publish", {})]


async def test_publish_document_draft_requires_a_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        _FakeClient(
            exc=_http_error(
                400, "Can't publish because this document can only be edited through a branch", method="POST"
            )
        ),
    )

    result = await omni_publish_document_draft(PublishDocumentDraftInput(document_id="q2-revenue"))

    assert result.startswith("Error (400):")
    assert "only be edited through a branch" in result


# --------------------------------------------------------------------------
# omni_get_document_draft_state
# --------------------------------------------------------------------------


async def test_get_document_draft_state_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=READ_STATE))

    result = await omni_get_document_draft_state(
        GetDocumentDraftStateInput(document_id="q2-revenue", draft_id="q2-revenue-draft-7f3a")
    )

    assert "- State: **draft** (`q2-revenue-draft-7f3a`)" in result
    assert "## Tiles (2)" in result
    assert fake.calls == [
        ("GET", "/v2/documents/q2-revenue/draft/q2-revenue-draft-7f3a", {"params": None}),
    ]


async def test_get_document_draft_state_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=READ_STATE))

    payload = json.loads(
        await omni_get_document_draft_state(
            GetDocumentDraftStateInput(
                document_id="q2-revenue",
                draft_id="draft/7f3a",
                pretty=False,
                response_format=ResponseFormat.JSON,
            )
        )
    )

    assert payload["draftId"] == "draft/7f3a"
    assert payload["state"]["name"] == "Q2 Revenue"
    assert fake.calls == [
        ("GET", "/v2/documents/q2-revenue/draft/draft%2F7f3a", {"params": {"pretty": False}}),
    ]


async def test_get_document_draft_state_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeClient(exc=_http_error(403, "Insufficient permissions to read the draft")))

    result = await omni_get_document_draft_state(
        GetDocumentDraftStateInput(document_id="q2-revenue", draft_id="draft-1")
    )

    assert result.startswith("Error (403):")
    assert "Insufficient permissions to read the draft" in result


# --------------------------------------------------------------------------
# omni_patch_document_draft
# --------------------------------------------------------------------------


async def test_patch_document_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=PATCH_DRAFT_RESPONSE))

    result = await omni_patch_document_draft(
        PatchDocumentDraftInput(
            document_id="q2-revenue",
            draft_id="q2-revenue-draft-7f3a",
            summary="Update the subtitle",
            query_presentations={
                "data": {
                    "2": {
                        "type": "query",
                        "name": "Revenue by month",
                        "subTitle": "Trailing 12 months",
                        "topicName": "order_items",
                    }
                }
            },
        )
    )

    assert "Patched draft `q2-revenue-draft-7f3a` on document `q2-revenue`" in result
    assert fake.calls == [
        (
            "PATCH",
            "/v2/documents/q2-revenue/draft/q2-revenue-draft-7f3a",
            {
                "json_body": {
                    "summary": "Update the subtitle",
                    "queryPresentations": {
                        "data": {
                            "2": {
                                "type": "query",
                                "name": "Revenue by month",
                                "subTitle": "Trailing 12 months",
                                "topicName": "order_items",
                            }
                        }
                    },
                }
            },
        )
    ]


async def test_patch_document_draft_body_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=PATCH_DRAFT_RESPONSE))

    await omni_patch_document_draft(
        PatchDocumentDraftInput(
            document_id="q2-revenue",
            draft_id="q2-revenue-draft-7f3a",
            name="ignored",
            body={"name": "Q2 Revenue", "settings": {"refreshInterval": None}},
        )
    )

    assert fake.calls == [
        (
            "PATCH",
            "/v2/documents/q2-revenue/draft/q2-revenue-draft-7f3a",
            {"json_body": {"name": "Q2 Revenue", "settings": {"refreshInterval": None}}},
        )
    ]


async def test_patch_document_draft_empty_patch_sends_an_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, _FakeClient(payload=PATCH_DRAFT_RESPONSE))

    await omni_patch_document_draft(PatchDocumentDraftInput(document_id="q2-revenue", draft_id="q2-revenue-draft-7f3a"))

    assert fake.calls[0][2] == {"json_body": {}}


# --------------------------------------------------------------------------
# omni_rename_document_identifier
# --------------------------------------------------------------------------


async def test_rename_document_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(
        monkeypatch,
        _FakeClient(
            payload={
                "identifier": "new-q2-revenue",
                "name": "Q2 Revenue Analysis",
                "description": "Quarterly revenue review",
            }
        ),
    )

    result = await omni_rename_document_identifier(
        RenameDocumentIdentifierInput(document_id="q2-revenue", identifier="new-q2-revenue")
    )

    assert "Renamed document `q2-revenue` to identifier `new-q2-revenue`" in result
    assert "redirect" in result
    assert fake.calls == [
        (
            "PUT",
            "/v2/documents/q2-revenue/identifier",
            {"json_body": {"identifier": "new-q2-revenue"}},
        )
    ]


async def test_rename_document_identifier_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        _FakeClient(exc=_http_error(409, "That identifier is already in use", method="PUT")),
    )

    result = await omni_rename_document_identifier(
        RenameDocumentIdentifierInput(document_id="q2-revenue", identifier="taken-slug")
    )

    assert result.startswith("Error (409):")
    assert "That identifier is already in use" in result


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x", unexpected="y")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        GetDocumentStateInput(document_id="x", unexpected="y")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        PublishDocumentDraftInput(document_id="x", unexpected="y")  # type: ignore[call-arg]


def test_create_rejects_a_bad_identifier() -> None:
    with pytest.raises(ValueError):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x", identifier="-leading-hyphen")
    with pytest.raises(ValueError):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x", identifier="Upper-Case")


def test_create_rejects_an_over_long_name() -> None:
    with pytest.raises(ValueError):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x" * 255)


def test_rename_rejects_a_bad_identifier() -> None:
    with pytest.raises(ValueError):
        RenameDocumentIdentifierInput(document_id="x", identifier="a")
    with pytest.raises(ValueError):
        RenameDocumentIdentifierInput(document_id="x", identifier="not a slug")
    with pytest.raises(ValueError):
        RenameDocumentIdentifierInput(document_id="x", identifier="x" * 49)


def test_query_presentations_cap_is_enforced() -> None:
    tiles = {
        str(index): {"type": "query", "name": f"Tile {index}"}
        for index in range(1, MAX_QUERY_PRESENTATIONS_PER_PATCH + 2)
    }
    with pytest.raises(ValueError, match="at most 48 query presentations"):
        PatchDocumentDraftInput(document_id="x", draft_id="d", query_presentations={"data": tiles})


def test_query_presentation_keys_must_be_record_keys() -> None:
    with pytest.raises(ValueError, match="record key"):
        PatchDocumentDraftInput(document_id="x", draft_id="d", query_presentations={"data": {"0": {"type": "query"}}})
    with pytest.raises(ValueError, match="record key"):
        PatchDocumentDraftInput(document_id="x", draft_id="d", query_presentations={"order": ["tile-1"]})


def test_query_presentation_type_is_required_and_validated() -> None:
    with pytest.raises(ValueError, match="required `type`"):
        PatchDocumentDraftInput(document_id="x", draft_id="d", query_presentations={"data": {"1": {"name": "n"}}})
    with pytest.raises(ValueError, match="must be one of"):
        PatchDocumentDraftInput(document_id="x", draft_id="d", query_presentations={"data": {"1": {"type": "pivot"}}})


def test_query_presentations_rejects_unknown_sections() -> None:
    with pytest.raises(ValueError, match="only `data` and `order`"):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x", query_presentations={"tiles": {"1": {"type": "query"}}})


def test_settings_validation() -> None:
    with pytest.raises(ValueError, match="runQueriesOn"):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x", settings={"runQueriesOn": "some-pages"})
    with pytest.raises(ValueError, match="settings accepts only"):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x", settings={"refreshIntervalSeconds": 60})
    assert CreateDocumentV2Input(
        model_id=MODEL_ID, name="x", settings={"runQueriesOn": None, "refreshInterval": None}
    ).settings == {"runQueriesOn": None, "refreshInterval": None}


def test_controls_validation() -> None:
    with pytest.raises(ValueError, match="only `data` and `order`"):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x", controls={"filters": {}})
    with pytest.raises(ValueError, match="controls.order"):
        CreateDocumentV2Input(model_id=MODEL_ID, name="x", controls={"order": "state_filter"})
