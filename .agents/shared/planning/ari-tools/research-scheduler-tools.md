# Research: task_schedule engine + tool-registration pattern (for ari-tools)

Worktree: /home/nea/ensemble-src-wt-ari-tools (branch feature/ari-tools, base latest@630cefebb).
All anchors from THIS checkout. Read-only research; sole output file.

---

## 1. TASK_SCHEDULE ENGINE

### 1a. Service + instantiation
- Service is a MODULE of module-level async functions, not a class: `daemon/services/scheduling_service.py` (1070 lines). Module docstring :1-84 — "the single seam behind agent tools AND REST"; canonical home for create/cancel/get/list/update (:3-7). Non-goal: it NEVER dispatches — never mints JobItems, never touches the enqueue path (:80-83). The SchedulerAdapter fires schedules.
- API surface: `ScheduleCreatePayload` :123, `ScheduleCreateResponse` :169, `ScheduleCancelResponse` :178, `ScheduleDetail` :185, `ScheduleListItem` :204, `ScheduleUpdatePayload` :208, `ScheduleUpdateResponse` :217. Functions: `create_schedule` / `cancel_schedule` / `get_schedule` / `list_schedules` / `update_schedule` (module-level; REST imports them verbatim, :4-7).
- Manager wiring: `daemon/manager.py:1284-1288` — comment "Scheduled-tasks (phase 2+4): the shared scheduling service module. Mounted LAZILY via the ``scheduling_service`` property"; `self._scheduling_service = None` (:1288). Property `InstanceManager.scheduling_service` at `daemon/manager.py:13534-13555`: first access imports the module and calls `configure(source_repo=self._source_repository, source_registry=self.source_registry, project_repository=self._project_repository)` (:13546-13554), caches on the manager.
- Deps inside the service: `_Deps` class :233-239; intentional single-manager module singleton `_deps` :242-248; `configure()` idempotent :265-284; `_require_repo()` :287 / `_require_registry()` :296.
- Concurrency: per-source `asyncio.Lock` map `_source_locks` :250-262, never evicted (docstring :40-45); update+cancel share one lock (cancel-vs-cancel / cancel-vs-update race answer, :29-39).

