"""Tools for the model Git configuration and model branch merge endpoints.

These tools drive the whole promotion flow Omni exposes over the API — the
path a modelling change takes from a branch to the shared model and the
repository:

1. **Configure git once per shared model** — `omni_create_git_configuration`.
   Inspect it later with `omni_get_git_configuration`, change it with
   `omni_update_git_configuration`, disconnect it with
   `omni_delete_git_configuration`.
2. **Branch, then edit the model YAML on that branch.** Creating the Omni
   branch and editing its files belongs to the model tools; this module takes
   over once the branch exists.
3. **Open or update the pull request** —
   `omni_create_or_update_model_branch_pull_request` pushes the branch's model
   contents to git and creates the git branch + pull request (or adds a commit
   to an existing one). It returns the pull request URL.
4. **Merge the branch** — once the pull request is approved and merged in the
   git provider, `omni_merge_model_branch` merges the Omni branch into the
   shared model and publishes its attached drafts.
5. **Sync with the repository** — `omni_sync_model_with_git` pulls the latest
   repository state into the shared model. Use it after changes land in git
   outside Omni, or to confirm the model is in sync.

Removed endpoints: none. Every operation in this area is live — the API
publishes no `410 Gone` / `Sunset` response for model git configuration or for
branch merge, so all seven operations are implemented here. One *field* is
deprecated: the request and response field `sshUrl` is superseded by
`cloneUrl`, which is what these tools send and report.

Secrets: these tools never echo a deploy token, PAT, private key, or webhook
secret back to the caller. Public deploy keys (`publicKey`) are shown — they
are meant to be copied into the git provider — while anything named
token/secret/password/passphrase/private key is redacted in both markdown and
JSON output.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from omni_mcp.client import get_client
from omni_mcp.errors import handle_api_error
from omni_mcp.formatters import ResponseFormat, to_json, truncate_result
from omni_mcp.server import mcp

#: Substrings (lower-cased, underscores stripped) that mark a value as a secret.
SENSITIVE_KEY_PARTS: tuple[str, ...] = ("token", "secret", "password", "passphrase", "privatekey")

#: What a redacted value is replaced with in every output.
REDACTED = "***redacted***"

#: Auth methods accepted by the git configuration endpoints.
AuthMethod = Literal["ssh", "https_token"]

#: Git providers accepted on write; `auto` detects the provider from the URL.
GitServiceProvider = Literal["auto", "github", "gitlab", "azure_devops", "bitbucket", "bitbucket_datacenter"]

#: When Omni requires a pull request for model changes.
RequirePullRequest = Literal["always", "users-only", "never"]

#: A PEM key must keep its exact bytes — leading/trailing newlines included.
PemStr = Annotated[str, StringConstraints(strip_whitespace=False)]


def _is_sensitive(key: str) -> bool:
    """True when a response key names a credential rather than a public value.

    `publicKey` is deliberately *not* sensitive: the deploy public key exists to
    be copied into the git provider.
    """
    lowered = key.replace("_", "").replace("-", "").lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _redact(value: Any) -> Any:
    """Recursively replace every credential-looking value with `REDACTED`."""
    if isinstance(value, Mapping):
        return {
            str(key): (REDACTED if _is_sensitive(str(key)) and item is not None else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _compact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop `None` values so unset optional fields are never sent to the API."""
    return {key: value for key, value in payload.items() if value is not None}


def _path_segment(value: str) -> str:
    """Percent-encode one path segment (branch names legitimately contain `/`)."""
    return quote(value, safe="")


def _as_dict(payload: Any) -> dict[str, Any]:
    """Normalise a decoded response body to a dict."""
    return payload if isinstance(payload, dict) else {}


def _safe_url(value: Any) -> Any:
    """Replace the `user:password@` part of an HTTP(S) clone URL with the redaction marker.

    `https://x-access-token:<PAT>@github.com/org/repo.git` is a valid clone URL,
    so a token can reach this server both in the caller's `clone_url` and in the
    API's `cloneUrl` — and neither may be echoed back. SSH URLs
    (`git@github.com:org/repo.git`, `ssh://git@host/repo`) carry a user name
    rather than a credential and are left alone.
    """
    if not isinstance(value, str) or "@" not in value:
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or "@" not in parsed.netloc:
        return value
    host = parsed.netloc.rsplit("@", 1)[1]
    return urlunsplit((parsed.scheme, f"{REDACTED}@{host}", parsed.path, parsed.query, parsed.fragment))


def _flag(value: Any) -> str:
    """Render a tri-state boolean from the API."""
    if value is None:
        return "not reported"
    return "yes" if bool(value) else "no"


