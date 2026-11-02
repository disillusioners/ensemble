# Plane Remote MCP Tool Inventory — captured 2026-11-02

**Captured by:** technical-analysis worker (plane-pm-issue-tools lane)
**Probe bounds:** JSON-RPC `initialize` + `notifications/initialized` + `tools/list` ONLY.
No create/update/delete/write verb was invoked. No workspace data was written.
**Credential used:** `PLANE_MCP_API_KEY` from `/home/nea/ensemble-src/.env` (dev context).
The live-install key is DEAD — see §5. No secret value appears in this file.

**Server:** `Plane MCP Server (header-http)` v`3.2.0`
**Total tools:** 177  |  **Endpoint:** `https://mcp.ensem.dev/plane/http/api-key/mcp`

---

## 1. Headline findings

| # | Finding | Evidence |
|---|---------|----------|
| F1 | **Path A write coverage for issue CRUD + comments + cycles is COMPLETE.** No Path B fallback is needed for the Task 3 use case. | §3, §4 |
| F2 | **ZERO `*_issue` verbs exist.** The charter vocabulary `plane_create_issue` / `plane_list_issues` is wrong; the real entity is `work_item`. | §2 |
| F3 | **The raw tool names carry NO `plane_` prefix.** The prefix is applied daemon-side by `tool_adapter.py` (`tool_name_prefix` override). Runtime names = `plane_<raw_verb>`. | §2 |
| F4 | **`tools/list` is workspace-agnostic.** The e2e and nea slugs return byte-identical 177-tool lists. | §6 |
| F5 | **`create_work_item` has an `is_draft` parameter** — the Task 3 DRAFT-issue creation is directly expressible. | §3 |
| F6 | **The live-install `PLANE_MCP_API_KEY` is EXPIRED/INVALID (HTTP 401 `invalid_token`).** The dev-context key works. | §5 |
| F7 | **The `e2e` workspace EXISTS and the REST key reaches it** (HTTP 200, 2 projects). | §7 |

---

## 2. Full inventory — 177 raw verb names

The daemon prefixes every one of these with `plane_` (see `daemon/mcp/builtin_servers/plane.py` `tool_name_prefix` → `daemon/mcp/tool_adapter.py:487-511`).
So `create_work_item` reaches the agent as **`plane_create_work_item`**.

