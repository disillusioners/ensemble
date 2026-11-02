# Gate (i) — remote `tools/list` RE-CAPTURE — lane `plane-pm-issue-tools` Phase 1

**Executed:** 2026-11-02 ~08:12Z · **Executor:** Phase-1 coder (instance 1 of 3)
**Probe:** `probe_plane_mcp.py` (this directory — read-only by construction, verb allow-list `tools/list` + `list_projects` only, never prints a credential)
**Probe environment:** `/home/nea/ensemble-src` (the armed main checkout — G2-fold B1(f) ruling). The probe is a **direct JSON-RPC call** over the `streamable-http` transport: **no daemon, no boot, no port**.
**Credential:** `PLANE_MCP_API_KEY` read from the main checkout `.env` for the **outbound credential ONLY** — never written, never copied, never echoed. **Key NAMES only in every artifact.**
**Secret-leak check performed:** every `KEY=VALUE` from the main checkout `.env` was tested for presence in the raw capture — **zero values present**.

---

## 1. Outcome — PASS

| Probe step | Result |
|---|---|
| `initialize` | **HTTP 200** — `credential_state: accepted` (**the dev-context credential is LIVE — not the known-expired live/demo token**) |
| `notifications/initialized` | HTTP 202 |
| `tools/list` | **HTTP 200** |
| server | `Plane MCP Server (header-http)` v`3.2.0`, protocol `2025-06-18` (identical to the wave-1 capture) |
| **tool count** | **177** |
| workspace slug used | `e2e` (the main checkout's `PLANE_MCP_WORKSPACE_SLUG`) |

**Pass criteria:**

| # | Criterion | Verdict |
|---|---|---|
| 1 | `tools/list` succeeds (HTTP 200) ⇒ credential live | **PASS** |
| 2 | `create_work_item` (+ `is_draft`), `update_work_item`, `delete_work_item`, `list_work_items`, `retrieve_work_item`, all 5 comment verbs, all 10 cycle verbs present | **PASS** (verified per-verb against the keep-set — see §5) |
| 3 | **No** `*_issue` verb appears | **PASS** — zero matches for `*_issue` / `*_issue_*` |
| 4 (P1-B re-worded) | **Set-membership**: no verb in `gate-i-keep-set.json` is absent from the re-captured catalog | **PASS** — 28/28 present; `fc1_fires: false` |

**Wave-1 cross-check (`tools-list-inventory.md` §2):** the wave-1 177-name list and this re-capture are **byte-identical name sets** (177 == 177, symmetric difference empty) ⇒ **no catalog drift** between wave 1 (2026-11-02 early) and this re-capture (FC2/FC10 diff = 0).

---

## 2. Full name list — 177 raw verbs (sorted)

Sha256 of the sorted name set (FC2/FC10 drift anchor — canonical form below):

```
catalog_sorted_names_sha256 = 0df212f44bc11cda84c1fb92171eef3c999436d03f978fff28fcb4f0b66e745a
algorithm = sha256( "\n".join(sorted_names) + "\n" ) over UTF-8
```

**This digest is reproducible** from the committed fixture (`tests/fixtures/plane_catalog.json`) via `tests/helpers/plane_catalog_fixture.py::sorted_names_sha256`.

```
attach_page_to_work_item               complete_cycle                         count_work_items
create_customer                        create_customer_property               create_customer_request
create_cycle                           create_initiative                      create_intake_work_item
create_label                           create_milestone                       create_module
create_page                            create_project                         create_project_estimate
create_project_estimate_points         create_release                         create_release_label
create_release_tag                     create_state                           create_work_item
create_work_item_comment               create_work_item_link                  create_work_item_property
create_work_item_property_option       create_work_item_relation              create_work_item_relation_definition
create_work_item_type                  create_work_log                        delete_customer
delete_customer_property               delete_customer_request                delete_cycle
delete_initiative                      delete_intake_work_item                delete_label
delete_milestone                       delete_module                          delete_project
delete_project_estimate                delete_project_estimate_point          delete_release
delete_release_label                   delete_release_tag                     delete_state
delete_work_item                       delete_work_item_attachment            delete_work_item_comment
delete_work_item_link                  delete_work_item_property              delete_work_item_property_option
delete_work_item_property_value        delete_work_item_relation_definition   delete_work_item_type
delete_work_log                        detach_page_from_work_item             get_customer_property_values
get_features                           get_me                                 get_pql_reference
get_project_estimate                   get_project_members                    get_project_worklog_summary
get_release_changelog                  get_work_item_attachment_download_url  get_work_item_property_value
get_workspace_members                  import_work_item_types_to_project      link_estimate_to_project
list_archived_work_items               list_customer_properties               list_customer_requests
list_customer_work_items               list_customers                         list_cycle_work_items
list_cycles                            list_initiative_projects               list_initiatives
list_intake_work_items                 list_labels                            list_milestone_work_items
list_milestones                        list_module_work_items                 list_modules
list_pages                             list_project_estimate_points           list_projects
list_release_labels                    list_release_tags                      list_release_work_items
list_releases                          list_roles                             list_states
list_work_item_activities              list_work_item_attachments             list_work_item_comments
list_work_item_links                   list_work_item_pages                   list_work_item_properties
list_work_item_property_options        list_work_item_relation_definitions    list_work_item_relations
list_work_item_types                   list_work_items                        list_work_logs
manage_customer_work_items             manage_cycle_archive                   manage_cycle_work_items
manage_initiative_projects             manage_milestone_work_items            manage_module_archive
manage_module_work_items               manage_project_archive                 manage_release_labels
manage_release_work_items              manage_work_item_archive               manage_work_item_assignee
manage_work_item_label                 manage_work_item_type_properties       read_work_item_attachment
remove_work_item_relation              resolve_work_item_type                 retrieve_customer
retrieve_customer_property             retrieve_customer_request              retrieve_cycle
retrieve_initiative                    retrieve_intake_work_item              retrieve_label
retrieve_milestone                     retrieve_module                        retrieve_page
retrieve_project                       retrieve_release                       retrieve_release_tag
retrieve_role                          retrieve_state                         retrieve_work_item
retrieve_work_item_activity            retrieve_work_item_by_identifier       retrieve_work_item_comment
retrieve_work_item_link                retrieve_work_item_property            retrieve_work_item_property_option
retrieve_work_item_type                search_work_items                      set_customer_property_values
set_work_item_property_value           transfer_cycle_work_items              update_customer
update_customer_property               update_customer_request                update_cycle
update_initiative                      update_intake_work_item                update_label
update_milestone                       update_module                          update_project
update_project_estimate                update_project_estimate_point          update_project_features
update_release                         update_release_changelog               update_release_label
update_release_tag                     update_state                           update_work_item
update_work_item_comment               update_work_item_link                  update_work_item_property
update_work_item_property_option       update_work_item_relation_definition   update_work_item_type
update_work_log                        update_workspace_features              upload_work_item_attachment_from_url
```

---

## 3. Three-number census (P1-B — the 116/177 figure is NOT used as a threshold)

Recomputed from THIS capture (reproduces the architect's on-disk arithmetic **exactly** — the acid test):

| # | Method | Read | Write | Unclassified |
|---|--------|------|-------|--------------|
| 1 | The inventory's **stated** method (read = list/retrieve/get/count/search/read) | **73** | **104** | 0 |
| 3 | The daemon's **actual** classifier as on disk (`plane.py` `read_tool_patterns=("list_","get_","search_")` / `write_tool_patterns=(create_,update_,delete_,add_,remove_,set_,edit_,assign_)`; `resilience.py::is_read_tool`) | **48** | **82** | **47** |
| 4 | The **corrected** classifier (P2-K + the `read_` amendment: read += `retrieve_`,`count_`,`read_`; write += `manage_`,`transfer_`,`complete_`,`link_`,`import_`,`attach_`,`detach_`,`upload_`,`resolve_`; dead `add_`/`edit_`/`assign_` dropped) | **73** | **104** | **0** |

Sub-counts captured: `list_`36 · `get_`11 · `search_`1 · `retrieve_`23 · `count_`1 · `read_`1 · `create_`26 · `update_`27 · `delete_`26 · `remove_`1 · `set_`2 · `manage_`14 · `transfer_`1 · `complete_`1 · `link_`1 · `import_`1 · `attach_`1 · `detach_`1 · `upload_`1 · `resolve_`1 · (`add_`=`edit_`=`assign_`=**0**).

Reconciliation confirmed: **row 4 == row 1** (the drift was in the daemon's classifier, not the inventory); R18 closure re-proven by set arithmetic (corrected read set is a strict superset of today's, +25 restored / 0 removed).

---

## 4. Discovery-verb required-params (P1-A — a CAPTURE, not an inference)

| Verb | Present | `required` | Properties |
|---|---|---|---|
| `list_projects` | ✅ | *(none)* | `cursor`, `order_by`, `per_page` |
| `list_states` | ✅ | `project_id` | `params`, `project_id` |
| `retrieve_state` | ✅ | `project_id`, `state_id` | `project_id`, `state_id` |
| `list_work_item_types` | ✅ | *(none)* | `params`, `project_id` |
| `retrieve_work_item_type` | ✅ | `work_item_type_id` | `project_id`, `work_item_type_id` |
| `resolve_work_item_type` | ✅ | `project_id`, `name` | `name`, `project_id` |
| `list_members` | ❌ **ABSENT from the catalog** — the architect's "members" family verb **does not exist** (the members surface is `get_workspace_members` / `get_project_members`) | — | — |
| `list_labels` | ✅ | `project_id` | `params`, `project_id` |
| `search_work_items` | ✅ | `query` | `expand`, `external_id`, `external_source`, `fields`, `order_by`, `query` |

> **Honest finding:** the keep-set's discovery/typing-reads family (4 verbs) is unaffected — none of the 28 references `list_members`. The absence is recorded so the Phase-4 charter does not name it.

---

## 5. Keep-set verbs against the live catalog (P-G1 — property names per verb)

`create_work_item` properties include **`is_draft`** ✅ (the Task-3 draft capability is directly expressible); `required = {project_id, name}`.

| raw verb | daemon name | required | properties | schema_tokens |
|---|---|---|---|---|
| `create_work_item` | `plane_create_work_item` | `name`, `project_id` | 19 | 364 |
| `update_work_item` | `plane_update_work_item` | `project_id`, `work_item_id` | 20 | 386 |
| `delete_work_item` | `plane_delete_work_item` | `project_id`, `work_item_id` | 2 | 37 |
| `retrieve_work_item` | `plane_retrieve_work_item` | `project_id`, `work_item_id` | 7 | 130 |
| `retrieve_work_item_by_identifier` | `plane_retrieve_work_item_by_identifier` | `work_item_identifier` | 6 | 120 |
| `list_work_items` | `plane_list_work_items` | — | 9 | 313 |
| `search_work_items` | `plane_search_work_items` | `query` | 6 | 116 |
| `count_work_items` | `plane_count_work_items` | — | 3 | 203 |
| `create_work_item_comment` | `plane_create_work_item_comment` | `project_id`, `work_item_id` | 7 | 136 |
| `list_work_item_comments` | `plane_list_work_item_comments` | `project_id`, `work_item_id` | 3 | 60 |
| `update_work_item_comment` | `plane_update_work_item_comment` | `comment_id`, `project_id`, `work_item_id` | 8 | 146 |
| `delete_work_item_comment` | `plane_delete_work_item_comment` | `comment_id`, `project_id`, `work_item_id` | 3 | 47 |
| `retrieve_work_item_comment` | `plane_retrieve_work_item_comment` | `comment_id`, `project_id`, `work_item_id` | 3 | 47 |
| `create_cycle` | `plane_create_cycle` | `name`, `owned_by`, `project_id` | 9 | 155 |
| `update_cycle` | `plane_update_cycle` | `cycle_id`, `project_id` | 10 | 184 |
| `delete_cycle` | `plane_delete_cycle` | `cycle_id`, `project_id` | 2 | 35 |
| `list_cycles` | `plane_list_cycles` | `project_id` | 6 | 124 |
| `retrieve_cycle` | `plane_retrieve_cycle` | `cycle_id`, `project_id` | 2 | 35 |
| `complete_cycle` | `plane_complete_cycle` | `cycle_id`, `project_id` | 2 | 35 |
| `manage_cycle_work_items` | `plane_manage_cycle_work_items` | `cycle_id`, `project_id` | 4 | 85 |
| `transfer_cycle_work_items` | `plane_transfer_cycle_work_items` | `cycle_id`, `new_cycle_id`, `project_id` | 3 | 47 |
| `list_cycle_work_items` | `plane_list_cycle_work_items` | `cycle_id`, `project_id` | 8 | 278 |
| `manage_cycle_archive` | `plane_manage_cycle_archive` | `archive`, `cycle_id`, `project_id` | 3 | 43 |
| `manage_work_item_assignee` | `plane_manage_work_item_assignee` | `project_id`, `work_item_id` | 4 | 77 |
| `list_projects` | `plane_list_projects` | — | 3 | 69 |
| `list_work_item_types` | `plane_list_work_item_types` | — | 2 | 55 |
| `resolve_work_item_type` | `plane_resolve_work_item_type` | `name`, `project_id` | 2 | 33 |
| `list_states` | `plane_list_states` | `project_id` | 2 | 48 |


**Every one of the 28 raw verbs is a member of the re-captured catalog** (0 absent) ⇒ **criterion 4 PASS · FC1 does not fire**. Machine-readable artifact: [`gate-i-keep-set.json`](gate-i-keep-set.json).

---

## 6. Per-tool schema token counts (P1-A — feeds FC9)

Tokeniser: **tiktoken `cl100k_base`** (the LLM lane's own — `daemon/loader.py::estimate_tokens`). Counting form:

```
schema_tokens(tool)   = estimate_tokens(json.dumps(inputSchema,        sort_keys=True, separators=(",",":")))
tool_total_tokens(t)  = estimate_tokens(json.dumps({name, description, inputSchema}, sort_keys=True, separators=(",",":")))
```

| Aggregate | Value |
|---|---|
| `schema_token_total` (inputSchema bodies, 177 tools) | **16 264** |
| `tool_total_token_total` (name+description+schema, 177 tools) | **36 805** |
| The wave-1 "10–18 k tokens" figure for the schema bodies | **CONFIRMED** (16 264 sits inside the plausible band — now verified, no longer unverified) |

Per-tool values are in the committed fixture (`tool_schemas[name].schema_tokens` / `.tool_total_tokens`).

---

## 7. FC9 baseline acquisition (G2-fold B3 / X1.8)

**`baseline_tokens = 20 111`** — RECORDED (FC9 is evaluable at Phase 5).

| Component | Value |
|---|---|
| `system_prompt_tokens` (PM system prompt — the loader's own assembly, tool NAMES included via `loader.py:704-706`) | 7 994 |
| `tool_schema_tokens` (the 47 bound tools, OpenAI-serialised via `convert_to_openai_tool` — what `llm.bind_tools(tools)` sends, `graph.py:10531`/`:10549`) | 12 117 |
| **`baseline_tokens`** | **20 111** |
| PM resolved toolset size | 47 |
| Formula | `baseline_tokens = estimate_tokens(PM system prompt) + Σ estimate_tokens(json.dumps(convert_to_openai_tool(t)))` — tokeniser tiktoken `cl100k_base` |

**Boot mechanism (recorded per the dispatch):** source-run form from the worktree — `cd /home/nea/ensemble-src-wt-plane-pm && env -i <allowlist> .venv-python -m daemon` (see §7.1); the measurement harness enters the SAME app lifespan (`create_app()` + `lifespan_context`) in the SAME fenced env, resolves PM's toolset via `create_instance_tools` and the prompt via `load_and_cache_prompt`, and counts with the daemon's own `estimate_tokens`.

**Plane-absent condition (by construction):** `PLANE_MCP_URL` (and `PLANE_MCP_API_KEY`/`PLANE_MCP_WORKSPACE_SLUG`) unset **for that process only** — **no `.env` was mutated anywhere** (F11).

### 7.1 Baseline-boot assertions (X1.8 — all six, in order)

1. **Scratch daemon boots** — `/livez` **200** (`{"status":"alive","version":"0.28.5"}`), `/readyz` **200** (`status: ready`, ALL components true: database, queue_freshness, services, checkpoint_saver; `reasons: []`), with **`ENSEMBLE_SELF_ENV=sandbox` pinned explicitly**. ✅
2. **No `plane` `mcp_servers` row exists** — `select count(*) from mcp_servers where name='plane'` = **0** (scratch DB held only `context7` + `webfetch`). Boot log: `Builtin 'plane' skipped — missing environment configuration (see server definition docs)`. ✅
3. **PM's resolved toolset contains ZERO plane MCP tools** — `plane_mcp_tool_count = 0` (the only `mcp_*` tools preloaded were the 2 `context7` tools). ✅ **with a disclosure:** PM's toolset DOES contain **`plane_sync_project`** (1 tool) — the **`plane_sync` REST tool swept into the `plane` category by the dynamic-prefix sweep because the category is empty**. This is **S7's predicted ownership-guard defect, now empirically confirmed at the baseline boot** (`daemon/tools/instance.py:499-511`; Phase 3 fixes it). It is **not** a plane-MCP tool and is present in the measurement basis of BOTH FC9 legs, so the growth delta is unaffected; recorded so Phase 5 can disclose it.
4. **`baseline_tokens` measured at the prompt-assembly point** = 20 111 (see the table above). ✅
5. **Live fence held** — see §7.2. ✅
6. **Teardown SIGTERM-only**, no orphans, scratch DB + role dropped, scratch root removed (cleanup at commission end). ✅

`baseline_tokens` **IS recorded** ⇒ FC9 evaluable downstream. **No substituted guess.**

### 7.2 Fences (F1–F11) — all asserted

| Fence | Value / proof |
|---|---|
| F1 scratch root | `mktemp -d /tmp/plane-pm-fc9.scratch.XXXXXX` (never `~/agents-ensemble`, never `~/ensemble-src`) |
| F2 no frozen binary | **source-run from the worktree** (`python -m daemon`, worktree `daemon/` imported via `PYTHONPATH` + cwd; no frozen binary, no staged artifact) |
| F3 `ENSEMBLE_SELF_ENV` | `sandbox` — pinned **explicitly** in every boot env |
| F4 boot sweep | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` — pinned **explicitly**, never `env -u` |
| F5 PG scrub | **`env -i` allowlist** (strictest form — nothing ambient inherited at all; the worker shell's ambient is live-poisoned: `POSTGRES_DB=ensemble_prod`, `ENSEMBLE_SELF_ENV=live`); `POSTGRES_*` set **explicitly** to the scratch DB |
| F6 FAKE_HOME | `mktemp -d` fake HOME + escape guard (both paths asserted not under `/home/nea/agents-ensemble*` / `/home/nea/ensemble-src*`); the daemon's promote journal resolved **inside the fake HOME** (`journal absent at <FAKE_HOME>/agents-ensemble/releases/state.json` — never the live install) |
| F7 scratch PG | throwaway role `plane_pm_p1_*` + scratch DBs on the **LOCAL** cluster `127.0.0.1:5432` (PG 16.15 — a different server from the live remote 10.44.0.2:5432/PG 18.4); `ensemble_prod` **never addressed**; role + DBs DROPped at cleanup |
| F8 port | **8971** (verified free via `ss`; neither **9797** nor **7979** — both confirmed LIVE and listening) |
| F9 teardown | **SIGTERM-only** (both boots + the harness); graceful shutdown observed in logs; **no orphans** (no `python -m daemon`, no MCP stdio children), port freed |
| F10 live fence | live `state.json` sha256 **PRE == MID == POST** = `1262190e16f85d6ee682de10066545bbdba3b78b74d5f93a383398644099852a`; live daemon **pid 643626 unchanged**, uptime continuous (35 544 s → 37 684 s, v0.28.4 throughout); `current -> releases/v0.28.4` untouched |
| F11 no `.env` mutation | the plane-absent condition is a **per-process env override**; zero writes to any `.env` (main checkout / worktree / live install / scratch) |

**Worktree cleanliness:** `git status --porcelain` after all boots shows only the lane's own untracked planning dir — the boots wrote nothing into the tree.

### 7.3 Baseline comparability disclosure + re-baseline instruction (fix-cycle F1, 2026-11-02)

**The recorded `baseline_tokens = 20 111` (measured 08:12Z) PREDATES the prefix-sweep ownership guard** (commit `95e999b20`, 10:54Z) — the fix that removed `plane_sync_project` from PM's resolved toolset (assertion 3 above disclosed it as IN the baseline's 47-tool toolset: the `plane_sync` REST tool swept into the empty `plane` category). That tool contributed **≈105 tokens** (REVIEWER ESTIMATE derived from the council's analysis — NOT re-measured; recomputation at the FC9 formula gives 59–84 tokens with the short description, 441–466 with the full `_full_doc_`) of the measured `tool_schema_tokens`.

**Consequence for FC9:** Phase-5's present-leg, measured on the post-guard tree, will **NOT** contain `plane_sync_project` ⇒ the growth delta `present − baseline` computed against this 08:12Z figure is **UNDERSTATED by ≈105 tokens** (~0.5 % of baseline; ~2 % of the 5 028 ceiling = 25 % growth band) — a bias **AGAINST** the breach firing. The two legs would not be measured on the same code.

**RE-BASELINE INSTRUCTION (binding at Phase-5 start):** before evaluating FC9, **re-measure the baseline with the IDENTICAL formula** — plane-absent boot (`PLANE_MCP_URL` unset for the process only), same fences F1–F11, same measurement point (`estimate_tokens(PM system prompt) + Σ estimate_tokens(convert_to_openai_tool(t))`, tiktoken `cl100k_base`) — **on the then-current tree**, and use THAT number as the FC9 baseline. The 20 111 figure above stands as the historical record of the 08:12Z measurement; it is not the FC9 comparison input.

---

## 8. Artifact map (this gate's outputs)

| Artifact | Role |
|---|---|
| `gate-i-tools-list-recapture.md` (this file) | the evidence note |
| `gate-i-keep-set.json` | the machine-readable keep-set (28 verbs, both forms) — **gate-(i) criterion-4 input · Phase 4 §2.1's input · FC8 minimum-toolset input** |
| `tests/fixtures/plane_catalog.json` | the committed hermetic copy (sorted names + per-tool property map + token counts) |
| `tests/helpers/plane_catalog_fixture.py` | the loader + the `assert_keep_set_matches_catalog` membership assertion |
| `tests/unit/test_plane_catalog_artifact_integrity.py` | the scoped verification node (GREEN leg + the **RED-leg demonstrations** X1.10 requires — delete-entry / absent-name / daemon-name-drift / sha256-falsified / pruned-count-falsified / catalog-side-missing, 16/16 pass) |
