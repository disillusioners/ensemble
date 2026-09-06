# Changelog

All notable changes to the agents-ensemble project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — 2026-09-06

### Fixed — Unblock round (`fix/defer-self-witness-and-cleanup`)

#### Preflight defer-count wiring (ITEM 1)

The preflight endpoint (`GET /api/jobs/cleanup/preflight`) previously
read `manager._defer_block_resolver`, but `daemon/api.py:977-978`
wires **only** the `daemon.routers.queues` module-global singleton
(`set_defer_block_resolver(DeferBlockResolver(...))` is the
production-shape wiring). `manager._defer_block_resolver` was NEVER
assigned in production, so `defer_blocked_count` silently stayed `0`.
The preflight now consumes the already-wired singleton via
`daemon.routers.queues.get_defer_block_resolver()` — the same
factory the `GET /api/queues/defer-blocked` endpoint uses — so the
defer count surface and the resolver singleton can no longer drift.

The unit test that hand-set `manager._defer_block_resolver` (the
round-2 mask class that hid the gap) was updated to use
`set_defer_block_resolver(resolver)` and now exercises the
real-wiring path end-to-end.

#### `defer_pending_count` is an instance method (ITEM 4)

`daemon.services.defer_block_resolver.DeferBlockResolver.defer_pending_count()`
is now a public INSTANCE method (round-2's free function
`defer_pending_count(engine)` is gone). The engine is reached via
`self._job_repo.engine` internally — no direct
`_job_repo.engine` reach-through from the router anywhere. The SQL
constant `_DEFER_PENDING_COUNT_SQL` stays module-private (underscore
prefix).

#### Cycle docstring rewritten to truth (ITEM 3)

The `jobs_management.py` docstring claimed a `schemas → daemon.routers`
edge that does not exist. The real traced cycle goes through the
routers package boundary:

```
jobs_management → defer_block_resolver → routers.schemas →
  routers/__init__ → queues.py:32 → recursion
```

The deferred-import pattern stays (the cycle is real); the docstring
now describes the actual edges.

#### Canonical copy cross-surface pin (ITEM 5)

The cleanup-truth-split sentence (Round-2 ITEM 3 / T-H1) now lives
on three surfaces, each pinned by a plain-TS or Python spec:

* **BE**: `daemon/routers/jobs_management.py:cleanup_preflight`
  docstring;
* **FE**: `frontend/src/app/models/cleanup-preflight.model.ts` —
  exported constant `CLEANUP_TRUTH_SPLIT_COPY` referenced by
  `system-cleanup-confirm-dialog.component.ts`;
* **docs**: `docs/job-task-system.md` §8.5.

A drift between any two surfaces breaks `cleanup-preflight.model.spec.ts`
(plain-TS, no Angular TestBed) or `tests/unit/test_docs_cleanup_truth_split.py`.

### Known follow-ups (ledgered for visibility)

* **`TestIdlePredicatePgSqliteParity` boolean-bind helpers** —
  `tests/job_queue/test_defer_gate_post_settle_window.py` methods
  `_insert_queue` / `_insert_job` bind integer literals `1,0` for
  the boolean-typed columns (`is_system`, `is_paused`,
  `concurrency_limit`-as-flag). SQLite accepts the bind; PostgreSQL
  rejects it (`InvalidTextRepresentation` on the boolean type). The
  parity test currently runs red-on-PG until the helpers are made
  dialect-aware (e.g. use `True/False` and let SQLAlchemy coerce, or
  branch on `engine.dialect.name`). Tracked for the next round —
  not blocking the unblock.

---

### Fixed — WS4 Round-2 (`fix/defer-self-witness-and-cleanup`)

#### Force-complete TOCTOU re-check (Round-2 W1)

`JobQueueService.force_complete_defer_holder` now re-derives the
`has_live_work(instance_id)` predicate IMMEDIATELY before
`terminate_instance`, in addition to the original probe at the top
of the method. A small probe→terminate window remains; the second
call catches state that lands between the probe and the destructive
call (delegating-repo write, injected state). A busy re-check
returns `terminated=False, probe_busy=True` (200, NOT an exception).
The docstring no longer claims the guard is "race-proof" — the
remaining window is covered by `terminate_instance`'s own
idempotency-on-terminal cascade.

