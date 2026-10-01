# Plan Overview: Scheduled Tasks (Wall-Clock Scheduling — Agent Tools + REST)

Date: 2026-10-01 (Rev 2)
Author: planner[v2] via plan-creation worker
Branch: `feature/scheduled-tasks`
Status: Draft — Reconciled (Rev 2 folded architecture-recommendation.md §8 corrections)

> **🔴 BINDING INPUT (architecture review):** `.agents/shared/planning/scheduled-tasks/architecture-recommendation.md` §8 is the consolidated correction list — every implementer MUST read it first. This overview (Rev 2) and the phase files have been updated to match §8's ratified closures. The architect's verified facts (cancel = TWO sessions, stop path clobbers STOPPED unconditionally, PUT is DB-only, `DYNAMIC_TOOL_NAMES` at `_tool_registry.py:23-119`) are file:line-verified against `feature/scheduled-tasks` tip `e431251d` (architecture-recommendation.md:6).

> **Errata trail (Rev 2 → Rev 1):** this Rev 2 was produced after the architect's review rejected Rev 1 for documentation-reconciliation (not design — design was verified ~90% implementation-ready). Corrections applied: (C-1) idempotency key source `next_trigger` → `self._run_at`; (C-1b) added one-time-only emission gate + cron test; (C-2) added atomic `cancel_source_config` repository method + clobber guard in `stop_adapter`; (C-3) added `anchor_local_to_utc` helper; (C-4) rewrote adapter refresh as new stop→mutate→start + per-source `asyncio.Lock` + evict-before-stop; (C-5) reconciled phase2↔phase3 create contract (phase2 canonical, weekday=Sun=0, Pydantic-at-boundary dict-accept); (C-6) added runtime wiring ownership (instance.py + manager.scheduling_service + DYNAMIC_TOOL_NAMES); (C-7) added REST list filter default-exclude cancelled + `?include_cancelled=true`; (C-8) DROPPED OD-1 (defense-in-depth migration); plus OD closures, phase2+4 = ONE PR (no xfail), phase-5 pack as merge gate (no xfail/skip parking). D1-D10 + ADR substance intact.

## Executive Summary

The agents-ensemble scheduler is **adapter-internal and human-operator-only**: there is **zero** agent-facing tool, no `/api/schedules` POST/DELETE/GET-by-id, no host-local timezone resolution, no local+UTC echo on any surface, no idempotency guarantee on one-shot dispatch, no terminal cancel-state that survives restart without purging history, and the parse trap at `daemon/sources/adapters/scheduler.py:232-235` silently assumes UTC for naive ISO inputs. **The biggest material risk is silent double-dispatch of one-shot schedules across a daemon restart** (D4 — verified chain below; fixed in Rev 2 by sourcing the idempotency key from `self._run_at.isoformat()` and gating emission to `SCHEDULE_TYPE_ONE_TIME`).

This plan extends — **does not rebuild** — the existing scheduler substrate (`daemon/sources/adapters/scheduler.py` 907L, `source_configs` rows, `schedule_executions` history, six existing `/api/schedules` routes: two GET (`/schedules`, `/schedules/{id}/executions`) + four mutating (PUT update, POST trigger/start/stop)). It adds (a) `SchedulingConfig` + `daemon/utils/tz.py` + `anchor_local_to_utc` + `idempotency_key` threading that closes D4 and the past-due catch-up window D3, (b) a `SourceStatus.CANCELLED` enum value (in **both** `daemon/models/source.py:8-14` AND `daemon/repositories/source/models.py:20-25`) plus an atomic `cancel_source_config` repository method plus boot-filter extension plus `stop_adapter` clobber guard, (c) a shared `daemon/services/scheduling_service.py` seam used by both the new `scheduling` agent-tool category and the three new REST endpoints, (d) registration on `ari` (mandatory), `leader` + `jober` (desirable) plus canonical `tools_note.md` sections per `docs/agent-prompt-writing-guide.md`, (e) runtime wiring (instance.py + manager.scheduling_service + DYNAMIC_TOOL_NAMES), and (f) a full test surface (DST parameterized over both cron + one-shot anchor paths, catch-up, idempotency including cron-not-affected + 5s-retry-collapse, single-uuid contract, history-preservation-on-cancel + clobber + never-boot-starts + last-execution-id echo, REST list filter, DELETE guard placement) plus a registered acceptance pack as the merge gate.

**Key insight (Rev 2):** D4 closes by **threading `idempotency_key = f"scheduler:{source_id}:{self._run_at.isoformat()}"`** through the existing `enqueue_message_job` chain (architecture §1.2: NOT `next_trigger` — that returns `now` for past-due one-shots at `scheduler.py:486-487` and would mutate the key per boot/retry). Emission is **gated to `SCHEDULE_TYPE_ONE_TIME`** so cron fires keep minting fresh JobItems (architecture §1.3). The dispatch side already gates `dispatch_bus.notify_new_job` on `created and job is not None` at `daemon/services/job_queue_service.py:1232-1236`, so a replay returns the existing `JobItem` without re-firing. This is **at-most-once dispatch**, not just at-most-once-row. No new table, no new dispatcher loop, no daemon restart, no changes to existing job semantics.

**API backward compatibility:** All existing `/api/schedules` routes, `POST /api/sources`, `DELETE /api/sources/{id}`, `daemon/scheduler.py` constants, and the 5-second retry loop (`SCHEDULER_ERROR_RETRY_S` at `daemon/constants.py:236`) remain unchanged. Cancel = `SourceStatus.CANCELLED` enum value, NOT `delete_source_config` (which purges `schedule_executions` at `repository.py:281-284`).

## Scope Assessment

**MEDIUM** — 5 phases, ~30 tasks, ~17 files touched (8 daemon source files, 3 agent-prompt files, 3 docs, 3+ test files, 1 pack). Touching `daemon/config.py`, `daemon/utils/tz.py` (NEW), `daemon/services/scheduling_service.py` (NEW), `daemon/tools/scheduling.py` (NEW), `daemon/tools/_tool_registry.py`, `daemon/sources/adapters/scheduler.py`, `daemon/sources/registry.py`, `daemon/repositories/source/models.py`, `daemon/services/instance_messaging.py`, `daemon/manager.py`, `daemon/routers/schedules.py`, `daemon/models/schedule.py`, `daemon/models/source.py`, `config.yaml`, `agents/{ari,leader,jober}/meta.json`, `agents/{ari,leader,jober}/tools_note.md`, `docs/scheduling.md` (NEW), `docs/api-reference.md`, `docs/pluggable-sources-architecture.md`, plus new test files and one pack. No daemon restart; runtime exposure gated on the next daemon restart after phase-2 lands.

