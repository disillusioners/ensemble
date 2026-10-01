# Phase 1: Foundation — Config, TZ Helpers, Scheduler Adapter Touchpoints

## Objective

Land the configuration substrate (`SchedulingConfig` + tz resolver + host-local detector), close the **D4 double-dispatch window** for one-time schedules, implement the **D3 lateness cap**, pin run-at hygiene, define the **cancel-state** representation that survives restart, and document the **DST semantics** that the phase-5 test worker pins. This phase is the foundation phase 2 (shared scheduling service + agent tools) builds on top of. No new tables; no new dispatch loop; no daemon restart.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**:
  - `daemon/config.py` (all phases read `SchedulingConfig` fields)
  - `daemon/services/scheduling_service.py` (phase 2 NEW; uses `SchedulingConfig.default_timezone` + `resolve_timezone()` from this phase)
  - `daemon/tools/scheduling.py` (phase 2 NEW; calls `resolve_timezone()` to surface local+UTC)
  - `daemon/services/instance_messaging.py` (NEW parameter `idempotency_key` — both phase-2 caller and downstream JobItem at-most-once depend on it)
  - `daemon/manager.py` (`enqueue_message_job` wrapper signature — additive only)
  - `daemon/repositories/source/models.py` (additive `SourceStatus.CANCELLED` enum value only — NO column change; the `planned_run_at` defense-in-depth migration was DROPPED per architecture §2 OD-1)
- **Shared APIs/interfaces**:
  - `SchedulingConfig.default_timezone: str | None` (env `ENSEMBLE_SCHEDULING_DEFAULT_TZ`)
  - `SchedulingConfig.one_shot_max_lateness_seconds: int | None` (env `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS`)
  - `daemon.utils.tz.resolve_timezone(explicit: str | None, *, for_tool: bool = False) -> tuple[ZoneInfo, str]` — returns `(zone, warning_or_empty)`; `for_tool=True` surfaces the "fell back to UTC" warning into tool output
  - `daemon.utils.tz.detect_host_local_timezone() -> str | None` — cached host-IANA detector
  - `daemon.sources.adapters.scheduler.SchedulerAdapter._route_via_job_queue` — derives `idempotency_key = f"scheduler:{self.source_id}:{self._run_at.isoformat()}"` in-method (gated to `SCHEDULE_TYPE_ONE_TIME`) and passes `enqueue_message_job(..., idempotency_key=...)`; NO new kwargs upstream of `_route_via_job_queue` (Task 3.1 REMOVED the threading).
- **Why this coupling**: SchedulingConfig + tz resolver are the cross-cutting spine every later surface (service, tools, REST) reads. The D4 fix changes the message-dispatch contract that phase 2's service and the REST mirror both consume. The cancel-state enum extension gates the boot-filter skip clause that keeps cancelled rows from firing after restart.

## Context

The current scheduler adapter (`daemon/sources/adapters/scheduler.py`) reads its full config in `__init__` (`scheduler.py:99`), timezone included, and re-applies it only on `__init__`. The whole tz substrate is in two places: `daemon/tools/time.py:26-28` (`zoneinfo.ZoneInfo(timezone_str)`) and `daemon/sources/adapters/scheduler.py:10,123,126` — neither has a host-local detector, neither has a config-driven default, neither has a "fell back to UTC" warning that reaches tool output. There is **no `SchedulingConfig`** anywhere (`grep` of `config.py` and `config.yaml` returns zero hits). A user-stated local time currently has no canonical home — it dies at parse time as either `config.timezone` (cron path) or `config.run_at` (one-time path), with the tz-trap at `scheduler.py:232-235` (naive ISO → assumed UTC) and the **dead re-anchor branch at `scheduler.py:483-484`** (naive run_at re-anchored to adapter timezone — dead code because `__init__` already re-anchored at :234-235).

**D4 — verified:** the firing sequence `_run_schedule` (`scheduler.py:385-452`) → `_emit_scheduled_message` (`:430`) → `execution_callback("triggered")` (`:829`, fire-and-forget `run_in_executor` `registry.py:490-496`) → `_route_via_job_queue` (`:689-690`) → `manager.enqueue_message_job(source="scheduler")` (`:762-769`, **no `idempotency_key` parameter passed**) → one-time disable-write `update_source_status("stopped") + update_source_config(enabled=False)` (`registry.py:506-507`, honored by boot filter at `registry.py:281-294`) leaves a **crash window**: if the daemon dies between `enqueue_message_job` and the disable-write callback, the schedule re-fires on next boot with `_is_one_time_executed=False` (`scheduler.py:150` reset) and produces a second JobItem.

**D4 also exposes a parallel hazard:** the unbounded 5s retry loop for one-shot dispatch failures (`scheduler.py:447-450` + `:697-709` + `SCHEDULER_ERROR_RETRY_S` constant at `daemon/constants.py:236`). For a one-time schedule with run_at still past-due, the loop will keep re-attempting — and with **no** `idempotency_key` on the dispatch path, each attempt would mint a fresh JobItem even *within* a single daemon lifetime. The same `idempotency_key` fix that closes the cross-restart window also closes this intra-lifetime window.

**D3 — verified:** the past-due one-time branch (`scheduler.py:486-487`) currently always fires when `run_at <= now`. There is no cap, no execution-history marker for "skipped due to lateness", and no surface that distinguishes "ran late" from "ran on time". Existing `schedule_executions` rows are written only via the `execution_callback` chain (`scheduler.py:829`, `:777-787`); no caller has ever written a `SKIPPED` row from inside the adapter loop.