#### Holder-probe scope gap — task + child-instance arms folded in (Round-2 W2)

The original holder probe
(`JobRepository.has_active_non_deferred_work(None, requester_instance_id=<holder>)`)
was job-side only. It missed two live-work shapes the bulk zombie
scan already detected:

* a Task in `pending`/`running`/`paused` (no JobItem at all —
  direct Task, common for forked helpers / reaper sweep);
* a non-terminal child instance (a `waiting_children` parent whose
  subtree is still executing).

A new `SQLModelInstanceRepository.has_live_work(instance_id)`
single-instance companion reuses the same three CSV constants the
bulk `_build_zombie_scan_sql` bakes into the zombie predicate
(`_TERMINAL_STATUSES_FOR_ZOMBIE_SCAN`,
`_LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN`,
`_LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN`) — derive-don't-reimplement.
`force_complete_defer_holder` now uses this companion for both the
initial probe and the W1 re-check; the predicate arms cannot drift
from the bulk scan. A holder with a live Task (no JobItem) OR with
a non-terminal child is now refused with
`terminated=False, probe_busy=True`.

### Changed

#### Preflight copy truth (Round-2 ITEM 3 / T-H1)

The cleanup preflight docstring replaces "Live missions will remain"
with the canonical split sentence (FE const + docs §8.5 use the
same — cap-exception micro-round ITEM 2, 2026-09-06 broadens to
"settled mirrors, or running Tasks without JobItems" so the
sentence matches the truth-survivor filter's TRUE semantics):

> Every ACTIVE job is cancelled, together with its whole subtree.
> Only missions holding settled mirrors, or running Tasks without
> JobItems — are kept.

Term single-owner: "stalled mission" (operator-facing). The
`zombie_instance_count` wire field NAME stays technical (wire
stability).

#### Operator vocabulary (Round-2 ITEM 8)

The cleanup endpoint's router docstring replaces "nuclear press"
with "System Cleanup" (the operator-facing button label). The
holder-action term is "stalled mission". The technical wire fields
(`zombie_instance_count`, `live_instance_count`, etc.) STAY
unchanged.

### Added

#### Public `defer_pending_count` surface (Round-2 ITEM 7 → unblock-round ITEM 4)

Originally a public free function
`daemon.services.defer_block_resolver.defer_pending_count(engine)`;
replaced in the unblock round by the public instance method
`daemon.services.defer_block_resolver.DeferBlockResolver.defer_pending_count()`.
The preflight endpoint (`GET /api/jobs/cleanup/preflight`) calls
the instance method via the resolver singleton — NO direct
`_job_repo.engine` reach-through from the router anywhere.
Schema or shape changes to the defer-pending-count SELECT have ONE
place to update (the SQL constant `_DEFER_PENDING_COUNT_SQL`,
which stays module-private — underscore-prefixed, not re-exported).

#### Pattern-(g) defer-job watchdog + `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED`

`Pattern-(g)` is the JOB-SIDE watchdog complement to the
task-side `Pattern-(a)` recovery
(`daemon/services/job_recovery_service.py`). Pattern-(g) covers
stuck JobItems (stuck-active jobs with dead instances,
stuck-queued jobs behind dead instances) — the job-queue
lifecycle. Pattern-(a) covers stuck Tasks — the task lifecycle.
The two are complementary: a stuck JobItem with a healthy Task is
a Pattern-(g) job; a stuck Task with a settled JobItem is a
Pattern-(a) task. Pattern-(g) does NOT inspect Task state;
Pattern-(a) does NOT inspect JobItem state.

The `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` env var (default OFF,
env-only, restart-read) gates Pattern-(g)'s auto-promotion path.
Default OFF is conservative — operators flip ON after a ≤2-week
soak or on incident; OFF = instant revert path.

---

## [Phase 8] — Cleanup old architecture (FINAL)

The final cleanup phase. The `use_dependency_bus` feature flag and the `ENSEMBLE_JOB_SYSTEM_USE_DEPENDENCY_BUS` env var have been removed; the DependencyBus is now the SOLE completion authority with no flag, no kill-switch, and no fallback path.

### Removed

- **`USE_DEPENDENCY_BUS` flag** — `use_dependency_bus` field removed from `JobSystemConfig` (`daemon/config.py`). The `ENSEMBLE_JOB_SYSTEM_USE_DEPENDENCY_BUS` env var is no longer read. The bus path is now unconditional; all `if use_dep_bus:` / `_is_dependency_bus_enabled()` conditionals have been removed. The DependencyBus is the only completion authority for parent-waits-for-children; no rollback path is supported.

---

## [Unreleased] — 2026-06-21

### Phase D: Dependency Bus & Cleanup (M7 + M8)

The final phase of the decouple architecture migration. The system now has a single dispatcher, a single scheduling layer, and a single DB-backed completion authority. The CorrelationManager is retained as a rollback path; the `waiting_for` / `children` / `instance_hierarchy` artifacts are dead-but-present pending a manual migration.

### Changed

- **Source adapter default agent**: `default_agent` for Slack and Telegram source adapters changed from `"leader"` to `"ari"`. The `ari` agent is the designated chat-source front door (has `job` tool + `job-orchestration` skill). Existing deployments relying on the implicit `leader` default will now route chat messages to `ari` instead. Operators who need `leader` can set `default_agent: "leader"` explicitly in the source config.
- **Reasoning echo flipped from allowlist to denylist**: `reasoning_content` is now echoed back in multi-turn assistant messages for every model by default (previously a `deepseek`-only allowlist). Operators on endpoints that reject the extra `reasoning_content` field (e.g. the raw OpenAI API returning 400 on unknown fields) should set `OPENAI_REASONING_ECHO_DISABLED_MODELS=gpt-4o,claude` (example values) to opt those models out. The old `OPENAI_REASONING_ECHO_MODELS` env var is no longer read; leaving it set logs a deprecation warning at startup pointing to the new key.
- **`OPENAI_ALLOWED_MODELS` renamed to `OPENAI_SELECTABLE_MODELS`** (soft deprecation): the env var governing the spawn-time model allowlist has been renamed. The new primary name is `OPENAI_SELECTABLE_MODELS`; the legacy `OPENAI_ALLOWED_MODELS` is still honored when the new name is unset, but a one-shot deprecation warning is logged at startup. The internal config field (`config.llm.allowed_models`) and the governor `<allowed_models>` prompt block name are unchanged — only the env-var-level aliasing changed. **Operator action**: rename the env var in your `.env` / launcher exports from `OPENAI_ALLOWED_MODELS` to `OPENAI_SELECTABLE_MODELS` to silence the warning. The allowlist is consulted only by the four spawn-time selection flows (spawn `model=` override, weighted `llm_models` pool filter, `spawn_councilor` validation, session-restore re-validation); purpose-bound models (`model_title`, `model_keywords` when set to a fixed value, `model_vision`, compaction, skill evolution) are unaffected and continue to use their own env vars / YAML keys.
- **Empty / whitespace-only env values behave as unset** for both `OPENAI_SELECTABLE_MODELS` and `OPENAI_ALLOWED_MODELS` (legacy shell-style `:-` semantics preserved). A bare `KEY=` line in `.env` — which `launcher.sh` `load_env_file` exports verbatim — produces the documented default (`["agentic", "coding"]`) rather than an empty/unrestricted allowlist, and never fires a spurious deprecation warning. **There is no env-var path to unrestricted mode**; operators who want to lift restrictions entirely must hardcode `allowed_models: []` in `config.yaml`.

### Fixed

- **TOCTOU race in `job_create` watch registration**: When `watch=True`, the watcher is now registered BEFORE the job is enqueued, closing a race window where fast jobs could complete before the watcher was registered (causing missed `[JOB_EVENT]` notifications).
- **`job_continue` crash with `USE_WORKER_POOL=false`**: Direct `manager._task_repo` attribute access replaced with defensive `getattr` pattern.
- **`watch_job`/`watch_jobs` missing error/result context**: Terminal job notifications now pass `result_summary` explicitly so the downstream resolver can fill gaps.

### Added

- **DependencyBus service** (`daemon/services/dependency_bus.py`) — new authoritative parent-waits-for-children mechanism. DB-backed via the `dependency_watchers` table; watcher state survives restart by construction (no `rebuild_from_db` hack). Public API: `watch(source_task_id, FollowUp)`, `emit_terminal(task_id, Outcome)`, `cancel_for_target(target_instance_id)`. The `start()` method warms the in-memory cache from the DB and recovers FIRED-but-unsent rows for crash safety.
- **`dependency_watchers` table** (`daemon/repositories/dependency_bus/`) — durable storage for in-flight parent→child correlation. Keyed by `source_task_id` for O(1) terminal-emit lookup. Columns: `watch_id`, `source_task_id`, `target_instance_id`, `follow_up_payload` (JSON), `metadata` (JSON), `created_at`, `fired_at` (nullable), `state` (PENDING / FIRED / CANCELLED). Uses the `WriteGuardSession` pattern.
- **`use_dependency_bus` feature flag** (default `True`) — toggles between the DependencyBus (authoritative) and the CorrelationManager (rollback path). `use_dependency_bus=False` reverts to the proven in-memory CM path with no code change.
- **`completion_delivery_path=cm|bus` structured log metric** — every terminal emit writes this key, letting operators verify which authority is in effect per request.
- **30-test Dependency Bus test pack** (`tests/test_dependency_bus.py`, 25 SQLite + 5 PostgreSQL in `tests/postgres/test_dependency_bus_pg.py`) — proves the bus eliminates the double-decrement bug, survives restart, enforces backpressure, and that `cancel_for_target` prevents orphan FollowUps.
- **Pause pre-check in `JobProcessor.start_job`** — instance pause is now a pre-check before admitting a job (replaces the historical `MessageJobHandler.handle()` pause-vs-terminate discrimination).

### Changed

- **`use_dependency_bus` default flipped to `True`** — the Dependency Bus is the source of truth for parent-child correlation. The CorrelationManager is no longer on the hot path.
- **Single execution path** — WorkerPool is the sole execution layer for all work (messages, tasks, completion reports, error reports). The JobQueue is now scheduling vocabulary only (priority, queue management, project scoping for `Task` rows).
- **Pause semantics** — `pause_instance_cascade()` now calls `dependency_bus.cancel_for_target()` on the paused root, cancelling any in-flight FollowUps that would otherwise land on a paused parent. `terminate_instance()` calls the same on the terminated node (after the DB cascade + lifecycle event publish).
- **Completion delivery via DependencyBus** — child completions and error reports now flow through `dependency_bus.emit_terminal()` (gated behind `use_dependency_bus`, called from `child_reports.py` / `error_reporting.py`) instead of `CM.resolve_response()`. The `MessageProcessingPipeline` still owns the shared six-stage flow but delegates the terminal emit to those services — no separate stage 5 hook.
- **Documentation** — `docs/architecture/message-processing-and-correlation.md`, `docs/architecture/job-task-pause-resume.md`, and `docs/architecture.md` updated to reflect the new architecture (single dispatcher, DependencyBus, removed dual-path, dead-but-present columns).

### Removed

- **`MessageJobHandler` (770 lines, `daemon/services/message_job_handler.py`)** — deleted in D12. The MESSAGE-dispatch branch in `JobProcessor` is also gone (D11). Pause-vs-terminate discrimination has moved to a pre-check in `JobProcessor.start_job`.
- **MESSAGE-specific helpers in `JobQueueService`** (`daemon/services/job_queue_service.py`) — removed in D13. The JobQueue no longer owns a `JobItem` lifecycle for messages; only `Task` rows are written.
- **`job_type='message'` JobItem rows** — no longer written. `JobItem` rows now exist only for non-message work (scheduler, webhook, project-rooted tasks).

### Deprecated

- **`Instance.waiting_for`** — dead-but-present column. Was the legacy control-flow counter (ADR-011), then became a rebuild-only cache for the CM. Post-Phase-D, completion flows through the DependencyBus. Pending drop via `20260621_000002_drop_legacy_completion_columns.sql`.
- **`Instance.children`** (denormalized JSON array) — dead-but-present cache. Pending drop via the same migration.
- **`instance_hierarchy` table** — dead-but-present. The hierarchy is encoded in `Instance.parent_id` and the live `dependency_watchers` rows. Pending drop via the same migration.

### Migration

- **`20260621_000002_drop_legacy_completion_columns.sql`** (new, IRREVERSIBLE, **not auto-applied**) — drops `Instance.waiting_for`, `Instance.children`, and the `instance_hierarchy` table. Manual application required after **2+ weeks of clean bus operation** in production. Operators should drain in-flight jobs before applying.

### Rollback

- **`use_dependency_bus=False`** — reverts to the CorrelationManager (in-memory `_pending` + per-parent `asyncio.Lock`) as the completion authority. No code change required; the flag is a kill switch. The CM was the proven completion mechanism for the previous release.
- **`use_legacy_waiting_for_cascade=True`** — re-enables the legacy `waiting_for` SQL cascade and the `SELECT COUNT(*)` fallback (defensive last-resort). Useful only if the bus AND the CM both fail in production.
- **`debug_completion_invariant=True`** — keeps the CM tracking in parallel with the bus and logs divergence between CM pending counts and `dependency_watchers` rows. Observability safety net for one more release.

### Migration map: which call sites changed

| Site | Before (Phase C) | After (Phase D) |
|------|------------------|-----------------|
| `daemon/services/message_processing_pipeline.py` | `CM.resolve_response()` not present (CM hook fired by `child_reports`) | unchanged — pipeline delegates to `child_reports` / `error_reporting` |
| `daemon/tools/instance.py` `send_message` | `notify_corr_register` (CM hook) | `dependency_bus.watch(FollowUp)` (under flag) |
| `daemon/services/child_reports.py` | `notify_corr_resolve` (CM hook) | `dependency_bus.emit_terminal()` (under flag) |
| `daemon/services/error_reporting.py` | `notify_corr_resolve` (CM hook) | `dependency_bus.emit_terminal()` (under flag) |
| `daemon/services/instance_lifecycle.py` `pause_instance_cascade` | (no bus call) | `dependency_bus.cancel_for_target(root_id)` (under flag) |
| `daemon/services/instance_lifecycle.py` `terminate_instance` | (no bus call) | `dependency_bus.cancel_for_target(instance_id)` (under flag) |
| `daemon/services/job_processor.py` `_process_next_job` | `job_type='message'` branch | branch removed; pause pre-check on `start_job` |

---

## Earlier (2026-06-20) — Phase A + B + C

- **Phase A** (premature-completion bug class): `USE_LEGACY_WAITING_FOR_CASCADE` and `DEBUG_COMPLETION_INVARIANT` flags; `CorrelationManager` is the authoritative completion mechanism; `waiting_for` is a rebuild-only cache per ADR-011. Fixed Race #1, Race #3, Race #5, the cross-dispatcher checkpoint corruption, and the sync/async deadlock.
- **Phase B** (`watch_job` Variant B): `watch_job` now routes through the CorrelationManager via `pending_jobs`; eliminates the `watch_job` fire-and-forget premature-completion bug class.
- **Phase C** (single dispatcher): `MessageJobHandler` demoted to cross-instance handoff only (C-M5); ExecutionGate collapsed from DB-backed lease to per-instance `asyncio.Lock` (C-M6, ~700 lines → ~40); all WorkerPool + JobQueue dispatch paths share a unified `MessageProcessingPipeline`; pause/terminate matrix regression-tested.
