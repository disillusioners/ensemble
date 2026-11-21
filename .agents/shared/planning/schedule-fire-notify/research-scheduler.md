# Research: schedule-fire-notify substrate

Commission: when a scheduled task fires and its job is dispatched, the instance that CREATED the
schedule receives a best-effort notification carrying the work handle. This document is substrate
research only — no feature design. All file:line anchors verified against THIS tree
(worktree `/home/nea/ensemble-src-wt-schedule-fire-notify`, branch `feature/schedule-fire-notify`,
base `0b5624dc9` == origin/latest tip) at research time.

---

## Q1 — FIRE PATH (dispatch seam in the adapter)

### The wake → dispatch chain (all awaited, in the adapter's own asyncio task)

1. **Wake loop**: `SchedulerAdapter._run_schedule` (`daemon/sources/adapters/scheduler.py:703`).
   Per-adapter loop: computes `next_trigger = self._get_next_trigger_time()` (:710), sleeps via
   `asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)` (:792–795), and on due calls
   `await self._emit_scheduled_message()` (:804). Lateness-cap skip branch (:716–766) writes a
   SKIPPED row and does NOT dispatch.
2. **Fire tick**: `_emit_scheduled_message` (:2785). Mints `execution_id` (:2797); resolves the
   deferrable target READ-ONLY via `_resolve_deferrable_target()` (:2805 → :2428, **reuse_instance
   only** — returns `None` for new_instance by construction :2437–2438); age-cap breach check
   (:2826); acquires the execution-slot semaphore (:2837). Two fire outcomes:
   - **reuse + busy caller** → `await self._defer_delivery(...)` (:2855) — a DURABLE pending
     delivery to the pinned caller;
   - otherwise → `await self._execute_run(execution_id, "scheduled")` (:2885).
3. **Execution**: `_execute_run` (:2469) builds `metadata` (:2517–2539: `scheduler.{execution_id,
   trigger_type, trigger_time, instance_mode, run_number}` + `schedule_type` + `cron_expression`
   or `run_at`; top-level `agent`, `force_new_instance`), then routes:
   - `trigger_type == "scheduled"` AND manager present AND (`project_id` OR `reuse_instance`)
     → `await self._route_via_job_queue(...)` (:2554–2562);
   - else `_execute_immediate` (:2564) — manual-trigger twin and the legacy edge only.

### THE direct dispatch site (where a fire becomes a job)

`_route_via_job_queue` (`scheduler.py:2622`):
- `InstanceMapper.get_or_create_instance(...)` (:2659–2666) with `force_new = (mode ==
  NEW_INSTANCE)` (:2660) — resolves or mints the TARGET instance.
- D4 one-shot idempotency key `f"scheduler:{self.source_id}:{self._run_at.isoformat()}"`,
  gated to `SCHEDULE_TYPE_ONE_TIME` (:2688–2691; contract docstring :36–43).
- Inert-by-default mission-record mint (:2712–2721) — precedent for an OPTIONAL post-resolution,
  pre-enqueue augmentation riding `enqueue_kwargs` without changing fire-path behavior.
- **`result = await self._manager.enqueue_message_job(instance_id=instance_id, message=...,
  source="scheduler", priority=self._priority, metadata=metadata, **enqueue_kwargs)`**
  (:2723–2731). `result` carries `.job_id` and `.message_id` (logged at :2736) — **the work
  handle exists here**. Success callback follows at :2739+.

**The deferred twin**: `_defer_delivery` → `await self._manager.enqueue_message_job(...)` at
`scheduler.py:2109–2116` (metadata from `_deferred_metadata` :1285, key from
`_deferred_idempotency_key` :1310; row-first audit discipline at :2073). The deferred delivery
ALSO produces `job_id`/`message_id`. A fire-notify hook that watches only :2723 will miss every
deferred delivery; watching both sites is mandatory for correctness of "the schedule fired".

### Synchronicity

The enqueue is **synchronous (awaited) inside the wake loop**: `_run_schedule` →
`_emit_scheduled_message` → `_execute_run` → `_route_via_job_queue` → `await enqueue_message_job`.
The adapter task blocks for the duration (enqueue writes JobItem + Task + MessageQueue rows and
dispatches inline — see `daemon/services/instance_messaging.py:3131+`, row-write comments
:3504–3506). There is NO existing post-dispatch callback carrying the work handle: the
constructor's `execution_callback` (:289; fired at :2486, :2739, skip branches :750/:2873) is
status-only — `(execution_id, schedule_id, status, instance_id, error_message)` — no
`job_id`/`message_id`.