**Cancel-state — verified gap:** `SourceStatus` enum (`daemon/repositories/source/models.py:20-25`) has four values — `STOPPED`, `STARTING`, `RUNNING`, `ERROR`. There is **no `CANCELLED`**. Boot filter at `registry.py:281-294` only skips `enabled=False` and `status == SourceStatus.STOPPED.value`. Pause/resume uses status `stopped ↔ running`. There is no way to mark a row terminal-but-not-pausable. `delete_source_config` (`repository.py:263-295`) is unfit because it **purges `schedule_executions`** (`:281-284`).

**DST — verified:** zero manual DST arithmetic anywhere in `scheduler.py` or `daemon/tools/time.py`. croniter `>=3.0.0` (pinned in `pyproject.toml:25`) is the only DST-aware component. Cron computations pass aware datetimes (`scheduler.py:210-211`, `:466-467`); croniter's own behavior on spring-forward gaps / fall-back ambiguity is what the user gets. **No test pins that behavior today.**

**🔴 One-shot anchor gap (architecture §4.2):** the existing parse trap at `scheduler.py:232-235` uses `replace(tzinfo=utc)` for naive ISO inputs. For ONE-SHOT schedules with naive local times in the spring-forward gap, this applies the **pre-transition offset** to a nonexistent wall-clock time (naive `2026-03-08 02:30 America/New_York` → `07:30Z` EST, an hour early). The fix is `daemon.utils.tz.anchor_local_to_utc` (Task 9) — the croniter path is unaffected because it operates on `aware` datetimes via cron expressions.

## Tasks

### Task 1: Add `SchedulingConfig` section (env-driven, config.yaml-mirrored)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 1.1 | Add `SchedulingConfig` class | New `class SchedulingConfig(BaseSettings)` with `model_config = SettingsConfigDict(env_prefix="ENSEMBLE_SCHEDULING_")`. Fields: `default_timezone: str \| None = Field(default=None, description="Default IANA timezone for user-stated local times. None = use host-local tz detector, UTC fallback with loud warning.")`; `one_shot_max_lateness_seconds: int \| None = Field(default=None, description="Max lateness (seconds) for one-shot schedules past run_at. None = unlimited (current behavior preserved). Beyond cap = no dispatch, write SKIPPED row.")`; `tz_warning_echo_to_tool_output: bool = Field(default=True, description="When tz resolves to UTC fallback, include the warning in tool/list/REST output.")`; `host_local_tz_cache_seconds: int = Field(default=300, description="Cache duration for host-local tz detector.")` | `daemon/config.py` (NEW section) |
| 1.2 | Mount `SchedulingConfig` in main config | `scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)` next to the existing `job_system` mount (`config.py:2754`). Mirror order with neighbors; line position is cosmetic. | `daemon/config.py` |
| 1.3 | Add config.yaml mirror block | `scheduling:` block under `job_system:` (~line 351 of `config.yaml`); documented values mirror `SchedulingConfig` defaults. The YAML block is reference-only (pydantic-settings reads env + this class), so a stale block is harmless — keep it consistent for discoverability. | `config.yaml` |
| 1.4 | Smoke-test that `SchedulingConfig` is import-resolvable | Add a tiny unit test that `from daemon.config import SchedulingConfig` works and that `SchedulingConfig(default_timezone="UTC").default_timezone == "UTC"`. | `tests/unit/test_scheduling_config.py` (NEW) |

```python
# daemon/config.py — exact shape
class SchedulingConfig(BaseSettings):
    """Configuration for the scheduled-tasks feature.

    Scheduling is layered on top of the existing scheduler adapter
    (daemon/sources/adapters/scheduler.py) — this class only adds
    daemon-wide knobs. Per-schedule settings (cron expression,
    run_at, agent, message, etc.) live in source_configs.config.
    """

    model_config = SettingsConfigDict(env_prefix="ENSEMBLE_SCHEDULING_")

    default_timezone: str | None = Field(
        default=None,
        description="Default IANA timezone for user-stated local times. "
                    "None = use host-local tz detector, UTC fallback with loud warning.",
    )
    one_shot_max_lateness_seconds: int | None = Field(
        default=None,
        description="Max lateness (seconds) for one-shot schedules past run_at. "
                    "None = unlimited (current behavior preserved). "
                    "Beyond cap = no dispatch, write SKIPPED schedule_executions row.",
    )
    tz_warning_echo_to_tool_output: bool = Field(
        default=True,
        description="When tz resolves to UTC fallback, include the warning in tool/list/REST output.",
    )
    host_local_tz_cache_seconds: int = Field(
        default=300,
        description="Cache duration (seconds) for host-local tz detector. 0 disables cache.",
    )
    negative_cache_seconds: int = Field(
        default=60,
        description="Cache duration (seconds) for NEGATIVE host-local tz results (no detection). min(60, host_local_tz_cache_seconds) applied. Architecture §4.4.",
    )
```

> **Note:** `SchedulingConfig` is a daemon-wide knob, NOT a per-schedule knob. Per-schedule tz (when user explicitly supplies one) is stored in `source_configs.config.timezone` (existing key, already read by adapter at `scheduler.py:121-126`).