### 1b. Create surface — variants, tz, firing
- Recurrence variants (`ScheduleCreatePayload` fields, mirrored by the tool input `daemon/tools/scheduling.py:95-108`): `label` (1-128), `message`, `when`, `recurrence: once | daily | weekly | cron`, `timezone: IANA | None`, `weekday: 0=Sunday..6=Saturday` (weekly only), `cron_expression` (required when recurrence='cron'), `agent_id` (None ⇒ the calling agent), `project_id`, `priority 1-10` (default 5), `instance_mode: new_instance | reuse_instance`.
- Timezone: IANA names. Resolution chain D2 (`_resolve_schedule_timezone`, scheduling_service.py:362+; CATEGORY_DOC scheduling.py:47-53): explicit payload tz → user timezone setting (`_user_timezone_setting` :342-359 via `daemon/services/user_timezone_utils.get_user_timezone_preference`) → daemon default `SchedulingConfig().default_timezone` (`ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE`, `_default_scheduling_timezone` :310-318) → host-local → UTC — with a loud `tz_warning` echoed in every create/update response (never suppressed, ADR-002). Every echo returns BOTH `next_run_at_local` and `next_run_at_utc` (`_compute_next_run` :433-491).
- DST: croniter>=3.0 for cron path (ADR-008, scheduler.py docstring :3-31); service cron next-fire uses `daemon.util.tz.compute_next_cron_fire` (scheduling_service.py:474-478); naive one-shot anchoring via `daemon.util.tz.anchor_local_to_utc` (fold→0, gap→shift+warning).
- WHAT HAPPENS WHEN A SCHEDULE FIRES (`daemon/sources/adapters/scheduler.py`, class `SchedulerAdapter(MessageSourceAdapter)` :96):
  - Schedule types `SCHEDULE_TYPE_CRON/INTERVAL/ONE_TIME` :105-108; config keys read from `source_configs.config_json` (run_at / schedule / interval_seconds).
  - `_route_via_job_queue` :1025-1110 — the ONLY path (Phase 5 cutover, :1028-1035): resolves target agent (`self._agent` or metadata "agent", `registry.resolve_to_id` :1038-1046), then `InstanceMapper.get_or_create_instance(source_id, external_user_id=source_id, agent_id, force_new=instance_mode==NEW_INSTANCE)` :1056-1069 (fire targets a specific agent by creating/reusing that agent's instance).
  - One-time idempotency (D4/ADR-004): `idempotency_key = f"scheduler:{source_id}:{self._run_at.isoformat()}"` passed to enqueue, gated to ONE_TIME only (cron must keep minting fresh JobItems) :1090-1094; JobQueueService partial-UNIQUE collapses cross-restart/retry duplicates (:1071-1082). Key derived from `self._run_at` parsed once in `__init__` and identically every boot (`_parse_schedule_config`), NOT from `next_trigger`.
  - Dispatch: `await self._manager.enqueue_message_job(instance_id=..., message=formatted_message, source="scheduler", priority=self._priority, images=None, metadata=...)` :1096-1104 → creates a JobItem mirror + Task row, inline dispatch.
  - Lateness cap (D3/ADR-003, docstring :45-52): one-shot past-due beyond `SchedulingConfig.one_shot_max_lateness_seconds` → `schedule_executions` row `status='skipped'`, NO JobItem, schedule stays ARMED for re-fire via update.
  - Continuation messages for recurring runs: `_format_continuation_message` :801, used :962.

### 1c. Persistence
- Rows live in the `source_configs` table with `source_type='scheduler'` (`SCHEDULER_SOURCE_TYPE`, scheduling_service.py:100-103; service refuses non-scheduler rows :494-500). Schedule parameters serialized in `config_json` (recurrence keys :105-106).
- History table: `schedule_executions` — SQLModel `ScheduleExecution` at `daemon/repositories/source/models.py:151`. Migrations: born in `daemon/migrations/versions/20250326_000000_initial_schema.sql:15`; session→instance rename in `20260402_000001_rename_session_to_instance.sql:95-114` + index `:232`.
- Repository: `daemon/repositories/source/repository.py` — `create_source_config` :51, `update_source_config` :98, `get_source_config_by_name` :212 (label lookup), `cancel_source_config` :263 (atomic one-session `enabled=False` + `status='cancelled'`).
- Restart survival: rows persist in source_configs; adapters are re-created from config at boot via the SourceRegistry builder (`registry._create_adapter_from_config` used by `_rebuild_register_start`, scheduling_service.py:543-556; same builder start_all uses). Cancelled rows are skipped by the boot filter (module docstring :24-25). Idempotency key re-derives identically per boot (see above).

### 1d. Cancel / complete / delete / list / dedupe
- Cancel is TERMINAL (ADR-005/007): sole writer of `SourceStatus.CANCELLED` via atomic `cancel_source_config`; preserves `schedule_executions` history; cancelled hidden from default list (:18-25). NEVER purges history.
- Update = adapter-rebuild seam (§5.1): evict registry entry BEFORE drain (`_evict_and_stop` :519-540) → mutate row → `_create_adapter_from_config` → register → start (`_rebuild_register_start` :543+); documented <1s missed-fire window accepted (:71-78). Pause/resume via `paused` flag is RESUMABLE (tool docstring :17-18).
- In-flight residual (§5.3): an already-enqueued JobItem is NOT recallable; cancel echoes `last_execution_id` so the operator can kill the JobItem (:63-70).
- List: `list_schedules(project_id, status, include_cancelled, caller_agent_id)` (tool call scheduling.py:169-174).
- Dedupe-by-label: duplicate label REJECTED at create (full doc scheduling.py:276-277 "Duplicate labels are rejected"); no UNIQUE constraint on `name` — two racing creates both survive, second wins listing (:35-39). Label→id resolution is exact-match, first match wins (tool cancel :203-219 via `get_source_config_by_name`). Id-or-label accepted at REST too (`_resolve_schedule_ref` :503-516).
- NO hard delete exists: cancel is the terminal operation (history preserved).

### 1e. Existing schedule_* tools
- File: `daemon/tools/scheduling.py` (403 lines) — 4 tools: `task_schedule` (:110-149), `task_schedule_list` (:159-181), `task_schedule_cancel` (:189-226), `task_schedule_update` (:239-265). Registered under category **"scheduling"** (non-privileged).
- Registry entries: `DYNAMIC_TOOL_NAMES` `_tool_registry.py:156-164` (all four names, with comment "granted per-agent via tools.allow (agents/ari, agents/leader, agents/jober)"); `CATEGORY_MODULES` map `_tool_registry.py:736` area (`"shared_meta_kv": "daemon.tools.shared_meta_kv_tools"` is the pattern; scheduling module likewise mapped).
- Construction: `create_scheduling_tools_if_available(manager, current_instance_id, agent_id, agent_tag)` at `daemon/tools/instance.py:2271-2300` (returns [] when service unmounted); called inside `create_instance_tools` at `instance.py:5768-5775` ("Decorator-only registration is SILENTLY INVISIBLE — the extend ... is the third step of the three-step registration seam (decorator + registry entry + construction)" :2286-2289).
- Allow-listed agents (meta.json `tools.allow: "scheduling"`): `agents/ari/meta.json:32`, `agents/leader/meta.json:15`, plus `agents/jober` (per registry comment `_tool_registry.py:158-160`).
- REST twin: `daemon/routers/schedules.py` — body "mirrors the scheduling tool category's `task_schedule`" (:150); phase-3 handlers import the same service functions verbatim (:4-7).

---

## 2. TOOL REGISTRATION PATTERN

### 2a. Tool function definition
- Closure factory pattern: `create_<name>_tools(<deps>, *, current_instance_id: str, agent_id: str, agent_tag: str | None = None) -> list` (scheduling.py:62-91; tag param reserved for factory-signature parity, :85-87). Captured closure vars: `caller_agent_id` / `caller_instance_id` (:92-93).
- Per-tool: Pydantic `*Input(BaseModel)` with `Annotated[T, Field(description=...)]` fields (:95-108); then:
  ```python
  @register_tool_category("scheduling")   # ABOVE @tool (ens_db_tools.py:65 rule)
  @tool(args_schema=TaskScheduleInput)
  async def task_schedule(...) -> dict:
      """Create a scheduled task. Use tool_help("task_schedule") for details."""
  ```
  Short docstring stays one line for LLM context efficiency; long docs go on the `_full_doc_` attribute (scheduling.py:267-294 for task_schedule).
- House error rule (ADR-005): tools NEVER raise — every failure returns `"ERROR: {msg}"` (`_error` :57-59; every body wraps `try/except Exception → _error`, e.g. :148-149).
- Sync repo calls inside async tools: `await asyncio.to_thread(repo.method, ...)` (scheduling.py:206-216, "MINOR c" comment).
- Result truncation for list-shaped returns: `truncate_dict_result(result, "schedules")` (:179, from `._truncate`).

### 2b. Registration mechanics
- `@register_tool_category(category)` — `daemon/tools/_tool_registry.py:342-367`: sets `func._tool_category = category` + `func._tool_category_first_party = True` (:357-366; first-party flag blocks MCP-spoofed re-categorization).
- Docs registry: `_full_docs` dict :15, `_tool_metadata` :18; `register_full_doc()` :406-415 is "called automatically when a tool function has a `_full_doc_` attribute"; `get_full_doc` :418, `get_tool_metadata` :430, `list_tools_by_category` :451-463. `tool_help` serves these to agents.
- `DYNAMIC_TOOL_NAMES` frozenset `_tool_registry.py:23-179` — factory-built tools MUST be listed (scheduling four at :156-164; `shared_meta_kv` :58; secret sextet :137-142; settings trio :153-155; od.* Port tools :175-178 are runtime StructuredTools that belong ONLY here, :169-174). Frozen-binary drift pin test compares KNOWN_TOOL_NAMES against the pure source-`@tool` universe (:169-174).
- `CATEGORY_MODULES` map (module path per category, e.g. :736) + AST scan `_scan_category_module_sources` :466 / `discover_source_only_tool_names` :546 (frozen-binary name discovery).
- Allow/deny check: `resolve_tool_filter(allow, deny, tool_categories, all_tool_names)` at `daemon/tools/instance.py:373-434` — both empty → `None` (everything allowed); allow items expand categories via `list_tools_by_category()` (:409-410); deny wins (:384). Applied per-instance via `_apply_tool_filter` (referenced :2283). Privileged-category default-deny strip: `_strip_privileged_category_tools` (`daemon/tools/instance.py:6169`, cited from `_tool_registry.py:299,328-329`).
- ALL categories observed via `@register_tool_category("...")` stamps: `self` (access_memory.py:172, inner_soul.py:667), `mother` (agent_mother.py ×5), `attestation` (attestation.py:361), `bash` (bash.py:668), `blueprint` (blueprint.py ×9), `chart` (chart_tools.py:785), `doc_maintenance` (comment_edit.py:536, doc_commit.py:88, doc_write.py:267), `design` (compare_tools.py:1226), `context` (context_tools.py:55,87), `db` (db_tools.py ×5), `ens-db` (ens_db_tools.py ×4, PRIVILEGED), `ens-env` (ens_env_tools.py:253, PRIVILEGED), `filesystem` (filesystem.py ×6), `help` (help.py:164), `image-gen` (image_gen.py:695), `image` (image_tools.py ×4), `infra` (infra.py ×10), `instance` + `council` (instance.py:2489,2975,3155,3203,3354,3562,4490,5032,5330,5391,5461,5613,5629), `job` (job_queue.py ×17), `scheduling` (scheduling.py ×4), `shared_meta_kv` (shared_meta_kv_tools.py:69), `service` (loader.py:546), `snapshot` (loader.py:551). PRIVILEGED_TOOL_CATEGORIES (opt-in-only, `_tool_registry.py:239-338`): `system_upgrade`, `system-log`, `ens-db`, `ens-env`, `view-views`, `cu`, `secrets`, `settings`. Dynamic prefixes (runtime-only): `plane_`, `cu_` (:185-193).
- Privileged-category changes require SAME-PR five exact-set pin updates (:226-238) — NOT required for a non-privileged category like `scheduling`.

### 2c. Allow-list syntax + meta.json VERBATIM
Syntax: `tools.allow` / `tools.deny` entries are category names OR individual tool names (resolve_tool_filter docstring instance.py:388-389; deny wins).

`agents/ari/meta.json` (tools section, lines 10-39, VERBATIM):
```json
  "tools": {
    "allow": [
      "job",
      "bash",
      "filesystem",
      "time",
      "self",
      "help",
      "image",
      "image-gen",
      "context",
      "shared_meta_kv",
      "project",
      "job_messages",
      "job_tree",
      "job_progress",
      "job_inject",
      "pause_instance",
      "resume_instance",
      "system_upgrade",
      "mission",
      "service",
      "scheduling",
      "view-views",
      "cu",
      "secrets",
      "settings"
    ],
    "deny": ["edit_file", "write_file", "watch_job", "watch_jobs"]
  },
```
(ari full file: 41 lines; `default_queue: "system_parallel_queue"` :40; innate_skills `["job-orchestration","chart","todo"]` :9.)

`agents/leader/meta.json` (tools section, lines 13-16, VERBATIM):
```json
  "tools": {
    "allow": ["instance", "subtree_messages",
    "subtree_status", "self", "attestation", "project", "help", "image", "image-gen", "knowledge", "mcp", "critical_notes", "project_history", "shared_meta_kv", "question", "midflight", "job_pause", "job_resume", "scheduling", "view-views", "cu", "secrets", "settings"]
  },
```
(leader full file: 18 lines; team_members list :17; `no_force_explore: true` :9.)

### 2d. Docs surfacing
- Short docstring = LLM-visible; `tool_help(name)` serves `_full_docs[name]` (registry :1-6, :418-427). Authored inline as the `_full_doc_` string attribute right after the tool function (scheduling.py:267-294). Category-level doc: module-level `CATEGORY_NAME` + `CATEGORY_DOC` constants (scheduling.py:37-54; shared_meta_kv_tools.py:34-46).

---

## 3. SHARED_META_KV REFERENCE (simple stateful tool family)

`daemon/tools/shared_meta_kv_tools.py` (159 lines): factory `create_shared_meta_kv_tools(manager, current_instance_id) -> list` (:49) returns one tool `shared_meta_kv` built as `@register_tool_category("shared_meta_kv")` (:69) over `@tool` (:70). Per-tree scoping: `context_key` is resolved LAZILY inside the tool body — `manager._instance_repository.get_tree_root_id(current_instance_id)`, falling back to `current_instance_id` (:109-122); repo access also lazy: `manager.shared_meta_kv_repo` (:124-127). Underlying table `shared_context_metadata` (:14-17); set_kv/delete_keys/clear_all upsert-delete-clear order (:83-98); no-args call = read-only JSON state return (:100-102). Registration: `DYNAMIC_TOOL_NAMES` entry `_tool_registry.py:58`, `CATEGORY_MODULES` entry `:736` (`"daemon.tools.shared_meta_kv_tools"`), construction inside `create_instance_tools` at `daemon/tools/instance.py:6002-6003` (`create_shared_meta_kv_tools(manager, current_instance_id)` → `tools.extend(...)`). Category is non-privileged — reachable via the empty-allow default universe AND explicitly allow-listed for ari (:21) and leader (:15).

---

## 4. TOOL AUTH (`daemon/tools/_auth.py`)

- `TOOL_REQUIRED_AGENTS: dict[str, list[str]]` :35-51 — category → backing agents it needs to spawn: `knowledge→[explorer,kb-writer]`, `chart→[charter]`, `image→[image-reader]`, `council→[governor]`, `design→[image-comparator]`. Purpose: when a caller's `tools.allow` names such a category, those agents are implicitly added to its effective `team_members` allow-set — `tools.allow` becomes the single source of truth, removing double authorization (:7-18). `_check_team_membership` :90+ denies by default when team_members missing AND no category match (:111-112).
- `SECRET_TYPED_ARGS` :87 — currently EMPTY dict; opt-in slot for LLM-visible scalar-string args carrying `__ENS_SECRET__` markers, read by the wrapped_tools_node pre-invoke seam (`daemon/services/long_tool_nudge.py`) :53-87. Dict/list-valued fields are skipped (bash.env entry was DEAD and removed, :78-86).
- **Verdict for board_\***: simple DB-backed tools that never spawn agents need NONE of `_auth.py` — no TOOL_REQUIRED_AGENTS row (it is exclusively for agent-invoking categories), and SECRET_TYPED_ARGS only if board args must carry secret markers. What board_* DOES need: `@register_tool_category("board")`-style stamps, `DYNAMIC_TOOL_NAMES` entries for factory-built names, CATEGORY_MODULES entry, construction inside `create_instance_tools`, and per-agent `tools.allow: ["board"]` (or reliance on the empty-allow default universe if non-privileged). If a board tool ever calls `invoke_agent_and_wait`/spawn machinery, THEN add the TOOL_REQUIRED_AGENTS row (rule :31-34: category string MUST byte-match the `@register_tool_category` argument).

---

## Planning notes for self_alarm / board_*
- `self_alarm` = thin `task_schedule(recurrence="once", agent_id=None, message=...)` wrapper: the engine already does idempotent one-shots, lateness cap, tz chain. The ≥30min-horizon validation lives in the wrapper (input validation before ScheduleCreatePayload). Cancel path = task_schedule_cancel via label/source_id already exists.
- board_* family: mirror shared_meta_kv_tools.py structure (factory + lazy repo access via manager property) with a dedicated SQLModel table + migration file under `daemon/migrations/versions/` (Core Architecture: every new SQLModel table requires its own `*.sql` — the file is the persistent record), DYNAMIC_TOOL_NAMES + CATEGORY_MODULES entries, construction extend inside create_instance_tools (instance.py:6002 pattern), and ari meta.json `tools.allow` addition. Non-privileged ⇒ no five-pin SAME-PR updates.