## The Core Problem (Verified)

### Broken flow — agent has no way to schedule, and one-shots double-dispatch on restart

```
Today:
  Agent "ari" (a user) wants to fire "give me a morning briefing" at 06:00 America/New_York daily.
    ↓
  ┌──────────────────────────────────────────────────────────────────────────────────────────┐
  │ GAPS                                                                                    │
  │   ① No `task_schedule` (or any scheduling) tool on ari/leader/jober — only human REST.   │
  │   ② No /api/schedules POST/DELETE/GET-by-id — creation only via POST /api/sources.      │
  │   ③ User-stated "06:00" is tz-naive: scheduler.py:232-235 silently assumes UTC.         │
  │   ④ No `SchedulingConfig.default_timezone`; no host-local detector anywhere.             │
  │   ⑤ No echo of BOTH local+UTC on any surface (today: `next_run_at` only).               │
  │   ⑥ D3: no cap on past-due one-shots — they always fire no matter how late.             │
  │   ⑦ D4 (CRITICAL): one-shot dispatch has NO idempotency_key → double-dispatch on crash. │
  │   ⑧ No cancel-state: SourceStatus lacks CANCELLED; DELETE /api/sources/{id} PURGES     │
  │      schedule_executions history (sources.py:374-405 → repository.py:263-295).           │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
    ↓
  User gives up and pings ari in chat, manually sets a cron.
```

### Verified gap list (file:line pinned)

1. **D1 — No scheduling tool surface.** `daemon/tools/job_queue.py:1142-1151` exposes `job_create` (immediate dispatch only); `daemon/tools/time.py:1-78` is read-only time echo. `CATEGORY_MODULES` (`daemon/tools/_tool_registry.py:522-596`) has zero scheduling entries. `KNOWN_TOOL_NAMES` (`:630-842`) has zero `task_schedule*` entries.

2. **D1 — No REST create/cancel/get-by-id.** `daemon/routers/schedules.py:39-79/:83-172/:176-247/:251-339/:343-387/:391-458` lists six existing routes. **Missing**: `POST /schedules`, `DELETE /schedules/{id}`, `GET /schedules/{id}` — verified by absence; creation today routes through `POST /api/sources` (sources.py:235-239).

3. **D2 — Tz-naive parse trap.** `daemon/sources/adapters/scheduler.py:232-235` parses a tz-naive ISO `run_at` as **assumed-UTC** with no warning; `:483-484` has a **dead re-anchor branch** (already-anchored at `:234-235`); no `resolve_timezone()` chain anywhere in `daemon/utils/`, no `SchedulingConfig` in `daemon/config.py` (grep-verified zero hits).

4. **D2 — No host-local tz detector.** `daemon/tools/time.py:26-28` and `scheduler.py:10,123,126` are the only `zoneinfo.ZoneInfo(...)` call sites — neither has a `/etc/localtime` symlink walk or `TZ` env fallback. `tzdata` is NOT a dependency (`pyproject.toml` zero hits); minimal containers strip `/etc/localtime`.

5. **D2 — No local+UTC echo on any surface.** `GET /schedules/{id}` today returns `next_run_at` only (schedules.py:52-58 via `_get_next_trigger_time`); tools and `task_schedule_list` don't exist. Every echo surface added by this plan returns BOTH.

6. **D3 — No past-due cap.** `daemon/sources/adapters/scheduler.py:486-487` always fires when `run_at <= now`. No `one_shot_max_lateness_seconds` knob, no `_record_skipped_execution` helper, no `SKIPPED` row in `schedule_executions` from inside the adapter loop (only `record_execution_complete` callbacks at `:777-787/:829`).

7. **D4 — Double-dispatch window (CRITICAL).** Chain:
   - `_run_schedule` (`:385-452`) → `_emit_scheduled_message` (`:430`) → `execution_callback("triggered")` (`:829`, fire-and-forget `run_in_executor` at `registry.py:490-496`) → `_route_via_job_queue` (`:689-690`) → `manager.enqueue_message_job(source="scheduler")` (`:762-769`, **no `idempotency_key`**) → one-time disable-write `update_source_status("stopped") + update_source_config(enabled=False)` (`registry.py:506-507`).
   - `_is_one_time_executed` is an **in-memory bool** (`scheduler.py:150`, set `:434`) — does NOT survive restart.
   - `schedule_executions` has NO unique constraint (`models.py:143-169`).
   - Crash between `enqueue_message_job` (`:762-769`) and the disable-write callback (`:506-507`) → on next boot `_is_one_time_executed=False` (reset), the schedule re-fires and produces a **second** `JobItem`.
   - Parallel hazard: the **5-second retry loop** at `:447-450 + :697-709 + SCHEDULER_ERROR_RETRY_S=5.0` (constants.py:236) re-attempts a failing one-shot indefinitely within a single lifetime — and each attempt mints a fresh `JobItem` because no `idempotency_key` is passed.

8. **D5 — Cancel has no terminal state.** `SourceStatus` (`daemon/repositories/source/models.py:20-25`) has four values: `STOPPED, STARTING, RUNNING, ERROR`. **No `CANCELLED`**. Boot filter at `registry.py:290-294` skips only `enabled=False` and `status == SourceStatus.STOPPED.value`. `delete_source_config` (`:263-295`) PURGES `schedule_executions` (`:281-284`); reuse of `DELETE /api/sources/{id}` (sources.py:374-405) for cancel is therefore **history-destroying**.

9. **D9 — No registration tests.** `daemon/registry.py:1096-1118` produces only NON-FATAL warnings for unknown allow-list entries. No test asserts `"scheduling" ∈ meta["tools"]["allow"]` for any agent. Drift test `tests/unit/tools/test_frozen_tool_name_discovery.py:223-242` only catches `KNOWN_TOOL_NAMES` ↔ source mismatches.

## Pre-existing Infrastructure (Reused — Not Rebuilt)

