"""Unit tests for the user group (SCIM) and user/group model role tools."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.user_groups import (
    AssignUserGroupModelRoleInput,
    AssignUserModelRoleInput,
    CreateUserGroupInput,
    DeleteUserGroupInput,
    GetUserGroupInput,
    GetUserGroupModelRolesInput,
    GetUserModelRolesInput,
    ListUserGroupsInput,
    ReplaceUserGroupInput,
    UpdateUserGroupInput,
    omni_assign_user_group_model_role,
    omni_assign_user_model_role,
    omni_create_user_group,
    omni_delete_user_group,
    omni_get_user_group,
    omni_get_user_group_model_roles,
    omni_get_user_model_roles,
    omni_list_user_groups,
    omni_replace_user_group,
    omni_update_user_group,
)


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


GROUP: dict[str, Any] = {
    "id": "mEhXj6ZI",
    "displayName": "Blob Sales",
    "meta": {
        "resourceType": "Group",
        "created": "2024-12-04T00:08:03.250Z",
        "lastModified": "2024-12-04T00:08:03.250Z",
    },
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
    "members": [{"display": "blob.ross@blobsrus.co", "value": "9e8719d9-276a-4964-9395-a493189a247c"}],
}

LIST_GROUPS_BODY: dict[str, Any] = {
    "Resources": [GROUP],
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
    "itemsPerPage": 1,
    "startIndex": 1,
    "totalResults": 2,
}

GROUP_MODEL_ROLES_BODY: dict[str, Any] = {
    "userGroupId": "mEhXj6ZI",
    "results": [
        {
            "modelId": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
            "connectionId": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
            "roleName": "QUERIER",
            "baseRole": "QUERIER",
        }
    ],
}

ASSIGN_GROUP_ROLE_RESPONSE: dict[str, Any] = {
    "userGroupId": "mEhXj6ZI",
    "connectionId": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
    "modelId": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
    "roleName": "QUERIER",
}

USER_MODEL_ROLES_BODY: dict[str, Any] = {
    "membershipId": "9633bd79-7bdf-4773-8952-8fdd4098e51c",
    "results": [
        {
            "baseRole": "MODELER",
            "from": {"type": "User Role"},
            "priority": 350,
            "resolved": True,
            "roleName": "MODELER",
            "connectionId": "8a464dc9-1f0e-4a9e-86fa-e1e6d970157c",
            "modelId": "5fb90312-67b1-4cca-823a-9a341d549320",
        },
        {
            "baseRole": "QUERIER",
            "from": {"depth": 0, "miniUuid": "PgjffoEu", "name": "Super Group", "type": "Group Role"},
            "priority": 250,
            "resolved": False,
            "roleName": "QUERIER",
            "connectionId": "8a464dc9-1f0e-4a9e-86fa-e1e6d970157c",
            "modelId": "5fb90312-67b1-4cca-823a-9a341d549320",
        },
    ],
}

ASSIGN_USER_ROLE_RESPONSE: dict[str, Any] = {
    "userId": "9e8719d9-276a-4964-9395-a493189a247c",
    "connectionId": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
    "modelId": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
    "roleName": "QUERIER",
}


def _http_error(status: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/scim/v2/groups/mEhXj6ZI")
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# --- omni_list_user_groups -----------------------------------------------------


async def test_list_user_groups_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LIST_GROUPS_BODY)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_list_user_groups(ListUserGroupsInput())

    assert "Blob Sales" in result
    assert "mEhXj6ZI" in result
    assert "of **2**" in result
    assert "start_index=2" in result
    assert fake.calls == [("GET", "/scim/v2/groups", {"params": {"count": 100, "startIndex": 1}})]


async def test_list_user_groups_passes_params(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"Resources": [], "totalResults": 0, "itemsPerPage": 0, "startIndex": 51})
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_list_user_groups(ListUserGroupsInput(count=50, start_index=51))

    assert "No user groups found" in result
    assert "End of results." in result
    assert fake.calls == [("GET", "/scim/v2/groups", {"params": {"count": 50, "startIndex": 51}})]


async def test_list_user_groups_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=LIST_GROUPS_BODY)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    payload = json.loads(await omni_list_user_groups(ListUserGroupsInput(response_format=ResponseFormat.JSON)))

    assert payload["totalResults"] == 2
    assert payload["Resources"][0]["id"] == "mEhXj6ZI"


async def test_list_user_groups_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Personal Access Tokens are not supported for this endpoint"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_list_user_groups(ListUserGroupsInput())

    assert result.startswith("Error (403):")


# --- omni_get_user_group -------------------------------------------------------


async def test_get_user_group_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_get_user_group(GetUserGroupInput(user_group_id="mEhXj6ZI"))

    assert "Blob Sales" in result
    assert "blob.ross@blobsrus.co" in result
    assert fake.calls == [("GET", "/scim/v2/groups/mEhXj6ZI", {})]


async def test_get_user_group_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    payload = json.loads(
        await omni_get_user_group(GetUserGroupInput(user_group_id="mEhXj6ZI", response_format=ResponseFormat.JSON))
    )

    assert payload["id"] == "mEhXj6ZI"
    assert payload["displayName"] == "Blob Sales"


async def test_get_user_group_quotes_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    await omni_get_user_group(GetUserGroupInput(user_group_id="a/b"))

    assert fake.calls == [("GET", "/scim/v2/groups/a%2Fb", {})]


async def test_get_user_group_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Group not found"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_get_user_group(GetUserGroupInput(user_group_id="missing"))

    assert result.startswith("Error (404):")


# --- omni_create_user_group ----------------------------------------------------


async def test_create_user_group_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_create_user_group(
        CreateUserGroupInput(display_name="Blob Sales", member_ids=["9e8719d9-276a-4964-9395-a493189a247c"])
    )

    assert "Created user group **Blob Sales**" in result
    assert "mEhXj6ZI" in result
    assert fake.calls == [
        (
            "POST",
            "/scim/v2/groups",
            {
                "json_body": {
                    "displayName": "Blob Sales",
                    "members": [{"value": "9e8719d9-276a-4964-9395-a493189a247c"}],
                }
            },
        )
    ]


async def test_create_user_group_without_members(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "abc", "displayName": "No Members"})
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_create_user_group(CreateUserGroupInput(display_name="No Members"))

    assert "0 member(s)" in result
    assert fake.calls == [("POST", "/scim/v2/groups", {"json_body": {"displayName": "No Members"}})]


async def test_create_user_group_scim_body_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)
    raw_body = {"displayName": "Raw Group", "members": []}

    await omni_create_user_group(CreateUserGroupInput(scim_body=raw_body))

    assert fake.calls == [("POST", "/scim/v2/groups", {"json_body": raw_body})]


def test_create_user_group_requires_display_name_without_body() -> None:
    with pytest.raises(ValueError):
        CreateUserGroupInput()


async def test_create_user_group_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(403, "Personal Access Tokens are not supported for this endpoint"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_create_user_group(CreateUserGroupInput(display_name="Blob Sales"))

    assert result.startswith("Error (403):")


# --- omni_replace_user_group ----------------------------------------------------


async def test_replace_user_group_success(monkeypatch: pytest.MonkeyPatch) -> None:
    replaced = dict(GROUP, displayName="Blob SEs")
    fake = _FakeClient(payload=replaced)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_replace_user_group(
        ReplaceUserGroupInput(
            user_group_id="mEhXj6ZI",
            display_name="Blob SEs",
            member_ids=["9e8719d9-276a-4964-9395-a493189a247c"],
        )
    )

    assert "Replaced user group **Blob SEs**" in result
    assert fake.calls == [
        (
            "PUT",
            "/scim/v2/groups/mEhXj6ZI",
            {
                "json_body": {
                    "displayName": "Blob SEs",
                    "members": [{"value": "9e8719d9-276a-4964-9395-a493189a247c"}],
                }
            },
        )
    ]


async def test_replace_user_group_clears_members(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"id": "mEhXj6ZI", "displayName": "Empty", "members": []})
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_replace_user_group(
        ReplaceUserGroupInput(user_group_id="mEhXj6ZI", display_name="Empty", member_ids=[])
    )

    assert "now has 0 member(s)" in result
    assert fake.calls == [("PUT", "/scim/v2/groups/mEhXj6ZI", {"json_body": {"displayName": "Empty", "members": []}})]


def test_replace_user_group_requires_members_without_body() -> None:
    with pytest.raises(ValueError):
        ReplaceUserGroupInput(user_group_id="mEhXj6ZI", display_name="Blob SEs")
    with pytest.raises(ValueError):
        ReplaceUserGroupInput(user_group_id="mEhXj6ZI", member_ids=[])


async def test_replace_user_group_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Invalid request body"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_replace_user_group(
        ReplaceUserGroupInput(user_group_id="mEhXj6ZI", display_name="Blob SEs", member_ids=[])
    )

    assert result.startswith("Error (400):")


# --- omni_update_user_group -----------------------------------------------------


async def test_update_user_group_add_members(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_update_user_group(
        UpdateUserGroupInput(user_group_id="mEhXj6ZI", add_member_ids=["9e8719d9-276a-4964-9395-a493189a247c"])
    )

    assert "Updated user group **Blob Sales**" in result
    assert fake.calls == [
        (
            "PATCH",
            "/scim/v2/groups/mEhXj6ZI",
            {
                "json_body": {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                    "Operations": [
                        {
                            "op": "add",
                            "path": "members",
                            "value": [{"value": "9e8719d9-276a-4964-9395-a493189a247c"}],
                        }
                    ],
                }
            },
        )
    ]


async def test_update_user_group_remove_member(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    await omni_update_user_group(
        UpdateUserGroupInput(user_group_id="mEhXj6ZI", remove_member_ids=["9e8719d9-276a-4964-9395-a493189a247c"])
    )

    assert fake.calls == [
        (
            "PATCH",
            "/scim/v2/groups/mEhXj6ZI",
            {
                "json_body": {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                    "Operations": [
                        {"op": "remove", "path": 'members[value eq "9e8719d9-276a-4964-9395-a493189a247c"]'}
                    ],
                }
            },
        )
    ]


async def test_update_user_group_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=dict(GROUP, displayName="Blob SEs"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    await omni_update_user_group(UpdateUserGroupInput(user_group_id="mEhXj6ZI", display_name="Blob SEs"))

    assert fake.calls == [
        (
            "PATCH",
            "/scim/v2/groups/mEhXj6ZI",
            {
                "json_body": {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                    "Operations": [{"op": "replace", "path": "displayName", "value": "Blob SEs"}],
                }
            },
        )
    ]


async def test_update_user_group_operations_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)
    raw_ops = [{"op": "replace", "path": "displayName", "value": "Custom"}]

    await omni_update_user_group(UpdateUserGroupInput(user_group_id="mEhXj6ZI", operations=raw_ops))

    assert fake.calls == [
        (
            "PATCH",
            "/scim/v2/groups/mEhXj6ZI",
            {"json_body": {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": raw_ops}},
        )
    ]


def test_update_user_group_requires_an_operation() -> None:
    with pytest.raises(ValueError):
        UpdateUserGroupInput(user_group_id="mEhXj6ZI")
    with pytest.raises(ValueError):
        UpdateUserGroupInput(user_group_id="mEhXj6ZI", operations=[])


async def test_update_user_group_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(400, "Invalid patch operations"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_update_user_group(UpdateUserGroupInput(user_group_id="mEhXj6ZI", display_name="X"))

    assert result.startswith("Error (400):")


# --- omni_delete_user_group -----------------------------------------------------


async def test_delete_user_group_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=None)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_delete_user_group(DeleteUserGroupInput(user_group_id="mEhXj6ZI"))

    assert "Deleted user group" in result
    assert "mEhXj6ZI" in result
    assert fake.calls == [("DELETE", "/scim/v2/groups/mEhXj6ZI", {})]


async def test_delete_user_group_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "Group not found"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_delete_user_group(DeleteUserGroupInput(user_group_id="missing"))

    assert result.startswith("Error (404):")


# --- omni_get_user_group_model_roles --------------------------------------------


async def test_get_user_group_model_roles_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP_MODEL_ROLES_BODY)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_get_user_group_model_roles(GetUserGroupModelRolesInput(user_group_id="mEhXj6ZI"))

    assert "QUERIER" in result
    assert "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a" in result
    assert fake.calls == [("GET", "/v1/user-groups/mEhXj6ZI/model-roles", {"params": None})]


async def test_get_user_group_model_roles_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP_MODEL_ROLES_BODY)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    await omni_get_user_group_model_roles(
        GetUserGroupModelRolesInput(
            user_group_id="mEhXj6ZI",
            model_id="7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
            connection_id="bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
        )
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/user-groups/mEhXj6ZI/model-roles",
            {
                "params": {
                    "modelId": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
                    "connectionId": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
                }
            },
        )
    ]


async def test_get_user_group_model_roles_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=GROUP_MODEL_ROLES_BODY)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    payload = json.loads(
        await omni_get_user_group_model_roles(
            GetUserGroupModelRolesInput(user_group_id="mEhXj6ZI", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["userGroupId"] == "mEhXj6ZI"
    assert payload["results"][0]["roleName"] == "QUERIER"


async def test_get_user_group_model_roles_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"userGroupId": "mEhXj6ZI", "results": []})
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_get_user_group_model_roles(GetUserGroupModelRolesInput(user_group_id="mEhXj6ZI"))

    assert "No model role assignments found" in result


async def test_get_user_group_model_roles_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "User group not found in organization"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_get_user_group_model_roles(GetUserGroupModelRolesInput(user_group_id="missing"))

    assert result.startswith("Error (404):")


# --- omni_assign_user_group_model_role -------------------------------------------


async def test_assign_user_group_model_role_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ASSIGN_GROUP_ROLE_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_assign_user_group_model_role(
        AssignUserGroupModelRoleInput(
            user_group_id="mEhXj6ZI",
            connection_id="bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
            model_id="7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
            role_name="QUERIER",
        )
    )

    assert "Assigned role **QUERIER**" in result
    assert "mEhXj6ZI" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/user-groups/mEhXj6ZI/model-roles",
            {
                "json_body": {
                    "roleName": "QUERIER",
                    "connectionId": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
                    "modelId": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
                }
            },
        )
    ]


async def test_assign_user_group_model_role_connection_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payload={
            "userGroupId": "mEhXj6ZI",
            "connectionId": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
            "roleName": "CONNECTION_ADMIN",
        }
    )
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    await omni_assign_user_group_model_role(
        AssignUserGroupModelRoleInput(
            user_group_id="mEhXj6ZI",
            connection_id="bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
            role_name="CONNECTION_ADMIN",
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/user-groups/mEhXj6ZI/model-roles",
            {"json_body": {"roleName": "CONNECTION_ADMIN", "connectionId": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed"}},
        )
    ]


async def test_assign_user_group_model_role_shared_only_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(422, "Only shared and shared_extension models can be assigned model roles"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_assign_user_group_model_role(
        AssignUserGroupModelRoleInput(
            user_group_id="mEhXj6ZI",
            model_id="7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
            role_name="QUERIER",
        )
    )

    assert result.startswith("Error (422):")
    assert "Only shared and shared_extension models can be assigned model roles" in result


# --- omni_get_user_model_roles ---------------------------------------------------


async def test_get_user_model_roles_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=USER_MODEL_ROLES_BODY)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_get_user_model_roles(GetUserModelRolesInput(user_id="9e8719d9-276a-4964-9395-a493189a247c"))

    assert "MODELER" in result
    assert "Super Group" in result
    assert "9633bd79-7bdf-4773-8952-8fdd4098e51c" in result
    assert fake.calls == [("GET", "/v1/users/9e8719d9-276a-4964-9395-a493189a247c/model-roles", {"params": None})]


async def test_get_user_model_roles_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=USER_MODEL_ROLES_BODY)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    payload = json.loads(
        await omni_get_user_model_roles(
            GetUserModelRolesInput(user_id="9e8719d9-276a-4964-9395-a493189a247c", response_format=ResponseFormat.JSON)
        )
    )

    assert payload["membershipId"] == "9633bd79-7bdf-4773-8952-8fdd4098e51c"
    assert len(payload["results"]) == 2


async def test_get_user_model_roles_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "User not found in organization"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_get_user_model_roles(GetUserModelRolesInput(user_id="missing"))

    assert result.startswith("Error (404):")


# --- omni_assign_user_model_role -------------------------------------------------


async def test_assign_user_model_role_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=ASSIGN_USER_ROLE_RESPONSE)
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_assign_user_model_role(
        AssignUserModelRoleInput(
            user_id="9e8719d9-276a-4964-9395-a493189a247c",
            connection_id="bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
            model_id="7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
            role_name="QUERIER",
        )
    )

    assert "Assigned role **QUERIER**" in result
    assert "9e8719d9-276a-4964-9395-a493189a247c" in result
    assert fake.calls == [
        (
            "POST",
            "/v1/users/9e8719d9-276a-4964-9395-a493189a247c/model-roles",
            {
                "json_body": {
                    "roleName": "QUERIER",
                    "connectionId": "bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed",
                    "modelId": "7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
                }
            },
        )
    ]


async def test_assign_user_model_role_shared_only_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(422, "Only shared and shared_extension models can be assigned model roles"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_assign_user_model_role(
        AssignUserModelRoleInput(
            user_id="9e8719d9-276a-4964-9395-a493189a247c",
            model_id="7d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a",
            role_name="QUERIER",
        )
    )

    assert result.startswith("Error (422):")
    assert "Only shared and shared_extension models can be assigned model roles" in result


async def test_assign_user_model_role_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=_http_error(404, "User not found in organization"))
    monkeypatch.setattr("omni_mcp.tools.user_groups.get_client", lambda: fake)

    result = await omni_assign_user_model_role(
        AssignUserModelRoleInput(
            user_id="missing", connection_id="bc1f9c9f-208d-48a2-9ae3-ff80f2c79fed", role_name="VIEWER"
        )
    )

    assert result.startswith("Error (404):")


# --- shared input validation ------------------------------------------------------


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ListUserGroupsInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        GetUserGroupInput(user_group_id="x", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        DeleteUserGroupInput(user_group_id="x", unexpected="x")  # type: ignore[call-arg]


def test_id_fields_reject_empty_strings() -> None:
    with pytest.raises(ValueError):
        GetUserGroupInput(user_group_id="")
    with pytest.raises(ValueError):
        DeleteUserGroupInput(user_group_id="")
    with pytest.raises(ValueError):
        GetUserModelRolesInput(user_id="")


def test_list_user_groups_rejects_non_positive_count() -> None:
    with pytest.raises(ValueError):
        ListUserGroupsInput(count=0)
    with pytest.raises(ValueError):
        ListUserGroupsInput(start_index=0)


def test_create_user_group_display_name_max_length() -> None:
    with pytest.raises(ValueError):
        CreateUserGroupInput(display_name="x" * 65)