### What is in scope at the :2723 site

- `self.source_id` — the schedule id (= `source_configs.source_id`; == `external_user_id` in the
  mapper call :2663).
- `self._schedule_type` (cron / interval / one_time; :360–363, set by `_parse_schedule_config`
  :501–564), `self._run_at` (:363; one-shots only).
- Config fields read at `__init__` from `config.config` (:326): `instance_mode` (:329, one-time
  default-forcing :350–357), `agent` (:366 — the TARGET agent), `message` (:367),
  `timezone` (:388–406), `max_concurrent` (:418), `project_id` (:423), `priority` (:424–425).
  **The schedule label is `SourceConfig.name`** (the `name` column IS the label —
  `scheduling_service.py:729–733`, `create_source_config(SCHEDULER_SOURCE_TYPE, label, ...)`
  :812–820); reachable as `self.config.name` (base-adapter stored config), not a dedicated attr.
- Locals: `execution_id`, resolved target `instance_id` (:2661), `metadata`, and
  `result.job_id` / `result.message_id`.
- **NOT in scope: any creator/caller identity** — nothing in `source_configs.config` carries it
  today (see Q2).

### Candidate seams for a post-dispatch notify hook (facts, not design)

- **After the awaited enqueue**: immediately after :2731 (direct) and after :2116 (deferred),
  where `result.job_id`/`result.message_id` are in hand and the delivery is durable. To add zero
  latency and zero failure-coupling, the hook must be fire-and-forget
  (`asyncio.create_task` with its own exception guard) — the mission-record mint (:2712–2721,
  fail-open wrapper comment :2710–2711) is the in-file precedent for an OPTIONAL, failure-isolated
  augmentation of the dispatch path.
- The `execution_callback` constructor hook (:289) is the existing observation seam but carries
  no work handle — extending it changes its contract at 5+ call sites.
- The fire-outcome vocabulary (`FIRE_OUTCOME_DIRECT` / `REARMED` / `FAILED` /
  `REFUSED_PERMANENT`; :452–454, :2586, :2032, :2100) already discriminates which fires produced
  a real dispatch — relevant if "fired" must exclude re-arm-only ticks.

---

## Q2 — CREATOR CAPTURE GAP

### Caller identity IS available at the tool layer today

- Factory: `create_scheduling_tools(scheduling_service, *, source_repo=None,
  current_instance_id: str = "", agent_id: str = "", agent_tag=None)`
  (`daemon/tools/scheduling.py:132–139`). The per-instance wiring passes the CALLING instance's
  id: `create_scheduling_tools_if_available(...)` → `create_scheduling_tools(...,
  current_instance_id=current_instance_id, agent_id=agent_id, ...)`
  (`daemon/tools/instance.py:2831–2837`).
- Captured at factory time into closure vars: `caller_agent_id = agent_id` (:162),
  `caller_instance_id = current_instance_id` (:163). Docstring states `current_instance_id` is
  **"Caller's instance ID (audit echo only)"** (:150–151).
- `task_schedule` forwards both to the service: `scheduling_service.create_schedule(payload,
  caller_instance_id=caller_instance_id, caller_agent_id=caller_agent_id)`
  (`daemon/tools/scheduling.py:197–201`).

### The service drops them

`create_schedule(payload, *, caller_instance_id, caller_agent_id)`
(`daemon/services/scheduling_service.py:710–715`) uses them **for audit logging ONLY**
(docstring :720–722; the log line :836–844). They never reach the persisted row.

### `source_configs.config` JSON keys today

Built by `_build_config_json` (`scheduling_service.py:422–454`); adapter-read contract documented
at :432–434:

| key | when | line |
|---|---|---|
| `agent` | always (target agent id) | :437 |
| `message` | always | :438 |
| `timezone` | always (resolved IANA name) | :439 |
| `recurrence` | always (echo: once/daily/weekly/cron) | :440 |
| `local_time` | always (echo) | :441 |
| `project_id` | conditional (set when provided) | :443–444 |
| `priority` | conditional (omitted when == 5) | :445–446 |
| `instance_mode` | always (:447–449) | :449 |
| `run_at` | conditional (one-shot) | :450–451 |
| `schedule` | conditional (raw cron; note KEY IS `schedule`, not `cron_expression`) | :452–453 |

Runtime-added keys from the deferral lane (not create-time): `_run_counter`,
`_defer_gen`, `_defer_message_id`, `_defer_count`, `_defer_since` (prior-art plan §C0;
repository JSONB atomic ops).

### Creator field: CONFIRMED ABSENT

Grep `creator|created_by` across `daemon/**/*.py`: ZERO hits in the scheduler adapter,
scheduling_service, scheduling tools, or source models/config-JSON builders. All hits are
unrelated: the JobItem-creator census (`daemon/job_state/constitution.py`), board items
(`daemon/models/board.py:284`), custom-agent configs (`daemon/repositories/
custom_agent_configs/models.py:205,244`), and `projects.creator_session_id`
(`daemon/migrations/runner.py:460`). The REST create route
(`daemon/routers/schedules.py:53–85`) passes label/agent/message/when/instance_mode etc. and has
NO instance identity at all — REST-created schedules can never carry a creator instance (a
NULL-able field is therefore required whatever storage is chosen).

Prior art for capture: the `self_alarm` tool already binds a reuse_instance schedule to its
creator via the **instance mapping** (`daemon/tools/self_alarm.py:40, 143, 867–886`) — i.e. for
`reuse_instance` schedules the creator instance is ALREADY pinned in
`instance_mappings.agent_instance_id` (`daemon/repositories/source/models.py:115`; read at fire
time by `_resolve_deferrable_target`, `scheduler.py:2442–2452`). **The true gap is the
`new_instance` lane** — nothing records which instance created the schedule.

---

## Q3 — INSTANCE_MODE SEMANTICS (verified in code)

**`new_instance` — creator instance NOT involved at fire time.**
`_route_via_job_queue`: `force_new = self._instance_mode == SchedulerInstanceMode.NEW_INSTANCE`
(`scheduler.py:2660`) → `mapper.get_or_create_instance(..., force_new=force_new)`
(:2661–2666) mints/resolves a FRESH instance of the TARGET agent (`self._agent`); the fire
message becomes that instance's turn. Metadata echoes `force_new_instance: True`
(:2514, :2526). The creating instance receives nothing — this is exactly the lane where a
post-dispatch creator notify carries new information.

**`reuse_instance` — the wake IS the message to the calling instance.**
- Same job-queue route is used (:2554–2561: reuse takes the job-queue route EVEN WITHOUT
  `project_id` — the comment :2546–2553 explains why), but the mapper resolves the EXISTING
  mapping (no force) → delivery is an ordinary message into the pinned caller instance
  (`instance_mappings.agent_instance_id`, `models.py:115`).
- Busy-caller variant: `_emit_scheduled_message` :2848–2863 → `_defer_delivery` — a durable
  PENDING delivery to the SAME pinned caller (:2852–2855), delivered as its next turn.
- One-time schedules honor explicit `reuse_instance` since the 2026-10-20 ruling
  (`scheduler.py:330–357`).
=> Confirmed: the reuse lane needs no extra notify (the wake already reaches the creator
instance, because for self-created reuse schedules the pinned caller IS the creator —
self_alarm precedent). The feature's notify targets the **new_instance** lane (and must decide
policy for REST-created schedules with no creator).

---

## Q4 — SCHEMA

### Models

- `SourceConfig` — `daemon/repositories/source/models.py:62–98`, table `source_configs`
  (`source_id` PK :66, `source_type` idx :67, `name` idx :68 — the label, `config` JSONB via
  `JSONBType` :70–73, `credentials` :75, `enabled` :76, `autostart` :77, `status` :78,
  `error_message` :79, `created_at`/`updated_at` :81–82).
- `InstanceMapping` — :101–139, table `instance_mappings` (UNIQUE(source_id, external_user_id)
  :104–110; `agent_instance_id` :115 — the reuse pin).
