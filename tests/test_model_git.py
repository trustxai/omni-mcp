"""Unit tests for the model git configuration / branch merge tools."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.model_git import (
    REDACTED,
    CreateGitConfigurationInput,
    CreateOrUpdateModelBranchPullRequestInput,
    DeleteGitConfigurationInput,
    GetGitConfigurationInput,
    MergeModelBranchInput,
    SyncModelWithGitInput,
    UpdateGitConfigurationInput,
    omni_create_git_configuration,
    omni_create_or_update_model_branch_pull_request,
    omni_delete_git_configuration,
    omni_get_git_configuration,
    omni_merge_model_branch,
    omni_sync_model_with_git,
    omni_update_git_configuration,
)

MODEL_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
BRANCH_ID = "b2c3d4e5-f6a7-8901-bcde-f23456789012"
GIT_PATH = f"/v1/models/{MODEL_ID}/git"
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI-public-part"
SECRET_TOKEN = "supersecrettokenvalue"  # noqa: S105 — fixture value, not a real credential

GIT_CONFIG: dict[str, Any] = {
    "authMethod": "ssh",
    "baseBranch": "main",
    "branchPerPullRequest": False,
    "gitFollower": False,
    "gitServiceProvider": "github",
    "modelPath": "omni/blobs_r_us",
    "publicKey": PUBLIC_KEY,
    "requirePullRequest": "users-only",
    "cloneUrl": "git@github.com:org/repo.git",
    "webUrl": "https://github.com/org/repo",
    "webhookUrl": "https://app.example.com/api/webhooks/model/xyz",
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


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> _FakeClient:
    monkeypatch.setattr("omni_mcp.tools.model_git.get_client", lambda: fake)
    return fake


def _status_error(status: int, detail: str, method: str = "GET") -> httpx.HTTPStatusError:
    request = httpx.Request(method, f"https://acme.omniapp.co/api{GIT_PATH}")
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# --------------------------------------------------------------------------- get


async def test_get_git_configuration_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GIT_CONFIG))

    result = await omni_get_git_configuration(GetGitConfigurationInput(model_id=MODEL_ID))

    assert fake.calls == [("GET", GIT_PATH, {"params": None})]
    assert "git@github.com:org/repo.git" in result
    assert "users-only" in result
    assert "omni/blobs_r_us" in result
    assert PUBLIC_KEY in result  # public deploy keys are meant to be shown


async def test_get_git_configuration_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=GIT_CONFIG))

    payload = json.loads(
        await omni_get_git_configuration(
            GetGitConfigurationInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON)
        )
    )

    assert payload["modelId"] == MODEL_ID
    assert payload["gitConfiguration"]["cloneUrl"] == "git@github.com:org/repo.git"
    assert payload["gitConfiguration"]["publicKey"] == PUBLIC_KEY


async def test_get_git_configuration_passes_include(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GIT_CONFIG))

    await omni_get_git_configuration(GetGitConfigurationInput(model_id=MODEL_ID, include="webhookSecret"))

    assert fake.calls == [("GET", GIT_PATH, {"params": {"include": "webhookSecret"}})]


async def test_get_git_configuration_masks_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = dict(GIT_CONFIG, webhookSecret="whsec_abc123xyz789")
    _patch(monkeypatch, _FakeClient(payload=payload))

    markdown = await omni_get_git_configuration(GetGitConfigurationInput(model_id=MODEL_ID))
    raw = await omni_get_git_configuration(
        GetGitConfigurationInput(model_id=MODEL_ID, response_format=ResponseFormat.JSON)
    )

    assert "whsec_abc123xyz789" not in markdown
    assert "whsec_abc123xyz789" not in raw
    assert REDACTED in markdown
    assert json.loads(raw)["gitConfiguration"]["webhookSecret"] == REDACTED


async def test_get_git_configuration_encodes_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GIT_CONFIG))

    await omni_get_git_configuration(GetGitConfigurationInput(model_id="weird/id?x"))

    assert fake.calls[0][1] == "/v1/models/weird%2Fid%3Fx/git"


async def test_get_git_configuration_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(404, "Not Found: Git configuration not found for this model")
    _patch(monkeypatch, _FakeClient(exc=error))

    result = await omni_get_git_configuration(GetGitConfigurationInput(model_id=MODEL_ID))

    assert result.startswith("Error (404):")
    assert "Git configuration not found" in result


# ------------------------------------------------------------------------ create


async def test_create_git_configuration_sends_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GIT_CONFIG))

    result = await omni_create_git_configuration(
        CreateGitConfigurationInput(
            model_id=MODEL_ID,
            clone_url="git@github.com:org/repo.git",
            auth_method="ssh",
            base_branch="main",
            git_service_provider="github",
            model_path="omni/blobs_r_us",
            require_pull_request="users-only",
        )
    )

    assert fake.calls == [
        (
            "POST",
            GIT_PATH,
            {
                "json_body": {
                    "cloneUrl": "git@github.com:org/repo.git",
                    "authMethod": "ssh",
                    "baseBranch": "main",
                    "gitServiceProvider": "github",
                    "modelPath": "omni/blobs_r_us",
                    "requirePullRequest": "users-only",
                }
            },
        )
    ]
    assert "Created the git configuration" in result
    assert PUBLIC_KEY in result
    assert "https://app.example.com/api/webhooks/model/xyz" in result


async def test_create_git_configuration_never_echoes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = dict(GIT_CONFIG, authMethod="https_token", publicKey=None, token=SECRET_TOKEN)
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_create_git_configuration(
        CreateGitConfigurationInput(
            model_id=MODEL_ID,
            clone_url="https://github.com/org/repo.git",
            auth_method="https_token",
            token=SECRET_TOKEN,
        )
    )

    assert fake.calls[0][2]["json_body"]["token"] == SECRET_TOKEN  # the API still receives it
    assert SECRET_TOKEN not in result
    assert "Created the git configuration" in result


async def test_create_git_configuration_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(409, "Conflict: Git configuration already exists for this model", method="POST")
    _patch(monkeypatch, _FakeClient(exc=error))

    result = await omni_create_git_configuration(
        CreateGitConfigurationInput(model_id=MODEL_ID, clone_url="git@github.com:org/repo.git")
    )

    assert result.startswith("Error (409):")
    assert "already exists" in result


# ------------------------------------------------------------------------ update


async def test_update_git_configuration_sends_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = dict(GIT_CONFIG, baseBranch="develop", requirePullRequest="always")
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_update_git_configuration(
        UpdateGitConfigurationInput(model_id=MODEL_ID, base_branch="develop", require_pull_request="always")
    )

    assert fake.calls == [("PATCH", GIT_PATH, {"json_body": {"baseBranch": "develop", "requirePullRequest": "always"}})]
    assert "Updated the git configuration" in result
    assert "`baseBranch`" in result
    assert "develop" in result
    assert "always" in result


async def test_update_git_configuration_masks_rotated_token(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = dict(GIT_CONFIG, authMethod="https_token", token=SECRET_TOKEN)
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_update_git_configuration(
        UpdateGitConfigurationInput(model_id=MODEL_ID, auth_method="https_token", token=SECRET_TOKEN)
    )

    assert fake.calls[0][2]["json_body"] == {"authMethod": "https_token", "token": SECRET_TOKEN}
    assert SECRET_TOKEN not in result
    assert "Credential: updated" in result


async def test_update_git_configuration_requires_a_field(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GIT_CONFIG))

    result = await omni_update_git_configuration(UpdateGitConfigurationInput(model_id=MODEL_ID))

    # A client-side refusal, not an API answer — it must not impersonate one.
    assert result.startswith("Error: no fields to update")
    assert "(400)" not in result
    assert fake.calls == []


async def test_update_git_configuration_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(403, "Forbidden: Requires MANAGE_MODEL permission", method="PATCH")
    _patch(monkeypatch, _FakeClient(exc=error))

    result = await omni_update_git_configuration(UpdateGitConfigurationInput(model_id=MODEL_ID, base_branch="develop"))

    assert result.startswith("Error (403):")
    assert "MANAGE_MODEL" in result


# ------------------------------------------------------------------------ delete


async def test_delete_git_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_delete_git_configuration(DeleteGitConfigurationInput(model_id=MODEL_ID))

    assert fake.calls == [("DELETE", GIT_PATH, {})]
    assert "Deleted the git configuration" in result
    assert MODEL_ID in result


async def test_delete_git_configuration_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(404, "Not Found: Model does not exist", method="DELETE")
    _patch(monkeypatch, _FakeClient(exc=error))

    result = await omni_delete_git_configuration(DeleteGitConfigurationInput(model_id=MODEL_ID))

    assert result.startswith("Error (404):")


# -------------------------------------------------------------------------- sync


async def test_sync_model_with_git_without_message(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"didSync": True, "gitSha": "abc123def456", "inSync": True, "message": "Model synced successfully"}
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_sync_model_with_git(SyncModelWithGitInput(model_id=MODEL_ID))

    assert fake.calls == [("POST", f"{GIT_PATH}/sync", {"json_body": None})]
    assert "abc123def456" in result
    assert "Model synced successfully" in result


async def test_sync_model_with_git_sends_commit_message(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"didSync": False, "gitSha": None, "inSync": True, "message": "Already in sync"}
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_sync_model_with_git(
        SyncModelWithGitInput(model_id=MODEL_ID, commit_message="Updated model configuration")
    )

    assert fake.calls == [("POST", f"{GIT_PATH}/sync", {"json_body": {"commitMessage": "Updated model configuration"}})]
    assert "No sync needed" in result
    assert "Already in sync" in result


# ---------------------------------------------------------------- pull requests


async def test_create_or_update_pull_request_returns_url(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "pr_url": "https://github.com/myorg/repo/compare/main...feature?expand=1",
        "git_sha": "abc123def456",
        "in_sync": True,
        "did_sync": True,
    }
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_create_or_update_model_branch_pull_request(
        CreateOrUpdateModelBranchPullRequestInput(
            model_id=MODEL_ID, branch_id=BRANCH_ID, commit_message="Add new customer dimension"
        )
    )

    assert fake.calls == [
        (
            "POST",
            f"{GIT_PATH}/commit",
            {"json_body": {"branch_id": BRANCH_ID, "commit_message": "Add new customer dimension"}},
        )
    ]
    assert "https://github.com/myorg/repo/compare/main...feature?expand=1" in result
    assert "abc123def456" in result


async def test_create_or_update_pull_request_create_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"pr_url": "https://example.com/pr/1"}))

    await omni_create_or_update_model_branch_pull_request(
        CreateOrUpdateModelBranchPullRequestInput(
            model_id=MODEL_ID, branch_id=BRANCH_ID, commit_message="Initial push", allow_branch_exists=False
        )
    )

    assert fake.calls[0][2]["json_body"] == {
        "branch_id": BRANCH_ID,
        "commit_message": "Initial push",
        "allow_branch_exists": False,
    }


async def test_create_or_update_pull_request_rejects_conflicting_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={}))

    result = await omni_create_or_update_model_branch_pull_request(
        CreateOrUpdateModelBranchPullRequestInput(
            model_id=MODEL_ID,
            branch_id=BRANCH_ID,
            commit_message="Add new customer dimension",
            allow_branch_exists=False,
            require_branch_exists=True,
        )
    )

    # A client-side refusal, not an API answer — it must not impersonate one.
    assert result.startswith("Error: cannot set both")
    assert "(400)" not in result
    assert fake.calls == []


async def test_create_or_update_pull_request_branch_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(404, "Not Found: Branch does not exist", method="POST")
    _patch(monkeypatch, _FakeClient(exc=error))

    result = await omni_create_or_update_model_branch_pull_request(
        CreateOrUpdateModelBranchPullRequestInput(
            model_id=MODEL_ID, branch_id=BRANCH_ID, commit_message="Add new customer dimension"
        )
    )

    assert result.startswith("Error (404):")
    assert "Branch does not exist" in result


# ------------------------------------------------------------------------- merge


async def test_merge_model_branch_encodes_branch_name(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"success": True, "published_drafts_count": 2, "failed_drafts_count": 0, "git_synced": True}
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_merge_model_branch(
        MergeModelBranchInput(
            model_id=MODEL_ID,
            branch_name="feature/add-revenue-metrics",
            delete_branch=True,
            publish_drafts=True,
            commit_message="Merged revenue metrics branch via CI/CD pipeline",
        )
    )

    assert fake.calls == [
        (
            "POST",
            f"/v1/models/{MODEL_ID}/branch/feature%2Fadd-revenue-metrics/merge",
            {
                "json_body": {
                    "delete_branch": True,
                    "publish_drafts": True,
                    "commit_message": "Merged revenue metrics branch via CI/CD pipeline",
                }
            },
        )
    ]
    assert "Merged branch `feature/add-revenue-metrics`" in result
    assert "Drafts published: 2" in result
    assert "Synced to git: yes" in result


async def test_merge_model_branch_without_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True, "git_synced": False}))

    result = await omni_merge_model_branch(MergeModelBranchInput(model_id=MODEL_ID, branch_name="main-fix"))

    assert fake.calls == [("POST", f"/v1/models/{MODEL_ID}/branch/main-fix/merge", {"json_body": None})]
    assert "omni_sync_model_with_git" in result


async def test_merge_model_branch_force_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True, "git_synced": False}))

    await omni_merge_model_branch(
        MergeModelBranchInput(model_id=MODEL_ID, branch_name="hotfix/typo", force_override_git_settings=True)
    )

    assert fake.calls[0][2]["json_body"] == {"force_override_git_settings": True}


async def test_merge_model_branch_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(403, "Forbidden: Insufficient permissions", method="POST")
    _patch(monkeypatch, _FakeClient(exc=error))

    result = await omni_merge_model_branch(
        MergeModelBranchInput(model_id=MODEL_ID, branch_name="feature/x", force_override_git_settings=True)
    )

    assert result.startswith("Error (403):")


async def test_merge_model_branch_rejected_for_pr_required_model(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(400, "Bad Request: Merge not allowed for this model configuration", method="POST")
    _patch(monkeypatch, _FakeClient(exc=error))

    result = await omni_merge_model_branch(MergeModelBranchInput(model_id=MODEL_ID, branch_name="feature/x"))

    assert result.startswith("Error (400):")
    assert "Merge not allowed" in result


# -------------------------------------------------------------------- validation


def test_enums_are_validated() -> None:
    with pytest.raises(ValueError):
        CreateGitConfigurationInput(model_id=MODEL_ID, clone_url="git@x:y.git", auth_method="oauth")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CreateGitConfigurationInput(
            model_id=MODEL_ID,
            clone_url="git@x:y.git",
            require_pull_request="sometimes",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        CreateGitConfigurationInput(
            model_id=MODEL_ID,
            clone_url="git@x:y.git",
            git_service_provider="svn",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        UpdateGitConfigurationInput(model_id=MODEL_ID, auth_method="basic")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        GetGitConfigurationInput(model_id=MODEL_ID, include="publicKey")  # type: ignore[arg-type]


def test_required_fields_and_constraints() -> None:
    with pytest.raises(ValueError):
        GetGitConfigurationInput(model_id="")
    with pytest.raises(ValueError):
        CreateGitConfigurationInput(model_id=MODEL_ID, clone_url="")
    with pytest.raises(ValueError):
        CreateOrUpdateModelBranchPullRequestInput(model_id=MODEL_ID, branch_id=BRANCH_ID, commit_message="")
    with pytest.raises(ValueError):
        MergeModelBranchInput(model_id=MODEL_ID, branch_name="")
    with pytest.raises(ValueError):
        # The API constrains tokens to `^[a-zA-Z0-9_\-.]+$`.
        CreateGitConfigurationInput(model_id=MODEL_ID, clone_url="git@x:y.git", token="not a valid token!")


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        GetGitConfigurationInput(model_id=MODEL_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        DeleteGitConfigurationInput(model_id=MODEL_ID, force=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        SyncModelWithGitInput(model_id=MODEL_ID, message="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        MergeModelBranchInput(model_id=MODEL_ID, branch_name="x", squash=True)  # type: ignore[call-arg]


def test_deploy_private_key_keeps_its_trailing_newline() -> None:
    """A PEM key is byte-exact — stripping its trailing newline breaks the key."""
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"

    for model in (CreateGitConfigurationInput, UpdateGitConfigurationInput):
        params = model(model_id=MODEL_ID, clone_url="git@github.com:org/repo.git", deploy_private_key=pem)
        assert params.deploy_private_key == pem


async def test_create_git_configuration_strips_userinfo_from_the_clone_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTPS clone URL can carry a token; it must never be echoed back."""
    secret_url = "https://x-access-token:ghp_supersecrettoken@github.com/org/repo.git"
    _patch(monkeypatch, _FakeClient(payload={"cloneUrl": secret_url, "authMethod": "https_token"}))

    result = await omni_create_git_configuration(
        CreateGitConfigurationInput(model_id=MODEL_ID, clone_url=secret_url, auth_method="https_token")
    )

    assert "ghp_supersecrettoken" not in result
    assert "github.com/org/repo.git" in result


async def test_merge_reports_an_unsynced_git_only_when_the_api_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The advisory keys off `git_synced is False`, not off a missing key."""
    _patch(monkeypatch, _FakeClient(payload={"success": True, "published_drafts_count": 1}))

    result = await omni_merge_model_branch(MergeModelBranchInput(model_id=MODEL_ID, branch_name="feature/x"))

    assert "Git was not synced" not in result

    _patch(monkeypatch, _FakeClient(payload={"success": True, "git_synced": False}))

    result = await omni_merge_model_branch(MergeModelBranchInput(model_id=MODEL_ID, branch_name="feature/x"))

    assert "Git was not synced" in result