| Component | File | What It Does | This Plan's Use |
|-----------|------|--------------|-----------------|
| `SchedulerAdapter` | `daemon/sources/adapters/scheduler.py:1-907` | Existing scheduler adapter (cron + one-shot) | Rev 2: extended at `:121-126` (tz resolver via `resolve_timezone`), `:486-487` (D3 cap), `:689-690` (idempotency_key from `self._run_at.isoformat()`, gated to `SCHEDULE_TYPE_ONE_TIME`); boot-filter at `registry.py:292` skips `status in {STOPPED, CANCELLED}` |
| `source_configs` table | `daemon/repositories/source/models.py:46-66` | Schedule rows; `name` indexed, `config` JSONB | Stores `local_time`/`timezone`/`recurrence` human-intent keys + canonical `run_at`/`schedule` |
| `schedule_executions` table | `models.py:143-169` | History rows; `status` enum includes `SKIPPED` (`:38`) | Receives new `SKIPPED` rows from D3 cap path; history preserved on cancel |
| `JobItem.idempotency_key` | `daemon/repositories/job_queue/repository.py:476-489` + `daemon/services/job_queue_service.py:1232-1236` | At-most-once dispatch: `create_or_get_by_idempotency_key` returns existing without re-firing `dispatch_bus.notify_new_job` (`:1232-1236`, gated on `created and job is not None`). Partial UNIQUE index `idx_job_idempotency` at `daemon/repositories/job_queue/models.py:282-306`. | D4 fix (Rev 2): scheduler threads `idempotency_key=f"scheduler:{source_id}:{self._run_at.isoformat()}"` — derived from `self._run_at` (architecture §1.2), gated to one-time schedules (architecture §1.3). |
| `is_write_paused` | manager attribute | 503 gate on writes during migration | Phase-3 POST/DELETE handlers gate on this with literal `"Writes are paused for database migration"` |
| `RESERVED_SOURCE_PREFIXES` | `daemon/constants.py:763-784`, includes `"scheduler"` at `:783`; chat-source gate at `daemon/routers/jobs_crud.py:526-543` | 422 gate preventing forged `source='scheduler:*'` jobs | Inherited; no change |
| `CATEGORY_MODULES` + `KNOWN_TOOL_NAMES` | `daemon/tools/_tool_registry.py:522-596/:630-842` | Frozen-binary fallback universe | Phase-2 adds `"scheduling": "daemon.tools.scheduling"` + regen of four tool names |
| `PRIVILEGED_TOOL_CATEGORIES` | `_tool_registry.py:167-171` | `{"system_upgrade","system-log","ens-db"}` — triple-pinned D18/A14 | **Untouched** — `scheduling` is non-privileged (D7 grants per-agent) |
| `croniter>=3.0.0` | `pyproject.toml:25` | The sole DST-aware component; aware datetimes at `scheduler.py:210-211/:466-467` | Phase-1 pins semantics (D8); phase-5 tests parameterize on it |
| `StopIteration`-style boot filter | `daemon/sources/registry.py:281-294` | Skips `enabled=False` and `status == STOPPED` at boot | Extended for `CANCELLED` (phase-1 Task 7.2) |
| Existing `/api/schedules` routes | `daemon/routers/schedules.py:39-79/:83-172/:176-247/:251-339/:343-387/:391-458` | GET/PUT/trigger/start/stop/executions | Phase-3 adds POST/GET-by-id/DELETE; pause/resume reuses existing stop/start |
| `tests/conftest.py::clean_env` | `:653-688` with `_TRACKED_ENV_PREFIXES = ("OPENAI_", "ENSEMBLE_")` (`:644`) | Per-test env scrub | **Auto-covers `ENSEMBLE_SCHEDULING_*`** — NO `_TRACKED_ENV_EXACT` edit needed (corrects older recon advice) |

## Architecture: As-Is vs To-Be

### As-Is Architecture (gap-rich)

```mermaid
graph TB
    subgraph "Agent (no scheduling tool)"
        A[Agent "ari"]
    end

    subgraph "REST /api/schedules (creation-only via /api/sources)"
        R[GET /schedules]
        P[PUT /schedules/{id}]
        T[POST /schedules/{id}/trigger]
        SS[POST /schedules/{id}/start/stop]
        EX[GET /schedules/{id}/executions]
    end

    subgraph "Scheduler Adapter"
        SA[SchedulerAdapter]
        CB[config.timezone :121-126]
        PZ[parse trap :232-235]
        NEX[_is_one_time_executed :150 in-memory]
        ENQ[enqueue_message_job :762-769 no idempotency_key]
        DW[disable-write :506-507]
        RL[5s retry loop :447-450]
    end

    subgraph "Persistence"
        SC[(source_configs)]
        SE[(schedule_executions no unique index)]
        BF[boot filter :290-294 STOPPED only]
    end

    subgraph "Job queue"
        JQ[job_queue_service.enqueue]
        DB[dispatch_bus.notify_new_job]
    end

    A -. ❌ no tool .- R
    SA --> CB
    CB --> PZ
    SA --> NEX
    NEX --> ENQ
    ENQ -. crash window .-> DW
    DW -. lost on restart .-> NEX
    ENQ --> JQ
    JQ --> DB
    SA --> SC
    SA --> SE
    BF --> SA
    RL -. ∞ retries .-> ENQ

    style A fill:#ff6b6b,color:#fff
    style ENQ fill:#ff6b6b,color:#fff
    style BF fill:#ff6b6b,color:#fff
```

### To-Be Architecture (tool + REST + idempotent + cancel-terminal)