### Task 2: TZ resolution helpers (NEW `daemon/utils/tz.py`)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 2.1 | `detect_host_local_timezone()` (NEW, cached) | Robust **stdlib-only** approach (architecture §4.3 — NO third-party detector, no `tzlocal`, no `tzdata` dep): read `/etc/localtime` symlink target and walk the relative path against `/usr/share/zoneinfo/` to recover the IANA name (handles the common case where `/etc/localtime -> /usr/share/zoneinfo/Asia/Ho_Chi_Minh`); fall back to the `TZ` environment variable if set; return `None` when nothing resolves. Wrap result in `lru_cache(maxsize=1)` keyed on a `(cache_seconds, monotonic_now // cache_seconds)` tuple so the cache honors `SchedulingConfig.host_local_tz_cache_seconds`. **Two-tier caching (architecture §4.4):** positive results cache for `host_local_tz_cache_seconds` (default 300s); negative results (no resolution) cache for a **shorter `negative_cache_seconds` window (~60s, or `min(60, host_local_tz_cache_seconds)`)** so an operator fixing `/etc/localtime` mid-process isn't hidden for 5 minutes. | `daemon/utils/tz.py` (NEW) |
| 2.2 | `resolve_timezone(explicit, *, default, for_tool=False)` (NEW) | Implements D2's resolution order: (1) `explicit` if non-None (caller-supplied) → validate via `ZoneInfo` → `KeyError` → warn + UTC + `warning_message = "Invalid tz '{explicit}', fell back to UTC"`; (2) `default` if non-None (caller can pre-fill from `SchedulingConfig.default_timezone`) → same ZoneInfo validation; (3) `detect_host_local_timezone()`; (4) **terminal fallback = `datetime.timezone.utc`** (NOT `ZoneInfo('UTC')` — architecture §4.3: on a stripped container `ZoneInfo('UTC')` raises `ZoneInfoNotFoundError`; `datetime.timezone.utc` is C-level and cannot fail). `warning_message = "No host tz detected, fell back to UTC"`. Returns `tuple[ZoneInfo | datetime.tzinfo, str]` — second element is empty on clean resolution, warning on fallback. `for_tool=True` keeps the warning visible; `for_tool=False` (adapter internal) returns empty so logs stay quiet unless caller passes `for_tool=True`. No third detector (no `tzlocal` — not a declared dependency, contradicts ADR-002 zero-dep posture). | `daemon/utils/tz.py` (NEW) |
| 2.3 | Unit tests for tz resolver | 12+ cases: explicit UTC, explicit valid IANA (`"America/New_York"`), explicit invalid (`"Atlantis/Lemuria"` → fallback + warning), explicit `None` + config `None` + `/etc/localtime` symlink mocked, explicit `None` + config `None` + no symlink (container minimal) → UTC + warning, `TZ` env honored when set, cache hit/miss semantics via `monkeypatch`. | `tests/unit/test_tz_resolver.py` (NEW) |
| 2.4 | Wire resolver into scheduler adapter `__init__` | Replace the current 4-line block at `scheduler.py:121-126` (`ZoneInfo(timezone_str) → KeyError → warn+UTC`) with a single call: `self._timezone, _tz_warning = resolve_timezone(timezone_str, default=SchedulingConfig.default_timezone())`. Keep the per-config `timezone` precedence (explicit `config.timezone` > adapter-level default) — the resolver already implements this via `explicit` vs `default` args. | `daemon/sources/adapters/scheduler.py` |

```python
# daemon/utils/tz.py — public surface (not the whole file)
from __future__ import annotations

import os
import time as _time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def detect_host_local_timezone() -> str | None:
    """Resolve host-local IANA tz name via /etc/localtime symlink + TZ env fallback.

    Returns None if no IANA name is resolvable (e.g. minimal containers
    where /etc/localtime is missing and TZ is unset). Robust stdlib only —
    NO tzdata package dependency (project does not declare one; pyproject.toml
    verified zero hits).
    """
    # Implementation: /etc/localtime symlink target → relative-to-zoneinfo match
    # → TZ env fallback → None on miss. Stdlib-only (no tzlocal, no tzdata).
    # Full body in implementation; the docstring is the contract.


def resolve_timezone(
    explicit: str | None,
    *,
    default: str | None = None,
    for_tool: bool = False,
) -> tuple[ZoneInfo, str]:
    """Implement D2's tz resolution order. Returns (zone, warning_message).

    warning_message is empty on clean resolution. for_tool=True surfaces
    the warning into tool/REST output (caller decides); for_tool=False
    is for internal adapter use where the warning is logged separately.
    """
    ...
```

> **Note (non-goal, mandatory):** do NOT add `tzdata` as a dependency. Project uses system tz db via `zoneinfo`. Minimal containers that strip `/etc/localtime` will fall back to UTC + warning — that IS the documented behavior, not a bug.

### Task 3: D4 idempotency fix — close the double-dispatch window

This is the **critical item** of phase 1. The chosen approach is **option (ii) — reuse `JobItem.idempotency_key`** as the primary mechanism, because (a) zero schema migration risk (PG-only DDL is a known hazard — see `daemon/migrations/` SQLite-compat requirement), (b) reuses the already-tested at-most-once infrastructure at `daemon/repositories/job_queue/repository.py:476-489` + `daemon/services/job_queue_service.py:1232-1236` (the partial UNIQUE index `idx_job_idempotency` at `daemon/repositories/job_queue/models.py:282-306` enforces single-row insertion), (c) aligns with D1 "reuse not rebuild".

The research verified that `create_or_get_by_idempotency_key` returns the existing JobItem without re-firing `dispatch_bus.notify_new_job` (`job_queue_service.py:1232-1236` is gated on `created and job is not None`), so option (ii) gives **at-most-once dispatch**, not just at-most-once-row.

**🔴 CRITICAL — key value source (architecture §1.2):** The key MUST be composed from `self._run_at` — the configured `run_at` parsed once in `__init__` (`scheduler.py:114`, re-parsed identically every boot at `:232-235`). Using `next_trigger` (the value returned from `_get_next_trigger_time()`) is **WRONG** because that method returns `now` for past-due one-shots (`scheduler.py:486-487: if run_at <= now: return now`), which would mutate the key on every adapter cycle AND every boot — making the dedup never match across restart → D4 fix is a no-op. The corrected shape is:

```python
idempotency_key = f"scheduler:{self.source_id}:{self._run_at.isoformat()}"
```

