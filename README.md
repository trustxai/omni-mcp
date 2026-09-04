# omni-app-mcp

[![CI](https://github.com/trustxai/omni-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/trustxai/omni-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/omni-app-mcp.svg)](https://pypi.org/project/omni-app-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

An MCP server that puts the **whole Omni REST API** in front of an LLM client over stdio — users,
groups and permissions, connections and dbt, models and model YAML, git branches and pull requests,
content validation, documents (v1 and v2), folders and labels, queries returning decoded Apache
Arrow results, dashboards, schedules, AI jobs, routines, evals and credit governance — as typed,
self-documenting tools that return markdown or JSON. Every tool maps to one documented API
operation, carries a docstring written for a model choosing what to call, and never raises.

## Why this exists

Omni ships [its own MCP server](https://docs.omni.co/ai/mcp/index), and it is good at what it is
built for: a remote, OAuth-authenticated, query-focused server that turns natural language into
scoped queries against a model and topic. It is deliberately narrow — analysis, not administration.

This project covers the other half. It runs locally over stdio against the REST API, so an agent
can **administer, model, validate and publish**: branch a model, edit its YAML with checksum
conflict detection, validate it, open the pull request, merge the branch, sync git; find every
document still pointing at a renamed field and fix them; provision users, groups and model roles;
wire dbt; publish document drafts; run and pause schedules; audit AI credit limits.

The two are complementary and can be installed side by side — see
[docs/comparison.md](docs/comparison.md) for an honest side-by-side.

## Quick start

Once published on PyPI:

```bash
uvx omni-app-mcp
```

Until then, run it straight from the repository:

```bash
uvx --from git+https://github.com/trustxai/omni-mcp omni-app-mcp
```

The PyPI distribution and console script are both `omni-app-mcp` (the bare `omni-mcp` name is not
available on PyPI); the repository keeps its name.

The server speaks **stdio only** and needs two environment variables: your instance URL and an API
key.

> This is an independent open-source project. It is not affiliated with, endorsed by, or supported
> by Omni.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `OMNI_BASE_URL` | yes | — | Your instance URL, e.g. `https://your-instance.omniapp.co`. A trailing `/api` is accepted and normalised. |
| `OMNI_API_KEY` | yes | — | Organization API key or Personal Access Token, sent as `Authorization: Bearer`. |
| `OMNI_REQUEST_TIMEOUT_SECONDS` | no | `60` | Per-request timeout in seconds. |
| `OMNI_MAX_RETRIES` | no | `3` | Extra attempts on `429` (honouring `Retry-After`) and `502/503/504`. |
| `OMNI_MAX_RESULT_CHARS` | no | `900000` | Result-size budget in **UTF-8 bytes**; longer results are truncated with a visible marker, keeping them under the MCP 1 MB limit. |
| `OMNI_TOOL_MODULES` | no | *(empty — everything)* | Comma-separated allowlist of [tool modules](#tools) to register, e.g. `models,model_git,queries`. See [Selecting tool modules](#selecting-tool-modules). |

### Getting an API key

Two kinds of credential work, and which one you pick decides what the tools can reach (see the
[API authentication docs](https://docs.omni.co/api/authentication)):

- An **Organization API key** — **Settings → API access → Organization keys**. Organization Admin
  only, and it is what unlocks the whole surface.
- A **Personal Access Token** — generate it under **Profile → Manage account → Generate token**;
  once created it is listed under **Settings → API access → Personal tokens**. A PAT acts as a
  single user and carries that user's permissions.

A PAT covers the large majority of tools, but the API bars it from a documented set of endpoints,
which answer `403`:

- SCIM-backed user and group management — `omni_list_users`, `omni_create_user`,
  `omni_create_user_group`, `omni_update_user_group` and their siblings.
- Any tool that takes a `user_id` to act on another user's behalf (several AI tools do).
- Document export and import — `omni_export_dashboard`, `omni_import_dashboard`.
- Email-only user management — `omni_manage_email_only_user`,
  `omni_bulk_manage_email_only_users`.

Use an Organization API key for those. The API is rate limited to **60 requests per minute per
key**, and instances can ask Omni to raise it (up to 500 requests/minute is documented); the client
honours `Retry-After` either way. Start a session with `omni_health_check` to confirm the key works
and `omni_whoami` to see exactly what it can reach.

### Selecting tool modules

Every tool's schema travels with **every** request the client makes, and the full set is not small:
198 tools serialise to **685,960 bytes** of tool schemas. `OMNI_TOOL_MODULES` buys that context back
— a comma-separated allowlist of the modules in the [catalogue below](#tools). Unset or empty
registers all of them, exactly as before.

```bash
# Modelling: branch a model, edit and validate its YAML, run queries, open the PR.
OMNI_TOOL_MODULES=models,model_git,queries,content,health   # 34 tools, 140,749 bytes

# Administration: people, groups, model roles, connections and deliveries.
OMNI_TOOL_MODULES=users,user_groups,identity,connections,schedules,health   # 55 tools, 180,799 bytes
```

Those two are **examples, not presets**. The package deliberately ships no named bundles: which
tools an agent should see is policy, it differs per person and per task, and a bundle frozen into a
release would go stale while the client config is one line away from saying exactly what you want.

Entries are trimmed and empty ones ignored, so `models, ,queries,` is just `models,queries`. An
unknown name is a **startup error** that names the offending entry and lists the valid modules —
never a silent skip, which would leave you hunting for a tool that was never registered. Keeping
`health` in a narrowed set is worth it: `omni_get_api_info` reports the modules that are actually
registered (and names the ones that are not), so the filter stays visible from inside the session.

## Client configuration

### Claude Code

```bash
claude mcp add omni \
  --env OMNI_BASE_URL=https://your-instance.omniapp.co \
  --env OMNI_API_KEY=your-key \
  -- uvx omni-app-mcp
```

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "omni": {
      "command": "uvx",
      "args": ["omni-app-mcp"],
      "env": {
        "OMNI_BASE_URL": "https://your-instance.omniapp.co",
        "OMNI_API_KEY": "your-key"
      }
    }
  }
}
```

### Cursor

`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "omni": {
      "command": "uvx",
      "args": ["omni-app-mcp"],
      "env": {
        "OMNI_BASE_URL": "https://your-instance.omniapp.co",
        "OMNI_API_KEY": "your-key"
      }
    }
  }
}
```

### Any stdio MCP client

The generic shape — a command, its arguments, and the two environment variables:

```json
{
  "command": "uvx",
  "args": ["--from", "git+https://github.com/trustxai/omni-mcp", "omni-app-mcp"],
  "env": {
    "OMNI_BASE_URL": "https://your-instance.omniapp.co",
    "OMNI_API_KEY": "your-key"
  }
}
```

### Docker

No image is published yet; build one locally. The container talks MCP over stdin/stdout, so `-i`
is required and `-t` must be omitted.

```bash
docker build -t omni-app-mcp .
docker run -i --rm --env-file .env omni-app-mcp
```

Copy [`.env.example`](.env.example) to `.env` first. In a client config, the command becomes
`docker` with `args: ["run", "-i", "--rm", "--env-file", "/absolute/path/to/.env", "omni-app-mcp"]`.

## Tools

<!-- TOOL_MODULES_START -->
**198** tools across **20** modules, and every one of them is registered unless
[`OMNI_TOOL_MODULES`](#selecting-tool-modules) says otherwise. The full generated reference —
every tool, its access mode and its one-line description — is in [docs/TOOLS.md](docs/TOOLS.md).

| Module | Tools | Covers |
| --- | --- | --- |
| `ai` | 11 | AI jobs, conversations, query generation, topic picking, docs search |
| `ai_governance` | 17 | AI credit controls and usage, model suggestions |
| `ai_routines_evals` | 18 | Scheduled AI routines, eval prompt sets and runs |
| `connections` | 13 | Database connections, environments, schema refresh schedules |
| `content` | 6 | Content search, the content validator, dashboard export/import |
| `dashboards` | 6 | Dashboard downloads (PDF/CSV/…) and dashboard filters |
| `dbt` | 9 | dbt configuration, environments, exposures |
| `document_access` | 12 | Document permissions, favorites, labels |
| `documents` | 13 | Documents v1 — list, read, queries, drafts, move, duplicate, ownership |
| `documents_v2` | 7 | Documents v2 — create, draft, patch, publish, read state |
| `folders` | 14 | Folders, folder permissions, folder labels, labels |
| `health` | 2 | Local diagnostics — configuration and connectivity |
| `identity` | 6 | Who am I, API tokens, user attribute definitions |
| `model_git` | 7 | Git configuration, sync, branch pull requests, merges |
| `models` | 16 | Models, model YAML, validation, schemas, cache, topics |
| `queries` | 3 | Running queries and reading their results |
| `schedules` | 14 | Scheduled deliveries, alerts, recipients, email-only users |
| `uploads` | 4 | CSV uploads (data input tables) |
| `user_groups` | 10 | User groups and user/group model roles |
| `users` | 10 | Standard, embed and email-only users (SCIM) |
<!-- TOOL_MODULES_END -->

Start with `omni_health_check` to confirm the key works, and `omni_get_api_info` to see how the
server is configured.

## Workflows

Short, real sequences an agent can run end to end. Tool names are exactly as registered.

### Modelling on a branch, then promoting it

1. `omni_list_models` with `include: "activeBranches"` to see each shared model together with its
   active branches — or `model_kind: "BRANCH"` plus `base_model_id: <shared model id>` to list one
   model's branches directly.
2. `omni_get_model_yaml` on the branch — it returns each file with a **checksum** (this server
   defaults `include_checksums` to true precisely so the write-back is safe).
3. Edit the YAML, then `omni_update_model_yaml` with `files` (file name → new content) and
   `checksums` (the same file name → the checksum `omni_get_model_yaml` returned for it). A
   checksum mismatch means someone else wrote first; re-fetch rather than overwrite.
4. `omni_validate_model` on the branch and fix what it groups by file.
5. `omni_create_or_update_model_branch_pull_request` to push the branch and open (or update) the
   PR, then `omni_merge_model_branch` once it is approved.
6. `omni_sync_model_with_git` to bring the shared model back in line with the repository.

### Running a query and reading the rows

1. `omni_run_query` with the query definition (model, topic/fields, filters, limit). Results come
   back as base64 Apache Arrow and are decoded into a markdown table — or raw records with
   `response_format: json`.
2. If the query outlives its request, the API returns `408` with the job ids still running. Feed
   them to `omni_wait_for_query_results`, which polls and returns the rows when they land.
3. `omni_get_job_status` covers the other asynchronous jobs (schema refreshes, for example).

### Content governance: rename a field without breaking dashboards

1. `omni_validate_content` on the model — it walks every document and reports broken view, field
   and topic references, grouped by document.
2. `omni_find_and_replace_content` with the old reference and the new one. It rewrites every
   document on the model, so read the dry summary before confirming: the tool is marked
   destructive, and clients that surface `destructiveHint` will ask.
3. `omni_validate_content` again to confirm the list is empty.
4. `omni_search_dashboards` to spot-check the dashboards that referenced the field.

### Documents v2 lifecycle

1. `omni_create_document_v2` creates and publishes a document in one call.
2. `omni_create_draft_and_patch_document` opens a draft on a published document and applies the
   first patch; `omni_patch_document_draft` applies further ones.
3. `omni_get_document_draft_state` reads back what the draft currently holds — the unpublished
   counterpart of `omni_get_document_state`.
4. `omni_publish_document_draft` makes it live. This one is effectively irreversible; publish only
   after reading the draft state.

### Administration: people, roles and deliveries

1. `omni_whoami` first — it reports the key's scope, org role and per-model permissions, which
   tells you immediately whether SCIM tools will work.
2. `omni_list_users` / `omni_create_user` and `omni_create_user_group` / `omni_replace_user_group`
   to provision people and groups (Organization API key required).
3. `omni_assign_user_model_role` and `omni_assign_user_group_model_role` to grant model access,
   `omni_get_user_model_roles` to audit it.
4. `omni_list_schedules` to review deliveries, `omni_create_schedule` to add one,
   `omni_pause_schedule` / `omni_resume_schedule` to hold it, and `omni_trigger_schedule` only when
   you mean it — that sends a real delivery to real recipients.

## Safety notes

- **Destructive tools say so.** Every tool ships MCP `ToolAnnotations`; deletes, merges, publishes
  and anything irreversible carry `destructiveHint`, reads carry `readOnlyHint`. Clients that
  prompt on destructive tools will prompt on these.
- **Secrets are never echoed.** Connection passwords, deploy private keys, SFTP passwords, webhook
  secrets and CSV payloads never appear in a tool result. Tools report *whether* a credential is
  configured, never its value.
- **Rate limits are handled.** 60 requests/minute per key (raisable on request, up to a documented
  500); the client honours `Retry-After` on `429` and backs off on `502/503/504` automatically.
- **Results stay under the 1 MB MCP limit.** Everything goes through a byte-budgeted truncator that
  leaves a visible marker rather than silently cutting; tune it with `OMNI_MAX_RESULT_CHARS`.
- **Binary downloads go to disk.** Dashboard PDFs, CSVs and other binaries are written to the
  `output_path` you supply and never inlined into the result; the tools refuse to overwrite an
  existing file unless you ask.
- **The key is the boundary.** This server adds no permissions. Row-level security, model roles and
  content permissions apply exactly as they do in the app.

## Development

```bash
uv sync --group dev
uv run pre-commit install

uv run pytest -m "not live"
uv run ruff check src/ tests/ scripts/
uv run ruff format --check src/ tests/ scripts/
uv run mypy src/
uv run python scripts/tool_table.py
uv run python scripts/tool_table.py --write   # regenerate the module catalogue above
```

The catalogue in [Tools](#tools) is generated from the live registry — the counts and the `Covers`
column come from each module's `MODULE_SUMMARY`, and a test fails if the block drifts from what the
generator produces. It always lists every module in the package, whatever `OMNI_TOOL_MODULES` is set
to locally.

Live tests (`-m live`) run against a real instance and are skipped unless `OMNI_API_KEY` is set;
copy `.env.example` to `.env` to enable them. [CONTRIBUTING.md](CONTRIBUTING.md) is the contract
every tool module follows — naming, annotations, docstring sections, pagination, error handling —
and reading it once makes review short.

## Roadmap

- An embed URL signing helper, so embed sessions can be produced without leaving the agent.
- More convenience workflows layered over the existing tools (a modelling promote-and-validate
  loop, a content-migration wrapper), in the shape of `omni_ask_ai` and
  `omni_export_dashboard_file`.

## License

Apache-2.0 — see [LICENSE](LICENSE).