def _text(value: Any, fallback: str = "not set") -> str:
    """Render a scalar for markdown, collapsing `None`/empty to a fallback."""
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _config_markdown(config: Mapping[str, Any], title: str) -> str:
    """Render a `ModelGitConfig` payload, with every credential redacted."""
    lines = [
        f"# {title}",
        "",
        f"- Repository (`cloneUrl`): `{_text(_safe_url(config.get('cloneUrl') or config.get('sshUrl')))}`",
        f"- Auth method: **{_text(config.get('authMethod'), 'not reported')}**",
        f"- Git provider: **{_text(config.get('gitServiceProvider'), 'not reported')}**",
        f"- Base branch: `{_text(config.get('baseBranch'))}`",
        f"- Pull requests required: **{_text(config.get('requirePullRequest'), 'not reported')}**",
        f"- Branch per pull request: {_flag(config.get('branchPerPullRequest'))}",
        f"- Git follower (shared model read-only): {_flag(config.get('gitFollower'))}",
        f"- Model path in the repository: `{_text(config.get('modelPath'))}`",
        f"- Web URL: {_text(config.get('webUrl'))}",
        f"- Webhook URL (configure this in the git provider): {_text(config.get('webhookUrl'))}",
    ]
    if config.get("webhookSecret") is not None:
        lines.append(f"- Webhook secret: configured (value {REDACTED} — read it from the Omni UI when you need it)")
    public_key = config.get("publicKey")
    if public_key:
        lines.extend(
            [
                "",
                "## Deploy public key",
                "",
                "Authorise this key in the repository (read/write deploy key):",
                "",
                "```",
                str(public_key),
                "```",
            ]
        )
    return "\n".join(lines)


class GetGitConfigurationInput(BaseModel):
    """Input for `omni_get_git_configuration`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(
        ...,
        min_length=1,
        description="The unique identifier (UUID) of the shared model, e.g. `a1b2c3d4-e5f6-7890-abcd-ef1234567890`.",
    )
    include: Literal["webhookSecret"] | None = Field(
        default=None,
        description=(
            "Additional field to include in the response. Only `webhookSecret` is accepted, and it merely confirms "
            "that a webhook secret exists — the value itself is always redacted by this server."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="`markdown` for a readable summary, `json` for the raw configuration (credentials redacted).",
    )


class CreateGitConfigurationInput(BaseModel):
    """Input for `omni_create_git_configuration`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the shared model.")
    clone_url: str = Field(
        ...,
        min_length=1,
        description=(
            "Clone URL of the git repository. Varies with `auth_method`: `ssh` takes a string starting with "
            "`git@...` (e.g. `git@github.com:org/repo.git`), `https_token` a string starting with `https://...`. "
            "Supersedes the deprecated `sshUrl` body field."
        ),
    )
    auth_method: AuthMethod | None = Field(
        default=None,
        description="Authentication method: `ssh` for a deploy key, `https_token` for a deploy token/PAT.",
    )
    token: str | None = Field(
        default=None,
        max_length=1000,
        pattern=r"^[a-zA-Z0-9_\-.]+$",
        description=(
            "**Required when `auth_method` is `https_token`.** HTTPS token (deploy token value, PAT, …). "
            "Sent to the API and never echoed back in any tool output."
        ),
    )
    base_branch: str | None = Field(
        default=None,
        description="The target branch for Omni pull requests. The API defaults to `main` when omitted.",
    )
    branch_per_pull_request: bool | None = Field(
        default=None,
        description=(
            "If `true`, all pull requests create a branch in Omni, even those opened outside of Omni. "
            "API default: `false`."
        ),
    )
    git_follower: bool | None = Field(
        default=None,
        description=(
            "If `true`, the shared model becomes read-only and can only be updated by merging pull requests to "
            "the base branch. API default: `false`."
        ),
    )
    git_service_provider: GitServiceProvider | None = Field(
        default=None,
        description=(
            "The git provider: `auto` (detect from the clone URL), `github`, `gitlab`, `azure_devops`, "
            "`bitbucket` (Cloud) or `bitbucket_datacenter` (self-hosted). API default: `auto`."
        ),
    )
    model_path: str | None = Field(
        default=None,
        description="Path to the model files within the repository, e.g. `omni/blobs_r_us`.",
    )
    require_pull_request: RequirePullRequest | None = Field(
        default=None,
        description=(
            "When pull requests are required: `always` (all changes), `users-only` (user-initiated changes only), "
            "`never`. API default: `never`."
        ),
    )
    web_url: str | None = Field(
        default=None,
        description=(
            "Custom web URL for the repository, e.g. `https://github.com/org/repo`. Use it when the clone URL goes "
            "through a tunnel/VPC and differs from the inferred HTTPS address."
        ),
    )
    deploy_private_key: PemStr | None = Field(
        default=None,
        description=(
            "**SSH authentication only.** Your own RSA or ED25519 private key in PEM format (OpenSSH, PKCS#1 or "
            "PKCS#8), instead of an Omni-generated keypair. Authorise the matching public key in the repository "
            "first. Never echoed back."
        ),
    )
    deploy_key_passphrase: str | None = Field(
        default=None,
        description=(
            "**SSH authentication only.** Passphrase used once to decrypt an encrypted `deploy_private_key`; the "
            "passphrase itself is not retained by Omni. Only valid together with `deploy_private_key`."
        ),
    )