```
attach_page_to_work_item                      complete_cycle                                count_work_items                            
create_customer                               create_customer_property                      create_customer_request                     
create_cycle                                  create_initiative                             create_intake_work_item                     
create_label                                  create_milestone                              create_module                               
create_page                                   create_project                                create_project_estimate                     
create_project_estimate_points                create_release                                create_release_label                        
create_release_tag                            create_state                                  create_work_item                            
create_work_item_comment                      create_work_item_link                         create_work_item_property                   
create_work_item_property_option              create_work_item_relation                     create_work_item_relation_definition        
create_work_item_type                         create_work_log                               delete_customer                             
delete_customer_property                      delete_customer_request                       delete_cycle                                
delete_initiative                             delete_intake_work_item                       delete_label                                
delete_milestone                              delete_module                                 delete_project                              
delete_project_estimate                       delete_project_estimate_point                 delete_release                              
delete_release_label                          delete_release_tag                            delete_state                                
delete_work_item                              delete_work_item_attachment                   delete_work_item_comment                    
delete_work_item_link                         delete_work_item_property                     delete_work_item_property_option            
delete_work_item_property_value               delete_work_item_relation_definition          delete_work_item_type                       
delete_work_log                               detach_page_from_work_item                    get_customer_property_values                
get_features                                  get_me                                        get_pql_reference                           
get_project_estimate                          get_project_members                           get_project_worklog_summary                 
get_release_changelog                         get_work_item_attachment_download_url         get_work_item_property_value                
get_workspace_members                         import_work_item_types_to_project             link_estimate_to_project                    
list_archived_work_items                      list_customer_properties                      list_customer_requests                      
list_customer_work_items                      list_customers                                list_cycle_work_items                       
list_cycles                                   list_initiative_projects                      list_initiatives                            
list_intake_work_items                        list_labels                                   list_milestone_work_items                   
list_milestones                               list_module_work_items                        list_modules                                
list_pages                                    list_project_estimate_points                  list_projects                               
list_release_labels                           list_release_tags                             list_release_work_items                     
list_releases                                 list_roles                                    list_states                                 
list_work_item_activities                     list_work_item_attachments                    list_work_item_comments                     
list_work_item_links                          list_work_item_pages                          list_work_item_properties                   
list_work_item_property_options               list_work_item_relation_definitions           list_work_item_relations                    
list_work_item_types                          list_work_items                               list_work_logs                              
manage_customer_work_items                    manage_cycle_archive                          manage_cycle_work_items                     
manage_initiative_projects                    manage_milestone_work_items                   manage_module_archive                       
manage_module_work_items                      manage_project_archive                        manage_release_labels                       
manage_release_work_items                     manage_work_item_archive                      manage_work_item_assignee                   
manage_work_item_label                        manage_work_item_type_properties              read_work_item_attachment                   
remove_work_item_relation                     resolve_work_item_type                        retrieve_customer                           
retrieve_customer_property                    retrieve_customer_request                     retrieve_cycle                              
retrieve_initiative                           retrieve_intake_work_item                     retrieve_label                              
retrieve_milestone                            retrieve_module                               retrieve_page                               
retrieve_project                              retrieve_release                              retrieve_release_tag                        
retrieve_role                                 retrieve_state                                retrieve_work_item                          
retrieve_work_item_activity                   retrieve_work_item_by_identifier              retrieve_work_item_comment                  
retrieve_work_item_link                       retrieve_work_item_property                   retrieve_work_item_property_option          
retrieve_work_item_type                       search_work_items                             set_customer_property_values                
set_work_item_property_value                  transfer_cycle_work_items                     update_customer                             
update_customer_property                      update_customer_request                       update_cycle                                
update_initiative                             update_intake_work_item                       update_label                                
update_milestone                              update_module                                 update_project                              
update_project_estimate                       update_project_estimate_point                 update_project_features                     
update_release                                update_release_changelog                      update_release_label                        
update_release_tag                            update_state                                  update_work_item                            
update_work_item_comment                      update_work_item_link                         update_work_item_property                   
update_work_item_property_option              update_work_item_relation_definition          update_work_item_type                       
update_work_log                               update_workspace_features                     upload_work_item_attachment_from_url        
```

### Entity coverage (2nd token of the raw verb)

| token | count | token | count | token | count |
|-------|-------|-------|-------|-------|-------|
| `work` | 61 | `customer` | 18 | `release` | 18 |
| `project` | 16 | `cycle` | 9 | `module` | 7 |
| `initiative` | 6 | `milestone` | 6 | `intake` | 5 |
| `page` | 4 | `label` | 4 | `state` | 4 |
| `workspace` | 2 | `features` | 1 | `me` | 1 |
| `pql` | 1 | `estimate` | 1 | `archived` | 1 |
| `customers` | 1 | `cycles` | 1 | `initiatives` | 1 |
| `labels` | 1 | `milestones` | 1 | `modules` | 1 |
| `pages` | 1 | `projects` | 1 | `releases` | 1 |
| `roles` | 1 | `states` | 1 | `role` | 1 |

---

## 3. WRITE-VERB REALITY CHECK (the Task-3 gate)

The commission asked: does the remote surface support **issue/work-item CRUD + comments + cycles**?
Answer: **YES, completely.**

### 3a. Issue / work-item CRUD