```mermaid
graph TB
    subgraph "Agents"
        AR[Agent "ari" — mandatory]
        LD[Agent "leader" — desirable]
        JB[Agent "jober" — desirable]
    end

    subgraph "scheduling tool category (phase-2)"
        TS[task_schedule]
        TSL[task_schedule_list]
        TSC[task_schedule_cancel]
        TSU[task_schedule_update]
    end

    subgraph "Shared scheduling service (phase-2)"
        SVC["daemon.services.scheduling_service"]
        CR[create_schedule]
        CA[cancel_schedule]
        GE[get_schedule]
        LI[list_schedules]
        UP[update_schedule]
    end

    subgraph "REST /api/schedules (phase-3 adds 3)"
        PC[POST /schedules]
        GC[GET /schedules/{id}]
        DC[DELETE /schedules/{id} — terminal, history preserved]
        EXR[GET /schedules/{id}/executions]
    end

    subgraph "TZ helpers (phase-1)"
        TZ["daemon.utils.tz"]
        RT[resolve_timezone]
        DH[detect_host_local_timezone]
        SCfg["SchedulingConfig (env ENSEMBLE_SCHEDULING_*)"]
    end

    subgraph "Scheduler Adapter (phase-1 patched)"
        SA2[SchedulerAdapter]
        CC[config.timezone via resolve_timezone :121-126]
        AT[anchor_local_to_utc :1 DST helper]
        RUN[self._run_at — replay-stable key source]
        ID[idempotency_key f'scheduler:{sid}:{self._run_at.iso}' :689-690, gated to SCHEDULE_TYPE_ONE_TIME]
        CAP[D3 cap at :486-487 → SKIPPED row]
        CAN[boot filter skip CANCELLED :292, atomic cancel_source_config + clobber guard]
    end

    subgraph "Job queue (unchanged)"
        JQ[job_queue_service.create_or_get_by_idempotency_key]
        DB2[dispatch_bus.notify_new_job gated on created=True :1234-1236]
    end

    subgraph "Persistence (no new tables)"
        SC2[(source_configs)]
        SE2[(schedule_executions, SKIPPED + history preserved)]
    end

    AR --> TS
    LD --> TSL
    JB --> TSC
    TS --> CR
    TSL --> LI
    TSC --> CA
    TSU --> UP
    PC --> CR
    GC --> GE
    DC --> CA
    SCfg --> RT
    DH --> RT
    RT --> CC
    CR --> SC2
    CA --> CAN
    CAN --> SE2
    SA2 --> RUN
    RUN --> ID
    ID --> JQ
    JQ --> DB2
    SA2 --> CAP
    CAP --> SE2

    style SVC fill:#51cf66,color:#fff
    style ID fill:#51cf66,color:#fff
    style RT fill:#51cf66,color:#fff
    style CAN fill:#51cf66,color:#fff
    style CAP fill:#51cf66,color:#fff
```

## Phases

| Phase | Title | Objective | Key files | Depends on |
|-------|-------|-----------|-----------|------------|
| 1 | Foundation — Config, TZ Helpers, Scheduler Adapter Touchpoints | Land `SchedulingConfig` + `daemon/utils/tz.py` + close D4 (idempotency_key) + close D3 catch-up window + `SourceStatus.CANCELLED` enum + boot-filter extension + DST semantic pin | `daemon/config.py` (NEW section), `daemon/utils/tz.py` (NEW), `daemon/sources/adapters/scheduler.py`, `daemon/sources/registry.py`, `daemon/repositories/source/models.py`, `daemon/services/instance_messaging.py`, `daemon/manager.py`, `config.yaml` | None (root) |
| 2 | Shared Scheduling Service + Agent Tools (category "scheduling") | Build `daemon/services/scheduling_service.py` (5 module-level async functions) + `daemon/tools/scheduling.py` (4 tools: `task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update`) + register `scheduling` category in `CATEGORY_MODULES` + regen `KNOWN_TOOL_NAMES` | `daemon/services/scheduling_service.py` (NEW), `daemon/tools/scheduling.py` (NEW), `daemon/tools/_tool_registry.py` | phase-1 (tight) |
| 3 | REST Surface — Create, Cancel, Get-by-ID | Add `POST /schedules`, `DELETE /schedules/{id}` (terminal cancel, history preserved), `GET /schedules/{id}` (BOTH local+UTC); all thin wrappers over phase-2 service; **explicit REST↔service field-mapping function** `_schedule_create_to_payload()` (Task 1.2) mapping REST `source_id/name/agent_id/local_time+run_at` → phase-2 `label/agent/when`; REST response wrappers (`ScheduleCreateRestResponse` / `ScheduleDetailRestResponse` / `ScheduleCancelRestResponse`) wrap phase-2 canonical models. **DELETE guard adoption in `daemon/routers/sources.py:382-394` (phase-3-owned, ADR-012 ADOPTED)** — `SourceStatus.CANCELLED` enum extension is owned by **phase-1 §Task 7.1** (phase-3 only USES the value, does not add it). | `daemon/routers/schedules.py`, `daemon/routers/sources.py` (Task 4.2), `daemon/models/schedule.py` | phase-2 (tight — service contract), phase-1 (loose — `SourceStatus.CANCELLED` already exists) |
| 4 | Tool Registration & Documentation | Add `"scheduling"` to `tools.allow` for `ari` (mandatory), `leader` (desirable), `jober` (desirable); add `## Scheduling` section to each agent's `tools_note.md` (agent-POV, no internals); author `docs/scheduling.md` user reference; cross-link from `docs/api-reference.md` + `docs/pluggable-sources-architecture.md`; static tests pin registration + cross-refs | `agents/{ari,leader,jober}/meta.json`, `agents/{ari,leader,jober}/tools_note.md`, `docs/scheduling.md` (NEW), `docs/api-reference.md`, `docs/pluggable-sources-architecture.md`, `tests/unit/test_scheduling_registration.py` (NEW) | phase-2 (tight — `KNOWN_TOOL_NAMES` regen) |
| 5 | Tests & Test Packs | Adapter-direct unit tests (TZ chain, DST parameterized on phase-1, catch-up, idempotency restart) + API tests (POST/GET/DELETE phase-3 contract, `delete_source_config.assert_not_called()`, cancel-by-label) + integration tests (D4 single-uuid contract, cancel-prevent-dispatch, D3 catch-up row) + acceptance pack `tests/packs/scheduled_tasks_acceptance.sh` registered in `.agents/tester/PACKS.md` | `tests/test_scheduler_adapter.py` (NEW classes appended), `tests/test_scheduler_api.py` (NEW classes + extended `mock_manager`), `tests/integration/test_scheduled_tasks_e2e.py` (NEW), `tests/packs/scheduled_tasks_acceptance.sh` (NEW), `.agents/tester/PACKS.md` (APPEND) | phase-1, phase-2, phase-3, phase-4 (tight on phase-2 service; loose on others) |

### Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|
| Phase 1 | — | tight (SchedulingConfig, tz, idempotency_key, SourceStatus.CANCELLED) | loose (CANCELLED enum) | independent | tight (tests assert phase-1 semantics) |
| Phase 2 | tight | — | **tight (service contract = the seam)** | tight (KNOWN_TOOL_NAMES regen) | tight (tests mock + integration) |
| Phase 3 | loose | tight | — | independent | tight (API tests) |
| Phase 4 | independent | tight | independent | — | tight (registration tests) |
| Phase 5 | tight (DST + idempotency semantics) | tight (service contract) | tight (API contract) | tight (registration) | — |

## Requirements Traceability

