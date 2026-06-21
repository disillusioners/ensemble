# Changelog

All notable changes to the agents-ensemble project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — 2026-06-21

### Phase D: Dependency Bus & Cleanup (M7 + M8)

The final phase of the decouple architecture migration. The system now has a single dispatcher, a single scheduling layer, and a single DB-backed completion authority. The CorrelationManager is retained as a rollback path; the `waiting_for` / `children` / `instance_hierarchy` artifacts are dead-but-present pending a manual migration.

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