class UpdateGitConfigurationInput(BaseModel):
    """Input for `omni_update_git_configuration`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the shared model.")
    clone_url: str | None = Field(
        default=None,
        description=(
            "New clone URL of the git repository (`git@...` for `ssh`, `https://...` for `https_token`). "
            "Supersedes the deprecated `sshUrl` body field."
        ),
    )
    auth_method: AuthMethod | None = Field(
        default=None,
        description="The authentication method to change to: `ssh` for a deploy key, `https_token` for a token/PAT.",
    )
    token: str | None = Field(
        default=None,
        max_length=1000,
        pattern=r"^[a-zA-Z0-9_\-.]+$",
        description=(
            "**Required when changing `auth_method` to `https_token`.** HTTPS token (deploy token value, PAT, …). "
            "Also how you rotate an existing token. Never echoed back."
        ),
    )
    base_branch: str | None = Field(default=None, description="The target branch for Omni pull requests.")
    branch_per_pull_request: bool | None = Field(
        default=None,
        description="If `true`, all pull requests create a branch in Omni, even those opened outside of Omni.",
    )
    git_follower: bool | None = Field(
        default=None,
        description=(
            "If `true`, the shared model becomes read-only and can only be updated by merging pull requests to "
            "the base branch."
        ),
    )
    git_service_provider: GitServiceProvider | None = Field(
        default=None,
        description=(
            "The git provider: `auto` (detect from the clone URL), `github`, `gitlab`, `azure_devops`, "
            "`bitbucket` (Cloud) or `bitbucket_datacenter` (self-hosted)."
        ),
    )
    model_path: str | None = Field(
        default=None, description="Path to the model files within the repository, e.g. `omni/blobs_r_us`."
    )
    require_pull_request: RequirePullRequest | None = Field(
        default=None,
        description=(
            "When pull requests are required: `always` (all changes), `users-only` (user-initiated changes only), "
            "`never`."
        ),
    )
    web_url: str | None = Field(
        default=None,
        description=(
            "Custom web URL for the repository. Use it when the clone URL goes through a tunnel/VPC and differs "
            "from the inferred HTTPS address."
        ),
    )
    deploy_private_key: PemStr | None = Field(
        default=None,
        description=(
            "**SSH authentication only.** Replacement RSA or ED25519 private key in PEM format; the new key takes "
            "effect on the next git operation. Authorise the matching public key in the repository first. Never "
            "echoed back."
        ),
    )
    deploy_key_passphrase: str | None = Field(
        default=None,
        description=(
            "**SSH authentication only.** Passphrase used once to decrypt an encrypted `deploy_private_key`. Only "
            "valid together with `deploy_private_key`."
        ),
    )


class DeleteGitConfigurationInput(BaseModel):
    """Input for `omni_delete_git_configuration`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(
        ..., min_length=1, description="The unique identifier (UUID) of the shared model to disconnect from git."
    )


class SyncModelWithGitInput(BaseModel):
    """Input for `omni_sync_model_with_git`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the shared model.")
    commit_message: str | None = Field(
        default=None,
        description="Optional commit message used when pushing changes to the repository.",
    )


class CreateOrUpdateModelBranchPullRequestInput(BaseModel):
    """Input for `omni_create_or_update_model_branch_pull_request`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the shared model.")
    branch_id: str = Field(
        ...,
        min_length=1,
        description="UUID of the Omni branch to commit, e.g. `a1b2c3d4-e5f6-7890-abcd-ef1234567890`.",
    )
    commit_message: str = Field(
        ..., min_length=1, description="Commit message for the git commit, e.g. `Add new customer dimension`."
    )
    allow_branch_exists: bool | None = Field(
        default=None,
        description=(
            "Set `false` for **create-only mode**: the call fails if the git branch already exists. "
            "API default: `true` (create or update)."
        ),
    )
    require_branch_exists: bool | None = Field(
        default=None,
        description=(
            "Set `true` for **update-only mode**: the call fails if the git branch does not exist. "
            "API default: `false`. Cannot be combined with `allow_branch_exists=false`."
        ),
    )