| # | User Requirement / Leader Decision | Maps to | Verified |
|---|---|---|---|
| U1 | Schedule a job at a given local time (one-shot default, daily/weekly recurring) | phase1 §Task 1 (SchedulingConfig), phase2 §Task 1.2-1.3 (create_schedule + recurrence mapping), phase3 §Task 2.1 (POST), phase4 §Task 1-3 (tools) | ✓ |
| U2 | List scheduled tasks (label, agent, next run LOCAL+UTC, status) | phase2 §Task 1.5-1.6 (get/list with BOTH local+UTC), phase3 §Task 4.1 (REST `GET /schedules` default-excludes cancelled + `?include_cancelled=true` — Rev 2 §3.4), phase4 §Task 4 (tools_note). **REST list leg: PARTIAL** — there is no new list REST endpoint added by this plan; existing `GET /schedules` (schedules.py:39-79) is extended with the cancel-filter (phase-3 §Task 4.1). `GET /schedules/{id}` (phase-3 §Task 2.2) DOES return cancelled rows. Tool (`task_schedule_list`) mirrors this default-exclude. | ✓ (partial — REST list filter leg added Rev 2) |
| U3 | Cancel by id/label + reschedule/update incl. pause-resume | phase2 §Task 1.4 + 1.7 (cancel/update + paused bool), phase3 §Task 2.3 (DELETE), phase4 §Task 4 (tools_note) | ✓ (pause/resume reuses existing `POST /schedules/{id}/stop/start`, no new routes) |
| U4 | CRITICAL TZ RULE: user-stated local, daemon UTC, store/echo BOTH, DST-correct | phase1 §Task 1 + 2 (SchedulingConfig + resolve_timezone + D8 contract), phase2 §Task 1.2-1.7 (echo both), phase3 §Task 1.4-1.5 + 2.2 (ScheduleDetail both), phase4 §Task 5.2 (docs), phase5 §Task 1.1-1.2 (TZ + DST tests) | ✓ |
| U5 | Register on Ari MANDATORY + leader/jober + tools_note ×3 + docs; no daemon restart | phase4 §Tasks 1-5 (meta.json edits + tools_note ×3 + docs), phase4 §Task 6 (registration tests — D9) | ✓ |
| U6 | REST mirror the tool surface | phase3 (POST/GET/DELETE), phase2 (service is the seam) | ✓ (REST adds 3 routes; pause/resume uses existing; list uses existing `GET /schedules`) |
| U7 | Plan → implement → test with unit tz/DST/recurrence/catch-up + integration + docs | phase5 §Task 1 (unit TZ/DST/catch-up/idempotency), phase5 §Task 2 (API tests), phase5 §Task 3 (integration: D4 single-uuid + cancel-prevent-dispatch), phase4 §Task 5 (docs), phase5 §Task 4 (pack) | ✓ |
| U8 | Self-contained: no changes to existing job semantics, no daemon restart | phase1 §Risk 1 (additive kwarg), phase2 §Constraint (no POST /api/jobs), all phases (no daemon restart; correctness proven statically + at next registry exposure per D9) | ✓ |
| D1 | Reuse-not-rebuild: NO new table, NO new dispatcher loop — extend scheduler adapter + source_configs + schedule_executions + /api/schedules | phase1-3 all extend existing; phase5 only ADDS test classes; pre-existing infrastructure table above | ✓ |
| D2 | Tz resolution chain: explicit → SchedulingConfig default `ENSEMBLE_SCHEDULING_DEFAULT_TZ` → host-local auto-detect → UTC+loud warning in tool output | phase1 §Task 1.1 (SchedulingConfig fields) + §Task 2.2 (resolve_timezone), phase2 §Task 1.2 (`for_tool=True` surfaces warning), phase4 §Task 4.1 (tool-note operational boundary), phase5 §Task 1.1 (TestTzResolution) | ✓ |
| D3 | Catch-up default preserved + config cap `one_shot_max_lateness_seconds` with skip-marker | phase1 §Task 4 (D3 lateness cap + `_record_skipped_execution` + `SKIPPED` row), phase5 §Task 1.3 + §Task 3.3 (catch-up tests) | ✓ |
| D4 | Idempotency: window CONFIRMED; fix must guarantee never-double-dispatch across restarts | phase1 §Task 3 (idempotency_key through enqueue_message_job → `create_or_get_by_idempotency_key` → `dispatch_bus.notify_new_job` gated on `created=True`), phase5 §Task 1.4 + §Task 3.1 (D4 tests) | ✓ |
| D5 | Tool surface: category "scheduling" with 4 tools; cancel terminal + hidden from default list; pause resumable | phase2 §Task 1 (service with terminal cancel + paused flag) + §Task 2 (tools), phase4 §Task 4 (tools_note), phase5 §Task 2.3 + §Task 3.2 (cancel tests) | ✓ |
| D6 | REST mirrors tools, 503 write-pause gates, no auth | phase3 (3 routes, is_write_paused gate, no auth); house posture preserved | ✓ |
| D7 | Ari mandatory + leader + jober + tools_note ×3 + docs | phase4 §Tasks 1-3 (meta.json ×3) + §Task 4 (tools_note ×3) + §Task 5 (docs) | ✓ |
| D8 | DST test pair | phase1 §Task 8.1 (DST semantic pin — croniter-default), phase5 §Task 1.2 (TestDstSemantics parameterized on phase-1) | ✓ |
| D9 | No restart, tests prove registration | phase4 §Task 6 (5 static tests); D9 acceptance proven at next daemon restart after phase-2 lands | ✓ |
| D10 | No job-semantics/queue/POST /api/jobs changes | phase1 §Risk 1 (additive kwarg; existing callers unaffected), phase2 §Constraint (no `POST /api/jobs`, no `delete_source_config`) | ✓ |

## Cross-Phase Dependencies & Merge Order

```
                  ┌─────────────────────────────────────────────────────────────┐
                  │  PHASE 1 (root)                                            │
                  │    SchedulingConfig + tz helpers + D4 fix + D3 cap +       │
                  │    SourceStatus.CANCELLED + boot-filter + DST contract    │
                  └─────────────────────────┬───────────────────────────────────┘
                                            │
                                            ▼
                  ┌─────────────────────────────────────────────────────────────┐
                  │  PHASE 2 (shared service + tools + KNOWN_TOOL_NAMES regen)│
                  │    scheduling_service.py (5 fns) + scheduling.py (4 tools) │
                  │    + CATEGORY_MODULES entry + KNOWN_TOOL_NAMES regen       │
                  └─────┬───────────────────────────────┬───────────────────────┘
                        │                               │
                        ▼                               ▼
        ┌──────────────────────────────┐  ┌─────────────────────────────────────┐
        │  PHASE 3 (REST surface)      │  │  PHASE 4 (registration + docs)       │
        │    POST/DELETE/GET-by-id     │  │    meta.json ×3 + tools_note ×3 +   │
        │    on /api/schedules         │  │    docs/scheduling.md + 5 tests     │
        └──────────────┬───────────────┘  └─────────────────┬───────────────────┘
                       │                                  │
                       └──────────────┬───────────────────┘
                                      │
                                      ▼
                  ┌─────────────────────────────────────────────────────────────┐
                  │  PHASE 5 (verification — pack gates merge)                  │
                  │    adapter unit + API + integration + acceptance pack       │
                  └─────────────────────────────────────────────────────────────┘
```