- `ScheduleExecution` — :159–185, table `schedule_executions` (`execution_id` PK :167,
  `schedule_id` FK→source_configs :168, `triggered_at` :169, `instance_id` nullable idx :170 —
  FK-free TEXT, `status` TEXT :171, `error_message` :172, `completed_at` :173).
- `ScheduleExecutionArchive` / `SourceConfigArchive` — :188–251 (source-GC archive; the archive
  snapshots `config` verbatim :224–230, so a config-JSON creator key would survive GC, a new
  column would NOT unless the archive table grows it).

### Migrations touching these tables (naming: `YYYYMMDD_HHMMSS_snake_description.sql`)

- `20250326_000000_initial_schema.sql` (creates both tables).
- `20260402_000001_rename_session_to_instance.sql` (session_id→instance_id rebuild of
  schedule_executions, :95–114).
- `20260425_000001_add_schedule_execution_indexes.sql` (2 indexes).
- `20260614_000001_add_autostart_to_source_configs.sql` — the canonical ADDITIVE-COLUMN
  precedent on source_configs: `ALTER TABLE source_configs ADD COLUMN autostart BOOLEAN DEFAULT
  TRUE;` (:8).
- `20260619_000003_add_unique_instance_mappings.sql`.
- `20261107_122648_create_source_gc_archive_tables.sql` (newest touching the family; creates the
  two archive tables).

Current highest migration number in THIS tree: **`20261109_021154_create_memoir_v2_tables.sql`**
(122 files, zero exceptions — every table change gets its own `.sql` file; the runner is
SQLite-only, PG evolution is `_ensure_postgres_columns`, `daemon/manager.py`).

### If a creator-instance COLUMN were added (additive-only sketch)

Name: e.g. `20261121_HHMMSS_add_creator_instance_id_to_source_configs.sql` —
`ALTER TABLE source_configs ADD COLUMN creator_instance_id TEXT;` (+ optional index), NULL for
pre-existing rows and REST-created schedules (backfill is impossible — the identity was never
recorded). Model change: one nullable `Field(default=None)` on `SourceConfig` (:62–98 pattern).
Follow the autostart migration (:60614_000001) as the shape. Also mirror into
`SourceConfigArchive` (:221–251) if GC-restore must round-trip it.

### Config-JSON alternative (key e.g. `creator_instance_id` in `config`)

- **Gains**: zero migration (JSONB column already exists :70–73); automatically survives
  source-GC archive/restore (config snapshot is the sole reconstruction input, :224–230);
  matches the established state-carriage precedent of this exact table (`_run_counter`,
  `_defer_*` keys — prior-art plan §C0); read anywhere the adapter reads config
  (`__init__` :326) with no schema coupling.