| capability | raw verb | agent-visible name | required params |
|------------|----------|-------------------|-----------------|
| create | `create_work_item` | `plane_create_work_item` | ['name', 'project_id'] |
| update | `update_work_item` | `plane_update_work_item` | ['project_id', 'work_item_id'] |
| delete | `delete_work_item` | `plane_delete_work_item` | ['project_id', 'work_item_id'] |
| retrieve | `retrieve_work_item` | `plane_retrieve_work_item` | ['project_id', 'work_item_id'] |
| retrieve by identifier | `retrieve_work_item_by_identifier` | `plane_retrieve_work_item_by_identifier` | ['work_item_identifier'] |
| list | `list_work_items` | `plane_list_work_items` | [] |
| search | `search_work_items` | `plane_search_work_items` | ['query'] |
| count | `count_work_items` | `plane_count_work_items` | [] |

### 3b. Comments — 5 verbs (full CRUD)

| raw verb | agent-visible name | required params |
|----------|-------------------|-----------------|
| `create_work_item_comment` | `plane_create_work_item_comment` | ['project_id', 'work_item_id'] |
| `delete_work_item_comment` | `plane_delete_work_item_comment` | ['comment_id', 'project_id', 'work_item_id'] |
| `list_work_item_comments` | `plane_list_work_item_comments` | ['project_id', 'work_item_id'] |
| `retrieve_work_item_comment` | `plane_retrieve_work_item_comment` | ['comment_id', 'project_id', 'work_item_id'] |
| `update_work_item_comment` | `plane_update_work_item_comment` | ['comment_id', 'project_id', 'work_item_id'] |

### 3c. Cycles — 10 verbs (full CRUD + assignment + transfer)

| raw verb | agent-visible name | required params |
|----------|-------------------|-----------------|
| `complete_cycle` | `plane_complete_cycle` | ['cycle_id', 'project_id'] |
| `create_cycle` | `plane_create_cycle` | ['name', 'owned_by', 'project_id'] |
| `delete_cycle` | `plane_delete_cycle` | ['cycle_id', 'project_id'] |
| `list_cycle_work_items` | `plane_list_cycle_work_items` | ['cycle_id', 'project_id'] |
| `list_cycles` | `plane_list_cycles` | ['project_id'] |
| `manage_cycle_archive` | `plane_manage_cycle_archive` | ['archive', 'cycle_id', 'project_id'] |
| `manage_cycle_work_items` | `plane_manage_cycle_work_items` | ['cycle_id', 'project_id'] |
| `retrieve_cycle` | `plane_retrieve_cycle` | ['cycle_id', 'project_id'] |
| `transfer_cycle_work_items` | `plane_transfer_cycle_work_items` | ['cycle_id', 'new_cycle_id', 'project_id'] |
| `update_cycle` | `plane_update_cycle` | ['cycle_id', 'project_id'] |

### 3d. `create_work_item` — full parameter list (the DRAFT path)

```
required: ['name', 'project_id']
optional: ['assignees', 'description_html', 'description_stripped', 'estimate_point', 'external_id', 'external_source', 'is_draft', 'labels', 'parent', 'point', 'priority', 'sort_order', 'start_date', 'state', 'target_date', 'type', 'type_id']
```
**`is_draft` is present** → the Task 3 DRAFT issue is directly expressible as
`plane_create_work_item(project_id=…, name=…, is_draft=true, description_html=…)`.

### 3e. Write-capability census

**116 of 177 tools (65%) are write-capable.**
Only 61 are read-only (list/retrieve/get/count/search/read).
There is **no write-poor scenario** for the Task 3 use case.

---

## 4. Verdict on the leader's Path-A conditional

> Leader lean #1: *"If the remote surface turns out write-poor for work items (e.g. no create/update work-item verbs), Path B becomes primary."*

**CONDITION DISCHARGED — the surface is NOT write-poor.**
All three named capability groups (work-item CRUD, comments, cycles) have complete write coverage
in the captured inventory. The leader's Path-B trigger does **not** fire.

---