class MergeModelBranchInput(BaseModel):
    """Input for `omni_merge_model_branch`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    model_id: str = Field(..., min_length=1, description="The unique identifier (UUID) of the model.")
    branch_name: str = Field(
        ...,
        min_length=1,
        description="The name of the branch to merge, e.g. `feature/add-revenue-metrics`. Slashes are encoded for you.",
    )
    delete_branch: bool | None = Field(
        default=None, description="Delete the branch after merging. API default: `false`."
    )
    publish_drafts: bool | None = Field(
        default=None,
        description="When enabled, publish branch-attached drafts while merging. API default: `true`.",
    )
    commit_message: str | None = Field(
        default=None,
        description='Custom commit message for the git sync. Defaults to `"branch <name> merged via API"`.',
    )
    force_override_git_settings: bool | None = Field(
        default=None,
        description=(
            "**Requires Connection Admin or Organization Admin permissions** (lesser roles get `403 Forbidden`). "
            "Allows the merge for PR-required or git-follower models. The merge succeeds but git is NOT synced, to "
            "avoid force-pushing to the base branch. API default: `false`."
        ),
    )


@mcp.tool(
    name="omni_get_git_configuration",
    annotations=ToolAnnotations(
        title="Get Model Git Configuration",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_get_git_configuration(params: GetGitConfigurationInput) -> str:
    """Retrieve the Git configuration of a shared model.

    Calls `GET /v1/models/{modelId}/git` and reports the repository, auth
    method, base branch, pull-request policy, model path, webhook URL and the
    deploy public key. This is the first call of the promotion flow: it tells
    you whether a model is connected to git at all, whether pull requests are
    required (`always` / `users-only` / `never`), and whether the model is a
    git follower (read-only, updated only by merging pull requests).

    When to Use:
    - Before opening a pull request or merging a branch, to learn the model's
      promotion rules.
    - To fetch the webhook URL to configure in the git provider, or the deploy
      public key to authorise in the repository.
    - To confirm a configuration change landed after
      `omni_create_git_configuration` / `omni_update_git_configuration`.

    When NOT to Use:
    - To read a deploy token, PAT, private key or webhook secret — this server
      never returns credential values.
    - To list models or branches (use the model tools).

    Returns:
    A markdown summary of the configuration with credentials redacted, or the
    raw payload (also redacted) when `response_format` is `json`. On failure,
    an `Error ...` string.

    Examples:
    - Read the configuration: `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - Confirm a webhook secret exists:
      `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "include": "webhookSecret"}}`
    - Raw payload: `{"params": {"model_id": "a1b2...", "response_format": "json"}}`

    Error Handling:
    Requires the `MANAGE_MODEL` permission — `403 Forbidden: Requires
    MANAGE_MODEL permission` otherwise. `400` means `modelId` is not a valid
    UUID. `404` is either `Model does not exist` or `Git configuration not
    found for this model` — the latter means the model simply is not connected
    to git yet, so create the configuration first.
    """
    try:
        query: dict[str, Any] = {}
        if params.include:
            query["include"] = params.include
        payload = await get_client().request_json(
            "GET",
            f"/v1/models/{_path_segment(params.model_id)}/git",
            params=query or None,
        )
        config = _as_dict(payload)
        safe = _as_dict(_redact(config))
        if params.response_format is ResponseFormat.JSON:
            return truncate_result(to_json({"modelId": params.model_id, "gitConfiguration": safe}))
        return truncate_result(_config_markdown(safe, f"Git configuration — model `{params.model_id}`"))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_git_configuration",
    annotations=ToolAnnotations(
        title="Create Model Git Configuration",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_git_configuration(params: CreateGitConfigurationInput) -> str:
    """Connect a shared model to a Git repository.

    Calls `POST /v1/models/{modelId}/git`. This is step 1 of the promotion
    flow — once a model is connected, changes travel: branch → edit the model
    YAML → `omni_create_or_update_model_branch_pull_request` → merge the pull
    request in the provider → `omni_merge_model_branch` →
    `omni_sync_model_with_git`.

    With `auth_method: ssh` Omni generates a deploy keypair and returns the
    public key (unless you supply your own `deploy_private_key`); authorise it
    in the repository with write access. With `auth_method: https_token` you
    must pass `token`. Configure the returned webhook URL in the git provider
    so pushes reach Omni.

    When to Use:
    - The first time a shared model is put under version control.
    - When setting `require_pull_request` and `git_follower` to enforce a
      review-before-merge promotion policy.

    When NOT to Use:
    - When the model already has a git configuration — that returns `409
      Conflict`; use `omni_update_git_configuration` instead.
    - To rotate only a token or key — that is an update, not a create.

    Returns:
    A short confirmation with the model id, repository, base branch, provider,
    pull-request policy, the webhook URL to configure, and the deploy public
    key when the API returns one. Credentials are never echoed. On failure, an
    `Error ...` string.

    Examples:
    - SSH deploy key:
      `{"params": {"model_id": "a1b2...", "clone_url": "git@github.com:org/repo.git", "auth_method": "ssh",
      "base_branch": "main", "git_service_provider": "github", "model_path": "omni/blobs_r_us",
      "require_pull_request": "users-only"}}`
    - HTTPS token:
      `{"params": {"model_id": "a1b2...", "clone_url": "https://github.com/org/repo.git",
      "auth_method": "https_token", "token": "REDACTED_TOKEN_VALUE"}}`

    Error Handling:
    Requires the `MANAGE_MODEL` permission (`403 Forbidden: Requires
    MANAGE_MODEL permission`). `400` covers an invalid `modelId`, an invalid
    clone URL, an invalid `require_pull_request` value, and the deploy-key
    cases: `Invalid key format`, `Wrong passphrase`, `Encrypted key requires
    deployKeyPassphrase`, `deployKeyPassphrase cannot be provided without
    deployPrivateKey`, and `deployPrivateKey not supported with authMethod:
    https_token`. `404` means the model does not exist; `409` means a git
    configuration already exists for it.
    """
    try:
        body = _compact(
            {
                "cloneUrl": params.clone_url,
                "authMethod": params.auth_method,
                "token": params.token,
                "baseBranch": params.base_branch,
                "branchPerPullRequest": params.branch_per_pull_request,
                "gitFollower": params.git_follower,
                "gitServiceProvider": params.git_service_provider,
                "modelPath": params.model_path,
                "requirePullRequest": params.require_pull_request,
                "webUrl": params.web_url,
                "deployPrivateKey": params.deploy_private_key,
                "deployKeyPassphrase": params.deploy_key_passphrase,
            }
        )
        payload = await get_client().request_json(
            "POST", f"/v1/models/{_path_segment(params.model_id)}/git", json_body=body
        )
        config = _as_dict(_redact(_as_dict(payload)))
        lines = [
            f"Created the git configuration for model `{params.model_id}`.",
            "",
            f"- Repository: `{_text(_safe_url(config.get('cloneUrl') or params.clone_url))}`",
            f"- Auth method: **{_text(config.get('authMethod') or params.auth_method, 'not reported')}**",
            f"- Provider: **{_text(config.get('gitServiceProvider'), 'not reported')}**",
            f"- Base branch: `{_text(config.get('baseBranch'))}`",
            f"- Pull requests required: **{_text(config.get('requirePullRequest'), 'not reported')}**",
            f"- Git follower: {_flag(config.get('gitFollower'))}",
            f"- Webhook URL to configure in the git provider: {_text(config.get('webhookUrl'))}",
        ]
        public_key = config.get("publicKey")
        if public_key:
            lines.extend(["", "Authorise this deploy public key in the repository (write access):", "", "```"])
            lines.extend([str(public_key), "```"])
        lines.extend(
            [
                "",
                "Next: create a model branch, edit its YAML, then call "
                "`omni_create_or_update_model_branch_pull_request`.",
            ]
        )
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_update_git_configuration",
    annotations=ToolAnnotations(
        title="Update Model Git Configuration",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_update_git_configuration(params: UpdateGitConfigurationInput) -> str:
    """Change a shared model's Git configuration; only supplied fields change.

    Calls `PATCH /v1/models/{modelId}/git`. Use it to tighten or relax the
    promotion policy (`require_pull_request`, `git_follower`), retarget the
    base branch, move the model path, switch the auth method, or rotate a
    credential (`token` for HTTPS, `deploy_private_key` for SSH — authorise the
    matching public key in the repository first; a new key takes effect on the
    next git operation).

    When to Use:
    - To require pull requests (`always` / `users-only`) once a model is ready
      for a reviewed promotion flow.
    - To rotate a deploy token or SSH deploy key.
    - To point the model at a different base branch, repository URL or path.

    When NOT to Use:
    - When the model has no git configuration yet — that returns `404`; use
      `omni_create_git_configuration`.
    - To disconnect git entirely — use `omni_delete_git_configuration`.

    Returns:
    A short confirmation naming the model and the fields that were updated
    (credential values are shown as `updated`, never echoed), plus the
    resulting policy. On failure, an `Error ...` string.

    Examples:
    - Require pull requests on a different base branch:
      `{"params": {"model_id": "a1b2...", "base_branch": "develop", "require_pull_request": "always"}}`
    - Rotate an HTTPS token:
      `{"params": {"model_id": "a1b2...", "auth_method": "https_token", "token": "REDACTED_TOKEN_VALUE"}}`
    - Make the model a git follower (read-only):
      `{"params": {"model_id": "a1b2...", "git_follower": true}}`

    Error Handling:
    Requires the `MANAGE_MODEL` permission (`403`). `400` covers an invalid
    `modelId`, `Invalid cloneUrl format`, `Invalid requirePullRequest value`
    and the deploy-key cases (`Invalid key format`, `Wrong passphrase`,
    `Encrypted key requires deployKeyPassphrase`, `deployKeyPassphrase cannot
    be provided without deployPrivateKey`, `deployPrivateKey not supported with
    authMethod: https_token`). `404` means the model does not exist or has no
    git configuration. `clone_url` is sent as `cloneUrl`; on an instance old
    enough to reject that key, the repository URL is the deprecated `sshUrl`
    field instead.
    """
    try:
        # `cloneUrl` is the modern name of the deprecated `sshUrl` body field —
        # the API validates it under that name (`Bad Request: Invalid cloneUrl
        # format`), so that is what this tool sends.
        body = _compact(
            {
                "cloneUrl": params.clone_url,
                "authMethod": params.auth_method,
                "token": params.token,
                "baseBranch": params.base_branch,
                "branchPerPullRequest": params.branch_per_pull_request,
                "gitFollower": params.git_follower,
                "gitServiceProvider": params.git_service_provider,
                "modelPath": params.model_path,
                "requirePullRequest": params.require_pull_request,
                "webUrl": params.web_url,
                "deployPrivateKey": params.deploy_private_key,
                "deployKeyPassphrase": params.deploy_key_passphrase,
            }
        )
        if not body:
            return "Error: no fields to update — supply at least one field besides `model_id`. No request was sent."
        payload = await get_client().request_json(
            "PATCH", f"/v1/models/{_path_segment(params.model_id)}/git", json_body=body
        )
        config = _as_dict(_redact(_as_dict(payload)))
        updated = ", ".join(f"`{key}`" for key in sorted(body))
        lines = [
            f"Updated the git configuration for model `{params.model_id}` — fields updated: {updated}.",
            "",
            f"- Repository: `{_text(_safe_url(config.get('cloneUrl') or config.get('sshUrl')))}`",
            f"- Auth method: **{_text(config.get('authMethod'), 'not reported')}**",
            f"- Base branch: `{_text(config.get('baseBranch'))}`",
            f"- Pull requests required: **{_text(config.get('requirePullRequest'), 'not reported')}**",
            f"- Git follower: {_flag(config.get('gitFollower'))}",
            f"- Model path: `{_text(config.get('modelPath'))}`",
        ]
        if "token" in body or "deployPrivateKey" in body:
            lines.append("- Credential: updated (value never echoed).")
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_delete_git_configuration",
    annotations=ToolAnnotations(
        title="Delete Model Git Configuration",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def omni_delete_git_configuration(params: DeleteGitConfigurationInput) -> str:
    """Disconnect a shared model from its Git repository.

    Calls `DELETE /v1/models/{modelId}/git`. The repository itself is
    untouched, but Omni stops syncing: pull requests can no longer be opened
    from branches, the pull-request policy stops applying, and a git-follower
    model becomes editable again. The stored credentials (deploy key or token)
    and the webhook secret are discarded, so reconnecting later means issuing a
    new credential and re-configuring the webhook.

    When to Use:
    - To retire a git integration, or before reconnecting the model to a
      different repository (create returns `409` while a configuration exists).

    When NOT to Use:
    - To change a setting or rotate a credential — use
      `omni_update_git_configuration`; deleting throws the configuration away.
    - To delete the model, a branch, or anything in the repository — this only
      removes the link between them.

    Returns:
    A short confirmation naming the model, or an `Error ...` string.

    Examples:
    - `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`

    Error Handling:
    Requires the `MANAGE_MODEL` permission (`403 Forbidden: Requires
    MANAGE_MODEL permission`). `400` means `modelId` is not a valid UUID.
    `404` means the model does not exist or already has no git configuration —
    a repeated delete lands there, which is safe to treat as already done.
    """
    try:
        payload = await get_client().request_json("DELETE", f"/v1/models/{_path_segment(params.model_id)}/git")
        body = _as_dict(payload)
        state = "confirmed" if body.get("success") else "the API reported no explicit success flag"
        return truncate_result(
            f"Deleted the git configuration for model `{params.model_id}` ({state}). "
            "Stored credentials and the webhook secret were discarded; reconnecting requires a new configuration."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_sync_model_with_git",
    annotations=ToolAnnotations(
        title="Sync Model With Git Repository",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_sync_model_with_git(params: SyncModelWithGitInput) -> str:
    """Sync a shared model with its configured Git repository.

    Calls `POST /v1/models/{modelId}/git/sync`, which pulls the latest changes
    from the repository and applies them to the shared model. This is the last
    step of the promotion flow — branch → edit YAML → open/update the pull
    request → merge the branch → sync — and also the way to pick up commits
    that landed in the repository outside Omni (a merged pull request from
    another author, a manual commit, a revert).

    When to Use:
    - After a pull request is merged in the git provider, to bring the shared
      model up to date.
    - To check whether the model is in sync: `didSync` is `false` and `inSync`
      is `true` when there was nothing to pull.
    - For git-follower models, where merging pull requests to the base branch
      is the only way the shared model changes.

    When NOT to Use:
    - To push a branch or open a pull request — use
      `omni_create_or_update_model_branch_pull_request`.
    - To merge an Omni branch into the shared model — use
      `omni_merge_model_branch`.

    Returns:
    A short confirmation with whether a sync happened (`didSync`), the
    resulting git SHA, whether the model is now in sync, and the API's status
    message. On failure, an `Error ...` string.

    Examples:
    - Plain sync: `{"params": {"model_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}`
    - With a commit message:
      `{"params": {"model_id": "a1b2...", "commit_message": "Updated model configuration"}}`

    Error Handling:
    Requires the `MANAGE_MODEL` permission (`403`). `400` means `modelId` is
    not a valid UUID. `404` means the model does not exist or has no git
    configuration — connect it with `omni_create_git_configuration` first.
    """
    try:
        body = _compact({"commitMessage": params.commit_message})
        payload = await get_client().request_json(
            "POST",
            f"/v1/models/{_path_segment(params.model_id)}/git/sync",
            json_body=body or None,
        )
        result = _as_dict(payload)
        did_sync = bool(result.get("didSync"))
        headline = "Synced" if did_sync else "No sync needed for"
        lines = [
            f"{headline} model `{params.model_id}` with its git repository.",
            "",
            f"- Sync performed: {_flag(result.get('didSync'))}",
            f"- Git SHA: `{_text(result.get('gitSha'), 'none (no sync performed)')}`",
            f"- In sync with the repository: {_flag(result.get('inSync'))}",
            f"- Message: {_text(result.get('message'), 'none returned')}",
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_create_or_update_model_branch_pull_request",
    annotations=ToolAnnotations(
        title="Create Or Update Model Branch Pull Request",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_create_or_update_model_branch_pull_request(
    params: CreateOrUpdateModelBranchPullRequestInput,
) -> str:
    """Push a model branch to Git and create or update its pull request.

    Calls `POST /v1/models/{modelId}/git/commit`, the API equivalent of the
    **Create Pull Request** / **Update Pull Request** buttons in Omni. It is
    step 3 of the promotion flow: create a branch in Omni and edit its model
    YAML, call this tool to push the branch and open the pull request, get it
    reviewed and merged in the git provider, then `omni_merge_model_branch`
    and `omni_sync_model_with_git`.

    By default the endpoint auto-detects whether the git branch exists: if it
    does not, it creates the branch and opens a pull request; if it does, it
    adds a commit to it. Constrain that with `allow_branch_exists: false`
    (create-only — fail if the git branch exists) or `require_branch_exists:
    true` (update-only — fail if it does not). Setting both is rejected.

    When to Use:
    - To open a pull request for a model branch, or to push follow-up commits
      to an already-open one.
    - To drive the review workflow programmatically from CI or an agent.

    When NOT to Use:
    - To merge the branch into the shared model — use
      `omni_merge_model_branch` after the pull request is merged.
    - To pull repository changes into the shared model — use
      `omni_sync_model_with_git`.
    - When the model has no git configuration — connect it first.

    Returns:
    A short confirmation with the pull request URL, the git SHA of the commit,
    and whether the shared model is in sync with its default git branch. On
    failure, an `Error ...` string.

    Examples:
    - Create or update: `{"params": {"model_id": "a1b2...", "branch_id": "b2c3...",
      "commit_message": "Add new customer dimension"}}`
    - Create-only: `{"params": {"model_id": "a1b2...", "branch_id": "b2c3...",
      "commit_message": "Add new customer dimension", "allow_branch_exists": false}}`
    - Update-only: `{"params": {"model_id": "a1b2...", "branch_id": "b2c3...",
      "commit_message": "Address review comments", "require_branch_exists": true}}`

    Error Handling:
    Requires **Modeler** or **Connection Admin** permissions — otherwise `403
    Forbidden: Requires MANAGE_MODEL permission`. `400` covers an invalid
    `modelId` or `branch_id`, a missing `commit_message`, and `Cannot set both
    allow_branch_exists=false and require_branch_exists=true` (this tool
    rejects that combination before calling the API). `404` means the model,
    the branch, or the model's git configuration does not exist. A create-only
    call against an existing git branch — or an update-only call against a
    missing one — also fails with `400`.
    """
    try:
        if params.allow_branch_exists is False and params.require_branch_exists is True:
            return (
                "Error: cannot set both `allow_branch_exists=false` (create-only) and "
                "`require_branch_exists=true` (update-only). Pick one mode, or omit both to create or update. "
                "No request was sent."
            )
        body = _compact(
            {
                "branch_id": params.branch_id,
                "commit_message": params.commit_message,
                "allow_branch_exists": params.allow_branch_exists,
                "require_branch_exists": params.require_branch_exists,
            }
        )
        payload = await get_client().request_json(
            "POST", f"/v1/models/{_path_segment(params.model_id)}/git/commit", json_body=body
        )
        result = _as_dict(payload)
        pr_url = _text(result.get("pr_url"), "not returned")
        lines = [
            f"Pushed branch `{params.branch_id}` of model `{params.model_id}` to git.",
            "",
            f"- Pull request: {pr_url}",
            f"- Commit SHA: `{_text(result.get('git_sha'), 'not returned')}`",
            f"- Shared model in sync with its default git branch: {_flag(result.get('in_sync'))}",
            f"- Sync performed during this call: {_flag(result.get('did_sync'))}",
            "",
            "Next: get the pull request reviewed and merged in the git provider, then call "
            "`omni_merge_model_branch` and `omni_sync_model_with_git`.",
        ]
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="omni_merge_model_branch",
    annotations=ToolAnnotations(
        title="Merge Model Branch",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def omni_merge_model_branch(params: MergeModelBranchInput) -> str:
    """Merge a model branch into the shared model (irreversible).

    Calls `POST /v1/models/{modelId}/branch/{branchName}/merge`. It is step 4
    of the promotion flow — branch → edit YAML → open/update the pull request
    → **merge the branch** → sync — and it changes the shared model everyone
    queries, so treat it as irreversible: there is no unmerge, and
    `delete_branch: true` also removes the branch.

    How the model's git settings gate the merge:

    - No git configuration: the merge succeeds, nothing is synced to git.
    - Git enabled without required pull requests: the merge succeeds and syncs
      to git.
    - Git with pull requests required, or a git-follower model: the merge is
      rejected with `400`, because it would bypass the review workflow. Merge
      the pull request in the git provider instead, then call
      `omni_sync_model_with_git`. `force_override_git_settings: true` overrides
      that check — the merge then succeeds but git is NOT synced, to avoid
      force-pushing to the base branch.

    When to Use:
    - To promote a branch's changes into the shared model once they are
      reviewed.
    - From CI, after the corresponding pull request has been merged.

    When NOT to Use:
    - To open or update a pull request — use
      `omni_create_or_update_model_branch_pull_request`.
    - As the routine path on a PR-required or git-follower model: merge the
      pull request in the provider and sync instead of forcing an override.
    - When you are unsure the branch is ready — this cannot be undone.

    Returns:
    A short confirmation with the branch, how many drafts were published and
    failed, and whether git was synced. On failure, an `Error ...` string.

    Examples:
    - Merge and delete the branch: `{"params": {"model_id": "a1b2...",
      "branch_name": "feature/add-revenue-metrics", "delete_branch": true, "publish_drafts": true}}`
    - Custom commit message: `{"params": {"model_id": "a1b2...", "branch_name": "feature/add-revenue-metrics",
      "commit_message": "Merged revenue metrics branch via CI/CD pipeline"}}`
    - Override a PR-required model (admins only): `{"params": {"model_id": "a1b2...",
      "branch_name": "hotfix/typo", "force_override_git_settings": true}}`

    Error Handling:
    `400` means the merge is not allowed for this model configuration — the
    model requires pull requests or is a git follower. `403` means insufficient
    permissions: `force_override_git_settings` requires **Connection Admin** or
    **Organization Admin**. `404` means the model or branch does not exist, and
    `405` an invalid HTTP method.
    """
    try:
        body = _compact(
            {
                "delete_branch": params.delete_branch,
                "publish_drafts": params.publish_drafts,
                "commit_message": params.commit_message,
                "force_override_git_settings": params.force_override_git_settings,
            }
        )
        path = f"/v1/models/{_path_segment(params.model_id)}/branch/{_path_segment(params.branch_name)}/merge"
        payload = await get_client().request_json("POST", path, json_body=body or None)
        result = _as_dict(payload)
        succeeded = result.get("success")
        headline = (
            f"Merged branch `{params.branch_name}` into shared model `{params.model_id}`."
            if succeeded is not False
            else f"The API did NOT report success merging branch `{params.branch_name}` into model `{params.model_id}`."
        )
        lines = [
            headline,
            "",
            f"- Success: {_flag(succeeded)}",
            f"- Drafts published: {_text(result.get('published_drafts_count'), 'not reported')}",
            f"- Drafts that failed to publish: {_text(result.get('failed_drafts_count'), 'not reported')}",
            f"- Synced to git: {_flag(result.get('git_synced'))}",
        ]
        if params.delete_branch:
            lines.append(f"- Branch `{params.branch_name}` was requested to be deleted after the merge.")
        if result.get("git_synced") is False:
            lines.extend(
                [
                    "",
                    "Git was not synced by this merge — call `omni_sync_model_with_git` if the shared model should "
                    "track the repository.",
                ]
            )
        return truncate_result("\n".join(lines))
    except Exception as exc:
        return handle_api_error(exc)