### Merge-gate decisions

- **Phase 4 → MUST merge together with phase 2 as ONE PR (architecture §8 item 16 ratified; non-negotiable).** Phase 4's `test_meta_json_allowlist_subset_of_registry` (Task 6.3) and `test_scheduling_in_meta_json_allowlist` FAIL until `KNOWN_TOOL_NAMES` regen lands (phase 2). The drift test (`tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) is bidirectional and runs in CI: a red CI merge is not mergeable. Single green CI gate proves ADR-009's static posture. If leader/jober registration is deferred past the merge, parameterize their registration tests over only the agents actually registered — do NOT xfail.
- **Phase 5 → pack is the merge gate (architecture §8 item 16 ratified; NO xfail/skip parking).** All architecture-pinned semantics (croniter-default for cron, anchor_local_to_utc fold=0/gap-shift for one-shot, key from `self._run_at`, gate to `SCHEDULE_TYPE_ONE_TIME`) are asserted directly. `tests/packs/scheduled_tasks_acceptance.sh` is the CI merge gate. **Precise merge order:** phase 1 (foundation) precedes → phase 2 + phase 4 land as ONE PR → phase 3 follows phase 2 → phase 5 pack closes the loop.
- **Phase 3 → blocked-by phase 2 service seam.** Phase 3 risk #1 flags that the service contract names (`create_schedule`, `cancel_schedule`, `get_schedule`, `list_schedules`, `update_schedule`) are aspirational symbols until phase-2 lands; phase-2's Task 5.1 (`test_phase3_service_contract_resolved`) lands the CI guard FIRST so phase-3 can wire against a stubbed-then-real implementation.

### Boot-filter × CANCELLED interaction (coherence check 1)

- **phase-1 Task 7.1** adds `SourceStatus.CANCELLED = "cancelled"` to the enum.
- **phase-1 Task 7.2** extends boot filter at `daemon/sources/registry.py:290-294` to skip `if config.status in (SourceStatus.STOPPED.value, SourceStatus.CANCELLED.value): skip`. ✓ Pinned.
- **phase-3 Context §"Idempotency (D4)"** restates the same invariant: "cancel endpoint MUST persist `enabled=False` AND set `status="cancelled"` in the SAME transaction — boot-time adapter enumeration reads `enabled` (registry.py:281-283) and `status` (:290-294), so a cancelled schedule that was live when the daemon restarted must not be revived." ✓ Consistent with phase-1.

### Service-contract match (coherence check 2)

| Function | phase-2 pins | phase-3 consumes | Match |
|---|---|---|---|
| `create_schedule(payload: ScheduleCreatePayload \| dict, *, caller_instance_id, caller_agent_id)` | phase2 §Task 1.1 + §Task 1.2 + skeleton `:147-152` | phase3 §Task 2.1 calls `manager.scheduling_service.create_schedule(payload)` where `payload` is a **fully-typed `ScheduleCreatePayload`** built by `_schedule_create_to_payload(req)` (Task 1.2/1.7 — F1 REWRITE). The dict-accept signature (architecture §2 OD-2) covers tools, tests, and future internal callers. | ✓ Names + types match. Service's `model_validate` bridge covers any non-REST caller that passes a dict. |
| `cancel_schedule(schedule_id: str)` | phase2 §Task 1.1 + §Task 1.4 | phase3 §Task 2.3 calls `manager.scheduling_service.cancel_schedule(schedule_id)` | ✓ Exact match |
| `get_schedule(schedule_id: str) -> ScheduleDetail \| None` | phase2 §Task 1.1 + §Task 1.5 | phase3 §Task 2.2 calls `manager.scheduling_service.get_schedule(schedule_id)` | ✓ Exact match |
| `list_schedules(*, project_id, status, include_cancelled, caller_agent_id)` | phase2 §Task 1.1 + §Task 1.6 | (no phase-3 consumer — list uses existing `GET /schedules`) | ✓ Names match; consumed only by `task_schedule_list` tool |
| `update_schedule(schedule_id, payload)` | phase2 §Task 1.1 + §Task 1.7 | (no phase-3 consumer — update uses existing `PUT /schedules/{id}`) | ✓ Names match; consumed only by `task_schedule_update` tool |

