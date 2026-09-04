# Tool reference

**Generated — do not edit by hand.** This file is the full output of `scripts/tool_table.py`, which
reads the live tool registry, so it is always in step with what the server actually exposes.
Regenerate it after adding, renaming or re-describing a tool:

```bash
uv run python scripts/tool_table.py > docs/TOOLS.md   # then restore this header
```

`Access` reports each tool's `readOnlyHint` annotation: **read-only** tools only call `GET`-style
endpoints, **writes** tools change something (in Omni, or — for downloads — on your disk).
Destructive and idempotent hints travel with the tool itself; see the safety notes in the
[README](../README.md#safety-notes). For a compact per-module summary, see the README's
[Tools](../README.md#tools) section.

---

**198** tools across **20** modules.

### `ai` (11)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_ask_ai` | writes | Ask the AI a question and wait for the finished answer (consumes AI credits). |
| `omni_cancel_ai_job` | writes | Request cancellation of an AI job. |
| `omni_create_ai_job` | writes | Submit an AI job for asynchronous execution (consumes AI credits). |
| `omni_generate_query` | writes | Generate a structured query from a natural language prompt (consumes AI credits). |
| `omni_get_ai_branding` | read-only | Get the organization's AI Agent branding configuration. |
| `omni_get_ai_conversation` | read-only | Get one AI conversation with its full message history. |
| `omni_get_ai_job_result` | read-only | Get the full result of a completed AI job — the answer plus every action taken. |
| `omni_get_ai_job_status` | read-only | Get the current state, progress and result summary of an AI job. |
| `omni_list_ai_conversations` | read-only | List recent AI conversations, most recently active first. |
| `omni_pick_topic` | writes | Pick the model topic that best answers a natural language prompt (consumes AI credits). |
| `omni_search_omni_docs` | writes | Ask the documentation a natural language question (consumes AI credits). |

### `ai_governance` (17)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_delete_model_suggestion` | writes | Permanently delete an AI-generated model suggestion. |
| `omni_disable_model_suggestions_schedule` | writes | Disable the daily AI suggestion generation schedule for a shared model. |
| `omni_dismiss_model_suggestion` | writes | Dismiss an AI-generated suggestion, removing it from the active list. |
| `omni_enable_model_suggestions_schedule` | writes | Enable the daily AI suggestion generation schedule for a shared model. |
| `omni_generate_model_suggestions` | writes | Trigger a new AI suggestion generation run for a shared model. |
| `omni_get_ai_credit_controls` | read-only | Get the organization's AI credit controls, thresholds, and current usage. |
| `omni_get_entity_group_ai_credit_usage` | read-only | Read specific embed entity groups' AI credit usage for the current billing period. |
| `omni_get_latest_suggestion_run` | read-only | Get the most recent AI suggestion generation run for a shared model. |
| `omni_get_suggestion_run_status` | read-only | Get the status of a specific AI suggestion generation run. |
| `omni_get_user_ai_credit_usage` | read-only | Read specific users' AI credit usage for the current billing period. |
| `omni_list_entity_group_ai_credit_limits` | read-only | List the organization's individual (per-embed-entity-group) AI credit limits. |
| `omni_list_model_suggestions` | read-only | List AI-generated context suggestions for a shared model. |
| `omni_list_user_ai_credit_limits` | read-only | List the organization's individual (per-user) AI credit limits. |
| `omni_restore_model_suggestion` | writes | Restore a previously dismissed AI-generated suggestion back to the active list. |
| `omni_set_entity_group_ai_credit_limits` | writes | Set individual embed entity groups' AI credit limits in bulk. |
| `omni_set_user_ai_credit_limits` | writes | Set individual users' AI credit limits in bulk. |
| `omni_update_ai_credit_controls` | writes | Update the organization's AI credit downgrade/shutoff thresholds and defaults. |

### `ai_routines_evals` (18)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_archive_eval_prompt_set` | writes | Archive (soft-delete) a prompt set. |
| `omni_archive_eval_run` | writes | Archive (soft-delete) an eval run. |
| `omni_cancel_eval_run` | writes | Cancel an in-flight eval run. |
| `omni_create_ai_routine` | writes | Create a new AI Routine that runs on the specified schedule. |
| `omni_create_eval_prompt_set` | writes | Create a new eval prompt set bound to a shared model. |
| `omni_delete_ai_routine` | writes | Permanently delete an AI Routine. This action cannot be undone. |
| `omni_get_ai_routine` | read-only | Retrieve details for a specific AI Routine by ID. |
| `omni_get_eval_prompt_set` | read-only | Get a single eval prompt set with all of its prompts. |
| `omni_get_eval_run` | read-only | Get an eval run with every per-prompt result row. |
| `omni_list_ai_routines` | read-only | List AI Routines for the calling user, newest first. |
| `omni_list_eval_prompt_sets` | read-only | List eval prompt sets, sorted alphabetically by name. |
| `omni_list_eval_runs` | read-only | List runs for a prompt set, newest first. |
| `omni_restore_eval_prompt_set` | writes | Restore an archived prompt set. |
| `omni_restore_eval_run` | writes | Restore an archived eval run. |
| `omni_start_eval_run` | writes | Create and start a new run against an existing prompt set. |
| `omni_trigger_ai_routine` | writes | Run an existing routine immediately, in addition to its schedule. |
| `omni_update_ai_routine` | writes | Update an existing AI Routine's name, prompt, schedule, model, or active state. |
| `omni_update_eval_prompt_set` | writes | Update a prompt set's name, description, and/or prompts. |

### `connections` (13)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_create_connection` | writes | Create a new database connection. |
| `omni_create_connection_environments` | writes | Associate one or more environment-specific connections with a base connection. |
| `omni_create_schema_refresh_schedule` | writes | Create a new schema refresh schedule for a connection. |
| `omni_delete_connection` | writes | Archive a connection (soft delete — moves it to trash). |
| `omni_delete_connection_environment` | writes | Delete a connection environment, removing its base-connection association. |
| `omni_delete_schema_refresh_schedule` | writes | Delete a schema refresh schedule. |
| `omni_get_connection` | read-only | Fetch a single database connection by ID. |
| `omni_get_schema_refresh_schedule` | read-only | Fetch one schema refresh schedule's details. |
| `omni_list_connections` | read-only | List database connections configured on this instance. |
| `omni_list_schema_refresh_schedules` | read-only | List the schema refresh schedules configured for a connection. |
| `omni_update_connection` | writes | Update connection credentials, base role, or environment user-attribute settings. |
| `omni_update_connection_environment` | writes | Set which user-attribute values route to a specific connection environment. |
| `omni_update_schema_refresh_schedule` | writes | Replace the cron schedule and/or timezone for an existing schema refresh schedule. |

### `content` (6)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_export_dashboard` | read-only | Export a dashboard as a migration payload for another instance. |
| `omni_find_and_replace_content` | writes | Replace a view, field, or topic reference across every document on a model. |
| `omni_get_content` | read-only | Retrieve a paginated list of documents and folders. |
| `omni_import_dashboard` | writes | Import an exported dashboard into a model on this instance. |
| `omni_search_dashboards` | read-only | Free-text search over the organization's dashboards. |
| `omni_validate_content` | read-only | Validate every document on a model and report broken view/field references. |

### `dashboards` (6)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_download_dashboard_file` | writes | Download a completed dashboard/tile export to a local file. |
| `omni_export_dashboard_file` | writes | Initiate, poll, and download a dashboard/tile export in one call. |
| `omni_get_dashboard_download_status` | read-only | Check the status of a dashboard/tile download job. |
| `omni_get_dashboard_filters` | read-only | Retrieve a dashboard's filter and control configuration. |
| `omni_initiate_dashboard_download` | writes | Start an asynchronous download job for a dashboard or single tile. |
| `omni_update_dashboard_filters` | writes | Set or reset values for a dashboard's filters and/or controls. |

### `dbt` (9)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_create_dbt_environment` | writes | Create a new dbt environment on a connection. |
| `omni_delete_dbt_configuration` | writes | Remove the dbt configuration for a connection by unlinking its dbt repository. |
| `omni_delete_dbt_environment` | writes | Delete a dbt environment from a connection. This action cannot be undone. |
| `omni_get_dbt_configuration` | read-only | Retrieve the dbt configuration for a connection. |
| `omni_get_dbt_exposures` | read-only | Compute and retrieve dbt exposures for a model, on demand. |
| `omni_list_dbt_environments` | read-only | List every dbt environment configured on a connection. |
| `omni_update_dbt_configuration` | writes | Update the dbt configuration for a connection. |
| `omni_update_dbt_environment` | writes | Update an existing dbt environment on a connection. |
| `omni_update_model_branch_dbt_environment` | writes | Update the active dbt environment on a model branch. |

### `document_access` (12)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_apply_document_label` | writes | Apply an existing label to a document, adding it alongside any labels already there. |
| `omni_bulk_update_document_labels` | writes | Add and/or remove multiple labels from a document in one atomic operation. |
| `omni_favorite_document` | writes | Add a published document to a user's favorites. |
| `omni_get_document_permissions` | read-only | Retrieve a document's ability toggles, plus one user's resolved permits. |
| `omni_grant_document_permissions` | writes | Grant a content role on a document to users and/or user groups. |
| `omni_list_document_access` | read-only | List every user and group with access to a document. |
| `omni_list_document_favoriters` | read-only | List the users who have favorited a published document. |
| `omni_remove_document_label` | writes | Remove one label from a document. |
| `omni_revoke_document_permissions` | writes | Revoke document access from users and/or user groups. |
| `omni_unfavorite_document` | writes | Remove a published document from a user's favorites. |
| `omni_update_document_permission_settings` | writes | Update a document's permission/interactivity ability settings and default org role. |
| `omni_update_document_permissions` | writes | Update the content role already granted to users and/or user groups on a document. |

### `documents` (13)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_archive_document_draft` | writes | Archive a document's current draft, discarding its unpublished changes. |
| `omni_create_document` | writes | Create a new document on a model, optionally seeded with query presentations. |
| `omni_create_document_draft` | writes | Create a draft of a document (or return the existing one). |
| `omni_delete_document` | writes | Delete a document, moving it to Trash. |
| `omni_duplicate_document` | writes | Duplicate a published document into a new document. |
| `omni_get_document` | read-only | Retrieve a dashboard document's configuration (name, filters, query presentations). |
| `omni_get_document_queries` | read-only | Retrieve the queries behind a document, ready to re-run through the query API. |
| `omni_list_document_drafts` | read-only | List the drafts attached to a published document. |
| `omni_list_documents` | read-only | List documents (dashboards and workbooks) with filtering and cursor pagination. |
| `omni_move_document` | writes | Move a document to another folder, or change its sharing scope. |
| `omni_remove_dashboard_from_document` | writes | Remove the dashboard from a draft, leaving a workbook-only document. |
| `omni_transfer_document_ownership` | writes | Transfer ownership of a document to another organization member. |
| `omni_upgrade_dashboard_layout` | writes | Upgrade a document to the advanced dashboard layout. |

### `documents_v2` (7)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_create_document_v2` | writes | Create a brand-new document and publish it in one call. |
| `omni_create_draft_and_patch_document` | writes | Open a draft on a published document and apply the first patch. |
| `omni_get_document_draft_state` | read-only | Read a named draft's state — the unpublished counterpart of the document state. |
| `omni_get_document_state` | read-only | Read a document's published state (tiles, controls, settings, layout). |
| `omni_patch_document_draft` | writes | Apply a further patch to an existing draft — no draft creation, no publish. |
| `omni_publish_document_draft` | writes | Publish the document's main draft, making it live (effectively irreversible). |
| `omni_rename_document_identifier` | writes | Rename a published document's identifier (URL slug), live and immediately. |

### `folders` (14)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_bulk_update_folder_labels` | writes | Add and/or remove multiple labels from a folder in a single all-or-nothing request. |
| `omni_create_folder` | writes | Create a new folder, optionally nested under a parent. |
| `omni_create_label` | writes | Create a new label for organizing and categorizing folders and documents. |
| `omni_delete_folder` | writes | Delete a folder. By default, only empty folders can be deleted. |
| `omni_delete_label` | writes | Delete a label from the organization. |
| `omni_get_folder_permissions` | read-only | Retrieve the folder permissions that apply to a specific user. |
| `omni_get_label` | read-only | Retrieve a single label by name. |
| `omni_grant_folder_permissions` | writes | Grant a content role on a folder to one or more users or user groups. |
| `omni_list_folders` | read-only | List folders within the organization, with filtering, sorting, and cursor pagination. |
| `omni_list_labels` | read-only | List every label defined in the organization. Takes no filters — the API returns all of them. |
| `omni_revoke_folder_permissions` | writes | Revoke folder permissions from one or more users or user groups. |
| `omni_update_folder` | writes | Update a folder's name and/or path segment. Requires Editor permissions or higher on the folder. |
| `omni_update_folder_permissions` | writes | Update the content role already granted to users or groups on a folder. |
| `omni_update_label` | writes | Update an existing label — rename it, or change its Verified/Homepage/color/description. |

### `health` (2)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_get_api_info` | read-only | Report how this server is configured, without calling the API. |
| `omni_health_check` | read-only | Verify connectivity and authentication against the Omni API. |

### `identity` (6)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_delete_api_token` | writes | Permanently delete (revoke) an API token. |
| `omni_get_api_token` | read-only | Retrieve a single API token by its id. |
| `omni_list_api_tokens` | read-only | List API tokens in the organization, optionally filtered by type. |
| `omni_list_user_attributes` | read-only | List all user attribute definitions in the organization. |
| `omni_update_api_token` | writes | Enable or disable an API token without permanently deleting it. |
| `omni_whoami` | read-only | Get the caller's full identity, API key scope, org role, and per-model permissions. |

### `model_git` (7)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_create_git_configuration` | writes | Connect a shared model to a Git repository. |
| `omni_create_or_update_model_branch_pull_request` | writes | Push a model branch to Git and create or update its pull request. |
| `omni_delete_git_configuration` | writes | Disconnect a shared model from its Git repository. |
| `omni_get_git_configuration` | read-only | Retrieve the Git configuration of a shared model. |
| `omni_merge_model_branch` | writes | Merge a model branch into the shared model (irreversible). |
| `omni_sync_model_with_git` | writes | Sync a shared model with its configured Git repository. |
| `omni_update_git_configuration` | writes | Change a shared model's Git configuration; only supplied fields change. |

### `models` (16)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_archive_model` | writes | Archive (soft-delete) a shared or shared extension model and its content. |
| `omni_create_model` | writes | Create a model on a connection (schema, shared, extension or branch). |
| `omni_delete_model_branch` | writes | Delete a development branch from a shared model. |
| `omni_delete_model_view` | writes | Delete or ignore a view in a model, choosing the layer it disappears from. |
| `omni_delete_model_yaml_file` | writes | Delete one topic, composite topic or view YAML file from a model. |
| `omni_get_model_ai_agent_actions` | read-only | List a model's configured AI agent actions — sample queries and skills. |
| `omni_get_model_yaml` | read-only | Read a model's YAML files, with the checksums needed to write them back. |
| `omni_get_topic` | read-only | Retrieve one topic's resolved views, fields and joins. |
| `omni_list_model_schemas` | read-only | List every schema name available in a model (physical, virtual and dynamic). |
| `omni_list_models` | read-only | List models with their metadata, optionally including active branches. |
| `omni_migrate_model` | writes | Copy a model's YAML from one connection to another at a given git ref. |
| `omni_refresh_model_schema` | writes | Start a schema refresh so a model picks up the data source's latest structure. |
| `omni_rename_model` | writes | Rename a shared, extension or branch model. |
| `omni_reset_model_cache` | writes | Invalidate the cached results of one cache policy on a model. |
| `omni_update_model_yaml` | writes | Create or overwrite model YAML files, with checksum conflict detection. |
| `omni_validate_model` | read-only | List a model's (or branch's) validation errors and warnings, grouped by file. |

### `queries` (3)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_get_job_status` | read-only | Get the status of an asynchronous job, such as a schema refresh. |
| `omni_run_query` | read-only | Run a query against an Omni model and return the resulting rows. |
| `omni_wait_for_query_results` | read-only | Poll query jobs that outlived their request and return their results. |

### `schedules` (14)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_add_schedule_recipients` | writes | Add recipients to an existing scheduled email delivery. |
| `omni_bulk_manage_email_only_users` | writes | Create or update up to 20 email-only delivery recipients in one call. |
| `omni_create_schedule` | writes | Create a scheduled delivery or alert for a dashboard. |
| `omni_delete_schedule` | writes | Permanently delete a schedule and stop all of its future deliveries. |
| `omni_get_schedule` | read-only | Retrieve one schedule's full configuration, destinations and current status. |
| `omni_list_schedule_recipients` | read-only | List who or what a schedule delivers to, with destination-specific detail. |
| `omni_list_schedules` | read-only | List scheduled dashboard deliveries and alerts, with filtering and sorting. |
| `omni_manage_email_only_user` | writes | Create or update an email-only delivery recipient (no Omni account needed). |
| `omni_pause_schedule` | writes | Pause a schedule so it stops running until it is resumed. |
| `omni_remove_schedule_recipients` | writes | Remove recipients from an existing scheduled email delivery. |
| `omni_resume_schedule` | writes | Resume a paused schedule so it runs on its cadence again. |
| `omni_transfer_schedule_ownership` | writes | Transfer a schedule to a new owner in the same organization (irreversible here). |
| `omni_trigger_schedule` | writes | Run a schedule immediately — this sends a real delivery to real recipients. |
| `omni_update_schedule` | writes | Update an existing schedule; omitted properties keep their current values. |

### `uploads` (4)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_delete_upload` | writes | Delete a CSV upload, removing its file from storage. |
| `omni_list_uploads` | read-only | List CSV and spreadsheet uploads (data input tables) in the organization. |
| `omni_replace_upload_data` | writes | Replace the data in an existing CSV upload, keeping its id and view name. |
| `omni_upload_csv` | writes | Upload a CSV file to create a new data input table and view. |

### `user_groups` (10)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_assign_user_group_model_role` | writes | Assign or update a model role for a user group. |
| `omni_assign_user_model_role` | writes | Assign or update a model role for a user. |
| `omni_create_user_group` | writes | Create a user group. |
| `omni_delete_user_group` | writes | Delete the specified user group. |
| `omni_get_user_group` | read-only | Retrieve a user group using its unique ID. |
| `omni_get_user_group_model_roles` | read-only | Retrieve the model role assignments for a user group. |
| `omni_get_user_model_roles` | read-only | Retrieve the model role assignments for a user. |
| `omni_list_user_groups` | read-only | List user groups, sorted by creation time. |
| `omni_replace_user_group` | writes | Replace the specified user group, including its membership. |
| `omni_update_user_group` | writes | Update a user group by applying SCIM 2.0 patch operations. |

### `users` (10)

| Tool | Access | Description |
| --- | --- | --- |
| `omni_create_user` | writes | Create a standard SCIM user. |
| `omni_delete_embed_user` | writes | Delete the specified embed user. |
| `omni_delete_user` | writes | Delete the specified standard user. |
| `omni_get_embed_user` | read-only | Retrieve an embed user using their unique ID. |
| `omni_get_user` | read-only | Retrieve a standard user using their unique ID. |
| `omni_list_email_only_users` | read-only | List email-only users and their user attributes. |
| `omni_list_embed_users` | read-only | List embed users in the organization, sorted by creation time. |
| `omni_list_users` | read-only | List standard users in the organization, sorted by creation time. |
| `omni_replace_user` | writes | Replace the specified user's entire SCIM resource. |
| `omni_update_user` | writes | Partially update the specified user via SCIM PATCH operations. |