Note `scheduler.py:683` already writes `self._run_at.isoformat()` into the JobItem metadata — the key can be derived inside `_route_via_job_queue` (`:689-690`) without threading a new kwarg through `_emit_scheduled_message`/`_execute_run` at all (simpler diff).

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 3.1 | (REMOVED — no `planned_run_at` kwarg threading) | The key is derived inside `_route_via_job_queue` from `self._run_at.isoformat()` (already in scope at `scheduler.py:683` metadata write). No upstream kwargs change. Eliminates the `next_trigger` source ambiguity (architecture §1.2). | `daemon/sources/adapters/scheduler.py` |
| 3.2 | Compose deterministic `idempotency_key` from `self._run_at` (NOT from `next_trigger`) and pass through dispatcher | Inside `_route_via_job_queue` (`scheduler.py:689-690`), **gated to one-time schedules** (see Task 3.2b): compose `idempotency_key = f"scheduler:{self.source_id}:{self._run_at.isoformat()}"` and add a new kwarg `idempotency_key=idempotency_key` to the `await self._manager.enqueue_message_job(...)` call at `:762-769`. **The key MUST be stable across replay** — using `self._run_at.isoformat()` (parsed once in `__init__` `scheduler.py:114`, re-parsed identically every boot at `:232-235`) gives replay-stable keys. The `next_trigger` value from `_get_next_trigger_time()` MUST NOT be used (architecture §1.2 — returns `now` for past-due one-shots at `:486-487`, mutating the key on every boot and every 5s retry). | `daemon/sources/adapters/scheduler.py` |
| 3.2b | 🔴 CRITICAL — gate key emission to one-time schedules | `_route_via_job_queue` (`:689-690`) is shared by cron AND one-shot paths (gated only by `trigger_type == "scheduled"`). Without a guard, emitting the key unconditionally would collapse **all future cron fires into one JobItem** → silent schedule death. **Mandatory guard:** wrap the `idempotency_key=` argument with `if self._schedule_type == SCHEDULE_TYPE_ONE_TIME:` (constant `SCHEDULE_TYPE_ONE_TIME` defined in `scheduler.py`; cron fires intentionally keep minting fresh JobItems because each fire is distinct work). Architecture §1.3. | `daemon/sources/adapters/scheduler.py` |
| 3.3 | Add `idempotency_key` parameter to `enqueue_message_job` (additive) | New keyword-only kwarg `idempotency_key: str \| None = None` on `enqueue_message_job` at `daemon/manager.py:7854-7887` and on the underlying `daemon/services/instance_messaging.py:2284-2297`. Forward through to `self._messaging_service.enqueue_message_job(...)` (`manager.py:7876-7886`). In `_messaging_service.enqueue_message_job`, forward through to `self._manager._job_queue_service.enqueue(..., idempotency_key=idempotency_key)` at `daemon/services/instance_messaging.py:2612-2629`. **No behavior change for callers that pass `None`** — only the scheduler path passes a real key. | `daemon/manager.py`, `daemon/services/instance_messaging.py` |
| 3.4 | D4 unit test — at-most-one JobItem across replay (key from `self._run_at`) | New test: construct a one-time schedule with `run_at = now + 600s`, capture `self._run_at.isoformat()`, call `_route_via_job_queue` twice (simulating the crash-between-enqueue-and-disable-write window), assert exactly **one** `JobItem` row exists with the deterministic key. Assert the second call's `execution_callback("queued")` (`scheduler.py:777-787`) still fires (for audit) but `_job_queue_service.enqueue` returned the existing job without dispatch_bus notify. **Additional assertion:** the key value equals `f"scheduler:{source_id}:{self._run_at.isoformat()}"` (NOT `next_trigger` value). | `tests/unit/sources/test_scheduler_idempotency.py` (NEW) |
| 3.5 | D4 integration test — restart closes window (key equality across reboot) | Boot a one-time schedule with `run_at = now - 60s`, simulate `_emit_scheduled_message` then `RuntimeError("simulated crash before disable-write")` → adapter exception propagates to `_run_schedule` outer catch (`:447-450`) → sleep 5s → loop re-enters → `_get_next_trigger_time` returns `now` (run_at still past-due). Restart the adapter (simulating daemon restart — fresh `_is_one_time_executed=False`). **Assert key equality across the reboot:** the key composed in the second run equals `f"scheduler:{source_id}:{self._run_at.isoformat()}"` byte-for-byte (NOT `now.isoformat()`). Assert exactly one JobItem exists. | `tests/integration/test_scheduler_d4_restart.py` (NEW) |
| 3.6 | D4 cron-path test — `idempotency_key` NOT emitted for cron | New test `test_cron_not_affected_by_idempotency_key` (architecture §1.3): construct a recurring schedule, call `_route_via_job_queue` with two distinct `next_trigger` values (simulating two cron fires), assert **two** distinct JobItems are created (cron fires intentionally keep minting fresh JobItems). This pins the `SCHEDULE_TYPE_ONE_TIME` guard at Task 3.2b. | `tests/unit/sources/test_scheduler_idempotency.py` (extend) |

> **Note (non-goal, mandatory):** the bounded 5s retry loop (`scheduler.py:447-450` + `:697-709` + `SCHEDULER_ERROR_RETRY_S=5.0` at `daemon/constants.py:236`) is **NOT removed** by this phase. The fix changes the *consequence* of a retry (no second JobItem, because of `idempotency_key`), but the loop behavior stays. Removing the loop is a separate decision and depends on the host's tolerance for repeated attempts of a failing one-shot. The bounded retry now degrades gracefully (returns existing JobItem) instead of doubling work.

