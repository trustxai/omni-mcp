# Changelog

## [0.2.1](https://github.com/trustxai/omni-mcp/compare/v0.2.0...v0.2.1) (2026-09-04)


### Bug Fixes

* **packaging:** publish the distribution as omni-app-mcp ([#27](https://github.com/trustxai/omni-mcp/issues/27)) ([c3ab901](https://github.com/trustxai/omni-mcp/commit/c3ab9012a52c0ff8a1d0b023b0c182f74b15c64d))

## [0.2.0](https://github.com/trustxai/omni-mcp/compare/v0.1.0...v0.2.0) (2026-09-04)


### ⚠ BREAKING CHANGES

* omni_import_dashboard renames `document` to `export_payload`; omni_list_users / omni_list_embed_users JSON output is now the raw SCIM envelope (Resources, totalResults, startIndex, itemsPerPage, schemas); omni_move_document requires folder_path or to_root=true; raw-body overrides (body / scim_body / operations) can no longer be combined with typed fields.

### Bug Fixes

* **ai_governance:** a missing success flag is not a confirmation ([424d900](https://github.com/trustxai/omni-mcp/commit/424d90077e8db24f32ab8ca1ea03dcc012fd968b))
* **ai_governance:** only an explicit success:false contradicts the 2xx ([24e3376](https://github.com/trustxai/omni-mcp/commit/24e3376bf5c0ac7aab60ebbda1f4b86d8a8a2389))
* apply review follow-ups across modules ([eb50b9a](https://github.com/trustxai/omni-mcp/commit/eb50b9ab32369d0439aba67280522107c0e84233))
* **connections:** truncate the mutating confirmations ([c05f483](https://github.com/trustxai/omni-mcp/commit/c05f483a045e1582a310f9c07dab3e9a4c7f9a78))
* **content:** honest export annotation, closable fence, stricter import pre-flight ([a28606f](https://github.com/trustxai/omni-mcp/commit/a28606fb30d484ac04b0c4f5efb937792d6aa969))
* **document_access:** send accessBoost only when set, guard the empty PUT ([dba5dd6](https://github.com/trustxai/omni-mcp/commit/dba5dd66fce6008dc60384b3f1624909a84bb184))
* **documents:** drop the move example the destination guard now rejects ([720c96b](https://github.com/trustxai/omni-mcp/commit/720c96bde1f5b61f82e4a439ba2575816e066854))
* **documents:** explicit move destination, no empty folderPath, no empty bodies ([dfc5dca](https://github.com/trustxai/omni-mcp/commit/dfc5dcaa2e5309b54f2d0eeb60984918bc4f94eb))
* **folders:** check success on permission writes, stop inventing a scope ([6efc573](https://github.com/trustxai/omni-mcp/commit/6efc57371f9660e549b35e7ae41b645b80aadfb1))
* **foundation:** report the rejected setting, keep key state honest, narrow ValidationError ([cea929e](https://github.com/trustxai/omni-mcp/commit/cea929eaf9fe3bf2cde7011faf2b9b74adcabded))
* **model_git:** strip clone-URL userinfo, keep PEM bytes, drop fake API errors ([e8d1533](https://github.com/trustxai/omni-mcp/commit/e8d1533308458b187d69c0d610f5bac9b3852bcf))
* **models:** the YAML table's bytes column counts UTF-8 bytes ([2642d7e](https://github.com/trustxai/omni-mcp/commit/2642d7e277c8083d4d078fe335ccc1c856a2202b))
* tighten input contracts across modules ([b3fc962](https://github.com/trustxai/omni-mcp/commit/b3fc96249252ea746c942d7defe74dd98bb49142))
* **uploads:** render model_id/created_at, accept an empty 2xx delete ([f99473f](https://github.com/trustxai/omni-mcp/commit/f99473f6c11225c01c7b6da968b0e8ac2a7cfd67))
* **user_groups:** reject mixed raw overrides, guard renderers and SCIM filters ([634e33c](https://github.com/trustxai/omni-mcp/commit/634e33c8cbf8a50c285fa865509518525f3c4b68))
* **users:** first-class user_name PATCH, raw SCIM envelope, JSON booleans ([46bf45e](https://github.com/trustxai/omni-mcp/commit/46bf45ed784d447095f481d78c0af5a21d53af05))


### Documentation

* regenerate the tool table — omni_export_dashboard now writes ([43df22c](https://github.com/trustxai/omni-mcp/commit/43df22c49460c750658ef788b0d19e4fcb8b46a4))


### Miscellaneous Chores

* **release:** pin the next release to 0.2.0 ([5455a62](https://github.com/trustxai/omni-mcp/commit/5455a6265790e03d30a60bd624b8d4c9268c17dd))

## 0.1.0 (2026-09-04)


### Features

* add MCP server, tool registry and health tools ([7468af5](https://github.com/trustxai/omni-mcp/commit/7468af519ccf4bc1dd1097c25372a40638e430a7))
* add settings, HTTP client, error mapping and response formatters ([5fa943f](https://github.com/trustxai/omni-mcp/commit/5fa943f3ce1d1b76478683a28dcef09ac14a6efe))
* **ai_governance:** [t18-ai-governance] AI credit controls, usage, and model suggestions ([#19](https://github.com/trustxai/omni-mcp/issues/19)) ([59e1a2e](https://github.com/trustxai/omni-mcp/commit/59e1a2e39c11efbb67b65a8f0a50b463ebcbb2c1))
* **ai_routines_evals:** implement AI Routines + AI Eval tools (t17) ([#20](https://github.com/trustxai/omni-mcp/issues/20)) ([9f35f0c](https://github.com/trustxai/omni-mcp/commit/9f35f0c460e90fc2c062258ba0b03a6c136183ed))
* **ai:** [t16-ai] AI jobs, conversations, generate query, pick topic, docs search, branding ([#16](https://github.com/trustxai/omni-mcp/issues/16)) ([a5d54fa](https://github.com/trustxai/omni-mcp/commit/a5d54fa5e08c935b5c6130dab4534277c9f2a958))
* **connections:** [t4-connections] connections, connection environments, schema refresh schedules ([#7](https://github.com/trustxai/omni-mcp/issues/7)) ([d1b700d](https://github.com/trustxai/omni-mcp/commit/d1b700d8e2ee6f0609917bc46cdb9338ba41d315))
* **content:** add content, content-validator and migration tools ([#4](https://github.com/trustxai/omni-mcp/issues/4)) ([68742af](https://github.com/trustxai/omni-mcp/commit/68742af00d05f5f7725d7a519c14b5e6d6a5f27f))
* **dashboards:** [t14-dashboards] dashboard downloads & filters ([#15](https://github.com/trustxai/omni-mcp/issues/15)) ([7c8646c](https://github.com/trustxai/omni-mcp/commit/7c8646c05155a9c514504378a8f353d294cd98a9))
* **dbt:** [t5-dbt] dbt environments, configuration, exposures (9 ops) ([#6](https://github.com/trustxai/omni-mcp/issues/6)) ([4b206cd](https://github.com/trustxai/omni-mcp/commit/4b206cde05f9ac414c27174f1b5c2a1287fe9eda))
* **document_access:** implement document permissions, favorites, and labels tools ([#12](https://github.com/trustxai/omni-mcp/issues/12)) ([de56b78](https://github.com/trustxai/omni-mcp/commit/de56b789a395a006f6c95244acab09bc0060e221))
* **documents_v2:** add the 7 documents v2 lifecycle tools ([#11](https://github.com/trustxai/omni-mcp/issues/11)) ([2ec159c](https://github.com/trustxai/omni-mcp/commit/2ec159cc0478328af01e9e09c5d7771554a8d07c))
* **documents:** implement the documents v1 tools ([#8](https://github.com/trustxai/omni-mcp/issues/8)) ([f786699](https://github.com/trustxai/omni-mcp/commit/f7866992009d6594773b91a4846e575b266b6040))
* **folders:** implement folder, permission, label tools (t12-folders) ([#9](https://github.com/trustxai/omni-mcp/issues/9)) ([14c7a9d](https://github.com/trustxai/omni-mcp/commit/14c7a9dd29a392453da95785396254298326e747))
* **identity:** [t1-identity] Who Am I, API tokens, user attributes ([#1](https://github.com/trustxai/omni-mcp/issues/1)) ([c3ea7c6](https://github.com/trustxai/omni-mcp/commit/c3ea7c678b63d7ddc14213b78a00d7e4da97b195))
* **model_git:** add the 7 model git configuration and branch merge tools ([#5](https://github.com/trustxai/omni-mcp/issues/5)) ([c207b87](https://github.com/trustxai/omni-mcp/commit/c207b879bbfb4b6a8095d66ee186172c4a1d2a00))
* **models:** add the 16 model, YAML, schema, cache and topic tools ([#14](https://github.com/trustxai/omni-mcp/issues/14)) ([6fdea99](https://github.com/trustxai/omni-mcp/commit/6fdea998268c0096eef3c2bb49399ec185b616b7))
* **queries:** [t13-queries] run query with Arrow decoding, wait for results, job status ([#10](https://github.com/trustxai/omni-mcp/issues/10)) ([572ae61](https://github.com/trustxai/omni-mcp/commit/572ae61f4045e6d9ff955e6d0d6e64dad5d9531f))
* **schedules:** [t15-schedules] schedules + schedule recipients (14 ops) ([#17](https://github.com/trustxai/omni-mcp/issues/17)) ([37568f1](https://github.com/trustxai/omni-mcp/commit/37568f13f9e042a95f7cb26a6295488008f74323))
* **uploads:** [t19-uploads] CSV upload tools (list/upload/replace/delete) ([#13](https://github.com/trustxai/omni-mcp/issues/13)) ([0fe9f79](https://github.com/trustxai/omni-mcp/commit/0fe9f79439d57f7798494d0708c4148d5a7d70c1))
* **user_groups:** implement user group and model role tools (t3-user-groups) ([#3](https://github.com/trustxai/omni-mcp/issues/3)) ([8f44fde](https://github.com/trustxai/omni-mcp/commit/8f44fdeb7eb59fbefb019ef94d738f03cd354a1a))
* **users:** implement users, embed users, email-only users tools (10 ops) ([#2](https://github.com/trustxai/omni-mcp/issues/2)) ([5ce5433](https://github.com/trustxai/omni-mcp/commit/5ce5433f9027ff3396f72f7aa3390a01290d0955))


### Bug Fixes

* emit strict JSON and budget tool results in UTF-8 bytes ([dfd985e](https://github.com/trustxai/omni-mcp/commit/dfd985eb0d509ebd4d7f71e27c5c08f1bc08a541))
* follow redirects and bound the total retry wait ([ead2dc9](https://github.com/trustxai/omni-mcp/commit/ead2dc932db2012b59eb27fedf9eb6b62c4c2519))
* **foundation:** [t0b-polish] review follow-ups ([#18](https://github.com/trustxai/omni-mcp/issues/18)) ([ec06113](https://github.com/trustxai/omni-mcp/commit/ec06113cb80af8acae8133ef7f4f552e9052253f))
* **queries:** decode NDJSON responses from run/wait ([#24](https://github.com/trustxai/omni-mcp/issues/24)) ([0f194b8](https://github.com/trustxai/omni-mcp/commit/0f194b83ae782bcf6966e17193249a621734c178))
* register tools last, drop the unused register_all argument and discover modules ([223d703](https://github.com/trustxai/omni-mcp/commit/223d703a62aded4d8a88728bba2e2286758b6dc8))
* report the Deprecation header on 410 responses ([3b03cf6](https://github.com/trustxai/omni-mcp/commit/3b03cf60435d59734227e355ca013e2358ef1003))
* **tests:** reset the client singleton and block unpatched network calls ([2b6dd8f](https://github.com/trustxai/omni-mcp/commit/2b6dd8f78550d5c851b693898bbb805301ce78fd))
* validate the instance URL instead of building wrong request URLs ([a0c78a9](https://github.com/trustxai/omni-mcp/commit/a0c78a9bada709edb69eb09315097f98e22d6eeb))


### Documentation

* [t20-docs] README, tool reference, comparison, contributor conventions ([#22](https://github.com/trustxai/omni-mcp/issues/22)) ([16f1331](https://github.com/trustxai/omni-mcp/commit/16f13315e4fdc9c866d8567f86c96d159a567e5d))
* add README, contributor lane contract, security policy and license ([2adbf45](https://github.com/trustxai/omni-mcp/commit/2adbf454519463abd9be6d67d361cf791a580715))

## Changelog

All notable changes to this project are documented in this file. Entries are generated by
[release-please](https://github.com/googleapis/release-please) from
[Conventional Commits](https://www.conventionalcommits.org/).