- **Loses**: no typing/constraint/NOT-NULL enforcement; not SQL-queryable (cannot filter or
  join by creator without JSON-path queries; no index); invisible to the schema-audit trail
  (migrations dir); mixes identity with schedule config; **update hazard** —
  `update_source_config` is a FULL config replace (prior-art plan §C0: "its docstring forbids
  per-key mutation"), so `task_schedule_update` must re-thread the key or it is silently lost.

### Prior-art verdict: `.agents/shared/planning/scheduler-deferred-alarm-delivery/`

**HIGHLY relevant.** That lane (plan-overview.md, decisions.md, architecture-recommendation.md)
built the delivery-on-fire machinery this feature must coexist with: the DEFERRED execution
status (pure-Python enum member, `models.py:48–55`, NO migration — status is TEXT), the
`_defer_delivery` durable pending-delivery path (deferred enqueue at `scheduler.py:2109`),
the `_defer_*` config-JSONB state carriage with atomic `jsonb_set`/`json_set` sibling ops
(plan §C0), and the architect's §16 ruling "NO new table / migration / daemon service / polling
loop" for delivery concerns — the same gravity a creator-notify feature should respect
(config-JSONB carrier, not a column, under that precedent). It also established the
row-first audit discipline (:2073) and the DEC-12 deferred-count echo semantics
(`scheduling_service.py:204–217`) that any "schedule fired" echo surface must not contradict.
Key coupling: any fire-notify hook MUST cover both dispatch sites (direct :2723 AND deferred
:2109) — the prior lane proved the deferred path is the NORMAL shape for busy reuse callers,
not an edge case.

---

## Q5 — TEST IDIOM

- **Unit + REST**: `tests/test_scheduler_api.py` (1873 lines). Canonical `mock_manager`
  fixture (:22–129): `Mock()` manager with AsyncMock seams, a TEMP SQLite DB with hand-written
  `source_configs` / `instance_mappings` / `schedule_executions` DDL (:49–88), a Mock source
  repository (:93–103), and `manager.scheduling_service` AsyncMock stubs (:114–117). App
  mounting: `app.state.manager = mock_manager` (:132–139) — routes read
  `request.app.state.manager` (phase-3 convention). Adapter unit lives in
  `tests/test_scheduler_adapter.py` (pack line 11).
- **Integration**: `tests/integration/test_scheduled_tasks_e2e.py` (560 lines),
  `pytestmark = pytest.mark.integration` (:56) — DESELECTED by default addopts; run via
  `-m integration` or the pack. Idiom: REAL `InstanceManager` via the chat-source harness
  `wire_manager_only(engine)` (:51–54; no LLM, no worker pool), REAL scheduling_service,
  REAL SourceRegistry build/start, REAL inline dispatch chain
  `_route_via_job_queue → InstanceMapper → enqueue_message_job` with JobItem + Task +
  MessageQueue row assertions (:13–18). Exactly ONE documented stub seam:
  `spawn_instance_with_mcp` (:20–26).
- **Acceptance pack**: `tests/packs/scheduled_tasks_acceptance.sh` — 15 lines: `set -euo
  pipefail`; `export ENSEMBLE_SELF_ENV=dev` (:8); `cd "$(dirname "$0")/../.."`; `exec
  .venv/bin/python -m pytest tests/test_scheduler_adapter.py tests/test_scheduler_api.py
  tests/integration/test_scheduled_tasks_e2e.py -m "integration or not integration" -q -rA`
  (:10–15); caller wraps with outer `timeout 600` (:5–6). A new pack
  (`tests/packs/scheduler_fire_notify_acceptance.sh`) should copy this shape exactly.
- **PACKS.md registration**: `.agents/tester/PACKS.md`. CAVEAT (verified): the worktree copy
  exists but is >1.4 MB and exceeds the grep tool's size cap — grep-empty here ≠ unregistered.
  House rule: cross-check the MAIN checkout's PACKS.md copy; worktree copies branched before a
  gate's registration lack the newer sections. Registration + mutation-proven regression pins
  are the norm for new gates.

---

## Hinge summary for the planner

1. Seam: post-enqueue at `scheduler.py:2731` (direct) AND `:2109` (deferred) — both required;
   fire-and-forget to keep zero latency/failure coupling (mission-mint :2712 is the in-file
   precedent).
2. Work handle: `result.job_id` / `result.message_id` at both sites; the existing
   `execution_callback` carries neither.
3. Creator capture point: tool closure `caller_instance_id` (`scheduling.py:163`) →
   `create_schedule` kwargs (`scheduling_service.py:713–714`) — currently audit-log-only
   (:836–844); config JSON today = agent/message/timezone/recurrence/local_time[/project_id/
   priority/instance_mode/run_at/schedule] (`_build_config_json` :436–454); NO creator key
   anywhere.
4. Schema: config-JSON key (zero migration, GC-safe, prior-art `_defer_*` precedent; loses
   typing/queryability, full-replace update hazard) vs additive column
   (`YYYYMMDD_HHMMSS_...sql`, autostart-migration shape; highest file today
   `20261109_021154_create_memoir_v2_tables.sql`; archive-table mirror consideration).
5. instance_mode confirmed: new_instance = fresh target instance (creator uninvolved — THE gap);
   reuse_instance = wake lands in the pinned creator instance (no extra notify needed).
6. REST-created schedules have no creator identity at all — NULL-able semantics mandatory.
7. Prior art `scheduler-deferred-alarm-delivery` is directly on point (config-JSONB state
   carriage, "no new table" gravity, deferred = normal shape for busy reuse).
8. Test idiom: mock_manager fixture unit (test_scheduler_api.py) + integration-marked harness
   e2e (wire_manager_only, one stub seam) + 15-line acceptance pack; register in
   .agents/tester/PACKS.md (worktree copy oversized for grep — cross-check main checkout).
