# Architecture Decisions — Scheduled Tasks

> Decision record for the **Scheduled Tasks** feature (`feature/scheduled-tasks` branch). Synthesizes the leader decisions **D1-D10** and the design questions raised in `phase1-plan.md` / `phase3-plan.md` / `phase5-plan.md` into an ADR register. Each ADR is the canonical home for its decision; phase files reference back here.
>
> House style: `.agents/shared/planning/job-system-improvements/decisions.md`.

---

## ADR-001: Reuse the Existing Scheduler Substrate — No New Table, No New Dispatch Loop (D1)

**Decision:** Land the scheduled-tasks feature by extending the existing scheduler adapter (`daemon/sources/adapters/scheduler.py` 907L), `source_configs` rows, `schedule_executions` history, and the six existing `/api/schedules` routes. Do NOT introduce a new `scheduled_tasks` table, a new dispatcher loop, or a parallel dispatch path.

**Rationale:**
- The existing adapter already implements cron + one-shot firing, persistence in `source_configs` (indexed `name` column at `models.py:52`), and history in `schedule_executions` (`models.py:143-169`). All required primitives exist.
- A new table would orphan schedule data from the `source_configs` universe (existing migration story, existing repository, existing audit trail).
- A new dispatch loop would duplicate boot-time adapter enumeration, lifecycle hooks (`start_adapter`/`stop_adapter` at `registry.py:574-603/:661-664`), and the `RESERVED_SOURCE_PREFIXES` chat-source gate (`constants.py:783` + `jobs_crud.py:526-543`).
- Per-schedule identity is already provided by `source_configs.name` (the schedule label, indexed, `get_source_config_by_name` at `repository.py:212-216`).
- Per-schedule `project_id` is already a config-JSON key (adapter reads it at `scheduler.py:134`).