## 5. Credential state — the live key is DEAD

| credential source | variable | result |
|-------------------|----------|--------|
| `/home/nea/agents-ensemble/.env` (live install) | `PLANE_MCP_API_KEY` | **HTTP 401 `invalid_token`** — rejected for every header variant tried (`Authorization: Bearer`, `x-api-key`, both, none) |
| `/home/nea/ensemble-src/.env` (dev context) | `PLANE_MCP_API_KEY` | **HTTP 200** — `initialize` succeeded |

Server response body (verbatim, contains no secret):

```json
{"error": "invalid_token", "error_description": "Authentication failed. The provided bearer token is invalid, expired, or no longer recognized by the server. To resolve: clear authentication tokens in your MCP client and reconnect. Your client should automatically re-register and obtain new tokens."}
```

**Consequence for the lane:** the daemon's availability gate is PRESENCE-ONLY
(`plane.py:296-298` — `PLANE_MCP_URL` + `PLANE_MCP_API_KEY` non-empty).
A dead key therefore passes `is_available()` → the `mcp_servers` row IS created →
but every MCP session attempt then fails 401 at connect time.
**This is a distinct failure class from the wiring gap in Digest A** (which is "no row, no tools").
It is invisible from the daemon's own logs unless the session error is surfaced.

**Route:** key re-mint is USER-OWNED (no automated rotation path exists).
Recorded as risk R1 in `technical-analysis.md`.

---

## 6. Workspace-agnosticism of `tools/list`

Both slugs were probed with the working (dev) key:

| slug | tools/list result |
|------|-------------------|
| `slug=e2e` | ok=True count=177 |
| `slug=nea` | ok=True count=177 |

**The two 177-name lists are byte-identical** → the remote tool CATALOG is server-level,
not workspace-scoped. Two consequences:

1. The inventory capture is reproducible on ANY slug — the probe needs no workspace.
2. `tools/list` succeeding with a given slug does **NOT** prove that slug resolves to a
   reachable workspace (see §7 and verification gate (ii)).

---

## 7. e2e workspace existence + REST key reachability

Read-only `GET {PLANE_BASE_URL}/api/v1/workspaces/e2e/projects/` with the dev-context
`PLANE_API_KEY` (`X-Api-Key` header):

```
HTTP 200   count=2
  - E2E          | e2e
  - SYSTEMDEFAUL | __system_default__
```

**Verdict: the `e2e` workspace EXISTS and the REST credential reaches it.**
This satisfies the REST leg of verification gate (ii).

**What is NOT proven:** the `PLANE_MCP_API_KEY` (a *different* credential from `PLANE_API_KEY`)
reaching the `e2e` workspace's DATA over the MCP lane. The commission's hard rule forbids
calling any MCP verb other than `tools/list`, so `list_projects` over MCP was not run.
This remains an open item — see verification gate (ii) in `technical-analysis.md`.

---

## 8. `serverInfo.instructions` (head) — upstream's own semantics

```
## Epics

There are no epic tools — an epic is a work item whose type is named "Epic". Work
items always belong to a project; ask which if one is not named.
1. type = resolve_work_item_type(project_id, "Epic") — type.id is the type_id.
2. Create: create_work_item(project_id, type_id=type.id, name=...).
3. List: list_work_items(project_id, pql='type = "<type id>"').
4. Read / update / delete / nest: retrieve_work_item / update_work_item /
   delete_work_item by work item id (set parent=<work item id> to nest).
5. List an epic's children: list_work_items(project_id, pql='childOf("<EPIC-IDENTIFIER>")')
   using the epic's human-readable identifier (e.g. "PROJ-12") from retrieve_work_item.
```

Note the upstream terminology: **"an epic is a work item whose type is named 'Epic'"** —
i.e. upstream itself has NO `issue` concept; `work_item` is the entity.
The charter's `*_issue` vocabulary is upstream-drifted, not merely mis-prefixed.