**Coherence verdict:** the 5 function signatures are consistent across phase-2 (the source of truth) and phase-3/phase-5 (consumers). The single interpretation gap (dict vs Pydantic at the `create_schedule` boundary) is a contract-interpretation choice, not a name drift.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| a | **Regression risk to existing scheduler behavior.** `tests/test_scheduler_adapter.py` (1564L), `tests/test_scheduler_api.py` (1068L), `tests/test_scheduler_instance_mode.py` (1288L) must stay GREEN. | High | Medium | Phase-1 §Task 3.3 patches `enqueue_message_job` additively; phase-1 §Task 2.4 wires the resolver with explicit > default precedence preserved. Acceptance criteria of every phase includes "existing scheduler suites GREEN". |
| b | **`KNOWN_TOOL_NAMES` frozen-list regeneration drift.** Misspelling or forgetting one of the four tool names trips `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift`. | Medium | Medium | Phase-2 §Task 3.2 runs the regen command (`_tool_registry.py:615-618`) AS PART of phase-2 acceptance; drift test (Task 3.3) is the safety net. |
| c | **Env-poison isolation for new `ENSEMBLE_SCHEDULING_*` keys.** Older recon advice suggested adding `_TRACKED_ENV_EXACT` to `tests/conftest.py`. **CORRECTED FACT**: `_TRACKED_ENV_PREFIXES = ("OPENAI_", "ENSEMBLE_")` at `tests/conftest.py:644` auto-covers the prefix — no edit needed. | Medium | Low | Phase-5 §Task 1.5 (and corrected note at `:14`) explicitly carries the verified fact; **no `tests/conftest.py` edits in any phase**. |
| d | **Boot-filter × CANCELLED interaction.** A schedule cancelled while live, before the daemon restarts, must NOT auto-start on the next boot. | High | Low | Phase-1 §Task 7.1+7.2 extends enum + boot-filter atomically; phase-3 §Context restates the invariant. Cross-checked in coherence sweep — both phases pin it. |
| e | **Stale-config class.** Edits to a schedule's `config` JSONB do not reach a running adapter — only `stop_adapter` → `start_adapter` round-trip rebuilds it. | Medium | Medium | Phase-2 §Task 1.7-1.8 (architecture §5.1 — **NET-NEW stop→mutate→start logic** under per-source `asyncio.Lock`): the prior plan cited a "proven seam" — that was WRONG (`PUT /schedules/{id}` at `schedules.py:83-172` is DB-only and leaves the live adapter stale; `schedules.py:251-339` is `POST /{id}/start`, not PUT). Phase-2 implements the sequence itself: per-source lock → evict registry entry → `stop_adapter` → `update_source_config` → `start_adapter`. <1s rebuild gap documented. |
| f | **Intra-lifetime 5-second retry loop mints duplicate `JobItem`s** for failing one-shots (`scheduler.py:447-450/:697-709`) — pre-fix hazard, still present post-fix. | Low | Low | Phase-1 §Task 3.3 note explicitly documents: loop preserved; the `idempotency_key` returns the same `JobItem` so no duplicate work, but log-spam remains. Loop removal is a separate decision (house-punted). |
| g | **`DELETE /api/sources/{id}` history-purge hazard.** If cancel ever routes through `delete_source_config`, history is destroyed (sources.py:374-405 → repository.py:263-295 → :281-284 purges `ScheduleExecutions`). | High | Medium | Phase-2 §Task 1.4 NEVER calls `delete_source_config`; phase-3 §Task 2.3 + phase-5 §Task 2.3 (case (f): `delete_source_config.assert_not_called()`) enforce. Phase-3 §Task 4.2 closes this hazard at the HTTP boundary (ADOPTED per architecture §2 OD-5; placement corrected to AFTER the get-or-404 at `sources.py:382`/BEFORE `:394`). |
| h | **Merge-order violations.** Phase-4's registration-exposure test FAILs if it merges before phase-2's `KNOWN_TOOL_NAMES` regen. | High | Low | Architecture §8 item 16 ratified: phases 2 + 4 = ONE PR (no xfail, no merge-with-red-CI). Pack (`tests/packs/scheduled_tasks_acceptance.sh`) is the merge gate for phases 1-5 as a whole. |
| i | **Additive-kwarg safety on `enqueue_message_job` / `instance_messaging`.** Existing callers pass positional args past `metadata` — adding a kwarg after `metadata` is the only safe option. | High | Low | Phase-1 §Task 3.3 + §Risk 1 explicitly audits `grep -rn "enqueue_message_job("` — every existing site passes everything positionally before `metadata`; new kwarg after `metadata` is safe. Phase-2 test sweep confirms at merge time. |
| j | **`SourceStatus.CANCELLED` enum-extension missed by set-membership check sites.** Any caller doing `status in {STOPPED, STARTING, RUNNING, ERROR}` silently misses CANCELLED. | Medium | Medium | Phase-1 §Task 7.1 + §Risk 5 audits `grep -rn "SourceStatus\."` for set literals; convert to `SourceStatus.__members__` or explicit enumeration. Boot filter (Task 7.2) is the most critical site — explicitly extended. |
| k | **Adversarial merge of phase-3 router before phase-2 service exists.** Phase-3's `manager.scheduling_service.create_schedule(...)` would `AttributeError` at first request. | High | Medium | Phase-2 §Task 5.1 lands `test_phase3_service_contract_resolved` FIRST so phase-3 is merge-gated on phase-2. |
| l | **Frontend status badge gap.** Adding `SourceStatus.CANCELLED` may break any UI that hard-codes the four-value enum. | Medium | Medium | Phase-3 §Risk 5 explicitly calls out: "Add the new value to the frontend status badge map in a follow-up; mark as known gap in the PR description. Phase-5 acceptance covers it." |

## Success Criteria

### Functional

- [ ] **D4 window closed** — `test_no_double_dispatch_after_restart` (phase-5 §Task 1.4) PASSES after a simulated restart; exactly one `JobItem` row exists for the one-shot; key value equals `f"scheduler:{source_id}:{self._run_at.isoformat()}"` byte-for-byte (NOT `now.isoformat()`). Companion tests: `test_cron_not_affected_by_idempotency_key` PASSES (cron fires intentionally keep minting fresh JobItems); `test_5s_retry_collapse` PASSES (retry returns existing JobItem).
- [ ] **D4 single-uuid contract** — integration test `test_one_shot_schedule_creates_job_item` (phase-5 §Task 3.1) PASSES: `JobItem.job_id == Task.work_id`.
- [ ] **D2 TZ chain with loud-warning echo** — `TestTzResolution` (phase-5 §Task 1.1) PASSES all four cases (explicit wins, env default, host-local fallback, UTC fallback + WARNING in caplog).
- [ ] **D2 local+UTC on every surface** — every echo from `task_schedule`, `task_schedule_list`, `task_schedule_update`, `POST /schedules`, `GET /schedules/{id}` carries BOTH `next_run_at_local` AND `next_run_at_utc` (both `null` when no upcoming run; never only one populated).
- [ ] **D3 catch-up cap** — `TestCatchUpSemantics` (phase-5 §Task 1.3) PASSES; integration test `test_one_shot_beyond_cap_no_dispatch_with_reason` (phase-5 §Task 3.3) PASSES with `schedule_executions` row `status='skipped'` + reason in `error_message`.
- [ ] **D5 cancel-terminal + history-preserved** — `TestCancelSchedule` (phase-5 §Task 2.3) case (f) PASSES (`delete_source_config.assert_not_called()`); integration test `test_cancelled_schedule_never_dispatches` (phase-5 §Task 3.2) PASSES (no `JobItem` after cancel; history queryable).
- [ ] **D5 boot-filter × CANCELLED** — integration `test_cancelled_schedule_never_dispatches` survives a daemon restart.
- [ ] **D7 tools on ari/leader/jober proven statically** — `tests/unit/test_scheduling_registration.py` (phase-4 §Task 6) PASSES for all 3 agents on the 5 parametrized tests.
- [ ] **D8 DST pair pinned** — `TestDstSemantics` (phase-5 §Task 1.2) PASSES both spring-forward gap and fall-back ambiguity cases per phase-1's chosen croniter-default semantics (skip-the-gap; first-occurrence-on-fall-back).
- [ ] **REST create/cancel/get-by-id with 503 gates** — `TestCreateSchedule` + `TestGetScheduleById` + `TestCancelSchedule` (phase-5 §Task 2) PASS all cases including `is_write_paused` 503 with the literal `"Writes are paused for database migration"` text.