### Task 4: D3 lateness cap at past-due one-time branch

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 4.1 | Compute `lateness` and check cap | In `_get_next_trigger_time` (`:454-490`), at the past-due branch (`:486-487`), before returning `now`, compute `lateness = (now - run_at).total_seconds()` and compare against `SchedulingConfig.one_shot_max_lateness_seconds()`. If `lateness > cap`, return a sentinel `PAST_LATENESS_CAP` (module-level constant string) instead of `now`. The `_run_schedule` loop at `:392-400` checks for the sentinel and branches to a new `_record_skipped_execution(reason="past_lateness_cap", lateness_seconds=lateness)` helper. | `daemon/sources/adapters/scheduler.py` |
| 4.2 | `_record_skipped_execution` helper (NEW) | Synchronous helper that writes a `schedule_executions` row directly via `source_repo.record_execution_complete(execution_id="skipped-<uuid>", schedule_id=self.source_id, status="skipped", error_message=f"past_lateness_cap: lateness={lateness}s, cap={cap}s")`. Status enum already includes `SKIPPED` (`models.py:38`). The execution_id prefix `skipped-` distinguishes cap-skips from real runs in list output. **Does NOT call `on_complete_callback`** — schedule is NOT disabled; one-shot remains armed in case the user wants to update `run_at` to a future time. | `daemon/sources/adapters/scheduler.py` |
| 4.3 | Surface skipped rows in list | `task_schedule_list` (phase 2) and `GET /api/schedules/{id}/executions` (phase 3/5) already iterate `schedule_executions`; the `SKIPPED` status enum value is already valid (`models.py:38`). Add a `skip_reason` annotation in the list projection (phase 2's service is responsible for this) by reading `error_message` from the execution row. | `daemon/services/scheduling_service.py` (phase 2 owner; phase 1 only defines the row shape) |
| 4.4 | D3 unit test — cap blocks dispatch, records SKIPPED | Test with `one_shot_max_lateness_seconds=60`, schedule `run_at = now - 120s`. Assert no `JobItem` is created and one `schedule_executions` row exists with `status="skipped"`, `error_message` starting with `past_lateness_cap:`. | `tests/unit/sources/test_scheduler_d3_cap.py` (NEW) |

> **Note:** the existing behavior for `run_at` in the past is preserved when `SchedulingConfig.one_shot_max_lateness_seconds is None` (default). D3 is additive — old schedules behave identically unless the operator opts into the cap.

### Task 5: run-at hygiene + dead-code documentation

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 5.1 | Document the parse trap and dead branch | Add a comment block above the dead re-anchor at `scheduler.py:483-484` flagging it as DEAD CODE (naive run_at is already assumed-UTC at `:234-235`, so the re-anchor at `:483-484` only fires when `__init__` was bypassed, which doesn't happen in production paths). Recommend deletion in a fast-follow. The phase-5 test worker pins the aware-only contract. | `daemon/sources/adapters/scheduler.py` (comment-only edit) |
| 5.2 | Assert all NEW surfaces emit tz-qualified ISO | Phase 2 / phase 3 enforce this — phase 1 documents the requirement in `daemon/utils/tz.py` module docstring (Task 2.2 implicit). No code change in phase 1; the contract is captured here for the contract-pin test in phase 5. | `daemon/utils/tz.py` (docstring), `.agents/shared/planning/scheduled-tasks/decisions.md` (OPEN DECISION item — see Risks) |
| 5.3 | `cancelled_at` config-JSON marker (deferred) | NOT landed in phase 1. The phase-2 cancel-state uses an enum extension (`SourceStatus.CANCELLED`), not a config marker. See Task 7. | — |

### Task 6: [DELETED — architecture §2 OD-1 ratified DROP]

The original Task 6 ("Defense-in-depth — `schedule_executions` unique constraint on `(schedule_id, planned_run_at)`") was the proposed stretch / fast-follow for D4 hardening. Architecture review §2 OD-1 closed this decision as **DROP, permanently** — the only failure mode the migration would close that `idempotency_key` does not is a *different* schedule row claiming the same `(schedule_id, planned_run_at)`, which is **structurally unreachable today** (one adapter = one config row = one `run_at`). Both verification workers independently converged on drop; the migration solves a problem D4 does not pose, and keeping it on the critical path would add zero-migration-cost savings that aren't needed.

The "OPTIONAL planned_run_at column" clause is also removed from plan-overview.md and decisions.md (architecture §2 OD-1). The original §Task 6 content above is preserved here for historical traceability but is NOT to be implemented.

### Task 7: Cancel-state representation — enum extension + boot-filter + atomic repository method + clobber guard

> **🔴 CRITICAL (architecture §3.2/§3.3):** The original Task 7.3 claimed "same transaction" for the cancel write — that claim is **FALSE today**. `update_source_config` (`repository.py:98-145`) and `update_source_status` (`repository.py:237-261`) each open their own `Session` and commit independently. A second problem: `stop_adapter` (`registry.py:652-664`) unconditionally writes `SourceStatus.STOPPED.value` — any later stop touching a cancelled row makes the row **resumable via `/start`** (architecture §3.3 clobber class). Both must be closed in this task.

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 7.1 | Add `CANCELLED = "cancelled"` to **BOTH** `SourceStatus` enum sites | The enum is defined in two locations: `daemon/models/source.py:8-14` (lowercase str-Enum used by Pydantic / REST) and `daemon/repositories/source/models.py:20-25` (uppercase str-Enum used by SQLModel). **Both must gain the new value**, and `SourceStatus.is_valid()` (`:250`) gates the write path — the enum lands BEFORE any service code writes `'cancelled'`. New value is Python-only (no migration). Existing set-membership checks (`status in {STOPPED, STARTING, RUNNING, ERROR}`) silently miss CANCELLED — audited in phase-1 §Risk 5. | `daemon/models/source.py`, `daemon/repositories/source/models.py` |
| 7.2 | Extend boot-filter skip clause | At `daemon/sources/registry.py:292`, rewrite the equality to: `status in {SourceStatus.STOPPED.value, SourceStatus.CANCELLED.value}`. Three sequential `continue`s at `:281-294` mean the single equality at `:292` is the only site needing change. Cancelled rows never auto-start on boot — same gate as stopped. | `daemon/sources/registry.py` |
| 7.3 | **🔴 NEW repository method `cancel_source_config(source_id)` — atomic single-session write** | Architecture §3.2: today there is **no existing method** that writes both `enabled=False` AND `status='cancelled'` atomically — `update_source_config` and `update_source_status` are independent sessions. New repository method opens ONE `Session`, sets both fields on the row, commits, refreshes, returns. ~10 lines. Phases 2 + 3 call this method instead of the two-call sequence. Cost: ~10 lines; benefit: honest implementation of the plan's own atomicity contract. | `daemon/repositories/source/repository.py` |
| 7.4 | **🔴 Clobber guard in `stop_adapter` (`registry.py:652-664`)** | Architecture §3.3: when `stop_adapter` runs on a cancelled row, the unconditional `update_source_status(source_id, SourceStatus.STOPPED.value)` at `:662` resurrects the row to `stopped` (resumable via `/start`). Guard: skip the status persist when `config.status == 'cancelled'` — return success (the row is already terminal). The eviction at `:661` still runs (so a fresh `start_adapter` finds no adapter). Optionally make `cancel_schedule` the SOLE writer of `'cancelled'` (no other path writes it). | `daemon/sources/registry.py` |
| 7.5 | Phase-2 cancel writes via atomic method + adapter eviction | When phase 2's `cancel_schedule` runs: (1) evict the registry entry FIRST (architecture §5.2 — closes the eviction-after-stop window where a concurrent trigger can still reach a draining adapter), then `await adapter.stop()` (30s grace `constants.py:235`); (2) call new `repository.cancel_source_config(source_id)` (Task 7.3) for atomic write; (3) **does NOT call `delete_source_config`** (would purge `schedule_executions` history at `repository.py:281-284`). A fresh `start_adapter` after restart will fail because the boot filter (Task 7.2) skips the row. | `daemon/services/scheduling_service.py` (phase 2 owner; contract pinned here) |
| 7.6 | Echo `last_execution_id` from cancel response | Architecture §5.3: a trigger that already enqueued is outside the adapter's cancel surface. Echo the last `execution_id` from `schedule_executions` in the cancel response so operators can cancel the in-flight `JobItem` directly. Document in module docstring that a cancel racing a due fire may still see one final execution (the `triggered` row lands in history per M5 — executions are read by `schedule_id` with no status join, `schedules.py:391-458`, which is correct: history SHOULD show the race outcome). | `daemon/services/scheduling_service.py` |
| 7.7 | Test — cancelled schedule never fires (boot + clobber + smoke) | Three test functions: (a) create one-time schedule `run_at = now + 30s`, immediately `cancel_schedule(id)`, sleep 35s, assert no `JobItem` row created; (b) restart the adapter (simulate boot) — assert same; (c) `test_stop_adapter_does_not_clobber_cancelled`: cancel a schedule, call `stop_adapter` again, assert `status == "cancelled"` (NOT `stopped`); (d) `test_cancelled_never_boot_starts`: cancel a schedule, restart, assert no adapter is created. | `tests/integration/test_scheduler_cancel_terminal.py` (NEW) |

### Task 8: DST semantics — documentation + croniter contract pin

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 8.1 | Document croniter behavior | In `daemon/sources/adapters/scheduler.py` module docstring, add a `## DST semantics` section. State the chosen contract: croniter (>=3.0.0) is the sole DST-aware component. For daily/weekly cron, the schedule key is interpreted in the schedule's `timezone` (existing key, `scheduler.py:121-126`); croniter returns the next fire as an aware datetime. **Spring-forward gap** (e.g. America/New_York 02:30 on the day DST starts): croniter skips the gap and fires at the next valid local time (e.g. 03:30 EDT). **Fall-back ambiguity** (e.g. America/New_York 01:30 on the day DST ends): croniter picks the first occurrence (EDT, UTC-4) — disambiguation by croniter default, documented as the pinned semantic. | `daemon/sources/adapters/scheduler.py` (docstring) |
| 8.2 | Phase-5 contract test pin (NOT authored here) | The phase-5 test worker authors the actual DST tests. Phase 1 pins the contract (8.1) so the phase-5 worker writes tests against a documented semantic. | `.agents/shared/planning/scheduled-tasks/decisions.md` (OPEN DECISION item — see Risks) |

> **Note (non-goal, mandatory):** phase 1 does NOT write DST tests — that's phase 5. Phase 1 only pins the semantic so phase 5's tests assert against documented behavior.

### Task 9: `anchor_local_to_utc` — one-shot anchor helper (architecture §4.2)

> **🔴 CRITICAL (architecture §4.2):** Python stdlib `naive.replace(tzinfo=tz)` applies the **pre-transition offset** to a nonexistent wall-clock time — naive `2026-03-08 02:30 America/New_York` becomes `07:30Z` (EST), an instant that corresponds to no real local time and fires an hour "early". Without this helper the feature ships a D2 violation on day one.

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 9.1 | `daemon.utils.tz.anchor_local_to_utc(naive_local: datetime, tz: ZoneInfo) -> tuple[datetime, str]` | Three-rule implementation: (1) **already-aware** → trust caller, return `(aware, "")`; (2) **ambiguous (fold)** → use `fold=0`, the first occurrence — matches croniter default and ADR-008, one DST rule for the whole feature; (3) **nonexistent (gap)** → shift forward by the gap duration and emit a loud `tz_warning="shifted-forward from nonexistent local time {naive_local} (gap)"`. Returns `(aware_utc_or_local, warning)`. Caller decides which anchor to use (canonical = local-aware, then `.astimezone(ZoneInfo("UTC"))` for storage). Use `datetime.fold` and `tz.utcoffset()` to detect the gap/ambiguous case explicitly — do NOT use `replace(tzinfo=tz)` directly. | `daemon/utils/tz.py` (extend) |
| 9.2 | Use in scheduler adapter one-shot path | In `_emit_scheduled_message` (or wherever naive one-shot `run_at` is anchored), replace any direct `replace(tzinfo=...)` calls with `anchor_local_to_utc(naive, tz)`. Log the gap-warning via the existing logger; surface to tool/REST only if the user used the NEW surface. Existing parse trap at `scheduler.py:232-235` is in scope for this fix. | `daemon/sources/adapters/scheduler.py` |
| 9.3 | Unit tests — both paths covered | Test cases: (a) aware input → returned unchanged + no warning; (b) fold ambiguous (`America/New_York` 2026-11-01 01:30) → returns first occurrence (EDT, UTC-4) + no warning; (c) gap nonexistent (`America/New_York` 2026-03-08 02:30) → returns shifted-forward (03:30 EDT) + warning containing `"shifted-forward"`; (d) non-DST tz (UTC) → no shift, no warning; (e) DST tz `America/New_York` at non-transition date (e.g. 2026-06-15 06:00) → no shift, no warning. | `tests/unit/test_tz_resolver.py` (extend) |

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | The `idempotency_key` change to `enqueue_message_job` (`daemon/manager.py:7854`, `daemon/services/instance_messaging.py:2284`) is a **public-API signature change**. Existing callers that pass positional args past `metadata` would break. | High | Low | Add the parameter as keyword-only with default `None`. Audit `grep -rn "enqueue_message_job("` — every existing call site passes everything positionally before `metadata`; adding a kwarg after `metadata` is safe. Phase 2 test sweep confirms. |
| 2 | `resolve_timezone()` fallback chain (`Task 2.2`) returns UTC + warning on minimal containers (no `/etc/localtime`, no `TZ` env). Users in such containers get unexpected UTC dispatch times. | Medium | Medium | Loud warning echoed into tool/list/REST output (controlled by `SchedulingConfig.tz_warning_echo_to_tool_output=True` default). Surface the warning at the tool-result boundary — the agent sees the warning and can re-issue with explicit tz. Documented in `daemon/utils/tz.py` module docstring. |
| 3 | (RISK CLOSED — Task 6 was deleted per architecture §2 OD-1) | — | — | The defense-in-depth migration is DROPPED permanently. No migration; the only failure mode it would close is structurally unreachable today. |
| 4 | The 5s unbounded retry loop (`scheduler.py:447-450` + `:697-709`) is preserved by Task 3.3 note. For a one-time schedule whose dispatch keeps failing (e.g. agent_id unresolvable), the loop fires repeatedly, each call returning the same JobItem — safe for the JobItem but spammy in logs. | Low | Medium | Documented as known limitation. The fix is `idempotency_key` (no duplicate work) but not loop removal. Loop-removal is a separate decision. |
| 5 | The `SourceStatus.CANCELLED` enum extension (`Task 7.1`) is a NEW terminal state. Any caller that does `status in {STOPPED, STARTING, RUNNING, ERROR}` (set membership check, NOT enum iteration) silently misses CANCELLED. | Medium | Medium | Audit `grep -rn "SourceStatus\."` for set literals; convert to `SourceStatus.__members__` or explicit enumeration. Boot filter (Task 7.2) is the most critical site — explicitly extended. |
| 6 | Phase 1's DST semantics (Task 8.1) are pinned but **NOT** tested by phase 1. Phase 5 writes the tests. If phase 1 ships with the croniter-default behavior and phase 5 later disagrees, the contract changes retroactively. | Medium | Low | Decisions log entry: `.agents/shared/planning/scheduled-tasks/decisions.md` records croniter-default as the pinned semantic. Phase 5 cannot re-pin without an OPEN DECISION blockquote and planner sign-off. |

## Acceptance Criteria

- [ ] `SchedulingConfig` is importable from `daemon.config`; defaults match Task 1.1; env override via `ENSEMBLE_SCHEDULING_*` works (`tests/unit/test_scheduling_config.py::test_env_override`).
- [ ] `daemon/utils/tz.py` exists; `resolve_timezone()` returns `(ZoneInfo("UTC"), "fell back to UTC...")` for explicit invalid + `default=None` + no host tz; returns `(ZoneInfo("Asia/Ho_Chi_Minh"), "")` for explicit valid.
- [ ] `detect_host_local_timezone()` returns the correct IANA name for `/etc/localtime -> /usr/share/zoneinfo/America/New_York`; returns `None` when `/etc/localtime` is missing AND `TZ` is unset.
- [ ] The D4 idempotency unit test (`tests/unit/sources/test_scheduler_idempotency.py`) asserts exactly **one** `JobItem` row exists after two `_route_via_job_queue` calls with the same `self._run_at`.
- [ ] The D4 integration test (`tests/integration/test_scheduler_d4_restart.py`) asserts at-most-once JobItem across a simulated daemon restart.
- [ ] D3 lateness cap test (`tests/unit/sources/test_scheduler_d3_cap.py`) asserts no `JobItem` + one `schedule_executions(status="skipped", error_message startswith="past_lateness_cap:")` when `one_shot_max_lateness_seconds=60` and `run_at = now - 120s`.
- [ ] `tests/unit/test_tz_resolver.py::TestAnchorLocalToUtc` covers all 5 cases (aware-trust, fold=0, gap-shift+warning, UTC, non-DST-date). Anchor helper `daemon/utils/tz.py::anchor_local_to_utc` is referenced by the scheduler adapter one-shot path (architecture §4.2).
- [ ] `SourceStatus.CANCELLED` enum value exists in **BOTH** `daemon/models/source.py:8-14` AND `daemon/repositories/source/models.py:20-25`; boot filter at `daemon/sources/registry.py:292` skips `status == "cancelled"`; `tests/integration/test_scheduler_cancel_terminal.py` confirms cancelled one-time never fires across boot; `test_stop_adapter_does_not_clobber_cancelled` passes (architecture §3.3).
- [ ] Existing scheduler test suites (`tests/test_scheduler_adapter.py`, `tests/test_scheduler_api.py`, `tests/test_scheduler_instance_mode.py`) remain GREEN — no regressions.
- [ ] `KNOWN_TOOL_NAMES` drift test (`tests/unit/tools/test_frozen_tool_name_discovery.py`) remains GREEN (phase 1 doesn't add tools, but the test must still pass).
- [ ] No new tables; no daemon restart; `daemon/constants.py` scheduler constants unchanged.

## Constraints

- **No new table.** Per D1, phase 1 does NOT introduce `scheduled_tasks` or any new dispatcher loop. No migrations (Task 6 was deleted per architecture §2 OD-1).
- **No daemon restart.** All changes are import-time; tests prove correctness statically + at first daemon restart after merge.
- **Public-API additive only.** New parameters are keyword-only with default `None`; existing positional callers are not affected.
- **TZ-first surfaces only.** No raw datetime without tzinfo enters the system from a NEW surface. Existing scheduler.py parse trap (`:232-235`) is documented; cleanup deferred.
- **Cancel ≠ delete.** `delete_source_config` is NEVER called by scheduled-tasks code paths. Cancel = `enabled=False` + `status="cancelled"` + adapter eviction.

## OPEN DECISIONS (for `decisions.md`)

> **OPEN DECISION 1:** ~~Task 6 (defense-in-depth migration on `schedule_executions`)~~ — **DROPPED per architecture §2 OD-1 (ratified)**. Task 6 is deleted from this plan; no migration. The only failure mode the migration would close is structurally unreachable (one adapter = one config row = one `run_at`). See Task 6 [DELETED] note above.

> **OPEN DECISION 2:** ~~DST semantics~~ — **CLOSED per architecture §2 OD-2 (ratified): DEFAULT-TO-PHASE-1-DOC**. Task 8.1 pins croniter-default for spring-forward gap (skip the gap) and fall-back ambiguity (first occurrence / pre-DST). If product later wants different semantics (e.g. fire twice on fall-back), the croniter wrapper must override — flag before phase 5 tests land.

> **OPEN DECISION 3 (NEW — from architecture §4.2):** `anchor_local_to_utc(naive_local, tz)` semantics: aware→trust; fold→0 (first occurrence, matches croniter); gap→shift-forward+warning. This is the canonical one-shot anchor rule and MUST match croniter's behavior — one DST rule for the whole feature. Phase-5 gap/fold tests parameterize over BOTH paths (cron + one-shot anchor) so they can't drift. Implemented as Task 9 helper below.

## Key Files

| File | Role |
|------|------|
| `daemon/config.py` | NEW `SchedulingConfig` class; mount in main config |
| `daemon/utils/tz.py` (NEW) | `detect_host_local_timezone()` + `resolve_timezone()` |
| `daemon/sources/adapters/scheduler.py` | Wire resolver at `:121-126`; derive `idempotency_key` from `self._run_at.isoformat()` (one-time-gated) inside `_route_via_job_queue`; D3 cap at `:486-487`; cancel-aware iteration |
| `daemon/services/instance_messaging.py` | Additive `idempotency_key` kwarg on `enqueue_message_job` (`:2284`) and forward at `:2612-2629` |
| `daemon/manager.py` | Additive `idempotency_key` kwarg on wrapper (`:7854`) |
| `daemon/repositories/source/models.py` | Additive `SourceStatus.CANCELLED` enum value |
| `daemon/sources/registry.py` | Extend boot filter at `:290-294` to skip `status == "cancelled"` |
| `config.yaml` | `scheduling:` mirror block |
| `tests/unit/test_scheduling_config.py` (NEW) | Config env/default contract |
| `tests/unit/test_tz_resolver.py` (NEW) | TZ resolver contract |
| `tests/unit/sources/test_scheduler_idempotency.py` (NEW) | D4 unit |
| `tests/integration/test_scheduler_d4_restart.py` (NEW) | D4 across restart |
| `tests/unit/sources/test_scheduler_d3_cap.py` (NEW) | D3 cap enforcement |
| `tests/integration/test_scheduler_cancel_terminal.py` (NEW) | Cancel is terminal across boot |

## Deliverables

- [ ] `SchedulingConfig` class mounted in `daemon.config`
- [ ] `daemon/utils/tz.py` module with `detect_host_local_timezone()` + `resolve_timezone()`
- [ ] Scheduler adapter wires resolver in `__init__`
- [ ] `idempotency_key` threaded through `enqueue_message_job` chain (3 files: scheduler, instance_messaging, manager)
- [ ] D3 lateness cap at past-due branch + `_record_skipped_execution` helper
- [ ] `SourceStatus.CANCELLED` enum value + boot-filter extension
- [ ] DST semantics documented in `scheduler.py` module docstring
- [ ] All acceptance-criteria tests pass; existing scheduler suites stay GREEN
- [ ] OPEN DECISIONS recorded in `.agents/shared/planning/scheduled-tasks/decisions.md`
