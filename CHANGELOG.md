# Changelog

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
