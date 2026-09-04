# This server vs. the official Omni MCP Server

Omni ships its own MCP server. This project is a different thing with a different job, and the two
are complementary — you can install both at once. This page lays out what each one does, sourced
from the official documentation ([AI MCP Server](https://docs.omni.co/ai/mcp/index),
[MCP server tools](https://docs.omni.co/ai/mcp/tools),
[MCP authentication](https://docs.omni.co/ai/mcp/authentication),
[AI MCP Server settings](https://docs.omni.co/ai/settings/mcp)) and from this repository's own tool
registry ([TOOLS.md](TOOLS.md)).

## At a glance

| | Official Omni MCP Server | `omni-app-mcp` (this project) |
| --- | --- | --- |
| Who ships it | Omni | Independent open-source project, not affiliated with Omni |
| Transport | Remote HTTP (`https://callbacks.omniapp.co/callback/mcp` for OAuth, `https://<instance>/mcp/https` for API keys) | Local **stdio** — the client spawns the process |
| Authentication | OAuth 2.1 + PKCE (recommended; Omni mints an MCP OAuth PAT for you) or an API key in the `Authorization` header | `OMNI_API_KEY` env var — an Organization API key or a Personal Access Token, sent as `Authorization: Bearer` |
| Install | Add a URL to the client, or the Omni connector | `uvx omni-app-mcp`, `uvx --from git+https://github.com/trustxai/omni-mcp omni-app-mcp`, or a local Docker image |
| Server-side toggles | Organization Admin enables **MCP server**, **Agentic capabilities**, **Omni documentation search**, **Single shot query generation**, **Content access** in AI Hub | None — it is the REST API, so whatever the key can do, the tools can do |
| Shape of the surface | ~11 purpose-built tools around natural-language analysis | ~200 typed tools, one per REST operation, grouped by API area |
| Primary use | Ask questions of the data in natural language | Administer, model, validate, publish, schedule and audit the instance |
| Result payloads | Query results as JSON | Markdown by default, `json` on read tools; query results decoded from Apache Arrow; binaries written to local files |

## What the official server exposes

Grouped by the setting that enables them:

| Setting | Tools | What they do |
| --- | --- | --- |
| Agentic capabilities *(on by default)* | `askOmni`, `checkStatus`, `listBranches`, `pickModel`, `pickTopic` | Submit an open-ended, multi-step analysis job and poll it; the agent can also create/edit dashboards (edits land in a draft) and create routines. `listBranches` targets an existing model branch. |
| Single shot query generation *(off by default)* | `getData`, `runQuery`, `pickModel`, `pickTopic` | One-shot: translate a prompt into a query against the selected model/topic and return rows, or execute a JSON query definition directly. `runQuery` defaults to 500 rows, caps at 10,000, and refuses keys like `userEditedSQL` that would run raw SQL. |
| Content access *(on by default)* | `searchDashboards`, `listDashboardQueries`, `runSavedQuery` | Find an existing dashboard, list its tiles, and re-run a tile's saved query instead of generating a new one. |
| Omni documentation search *(on by default)* | `searchOmniDocs` | Answer "how do I…" questions from the Omni documentation, with citations. |

Access is deliberately narrow: interactions are scoped to the model (and topic, if supplied) given
to the server, which is what keeps natural-language querying from reaching data the caller should
not see. The querying and docs-search tools all run through the same AI pipeline as the in-app
Omni Agent, so disabling **Omni Agent** disables them — they still appear in the client but calls
return `403 Feature is not enabled`. `pickModel` is the exception: it only lists the models the
caller can reach, so it keeps working.

## What this server exposes

The REST surface, area by area. Counts are tools, and the full generated list with per-tool
descriptions lives in [TOOLS.md](TOOLS.md).

| API tags (spec) | Module | Ops in spec | Tools |
| --- | --- | --- | --- |
| Who Am I, API Tokens, User attributes | `identity` | 6 | 6 |
| Users | `users` | 10 | 10 |
| User groups, User group model roles, User model roles | `user_groups` | 10 | 10 |
| Connections, Connection environments, Schema refresh schedules | `connections` | 13 | 13 |
| dbt | `dbt` | 9 | 9 |
| Models, Topics | `models` | 16 | 16 |
| Model Git configuration, Model branches | `model_git` | 7 | 7 |
| Content, Content validator, Content migration | `content` | 6 | 6 |
| Documents | `documents` | 15 | 13 |
| Documents v2 | `documents_v2` | 7 | 7 |
| Document permissions, Document favorites, Document labels | `document_access` | 12 | 12 |
| Folders, Folder permissions, Folder labels, Labels | `folders` | 14 | 14 |
| Queries, Jobs | `queries` | 3 | 3 |
| Dashboard downloads, Dashboard filters | `dashboards` | 5 | 6 |
| Schedules, Schedule recipients | `schedules` | 14 | 14 |
| AI | `ai` | 10 | 11 |
| AI Routines, AI Eval | `ai_routines_evals` | 18 | 18 |
| AI Credit Controls, AI Credit Usage, AI Model Suggestions | `ai_governance` | 17 | 17 |
| Uploads | `uploads` | 4 | 4 |
| — (local, no API tag) | `health` | 0 | 2 |
| **40 tags** | **20 modules** | **196** | **198** |

Where a module has more tools than spec operations, the extras are convenience wrappers over an
existing polling loop (`omni_ask_ai`, `omni_export_dashboard_file`). Where it has fewer, the
missing operations are endpoints the spec marks as removed — two v1 document endpoints superseded
by Documents v2.

## What this server does *not* do

- **No natural-language-to-SQL of its own.** It has no query planner. What it has is the REST API's
  own AI endpoints (`omni_generate_query`, `omni_pick_topic`, `omni_create_ai_job`, `omni_ask_ai`,
  `omni_search_omni_docs`), which hand the prompt to Omni and return what Omni produced. Asking a
  data question in plain English is what the official server is for.
- **No embed session or embed URL signing.** Signing embed URLs is not part of the REST surface
  covered here.
- **No streaming.** Every tool returns one complete `str`. The AI job result endpoint is streamed
  by the API; this server reads it whole and assembles it locally rather than forwarding chunks.
- **No remote transport, no OAuth.** It is stdio only, so it runs where the client runs and reads
  its key from the environment. There is no browser flow to enrol a user.
- **No model/topic scoping.** The official server confines a session to one model (and optionally
  one topic). Here the boundary is the API key itself.

## Permission model

Both enforce the permissions of the credential in play, and neither adds permissions of its own.

- Official server: an OAuth connection creates an **MCP OAuth PAT** bound to your user, so queries
  run with your in-app permissions — a Viewer can complete the flow but still cannot query. With an
  API key, calls use the key creator's permissions.
- This server: every call carries `OMNI_API_KEY`. A **Personal Access Token** acts as one user;
  endpoints that require an **Organization API key** — SCIM user/group management, `userId`
  impersonation, document export/import (`omni_export_dashboard`, `omni_import_dashboard`) and
  email-only user management (`omni_manage_email_only_user`,
  `omni_bulk_manage_email_only_users`) — answer `403`. Row-level security, model roles and content
  permissions apply
  exactly as they do in the app. `omni_whoami` reports the key's scope, org role and per-model
  permissions before you rely on any of it.

## When to use which

Use the **official server** when the question is about the data: "how did signups trend last
quarter", "which dashboard already shows revenue by region", "how do I write a calculated field".
It is scoped, admin-governed, and built for that.

Use **this server** when the question is about the instance: build a model branch and validate it,
find every document that still references a renamed field, provision users and groups, wire a
connection to dbt, publish a document draft, set up a schedule, audit AI credit limits. Those are
REST operations, and there is no natural-language shortcut to them.

Running both is the common case. They use the same credentials model, they do not conflict, and an
agent can answer with one and act with the other — ask the official server what the numbers say,
then use this one to change what produces them.