**Consequences:**
- (+) Zero schema migration risk on the critical path (no migrations; OD-1's optional `schedule_executions.planned_run_at` column was DROPPED permanently per architecture §2 OD-1).
- (+) Inherits existing operational posture: `is_write_paused` 503 gate (`schedules.py:87-88, :185-186, :255-256, :347-348`), reserved-prefix chat-source gate, `RESERVED_SOURCE_PREFIXES`.
- (+) Cancel/pause/resume use the **net-new** `update_schedule` rebuild sequence under a per-source `asyncio.Lock` (phase-2 §Task 1.7-1.8, architecture §5.1): evict registry entry → `stop_adapter` → `update_source_config` → `start_adapter`. The previously-cited "proven seam" at `schedules.py:251-339` is actually `POST /{id}/start`; the real PUT at `schedules.py:83-172` is DB-only and leaves the live adapter stale (pre-existing latent defect). Phase-2 implements the rebuild as new logic.
- (-) We inherit the existing 5-second retry loop (`scheduler.py:447-450/:697-709`) and the in-memory `_is_one_time_executed` flag (`scheduler.py:150`) — both mitigated by ADR-004 (`idempotency_key`).

---

## ADR-002: TZ Resolution Chain — Explicit → Config Default → Host-Local → UTC+Loud Warning (D2)

**Decision:** A user-stated time `when` is **always** interpreted in the caller's local timezone `tz`. The resolution chain, applied at every NEW surface (tools, REST, service), is:

```
1. caller-supplied `tz` if non-None   → ZoneInfo (KeyError → UTC + warning)
2. SchedulingConfig.default_timezone  → ZoneInfo (KeyError → UTC + warning)
   (env: ENSEMBLE_SCHEDULING_DEFAULT_TZ)
3. detect_host_local_timezone()       → /etc/localtime symlink + TZ env fallback
   (cached; honor host_local_tz_cache_seconds)
4. UTC + warning_message              → "No host tz detected, fell back to UTC"
```

When the chain falls through to UTC, the warning is **echoed into the tool / REST response** (controlled by `SchedulingConfig.tz_warning_echo_to_tool_output=True`, default ON). The warning is informational — the user sees the chain resolved and can re-issue with explicit `tz`.

**Rationale:**
- The user-stated-local invariant is the **CRITICAL TZ RULE** (D2, U4) — the daemon's UTC clock must not silently bind to local time.
- A single, well-known resolution chain keeps `daemon/sources/adapters/scheduler.py:121-126` (cron-path), `daemon/tools/time.py:26-28` (time echo), and the new service + tools + REST aligned.
- Loud-warning-echo-at-the-tool-boundary follows the house pattern from `daemon/tools/time.py:31,61,65-66` (returns `"ERROR: {msg}"` strings, never silent).
- Host-local auto-detect via `/etc/localtime` symlink walk + `TZ` env fallback is robust stdlib — **no `tzdata` package dependency** (project does not declare one; `pyproject.toml` zero hits).
- The four-step chain matches the explicit-precedence convention of the existing scheduler adapter (cron-path already prefers `config.timezone` over adapter-level default at `:121-126`).

**Consequences:**
- (+) Single resolution chain; one place to audit (`daemon/utils/tz.py::resolve_timezone`).
- (+) User is informed when their local tz wasn't detected (loud warning) and can correct.
- (+) Minimal containers that strip `/etc/localtime` and unset `TZ` get UTC + warning — documented behavior, not a bug.
- (-) Operators must set `ENSEMBLE_SCHEDULING_DEFAULT_TZ` explicitly to suppress the warning in a homogeneous fleet.

---

## ADR-003: D3 Catch-Up Window — Cap with SKIPPED Row (D3)

**Decision:** When a one-shot schedule's `run_at` is in the past at fire time, the scheduler computes `lateness = (now - run_at).total_seconds()` and compares against `SchedulingConfig.one_shot_max_lateness_seconds`. If `lateness > cap`, the scheduler writes a `schedule_executions` row with `status="skipped"` + `error_message="past_lateness_cap: lateness={X}s, cap={Y}s"` and does NOT dispatch a `JobItem`. The schedule remains armed (NOT disabled) — the user can update `run_at` to a future time and re-fire.

When `one_shot_max_lateness_seconds` is `None` (default), the cap is opt-out and the existing always-fire behavior is preserved exactly.

**Rationale:**
- The current behavior at `scheduler.py:486-487` (always fires past-due one-shots) is a silent-bug class: a 2-hour-stale one-shot fires and a user wonders why their "6 AM" ran at "8 AM".
- A `SKIPPED` row is the audit trail: `task_schedule_list` and `GET /schedules/{id}/executions` can surface the skip reason.
- Disabling the schedule on skip would be more aggressive than the user expects ("I missed my window by 1 hour and now my whole schedule is dead"). Keeping it armed respects the operator's intent.
- The `schedule_executions.status` enum already includes `SKIPPED` (`models.py:38`) — no schema change.
- Default `None` preserves backward compatibility; opt-in via env.

**Consequences:**
- (+) Operators get a configurable cap without breaking legacy schedules.
- (+) Skip reason visible in execution history.
- (+) Schedule remains armed — user can re-schedule without recreating.
- (-) Skipped rows accumulate in history if the cap is hit repeatedly — operators must monitor `schedule_executions` or implement a retention policy (out of scope).

---

## ADR-004: D4 Idempotency via `JobItem.idempotency_key` (D4)

**Decision:** Close the double-dispatch window for one-shot schedules by threading a deterministic `idempotency_key` through the existing dispatch chain. The key is composed in `SchedulerAdapter._route_via_job_queue` as:

```python
idempotency_key = f"scheduler:{self.source_id}:{self._run_at.isoformat()}"
```

and passed to `manager.enqueue_message_job(..., idempotency_key=...)`. `enqueue_message_job` (at `daemon/manager.py:7854-7887`) forwards the kwarg to `daemon/services/instance_messaging.py:2284-2297`, which forwards to `daemon/services/job_queue_service.enqueue(..., idempotency_key=...)`. The existing `create_or_get_by_idempotency_key` (`repository.py:364-540`) returns the existing `JobItem` without re-firing `dispatch_bus.notify_new_job` — **gated on `created=True`** at `job_queue_service.py:1234-1236`.

This gives **at-most-once dispatch**, not just at-most-once-row.

**Rationale:**
- The window is **verified**: `_is_one_time_executed` is an in-memory bool (`scheduler.py:150`, reset on every boot); `schedule_executions` has NO unique constraint (`models.py:143-169`); the post-success disable-write at `registry.py:506-507` is fire-and-forget via `run_in_executor` (`registry.py:490-496`) and races against the enqueue.
- Reusing `JobItem.idempotency_key` is **zero schema migration risk** (PG-only DDL is a known hazard per project history; SQLite-compat required).
- The dispatch side is **already at-most-once** — `dispatch_bus.notify_new_job` is gated on `created=True` (spot-verified at `job_queue_service.py:1234-1236`). We only need to supply the key.
- The same fix closes the parallel intra-lifetime hazard: the 5-second retry loop (`scheduler.py:447-450/:697-709`) returns the existing `JobItem` on retry attempts (each attempt without a fresh `JobItem`).
- A defense-in-depth migration (unique partial index on `schedule_executions(schedule_id, planned_run_at)`) is **DROPPED permanently** (architecture §2 OD-1 ratified; phase-1 §Task 6 deleted) — see the Open Decisions Register, OD-1.

**Consequences:**
- (+) Closes both the cross-restart window AND the intra-lifetime retry-loop window with one mechanism.
- (+) Zero new table; zero new code path; additive kwarg only.
- (+) Operators can re-fire one-shots with the same `run_at` without surprise duplicates.
- (-) Existing callers of `enqueue_message_job` must continue to pass everything positionally before `metadata`; the new `idempotency_key` kwarg is added AFTER `metadata` (audit at phase-1 §Task 3.3 + §Risk 1).
- (-) The 5-second retry loop still fires repeatedly for a failing one-shot — the consequence is log-spam, not duplicate work. Loop removal is a separate decision (house-punted; not in scope).

---

## ADR-005: Tool Surface — `scheduling` Category with 4 Tools; Cancel Terminal + Hidden; Pause Resumable (D5)

**Decision:** Land four agent-facing tools under a new non-privileged tool category `"scheduling"` (registered in `CATEGORY_MODULES` at `daemon/tools/_tool_registry.py:522-596`; `KNOWN_TOOL_NAMES` regenerated):

| Tool | Purpose | Terminal? | Hidden-by-default? |
|------|---------|-----------|---------------------|
| `task_schedule` | Create a schedule | — | — |
| `task_schedule_list` | List schedules (filterable) | — | cancelled is hidden |
| `task_schedule_cancel` | Cancel by `id` OR `label` | YES | YES |
| `task_schedule_update` | Reschedule / message / pause / resume | — | — |

`task_schedule_cancel` is **terminal** — it transitions `status → "cancelled"` (a NEW enum value, see ADR-007), evicts the adapter from the live registry, and **preserves** `schedule_executions` history (NEVER calls `delete_source_config`). Cancelled rows are hidden from default `task_schedule_list` output (filterable via `include_cancelled=True`).

`task_schedule_update(paused=True)` is **resumable** — it flips `enabled=False, status=SourceStatus.STOPPED.value`; `paused=False` flips `enabled=True, status=SourceStatus.STOPPED.value` and the registry round-trip rebuilds the adapter. Pause ≠ cancel; the two are explicitly different tools (cross-link in `tools_note.md`).

**Rationale:**
- The four-tool shape mirrors the four fundamental actions the user requested: create, list, cancel, update.
- Cancelled rows are HIDDEN from default list (matches user mental model: "I cancelled it, I don't want to see it anymore") but RETRIEVABLE on demand (for "did I actually cancel X?" queries).
- `task_schedule_update` is the reschedule/pause/resume path — the natural complement to `task_schedule_cancel`.
- Reusing `start_adapter`/`stop_adapter` for pause/resume keeps the operational vocabulary aligned with the existing HTTP routes (`POST /schedules/{id}/stop/start`).
- `task_schedule` allows `agent_id=None` to default to the calling agent (closure-captured by the factory, `caller_agent_id = agent_id`) — matches the precedent at `daemon/tools/job_queue.py:1142-1151` (`create_job_tools(... current_instance_id, agent_id, ...)`).
- The factory returns `"ERROR: {msg}"` strings on error (house pattern from `daemon/tools/time.py:31,61,65-66`); never raises.

**Consequences:**
- (+) Tools never raise; agent sees a clean error string.
- (+) Cancel/pause/update semantics are explicit and distinct.
- (+) Caller identity is auto-injected — `task_schedule(agent_id=None)` is "the calling agent".
- (-) Operators must learn two tools (`task_schedule_cancel` vs `task_schedule_update(paused=True)`) for two related but distinct actions; documented in `docs/scheduling.md`.

---

## ADR-006: REST Mirrors Tools — 3 Routes, 503 Gates, No Auth (D6)

**Decision:** Add exactly three HTTP endpoints to `/api/schedules`:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/schedules` | Create (mirrors `task_schedule`) |
| GET | `/schedules/{id}` | Single fetch (mirrors `task_schedule_list` filtered) |
| DELETE | `/schedules/{id}` | **Cancel** — terminal, history preserved (mirrors `task_schedule_cancel`) |

All three are thin wrappers over the shared `daemon.services.scheduling_service` (phase-2 seam). POST and DELETE gate on `manager.is_write_paused` and return `503` with the literal `"Writes are paused for database migration"` text. GET is ungated (house posture). Pause/resume reuse existing `POST /schedules/{id}/stop` + `POST /schedules/{id}/start` (no new routes). List uses existing `GET /schedules`. No auth dependencies added — house posture preserved.

**Rationale:**
- The existing router `daemon/routers/schedules.py:27` already mounts at `/schedules`; new routes inherit `tags=["schedules"]` automatically.
- The handler pattern is uniform: `_get_manager(request)` → 503 gate (POST/DELETE only) → `asyncio.to_thread(scheduling_service.X, ...)` → response model. Matches existing routes at `:47/:91/:135/:189/:259/:432`.
- Reusing the service seam means the same `cancel_schedule` logic backs both the tool and the REST route — no drift.
- **Critical**: `DELETE /api/schedules/{id}` MUST NOT call `manager._source_repository.delete_source_config` (which purges `schedule_executions` history at `repository.py:263-295/:281-284`). The router delegates to the service's `cancel_schedule`, which uses the `SourceStatus.CANCELLED` enum transition (ADR-007) and adapter eviction.

**Consequences:**
- (+) Existing `/api/schedules` routes unchanged; pause/resume reuse the proven stop/start routes.
- (+) Service seam is the single source of truth for create/cancel/get semantics; tools and REST cannot drift.
- (+) 503 gate text is literal-identical to existing sites (audit at phase-3 §Task 2 + §Risk 7).
- (-) Operators must understand the distinction between `DELETE /api/schedules/{id}` (cancel, history preserved) and `DELETE /api/sources/{id}` (purge, history destroyed). Phase-3 §Task 4.2 closes this hazard at the HTTP boundary (ADOPTED per architecture §2 OD-5; placement corrected to AFTER the get-or-404 at `sources.py:382`/BEFORE `:394`).

---

## ADR-007: Cancel-State as `SourceStatus.CANCELLED` Enum Extension — Atomic Repository Method, Boot-Filter, Clobber Guard

**Decision:** Add `SourceStatus.CANCELLED = "cancelled"` as a new value to **both** `SourceStatus` enum sites (`daemon/models/source.py:8-14` — lowercase str-Enum used by Pydantic/REST; `daemon/repositories/source/models.py:20-25` — uppercase str-Enum used by SQLModel). The boot-filter at `daemon/sources/registry.py:292` is rewritten to `status in {SourceStatus.STOPPED.value, SourceStatus.CANCELLED.value}` (the single equality at `:292` is the only extensible site; three sequential `continue`s above it stay). The cancel operation is implemented as a **NEW atomic repository method** `cancel_source_config(source_id)` that writes both `enabled=False` AND `status='cancelled'` in **ONE session/commit** — replacing the previous plan's two-call sequence (`update_source_config` + `update_source_status` were independent sessions, see architecture §3.2).

A **clobber guard** is added to `stop_adapter` (`daemon/sources/registry.py:652-664`): when `config.status == 'cancelled'`, skip the unconditional `update_source_status(STOPPED)` write at `:662` and return success — the row is already terminal. Without this, a later stop touching a cancelled row resurrects it to `stopped` (resumable via `/start`) — exactly the bug class §3.3 closes.

Cancel NEVER calls `delete_source_config`. Cancel NEVER routes through `DELETE /api/sources/{id}` (which purges `schedule_executions` history at `repository.py:263-295/:281-284`). The shared service's `cancel_schedule` is the **sole writer** of `'cancelled'` (architecture §3.2).

**Rationale:**
- Existing `SourceStatus` has 4 values (`STOPPED, STARTING, RUNNING, ERROR`); pause/resume uses `stopped ↔ running`. There is no terminal state — only "stopped" which is resumable.
- The boot filter at `registry.py:281-294` is the only gate that prevents a row from auto-starting on boot. Extending it for `CANCELLED` is the natural place.
- An enum extension is **Python-only** (no migration) and is **additive** — existing set-membership checks (`status in {STOPPED, STARTING, RUNNING, ERROR}`) silently miss CANCELLED, but the boot filter is the most critical site and is explicitly extended.
- The atomic method honors the plan's own "same transaction" contract (which the previous plan's two-call sequence did not actually implement). ~10 lines; one commit; one session.
- The clobber guard closes a subtle resurrection bug: a stop-all sweep or a cancel-flow double-stop would otherwise make a cancelled row resumable. Architecture §3.3.

**Consequences:**
- (+) Terminal cancel distinct from resumable pause — two concepts previously conflated.
- (+) History preserved (`schedule_executions` rows queryable post-cancel).
- (+) Boot filter never re-fires a cancelled row.
- (+) Clobber guard prevents accidental resurrection.
- (-) Any set-literal membership check (e.g., `if status in {STOPPED, STARTING, RUNNING, ERROR}`) silently misses CANCELLED — audited at phase-1 §Risk 5; can be caught at code review.

---

## ADR-008: DST Semantics — Delegate to croniter Default (D8)

**Decision:** The scheduler delegates DST handling to `croniter>=3.0.0` (pinned in `pyproject.toml:25`) for the **cron path**. For the **one-shot path**, naive local times use the NEW `daemon.utils.tz.anchor_local_to_utc` helper (architecture §4.2; phase-1 §Task 9) — aware→trust, fold→0 (first occurrence), gap→shift-forward+warning. One DST rule for the whole feature: croniter's documented default (skip-the-gap on spring-forward, first-occurrence on fall-back ambiguity).

For daily/weekly cron schedules, the schedule key is interpreted in the schedule's `timezone` (existing config key, read at `scheduler.py:121-126`); croniter returns the next fire as an aware datetime. These are documented in `daemon/sources/adapters/scheduler.py` module docstring (phase-1 §Task 8.1) and `daemon/utils/tz.py` module docstring.

Phase-5 tests `TestDstSemantics` (phase-5 §Task 1.2) **parameterize over BOTH paths** — 4 named test cases (`test_dst_spring_forward_gap_cron`, `test_dst_fall_back_ambiguity_cron`, `test_dst_spring_forward_gap_one_shot_anchor`, `test_dst_fall_back_ambiguity_one_shot_anchor`) — assert the documented behavior. No `pytest.skip`.

**Rationale:**
- The adapter does no manual DST arithmetic (verified: `scheduler.py:10/:123/:126` only call `ZoneInfo(...)`; cron computation passes aware datetimes at `:210-211/:466-467`).
- `tzdata` is NOT a dependency (`pyproject.toml` zero hits); stdlib `zoneinfo` is the only source.
- Croniter is the project's existing DST-aware component for cron paths. The one-shot anchor helper mirrors croniter's default (fold=0, gap-shift) — one DST rule, two code paths (architecture §4.2).
- The pinned semantic (skip-the-gap, first-occurrence-on-fall-back, fold=0, gap-shift+warning) is the canonical rule. Phase-5's tests assert both paths; deviation requires an ADR amendment.
- Terminal tz fallback is `datetime.timezone.utc` (NOT `ZoneInfo('UTC')` — architecture §4.3: stripped containers raise `ZoneInfoNotFoundError`; `datetime.timezone.utc` is C-level and cannot fail).

**Consequences:**
- (+) Single DST semantic across cron + one-shot paths (architecture §4.2).
- (+) No manual arithmetic in the adapter or the one-shot helper.
- (+) Phase-5 tests pin both paths; deviation requires an ADR amendment.
- (+) Terminal fallback uses `datetime.timezone.utc` (cannot fail on stripped containers).
- (-) Operators wanting different semantics (e.g., fire twice on fall-back, or fire at the post-DST 01:30) cannot get them without forking croniter or adding a wrapper. Documented as a non-goal; one-shot anchor helper is the explicit extension point.

---

## ADR-009: Registration Correctness Proven by Tests; No Daemon Restart (D7, D9)

**Decision:** Tool registration correctness is proven by **static tests** (no daemon restart in this task):

- `tests/unit/test_scheduling_registration.py` — 5 parametrized test functions over `["ari", "leader", "jober"]`:
  1. `test_scheduling_in_meta_json_allowlist` — `"scheduling" ∈ meta["tools"]["allow"]`
  2. `test_meta_json_allowlist_subset_of_registry` — every allow-list entry is in `KNOWN_TOOL_NAMES ∪ CATEGORY_MODULES.keys()`
  3. `test_scheduling_section_no_system_internals` — closure grep per `docs/agent-prompt-writing-guide.md` §1 returns ZERO hits on forbidden tokens
  4. `test_scheduling_section_cross_refs_resolve` — every `See <X>` resolves to a real heading
  5. (×3 each via parametrize)
- `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` — the existing drift test catches any `KNOWN_TOOL_NAMES` ↔ source mismatch after phase-2's regen.

Runtime exposure (agents actually being able to CALL the new tools) is gated on the next daemon restart after phase-2 lands. Until then, the registry enumeration is statically known to be correct but the tools are inert.

`PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:167-171`) is **untouched** — `scheduling` is non-privileged by design (D7 grants per-agent via `meta.json`). The triple-pin (D18/A14) prevents the new category from silently gaining privileged behavior.

**Rationale:**
- The boot validation at `daemon/registry.py:1096-1118` produces only NON-FATAL WARNING for unknown allow-list entries — an unknown `"scheduling"` before phase-2 lands logs a warning but does not crash. Static tests make the rollout explicit.
- `meta.json` allow-list + `_full_doc` agent-prompt edits are INERT until the tool factory's `create_scheduling_tools` is invoked — which only happens after phase-2's category registration.
- Per `docs/agent-prompt-writing-guide.md` §10 pre-commit checklist, `tools_note.md` edits must pass the forbidden-token closure grep before merge.

**Consequences:**
- (+) Registration correctness is verifiable in CI without a daemon restart.
- (+) Phases 2 and 4 are merge-coupled — architecture §8 item 16 ratified: **ONE PR**, non-negotiable. Drift test is bidirectional; a red CI merge is not mergeable. Parameterize registration tests over only the agents actually registered; do NOT xfail.
- (+) `PRIVILEGED_TOOL_CATEGORIES` triple-pin preserved (D18/A14).
- (-) Operators cannot invoke `task_schedule` until the daemon restarts after the merged PR lands — explicit in the deployment plan.

---

## ADR-010: No Job-Semantics, Queue, or `POST /api/jobs` Changes (D10)

**Decision:** The scheduled-tasks feature does NOT modify job semantics, the job queue, or the `POST /api/jobs` endpoint. All dispatch continues to flow through the existing `SchedulerAdapter` → `manager.enqueue_message_job(source="scheduler", ...)` → `job_queue_service.enqueue(...)` path.

Concretely:
- **No call to `POST /api/jobs`** from `daemon/tools/scheduling.py` or `daemon/services/scheduling_service.py` (grep-verifiable).
- **No call to `delete_source_config`** from cancel paths (grep-verifiable; `delete_source_config.assert_not_called()` in phase-5 §Task 2.3 case (f)).
- **No new state on `JobItem`**. The `idempotency_key` kwarg is added AFTER `metadata` on `enqueue_message_job`; existing callers that pass everything positionally before `metadata` are unaffected (audit at phase-1 §Task 3.3 + §Risk 1).
- **No changes to `daemon/routers/jobs.py`**, `JobQueueService` state machine, or `JobStatus` enum.
- **No changes to `daemon/scheduler.py` constants**. The existing `SCHEDULER_ERROR_RETRY_S=5.0` is preserved.

**Rationale:**
- D1 reuse-not-rebuild — the existing dispatch path is the seam.
- The schedule feature is a TRIGGER mechanism; the job it produces is the same `JobItem` (with `source='scheduler'`) that `POST /api/jobs` produces. Operators inspecting job queues see a homogeneous population.
- Adding fields to `JobItem` or `JobStatus` would force a migration on the critical path; `idempotency_key` is already a column on `JobItem` (zero schema change).

**Consequences:**
- (+) Zero migration risk on the critical path.
- (+) Job queue semantics unchanged — operators' existing dashboards and alerts work.
- (+) Single dispatch path; one seam to audit.
- (-) Operators cannot query "which schedules fired this JobItem?" except by `source='scheduler'` and joining `source_configs` on the schedule label. Acceptable — the schedule label is part of the JobItem metadata.

---

## ADR-011: Additive Kwarg on `enqueue_message_job` — Backward Compatibility by Audit

**Decision:** The new `idempotency_key: str | None = None` parameter is added to `enqueue_message_job` (`daemon/manager.py:7854-7887`) and the underlying `daemon/services/instance_messaging.py:2284-2297` as a **keyword-only kwarg placed AFTER `metadata`**. The new kwarg is forwarded through the chain:

```python
# daemon/manager.py
async def enqueue_message_job(self, ..., metadata: dict | None = None, *,
                              idempotency_key: str | None = None) -> ...:
    return await self._messaging_service.enqueue_message_job(
        ..., metadata=metadata, idempotency_key=idempotency_key,
    )

# daemon/services/instance_messaging.py:2612-2629
async def enqueue_message_job(self, ..., metadata: dict | None = None, *,
                              idempotency_key: str | None = None) -> ...:
    return await self._manager._job_queue_service.enqueue(
        ..., idempotency_key=idempotency_key,
    )
```

Existing callers that pass everything positionally BEFORE `metadata` are unaffected. The audit (`grep -rn "enqueue_message_job("`) confirms every existing call site passes everything positionally before `metadata`; phase-2's test sweep re-confirms at merge time.

**Rationale:**
- Python keyword-only kwargs (after `*,`) are the only safe way to add a parameter without breaking positional callers.
- Placement AFTER `metadata` (the last positional parameter) is the canonical "additive extension point".
- The `None` default means existing behavior is preserved when the scheduler path is not used.

**Consequences:**
- (+) Zero risk of breaking existing callers.
- (+) Only the scheduler path passes a real key.
- (-) Future authors must respect the AFTER-`metadata` convention — flagged in the kwarg's docstring.

---

## ADR-012: Cancel-Confusion Guard — Extend `_reject_scheduler_lifecycle` (ADOPTED, architecture §2 OD-5)

**Decision (ADOPTED — ratified):** Extend `_reject_scheduler_lifecycle` at `daemon/routers/sources.py:44-66` to also cover `DELETE /api/sources/{id}` for scheduler source_type. A scheduler-row DELETE through `/api/sources` will be rejected with a 400 (`SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED`) pointing at `DELETE /api/schedules/{id}` (cancel) as the correct route. This is in addition to (not instead of) the `cancel_schedule` semantics — cancel routes through the service. **Placement correction (architecture §2 OD-5):** the guard call is inserted AFTER the get-or-404 at `sources.py:382` and BEFORE `:394`, so unknown ids still return 404 (NOT 400). Phase-3 owns this edit (in scope per architecture §2 OD-5 ADOPT-NOW).

**Rationale:**
- Operators may confuse `DELETE /api/sources/{id}` with `DELETE /api/schedules/{id}`; the former PURGES history (`sources.py:374-405` → `repository.py:263-295/:281-284`), the latter preserves it. This is exactly the bug class D5 was designed to prevent.
- `_reject_scheduler_lifecycle` already exists at `sources.py:44-66` for other lifecycle routes (start/stop); extending it to DELETE is a one-line addition.
- The response carries an explicit pointer to the correct route — operators see a clear next-action.
- Architecture §2 OD-5 ratified this ADOPT-NOW (placement corrected to after get-or-404 to preserve 404 semantics on unknown ids).

**Consequences:**
- (+) Cancel-confusion is impossible at the HTTP boundary.
- (+) History preservation is guaranteed at the route level, not just the service level.
- (+) Phase-3 owns the edit (no punt to phase-1/2); placement correction preserves 404-on-unknown.
- (-) The 400-vs-200-vs-204 surface is one more status code to document.

---

## Open Decisions Register

| # | Decision | Status | Owner | Affected Phase |
|---|----------|--------|-------|----------------|
| OD-1 | **Defense-in-depth migration on `schedule_executions`** (phase-1 §Task 6) — `planned_run_at` column + unique partial index on `(schedule_id, planned_run_at)`. Task 3 alone closes D4; Task 6 is belt-and-suspenders. | **DROPPED — permanently, not a fast-follow** (architecture §2 OD-1 ratified). The only failure mode the migration would close is structurally unreachable (one adapter = one config row = one `run_at`). Phase-1 §Task 6 is marked `[DELETED]` with a historical-traceability note; "OPTIONAL planned_run_at column" clause is scrubbed from plan-overview.md. | Architecture (ratified) | phase-1 |
| OD-2 | **DST semantics** (phase-1 §Task 8.1) — croniter-default (skip-the-gap, first-occurrence-on-fall-back) for cron path; `daemon.utils.tz.anchor_local_to_utc` (fold=0, gap-shift+warning) for one-shot path. Architecture §4.2. | **DEFAULT-TO-PHASE-1-DOC + anchor_local_to_utc helper**. Phase-5 §Task 1.2 parameterizes over BOTH paths; the croniter-default semantic is pinned and tested. | Architecture (ratified) | phase-1, phase-5 |
| OD-3 | **`task_schedule` agent existence check** (ratified by architecture §2 OD-3) — reject at create time if `agent_id` resolves to a non-existent agent? | **FAIL-FAST at create time** (architecture §2 OD-3 ratified). `agent_registry.exists(payload.agent)` in `create_schedule` (phase-2 §Task 6.4). Catches typos before stale `source_configs` row exists. | Architecture (ratified) | phase-2 |
| OD-4 | **Suppress tz-fallback warning for "now-ish" one-shots?** | **KEEP the warning — never suppress** (architecture §2 OD-4 ratified). Relative times anchor to daemon clock; warning is the only signal of mis-detected tz. Fleet operators kill wholesale via `ENSEMBLE_SCHEDULING_DEFAULT_TZ`. | Architecture (ratified) | phase-2 |
| OD-5 | **Cancel-confusion guard** (ADR-012) — extend `_reject_scheduler_lifecycle` to cover DELETE. | **ADOPT NOW in phase-3** (architecture §8 item 15 ratified; §2 OD-5). Placement corrected to AFTER the get-or-404 at `sources.py:382` and BEFORE `:394`, so unknown ids still 404 (NOT 400). Response code `SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED`. ~3 lines + one test. | Architecture (ratified) | phase-3 |
| OD-6 | **Phase 2 + Phase 4 merge gating** — drift test is bidirectional. | **ONE PR** (architecture §8 item 16 ratified). Phase-4 is NOT-MERGEABLE until phase-2 lands. If leader/jober registration is deferred, parameterize registration tests over only the agents actually registered — do NOT xfail. Single green CI gate proves ADR-009's static posture. | Architecture (ratified) | phase-2, phase-4 |
| OD-7 | **Phase-5 xfail/skip parking** — tests that depend on phase-1/2/3 semantics. | **REMOVED — pack is the merge gate** (architecture §8 item 16 ratified). All architecture-pinned semantics (croniter-default, anchor_local_to_utc fold=0/gap-shift, key from `self._run_at`, gate to `SCHEDULE_TYPE_ONE_TIME`) are asserted directly; no `pytest.skip`, no `@pytest.mark.xfail`. The acceptance pack (`tests/packs/scheduled_tasks_acceptance.sh`) is the CI merge gate. | Architecture (ratified) | phase-5 |
| OD-8 | **5-second retry loop removal** (`scheduler.py:447-450/:697-709`) — separate decision; phase-1 §Task 3.3 note documents the loop is preserved. | **DEFERRED**. Loop-removal depends on host tolerance for repeated attempts of a failing one-shot. Idempotency-key closes the duplicate-work consequence; log-spam remains. **NEW (architecture §1.5):** `tests/job_queue/test_idempotent_enqueue_atomic.py:365` already uses `source="scheduler"` — fixture audit needed so pre-set keys don't trip stale assertions. | Future commission | post-merge |

---

## ADR-013: Pydantic-at-Boundary Service Functions (Accept `ScheduleCreatePayload | dict`)

**Decision:** Service functions in `daemon.services.scheduling_service` accept **either a Pydantic model OR a `dict`** at the function boundary. The shape:

```python
async def create_schedule(
    payload: ScheduleCreatePayload | dict,
    *,
    caller_instance_id: str,
    caller_agent_id: str,
) -> ScheduleCreateResponse:
    if isinstance(payload, dict):
        payload = ScheduleCreatePayload.model_validate(payload)
    ...
```

Same pattern for `update_schedule(payload: ScheduleUpdatePayload | dict, ...)`. Tools always pass a Pydantic model (built from tool args via `ScheduleCreatePayload(**kwargs)`); phase-3 REST passes a fully-typed `ScheduleCreatePayload` (built by `_schedule_create_to_payload(req)` in `daemon/routers/schedules.py`, per F1 rewrite); the service's `model_validate` bridge covers any future dict-accept callers. One validated contract downstream of the boundary.

**Rationale:**
- Architecture §2 OD-2 (closed): dict-accept wins on Maintainability+Risk scoring (3.05 dict-only vs 4.30 weighted) and removes the phase-3 vs phase-2 contract drift risk that phase-2 §Risk 1 named as load-bearing.
- One line (`isinstance(payload, dict) → model_validate`) covers both callers without changing either surface.
- The Pydantic models live in `daemon.services.scheduling_service` as the canonical home (phase-2 §Task 1.1); tools and REST both consume the same `ScheduleCreatePayload` fields by name.
- **F1 REWRITE:** Phase-3 router builds a fully-typed `ScheduleCreatePayload` via `_schedule_create_to_payload(req)` (Task 1.2/1.7) and passes that — NOT `req.model_dump()` (which would fail `model_validate` on `name/agent_id/local_time+run_at` mismatching `label/agent/when`). The dict-accept signature is retained for non-REST callers (tools, tests, future internal callers) that may legitimately want to pass a dict.

**Consequences:**
- (+) One validated contract downstream of the boundary — no drift between tools and REST.
- (+) Phase-2 risk #1 (contract name drift) closes — phase-2 pins the names; phase-3 cannot redefine them.
- (+) Phase-3 router stays thin: handler builds typed payload via `_schedule_create_to_payload(req)` and delegates to `manager.scheduling_service.create_schedule(payload)`.
- (-) One extra branch at function entry (`isinstance(payload, dict)`) — negligible.
- (-) `ScheduleCreatePayload` fields must stay aligned with what the tools send (canonical field names: `timezone`, `cron_expression`, `weekday=Sun=0`). Any drift breaks the `model_validate` bridge.

---

## Tracking

- Created: 2026-10-01
- Last Updated: 2026-10-01 (Rev 2 — reconciled per architecture-recommendation.md §8)
- Status: synthesized from 5 phase files (`phase1-plan.md` / `phase2-plan.md` / `phase3-plan.md` / `phase4-plan.md` / `phase5-plan.md`) + leader decisions D1-D10. **Rev 2 folds in the architect's corrections** (architecture-recommendation.md §8) — every contradiction with §8 resolved; OD register closed; D1-D10 + ADR substance intact.
- House style exemplar: `.agents/shared/planning/job-system-improvements/decisions.md`
- 13 ADRs + 8 Open Decisions Register entries (OD-1 through OD-8; OD-1/2/3/4/5/6/7 ratified closed; OD-8 deferred)