### Non-regression

- [ ] Existing scheduler suites GREEN: `tests/test_scheduler_adapter.py` (1564L), `tests/test_scheduler_api.py` (1068L), `tests/test_scheduler_instance_mode.py` (1288L).
- [ ] `KNOWN_TOOL_NAMES` drift test GREEN: `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift`.
- [ ] `PRIVILEGED_TOOL_CATEGORIES` UNTOUCHED (no `"scheduling"` entry — D18/A14 triple-pin preserved).

### Operational

- [ ] New pack `tests/packs/scheduled_tasks_acceptance.sh` registered in `.agents/tester/PACKS.md` (verdict paragraph + `| Pack | Invocation | Scope | Result |` table row); pack exits 0 when all suites green. **Pack is the merge gate** (architecture §8 item 16) — no `@pytest.mark.xfail` parking, no `pytest.skip` (OD-7 ratified closed by architecture §2 OD-5).
- [ ] `docs/scheduling.md` (NEW) covers tool surface + REST + D2 TZ rule + D3 catch-up + D8 DST + D5 cancel-terminal/pause-resumable + `ENSEMBLE_SCHEDULING_DEFAULT_TZ` environment default.
- [ ] `docs/api-reference.md` lists the three new REST endpoints with `See Scheduling Reference` cross-link.
- [ ] `agents/{ari,leader,jober}/tools_note.md` each contain a `## Scheduling` section; closure grep per `docs/agent-prompt-writing-guide.md` §3 returns ZERO hits on forbidden tokens (`meta.json`, `tools.allow`, `daemon/`, `_tool_registry`, etc.).

### Constraints (verified at every acceptance gate)

- [ ] **NO daemon restart required** for correctness — proven by `tests/unit/test_scheduling_registration.py` static checks (D9). Runtime exposure gated on the next daemon restart after phase-2 lands.
- [ ] **NO job-semantics changes** — phase-1 §Risk 1 audit: additive `idempotency_key` kwarg after `metadata`; existing callers unaffected.
- [ ] **NO new table** — D1 reuse-not-rebuild verified; OD-1 (defense-in-depth migration on `schedule_executions`) DROPPED permanently per architecture §2 OD-1 (the only failure mode it would close is structurally unreachable today).
- [ ] **NO `POST /api/jobs` call from `daemon/tools/scheduling.py` or `daemon/services/scheduling_service.py`** (grep-verifiable).
- [ ] **NO `delete_source_config` call from cancel path** (grep-verifiable; `delete_source_config.assert_not_called()` in phase-5 §Task 2.3).

## Non-Goals

- **No scheduler rebuild.** Phase-1 patches the existing `SchedulerAdapter`; it does not replace it.
- **No new dispatch loop.** The existing 5-second retry loop (`scheduler.py:447-450/:697-709`) is preserved; only the consequence of a retry changes (no duplicate `JobItem` via `idempotency_key`).
- **No changes to job semantics, queue, or `POST /api/jobs`.** D10 — the schedule feature is dispatch via the existing scheduler adapter; jobs themselves are unchanged.
- **No daemon restart.** D9 — registration correctness proven statically.
- **No auth on `/api/schedules`** — house posture preserved; new routes inherit the existing posture.
- **No DST arithmetic in `scheduler.py`.** Phase-1 delegates to croniter (>=3.0.0); no manual DST handling.
- **No `tzdata` dependency.** Stdlib `zoneinfo` only; minimal containers fall back to UTC + loud warning (documented behavior).
- **No frontend UI changes.** Frontend status-badge gap for `SourceStatus.CANCELLED` is called out (phase-3 §Risk 5) as a follow-up, not in scope.
- **No new agent types.** `ari` mandatory + `leader` + `jober` desirable only; no other agent receives the scheduling category in this plan.
- **No change to the 5-second retry loop** (`SCHEDULER_ERROR_RETRY_S=5.0` at `daemon/constants.py:236`). Loop-removal is a separate decision; flagged in phase-1 §Task 3.3 note.
- **No changes to `daemon/scheduler.py` constants.** All new config lands under `ENSEMBLE_SCHEDULING_*` prefix (auto-covered by `_TRACKED_ENV_PREFIXES`).

---

## Tracking

- Created: 2026-10-01 (Rev 1)
- Last Updated: 2026-10-01 (Rev 2 — reconciled per architecture-recommendation.md §8)
- Status: Reconciled (Rev 2). Synthesis of 5 phase files; house-style exemplar: `.agents/shared/planning/job-system-improvements/plan-overview.md`.
- Branch: `feature/scheduled-tasks`
- Rev 2 changes: see "Errata trail" block at top of this file. Every contradiction with architecture-recommendation.md §8 resolved; OD register closed (OD-1/2/3/4/5/6/7); D1-D10 + ADR substance intact.
- Coherence sweep: performed 2026-10-01 (Rev 1) and re-verified 2026-10-01 (Rev 2); results in `Cross-Phase Dependencies & Merge Order` section.
- Errata applied in phase files (across Rev 1 + Rev 2): phase4-plan.md line 32 CATEGORY_MODULES citation (Rev 1); idempotency key source correction in phase1 §Task 3 + plan-overview §Executive Summary + ADR-004; atomic cancel + clobber guard in phase1 §Task 7; anchor_local_to_utc helper in phase1 §Task 9; adapter refresh rewrite in phase2 §Task 1.7-1.8; phase2↔phase3 contract reconciliation (phase2 canonical, weekday=Sun=0, `timezone`/`cron_expression` names); runtime wiring in phase2 §Task 6; REST list filter in phase3 §Task 4; OD-1 dropped + Task 6 deleted in phase1; phase2+4 = ONE PR; phase-5 pack = merge gate (no xfail/skip parking).
- Open decisions: see `decisions.md` OD-8 (5-second retry loop removal, deferred — future commission). OD-1/2/3/4/5/6/7 ratified closed by architecture §2; OD-8 deferred.
