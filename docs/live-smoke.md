# Live smoke suite

`tests/test_live_smoke.py` is a **read-only** smoke test of the server against a real Omni
instance. It walks one tool per API area, in dependency order, and asserts that each one comes
back with a rendered result instead of an `Error ...` string. It is the last check before a
release: the unit tests prove the tools build the right request and render the right markdown
against a fake client, and this suite proves the real API still agrees.

## Running it

The suite needs credentials. Copy the example env file and fill it in:

```bash
cp .env.example .env      # OMNI_BASE_URL + OMNI_API_KEY
uv run pytest -m live
```

Without `OMNI_API_KEY` in the environment or `.env`, every live test is skipped by
`tests/conftest.py` — that is the expected result in CI, which never carries credentials.
`uv run pytest -m "not live"` (the default developer loop) deselects the suite entirely.

Add `-v` to see one line per tool, and `-rs` to see the reason behind each skip.

## What it covers

The suite is a chain: each step feeds the next, and the shared results live in session-scoped
fixtures so the whole file costs roughly 25 API calls (the API allows 60 per minute per key, and
each call is spaced by half a second).

| Step | Tool | What it proves |
| --- | --- | --- |
| 1 | `omni_health_check` | The base URL and key reach the instance |
| 2 | `omni_whoami` | The key resolves to a caller, a scope and model roles |
| 3 | `omni_list_models` | Models paginate and carry ids |
| 4 | `omni_list_model_schemas` | Schema names resolve for the first model |
| 5 | `omni_get_model_yaml` | YAML files come back **with checksums** (names only — file contents are never asserted on or printed) |
| 6 | `omni_validate_model` | The validator answers for that model |
| 7 | `omni_list_documents` | Documents paginate and carry identifiers |
| 8 | `omni_get_document` | The first document renders |
| 9 | `omni_get_document_queries` | Every entry carries a runnable `query` object |
| 10 | `omni_run_query` | That query re-runs and the Arrow result decodes into rows |
| 11 | `omni_get_job_status` | The job the query reported can be looked up |
| 12 | `omni_list_folders` | The folder tree paginates |
| 13 | `omni_list_labels` | Organization labels render |
| 14 | `omni_list_connections` | Connections render **and no password-like field comes back unmasked** |
| 15 | `omni_list_schedules` | Schedules and alerts paginate |
| 16 | `omni_list_users` | SCIM user listing |
| 17 | `omni_list_user_groups` | SCIM group listing |
| 18 | `omni_list_uploads` | CSV uploads paginate |
| 19 | `omni_list_ai_conversations` | AI history reads back (no credits spent) |
| 20 | `omni_get_ai_credit_controls` | The org-admin credit-control settings read back |
| 21 | `omni_list_api_tokens` | The admin token listing answers |
| 22 | `omni_get_dashboard_filters` | The filter bar of the first document that has a dashboard |
| 23 | `omni_get_content` | The first page of the content tree |
| 24 | `omni_search_dashboards` | Free-text search, using a document name known to exist |

## It never writes

Every tool called here is a GET-style read. The file does not import — let alone call — any
create, update, delete, patch, publish, trigger, upload or find-and-replace tool, so there is
nothing for it to leave behind. `omni_run_query` is a read too: it re-runs a query the instance
already stores, capped at five rows.

Mutating live tests are a separate thing: they carry `@pytest.mark.destructive` on top of
`@pytest.mark.live` and additionally require `OMNI_TEST_ALLOW_DESTRUCTIVE=1`. None live in this
file.

The AI tools that spend credits (`omni_ask_ai`, `omni_generate_query`, `omni_pick_topic`,
`omni_create_ai_job`) are deliberately excluded. Only the conversation listing is exercised.

## Reading the results

A green run is every test passing. Skips are normal and are not failures — run with `-rs` to see
which of these it was:

- **`requires higher permission: …`** — the endpoint answered **403**. Several endpoints are
  admin-only, and several accept an Organization API key but not a Personal Access Token (SCIM
  users and groups, API tokens, `userId` impersonation). Running the suite with a PAT is expected
  to skip those; re-run with an Organization API key to cover them.
- **`not available on this instance: …`** — the endpoint answered **404** for an id that came out
  of an earlier discovery step. The id may have been archived between the two calls, or the
  endpoint may not apply to that object (a job record that already expired, a document with no
  dashboard).
- **`no models visible to this API key` / `no documents visible…` / `none of the listed documents
  has a dashboard` / `the first document exposes no runnable query`** — the discovery chain found
  nothing to work with. Expected on an empty or freshly provisioned instance; on a populated one
  it means the key's model role or content permissions are narrower than you thought.
- **`response exceeded the result budget and was truncated`** — the JSON response outgrew
  `OMNI_MAX_RESULT_CHARS` and could not be parsed. Only reachable on very large models.

Any other `Error …` fails the test and prints the full message, including the API's own detail.
A **401** always fails: it means the key is missing, invalid or revoked, which is a real problem
rather than a permission boundary.
