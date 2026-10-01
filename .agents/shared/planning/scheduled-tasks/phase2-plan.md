# Phase 2: Shared Scheduling Service + Agent Tools (category "scheduling")

## Objective

Land the shared scheduling service (`daemon/services/scheduling_service.py`) used by BOTH the agent tools (this phase) AND the REST surface (phase 3), and land the four agent-facing tools in a NEW `scheduling` tool category (`daemon/tools/scheduling.py`) wired into `CATEGORY_MODULES` + `KNOWN_TOOL_NAMES`. Every echo surface (create, list, cancel, update) returns BOTH local + UTC. Cancelled is terminal + hidden from default list. Pause/resume uses existing `stopped ↔ running` semantics. Tools never call `POST /api/jobs` — they create/update schedules via the shared service; dispatch stays the adapter's job. No daemon restart; tool registration proven by tests (D9).

## Coupling

- **Depends on**: phase-1 (SchedulingConfig + tz resolver + idempotency_key + SourceStatus.CANCELLED + D3 cap)
- **Coupling type**: tight (with phase-1, phase-3)
- **Shared files with other phases**:
  - `daemon/services/scheduling_service.py` (NEW — phase 2 owns; phase 3 imports)
  - `daemon/tools/scheduling.py` (NEW — phase 2 owns)
  - `daemon/tools/_tool_registry.py` (NEW entry in `CATEGORY_MODULES` dict at `:522-596`; regen `KNOWN_TOOL_NAMES` at `:630-842`)
  - `daemon/sources/registry.py` (phase 2 calls `start_adapter`, `stop_adapter` via the existing seams — no API change)
  - `daemon/repositories/source/repository.py` (phase 2 calls `create_source_config` `:51-96`, `update_source_config` `:108-145`, `update_source_status`, `get_source_config` `:207`, `get_source_config_by_name` `:212`, `list_source_configs` `:218` — no API change)
  - `daemon/config.py` (reads `SchedulingConfig` fields mounted by phase 1)
  - `daemon/utils/tz.py` (NEW — phase 1 owner; phase 2 calls `resolve_timezone()`)
- **Shared APIs/interfaces** (contract pinned by phase 3 — phase2 is the CANONICAL HOME):
  - Module-level async functions on `daemon.services.scheduling_service`:
    - `async def create_schedule(payload: ScheduleCreatePayload | dict, *, caller_instance_id: str, caller_agent_id: str) -> ScheduleCreateResponse` — accepts **either Pydantic model OR `dict`** at the service boundary; if dict, validate via `ScheduleCreatePayload.model_validate(payload)` at function entry (architecture §2 OD-2 closure; covers phase-3 REST caller passing `model_dump()` dict, ensures one validated contract for tools+REST).
    - `async def cancel_schedule(schedule_id: str) -> ScheduleCancelResponse` — returns `{source_id, status, cancelled_at, last_execution_id}` (last_execution_id is NEW per architecture §5.3).
    - `async def get_schedule(schedule_id: str) -> ScheduleDetail | None`
    - `async def list_schedules(*, project_id: str | None = None, status: str | None = None, include_cancelled: bool = False, caller_agent_id: str | None = None) -> list[ScheduleListItem]`
    - `async def update_schedule(schedule_id: str, payload: ScheduleUpdatePayload | dict) -> ScheduleUpdateResponse` — same dict-accept pattern.
- **Why this coupling**: The shared service IS the seam — phase 3 calls the same five functions; phase-5 tests exercise the service directly. Tool surface is a thin closure layer over the service that injects caller identity (instance_id, agent_id) and translates user-stated local time into the canonical config shape.

## Context

The current scheduler surface is adapter-internal and HTTP-only (`daemon/routers/schedules.py`). There is **no service layer** between the HTTP router and `source_repository` + `source_registry`. Every router endpoint does its own DB write + registry manipulation, with duplicated `is_write_paused` checks (`schedules.py:87-88`, `:185-186`, `:255-256`, `:347-348`), duplicated stale-config rebuild seams (`:294-308`), and duplicated tz formatting (none today — REST today returns `next_run_at` without local+UTC echo).

There is **no agent-facing tool** for scheduled tasks. The `job` category (`daemon/tools/job_queue.py`) has `job_create` which schedules an immediate job but no recurring/once-at-time-X semantic. The `time` category (`daemon/tools/time.py:1-78`) only echoes the current time — no scheduling.

`CATEGORY_MODULES` (`daemon/tools/_tool_registry.py:522-596`) is the explicit dispatch dict — adding a new category without an entry here is the #1 silent-failure mode. `KNOWN_TOOL_NAMES` (`:630-842`) is the frozen-binary fallback universe — it MUST be regenerated IN THIS PHASE via the documented command (`:615-618`) and drift-enforced bidirectionally by `tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`.

`PRIVILEGED_TOOL_CATEGORIES` (`:167-171`) is **triple-pinned** by three test files (per the D18/A14 SAME-PR RULE comment) — adding `"scheduling"` here would break the universe. **The new category is non-privileged by design** (D7 grants per-agent via `meta.json` `tools.allow`).

The factory pattern at `daemon/tools/job_queue.py:1142-1151` (`create_job_tools(job_service, ..., current_instance_id, agent_id, ...)`) is the established seam. The agent_id default for caller (i.e. `task_schedule(agent_id=None)` means "the calling agent") comes from the factory closure injection — verified by phase-3 wiring and the existing `create_job_tools` precedent.

`source_repo.create_source_config` (`daemon/repositories/source/repository.py:51-96`) mints `source_id` if None, initializes `status="stopped"` (`:85`). The first `start_adapter` (via `registry.start_adapter` at `daemon/sources/registry.py:574-603`) is what moves it to RUNNING. POST `/api/sources` auto-starts when `enabled=True` (`daemon/routers/sources.py:235-239`, failure swallowed to warning).

Per Contract clause (a), the **table-level name column** (`source_configs.name`, indexed at `models.py:52`) is the schedule label. `get_source_config_by_name` exists at `:212-216`. Per Contract clause (b), `project_id` is a config-JSON key (adapter reads it at `scheduler.py:134`). Per Contract clause (c), recurrence mapping is: `once` → `config.run_at` (tz-qualified ISO), `daily`/`weekly` → generated cron in `config.schedule` + `config.timezone` + human-intent keys preserved (so list shows "daily 06:00 Asia/Ho_Chi_Minh"). Per Contract clause (d), cancel uses `SourceStatus.CANCELLED` (phase-1 enum extension), NOT `delete_source_config`. Per Contract clause (e), tools never call `POST /api/jobs`; they create schedules via shared service. Per Contract clause (f), this phase owns the service; phase 3 reuses it.

## Tasks

### Task 1: Shared scheduling service — `daemon/services/scheduling_service.py` (NEW)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 1.1 | Module surface — Pydantic payload models | Define `ScheduleCreatePayload`, `ScheduleUpdatePayload`, `ScheduleCreateResponse`, `ScheduleCancelResponse`, `ScheduleDetail`, `ScheduleListItem`, `ScheduleUpdateResponse` as `pydantic.BaseModel`. Pinned contract names (phase 3 imports these): `create_schedule(payload, *, caller_instance_id, caller_agent_id)`, `cancel_schedule(schedule_id)`, `get_schedule(schedule_id)`, `list_schedules(*, project_id, status, include_cancelled, caller_agent_id)`, `update_schedule(schedule_id, payload)`. | `daemon/services/scheduling_service.py` (NEW) |
| 1.2 | `create_schedule()` — full create flow | Steps: (1) validate `payload.label` (non-empty, ≤128 chars, unique via `source_repo.get_source_config_by_name(payload.label)` → 409 Conflict if exists); (2) **fail-fast agent existence check** (architecture §2 OD-3, phase-2 §Task 6.4): `agent_registry.exists(payload.agent)` → raise `ValueError("agent_id {X} does not exist")` BEFORE any DB write; (3) resolve tz via `resolve_timezone(payload.timezone, default=SchedulingConfig.default_timezone(), for_tool=True)` (phase 1 helper — canonical `timezone` field name, NOT `tz`); (4) **anchor user-stated local time via `daemon.utils.tz.anchor_local_to_utc`** (architecture §4.2, phase-1 §Task 9): for naive `payload.when` (HH:MM or ISO without offset), the helper applies the pinned rule — aware→trust, fold→0 (first occurrence), gap→shift-forward+`tz_warning="shifted-forward from nonexistent local time …"`; aware `payload.when` is trusted as-is; (5) build `config_json` per recurrence (see Task 1.3) — for `once`, store `run_at` as the anchored tz-qualified ISO + human-intent keys (`recurrence`, `local_time`, `timezone`); (6) `source_repo.create_source_config(source_type="scheduler", name=payload.label, config=config_json, enabled=True, autostart=True)`; (7) `source_registry.start_adapter(source_id)` (best-effort; surface warning if start fails); (8) return `ScheduleCreateResponse(source_id=..., label=..., next_run_at_local=..., next_run_at_utc=..., status=..., tz_warning=...)`. | `daemon/services/scheduling_service.py` |
| 1.3 | Recurrence → config mapping helper | `once` → `config = {"run_at": "<tz-qualified ISO>", "agent": ..., "message": ..., "project_id": ..., "priority": ..., "instance_mode": "new_instance", "recurrence": "once", "local_time": "<user-stated>", "timezone": "<resolved>"}`. `daily` → `config = {"schedule": "0 <HH> * * *", "timezone": "<resolved>", "agent": ..., "message": ..., "project_id": ..., "priority": ..., "instance_mode": "new_instance", "recurrence": "daily", "local_time": "<HH:MM>", "timezone": "<resolved>"}`. `weekly` → `config = {"schedule": "0 <HH> * * <DOW>", ...}` where DOW is 0-6 (Sun=0). `cron` → `config = {"schedule": "<raw cron>", "timezone": "<resolved>", ..., "recurrence": "cron"}` — raw cron escape hatch (D5). | `daemon/services/scheduling_service.py` |
| 1.4 | `cancel_schedule()` — terminal state (architecture §3.2 + §5.2 + §5.3) | Steps: (1) `source_repo.get_source_config(schedule_id)` → 404 if missing or `source_type != "scheduler"`; (2) if `status == SourceStatus.CANCELLED.value` → 409 (already cancelled); (3) **evict registry entry FIRST** (architecture §5.2 — closes the eviction-after-stop window at `registry.py:661` where a concurrent trigger lookup can still reach the draining adapter); (4) `await asyncio.to_thread(source_registry.stop_adapter, schedule_id)` (drains in-flight `_execute_run`, 30s grace `constants.py:235`); (5) `asyncio.to_thread(source_repo.cancel_source_config, schedule_id)` — the **NEW atomic single-session method** from phase-1 §Task 7.3 (writes `enabled=False` + `status='cancelled'` in ONE commit); (6) `schedule_executions` rows preserved (NEVER call `delete_source_config`); (7) **echo `last_execution_id`** from `schedule_executions` (`SELECT MAX(triggered_at)` for the schedule) so operators can cancel an in-flight `JobItem` directly (architecture §5.3); (8) return `ScheduleCancelResponse(source_id=..., status="cancelled", cancelled_at=<now>, last_execution_id=<from step 7> or None)`. | `daemon/services/scheduling_service.py` |
| 1.5 | `get_schedule()` — single schedule with BOTH local + UTC | Steps: (1) `source_repo.get_source_config(schedule_id)` → return None if missing or non-scheduler; (2) parse `config.timezone` → `ZoneInfo`; (3) compute `next_run_at_local` (in `config.timezone`) and `next_run_at_utc` (`.astimezone(ZoneInfo("UTC"))`) using the same logic as `SchedulerAdapter._get_next_trigger_time` (`scheduler.py:454-490`) — for `once`, use `config.run_at`; for `daily/weekly/cron`, use `croniter(config.schedule, datetime.now(ZoneInfo(config.timezone))).get_next(datetime)`; (4) return `ScheduleDetail(source_id=..., label=..., status=..., recurrence=..., local_time=..., timezone=..., next_run_at_local=..., next_run_at_utc=..., agent=..., project_id=..., last_run_at=<from schedule_executions MAX(triggered_at)>)`. | `daemon/services/scheduling_service.py` |
| 1.6 | `list_schedules()` — filterable, cancelled hidden by default | Steps: (1) `source_repo.list_source_configs(enabled=None, status=...)`; (2) filter to `source_type == "scheduler"`; (3) if `include_cancelled == False` (default), filter out `status == SourceStatus.CANCELLED.value`; (4) apply `project_id` filter (config-JSON key match); (5) apply `caller_agent_id` filter if non-None (caller-scoped — match `config.agent`); (6) for each row, compute local+UTC next-run via the same logic as 1.5; (7) return `list[ScheduleListItem]`. | `daemon/services/scheduling_service.py` |
| 1.7 | `update_schedule()` — reschedule / message / pause / resume (architecture §5.1/§5.2) | Steps: (1) `source_repo.get_source_config(schedule_id)` → 404 if missing or non-scheduler; (2) reject if `status == SourceStatus.CANCELLED.value` → 409 (cancelled is terminal); (3) acquire **per-source `asyncio.Lock`** keyed by `source_id` (held across the whole sequence — `SourceRegistry` has no internal per-source lock; without it two concurrent updates can interleave stop/register such that one raises `ValueError` on duplicate slot `registry.py:170-185`); (4) merge payload into existing config: `new_message`, `new_local_time + new_timezone` (rebuild cron + run_at, anchor via `daemon.utils.tz.anchor_local_to_utc`), `paused=True` → `enabled=False, status=SourceStatus.STOPPED.value`; `paused=False` → `enabled=True, status=SourceStatus.STOPPED.value` (let adapter flip to RUNNING on next tick); (5) **evict registry entry FIRST** (architecture §5.2 — closes the eviction-after-stop window at `registry.py:661` where a concurrent trigger lookup can still reach the draining adapter); (6) `await asyncio.to_thread(source_registry.stop_adapter, schedule_id)` (drains in-flight `_execute_run`, 30s grace `constants.py:235`); (7) `source_repo.update_source_config(schedule_id, name=..., config=merged)`; (8) if `paused=False` and `enabled=True`, `source_registry.start_adapter(schedule_id)`; (9) release lock; (10) return `ScheduleUpdateResponse(source_id=..., label=..., next_run_at_local=..., next_run_at_utc=..., status=..., paused=...)`. Document the **<1s rebuild gap** (architecture §5.3) — a missed fire during this brief window is acceptable and the user gets a `triggered` history row from the previous adapter. | `daemon/services/scheduling_service.py` |
| 1.8 | Stale-config seam audit | Architecture §5.1 — the plan's previous claim "reuses the proven seam" was WRONG: the cited `PUT /schedules/{id}` at `schedules.py:251-339` is actually `POST /{id}/start`; the **real PUT is `schedules.py:83-172` and is DB-only** (no adapter rebuild, leaves live adapter stale — pre-existing latent defect). The service **must implement** `evict → stop → mutate → create_adapter_from_config → register → start` itself (Task 1.7). The rebuild is NEW logic, not a reuse. Document the gap in `daemon/services/scheduling_service.py` module docstring. Pin as comment: "PUT today is DB-only; this service is the adapter-rebuild seam." | `daemon/services/scheduling_service.py` (docstring) |
| 1.9 | Service unit tests | Mock `source_repo` + `source_registry`; test create flow with each recurrence type (once/daily/weekly/cron); test cancel rejects already-cancelled + non-existent; test list hides cancelled by default; test update pause→resume round-trip; test update rejects cancelled (409); test tz warning surfaces when tz falls back to UTC. **NEW (architecture §5.1):** test per-source lock contention (two concurrent `update_schedule(same_id)` calls serialize correctly); test rebuild actually creates a fresh adapter (mock `_adapters[source_id]` cleared before start); test <1s rebuild gap documentation surfaces in module docstring. | `tests/unit/services/test_scheduling_service.py` (NEW) |

```python
# daemon/services/scheduling_service.py — public surface (not the whole file)
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel, Field

from daemon.config import SchedulingConfig
from daemon.repositories.source.models import SourceStatus
from daemon.utils.tz import resolve_timezone


# === Payload / response models ===

class ScheduleCreatePayload(BaseModel):
    label: str = Field(..., min_length=1, max_length=128)
    agent: str = Field(..., description="Agent ID to invoke")
    message: str = Field(..., min_length=1)
    when: str = Field(..., description="User-stated local time (ISO 8601 with offset, or HH:MM for daily/weekly)")
    timezone: str | None = Field(default=None, description="IANA timezone (e.g. 'America/New_York'); None = use SchedulingConfig.default_timezone, then host-local, then UTC. CANONICAL name (phase-2 is canonical home; phase-3 REST re-exports).")
    recurrence: str = Field(..., description="once | daily | weekly | cron")
    weekday: int | None = Field(default=None, ge=0, le=6, description="0=Sun..6=Sat for recurrence=weekly (Sun=0 canonical — matches cron DOW; phase-3 REST may surface Mon-Sun per UI convention but maps at boundary)")
    cron_expression: str | None = Field(default=None, description="Required when recurrence=cron. CANONICAL name (phase-2 canonical home).")
    project_id: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    instance_mode: str = Field(default="new_instance", description="new_instance (default) | reuse_instance")

    # CANONICAL HOME for create-payload field names (architecture §5 + cleanup):
    # timezone (not tz), cron_expression (not raw_cron), weekday=Sun=0.
    # Phase-3 REST re-exports / may surface Mon-Sun for UI; service maps at boundary.
    # Pydantic-at-boundary (architecture §2 OD-2): all fields are validated
    # here; phase-3 may pass a dict via model_validate.


class ScheduleCreateResponse(BaseModel):
    source_id: str
    label: str
    status: str
    next_run_at_local: str | None  # ISO in config.timezone
    next_run_at_utc: str | None    # ISO in UTC
    tz_warning: str = ""           # non-empty when tz fell back


class ScheduleCancelResponse(BaseModel):
    source_id: str
    status: str                    # "cancelled"
    cancelled_at: str              # ISO UTC


class ScheduleDetail(BaseModel):
    source_id: str
    label: str
    status: str
    recurrence: str | None
    local_time: str | None
    timezone: str | None
    next_run_at_local: str | None
    next_run_at_utc: str | None
    agent: str | None
    project_id: str | None
    last_run_at: str | None
    tz_warning: str = ""


class ScheduleListItem(ScheduleDetail):
    """Same shape as ScheduleDetail; list surfaces it directly."""


class ScheduleUpdatePayload(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    message: str | None = None
    when: str | None = None
    timezone: str | None = None
    paused: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=10)


class ScheduleUpdateResponse(BaseModel):
    source_id: str
    label: str
    status: str
    paused: bool
    next_run_at_local: str | None
    next_run_at_utc: str | None
    tz_warning: str = ""


# === Service functions (module-level async; phase 3 imports these names verbatim) ===

async def create_schedule(
    payload: ScheduleCreatePayload,
    *,
    caller_instance_id: str,
    caller_agent_id: str,
) -> ScheduleCreateResponse: ...
async def cancel_schedule(schedule_id: str) -> ScheduleCancelResponse: ...
async def get_schedule(schedule_id: str) -> ScheduleDetail | None: ...
async def list_schedules(
    *,
    project_id: str | None = None,
    status: str | None = None,
    include_cancelled: bool = False,
    caller_agent_id: str | None = None,
) -> list[ScheduleListItem]: ...
async def update_schedule(schedule_id: str, payload: ScheduleUpdatePayload) -> ScheduleUpdateResponse: ...
```

> **Note (non-goal, mandatory):** the service does NOT call `POST /api/jobs` and does NOT call `manager.enqueue_message_job` directly. Dispatch is the adapter's job — the service creates schedule rows; the adapter fires them.

### Task 2: Agent tools — `daemon/tools/scheduling.py` (NEW)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 2.1 | Module attrs + factory surface | Module-level `CATEGORY_NAME = "Scheduling"` + `CATEGORY_DOC = """Create, list, cancel, and update scheduled tasks. ... """`. Factory function: `def create_scheduling_tools(scheduling_service, *, current_instance_id: str = "", agent_id: str = "", agent_tag: str \| None = None) -> list`. Returns a list of 4 `@tool`-decorated functions. Closure-injected `caller_agent_id = agent_id` so `task_schedule(agent_id=None)` defaults to caller. | `daemon/tools/scheduling.py` (NEW) |
| 2.2 | `task_schedule` tool | Stacked `@register_tool_category("scheduling") @tool(args_schema=TaskScheduleInput)`. Params: `label: str`, `message: str`, `when: str` (user-stated local time; "2026-10-15 06:00" or "06:00" for daily/weekly, raw cron if `recurrence="cron"`), **`timezone: str \| None`** (canonical name, was `tz`), `recurrence: str = Field(description="once \| daily \| weekly \| cron")`, `weekday: int \| None = Field(default=None, ge=0, le=6)`, **`cron_expression: str \| None`** (canonical name, was `raw_cron`), `agent_id: str \| None = Field(default=None, description="Agent to invoke; default = calling agent")`, `project_id: str \| None`, `priority: int = Field(default=5, ge=1, le=10)`, `instance_mode: str = "new_instance"`. Implementation: build `ScheduleCreatePayload`, call `await scheduling_service.create_schedule(payload, caller_instance_id=current_instance_id, caller_agent_id=agent_id or caller_agent_id)`, return dict with `source_id`, `label`, `status`, `next_run_at_local`, `next_run_at_utc`, `tz_warning`. On `ValueError` from service (e.g. duplicate label, agent-does-not-exist from Task 6.4 fail-fast) → return error string `"ERROR: {msg}"` (house pattern from `time.py:31,61,65-66`). | `daemon/tools/scheduling.py` |
| 2.3 | `task_schedule_list` tool | Params: `status: str \| None = Field(default=None, description="Filter by status (running\|stopped\|cancelled)")`, `project_id: str \| None`, `include_cancelled: bool = Field(default=False)`, `agent_id: str \| None = Field(default=None, description="Filter by agent; default = calling agent")`. Implementation: call `await scheduling_service.list_schedules(status=status, project_id=project_id, include_cancelled=include_cancelled, caller_agent_id=agent_id or caller_agent_id)`, return dict `{"schedules": [...], "count": N}` where each item carries `source_id`, `label`, `status`, `recurrence`, `local_time`, `timezone`, `next_run_at_local`, `next_run_at_utc`, `agent`, `project_id`, `tz_warning`. Cancelled is hidden by default (matches D5). | `daemon/tools/scheduling.py` |
| 2.4 | `task_schedule_cancel` tool | Params: `source_id: str \| None = Field(default=None, description="Schedule ID")`, `label: str \| None = Field(default=None, description="Schedule label (alternative to source_id)")`. Exactly one of `source_id`/`label` must be non-None (return error string if both or neither). Implementation: if `label`, resolve via `source_repo.get_source_config_by_name(label)` (passed via closure); call `await scheduling_service.cancel_schedule(source_id)`, return dict `{"source_id", "status": "cancelled", "cancelled_at", "label"}`. | `daemon/tools/scheduling.py` |
| 2.5 | `task_schedule_update` tool | Params: `source_id: str`, `label: str \| None`, `message: str \| None`, `when: str \| None`, **`timezone: str \| None`** (canonical name), `paused: bool \| None`, `priority: int \| None = Field(default=None, ge=1, le=10)`. Implementation: build `ScheduleUpdatePayload`, call `await scheduling_service.update_schedule(source_id, payload)`, return dict with `source_id`, `label`, `status`, `paused`, `next_run_at_local`, `next_run_at_utc`, `tz_warning`. Returns error string if schedule is cancelled (service raises `ValueError` on cancelled update). | `daemon/tools/scheduling.py` |
| 2.6 | House-style docstrings | Every tool has a `tool._full_doc_ = """..."""` block following the precedent at `daemon/tools/job_queue.py` (e.g. `_full_docs` populated via `register_tool` at `_tool_registry.py:202-256`). Multi-line `Args:` + `Returns:` + `Examples:` following `daemon/tools/job_queue.py:1207-1220` pattern. | `daemon/tools/scheduling.py` |
| 2.7 | Tool unit tests | Test each tool with mocked `scheduling_service`; test echo surfaces BOTH local+UTC; test cancel-by-label resolution; test error-string returns on duplicate label / invalid input / cancelled update. | `tests/unit/tools/test_scheduling_tools.py` (NEW) |

```python
# daemon/tools/scheduling.py — public surface (factory function shape)
from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category

CATEGORY_NAME = "Scheduling"
CATEGORY_DOC = """\
Schedule jobs at a specific time (one-shot or recurring daily/weekly/cron).

Tools in this category:
- ``task_schedule``        — create a scheduled task
- ``task_schedule_list``   — list scheduled tasks (cancelled hidden by default)
- ``task_schedule_cancel`` — cancel a scheduled task (terminal, history preserved)
- ``task_schedule_update`` — update a scheduled task (reschedule, pause, resume)

CRITICAL: A user-stated time (``when``) is interpreted in the caller's local
timezone (``tz``). When ``tz`` is omitted, the daemon-wide default is used
(``ENSEMBLE_SCHEDULING_DEFAULT_TZ``), then the host-local timezone, then UTC
with a loud warning echoed in the response. Every response surfaces BOTH
``next_run_at_local`` (in the resolved tz) AND ``next_run_at_utc`` (UTC).
"""


def create_scheduling_tools(
    scheduling_service: "SchedulingServiceModule",
    *,
    source_repo=None,                    # for cancel-by-label resolution
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the four scheduling tools for one instance/agent.

    ``scheduling_service`` is the module exposing create_schedule / cancel_schedule /
    get_schedule / list_schedules / update_schedule (phase-2 module; this factory
    does NOT call into the message-dispatch path).
    """
    caller_agent_id = agent_id  # closure capture; task_schedule(agent_id=None) → caller
    caller_agent_tag = agent_tag
    ...
```

### Task 3: Wire `scheduling` category — `daemon/tools/_tool_registry.py` (mutations)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 3.1 | Add entry to `CATEGORY_MODULES` | Append `"scheduling": "daemon.tools.scheduling"` to `CATEGORY_MODULES` (`_tool_registry.py:522-596`). Place near `"job"` (`:531`) for readability — both are scheduling-adjacent. | `daemon/tools/_tool_registry.py` |
| 3.2 | Regenerate `KNOWN_TOOL_NAMES` | Run the documented command (`_tool_registry.py:615-618`): `uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"`. Add the four new tool names (`task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update`) to the frozenset at `:630-842`. Insert alphabetically (between existing neighbors). | `daemon/tools/_tool_registry.py` |
| 3.3 | Drift test green | `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` MUST pass after the regen. Run as part of phase 2 acceptance. | (test-only) |
| 3.4 | DO NOT touch `PRIVILEGED_TOOL_CATEGORIES` | `scheduling` is non-privileged by design (D7 grants via `meta.json` `tools.allow`). Triple-pinned by D18/A14 — adding `"scheduling"` here would break the universe. | `daemon/tools/_tool_registry.py:167-171` (UNTOUCHED) |

### Task 4: House-style polish + error patterns

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 4.1 | Error-string returns | All tools return `"ERROR: {message}"` strings (never bare exceptions) per the precedent at `daemon/tools/time.py:31,61,65-66`. Catch `ValueError`, `HTTPException`, `RuntimeError` and surface as error strings. | `daemon/tools/scheduling.py` |
| 4.2 | Result truncation | Use `_truncate.truncate_dict_result` (per house rule cited in research). Result dicts can be large when listing many schedules — apply truncation with sensible default. | `daemon/tools/scheduling.py` |
| 4.3 | Semantic-token module constants | If any tool emits semantic tokens for downstream parsing (e.g. `[SCHEDULE_CREATED]`), define them as module constants near the top — same pattern as `daemon/tools/job_queue.py` (referenced in research digest). | `daemon/tools/scheduling.py` |

> **Note (non-goal, mandatory):** the tools do NOT mint jobs directly and do NOT call `POST /api/jobs`. They create/update schedules via the shared service. Dispatch is the adapter's job (Phase 1 closes D4 in the adapter).

### Task 5: Phase-3 service-contract test (preemptive)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 5.1 | Service-contract test | `tests/unit/test_scheduler_api_phase3_contract.py::test_phase3_service_contract_resolved` (per phase 3's risk #1, which flags this as a CI guard). Asserts `daemon.services.scheduling_service` exposes `create_schedule`, `cancel_schedule`, `get_schedule`, `list_schedules`, `update_schedule` as module-level awaitables. Phase 2 lands this test FIRST so phase 3 can wire to a stubbed-then-real implementation. | `tests/unit/test_scheduler_api_phase3_contract.py` (NEW) |

### Task 6: Runtime wiring — instance.py + manager.scheduling_service + DYNAMIC_TOOL_NAMES (architecture §1.1 + §2 OD-3 + C-6)

> **🔴 CRITICAL (C-6 — runtime wiring unowned):** Without explicit ownership of the runtime wiring, registration is a no-op — agents have `"scheduling"` in `tools.allow` but `create_scheduling_tools` is never invoked (tools silently invisible), and `manager.scheduling_service` is never mounted (REST handlers raise `AttributeError`). All three sites must be claimed and verified in this phase.

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 6.1 | `create_scheduling_tools` invocation in `daemon/tools/instance.py` | Per the existing precedent at `daemon/tools/job_queue.py` factory wiring (the `create_job_tools(...)` call), extend `daemon/tools/instance.py` to invoke `create_scheduling_tools(scheduling_service, source_repo=source_repo, current_instance_id=current_instance_id, agent_id=agent_id, agent_tag=agent_tag)` for instances whose `meta.json` `tools.allow` contains `"scheduling"` (or any `task_schedule*` tool name). Wire only when the category resolves via `CATEGORY_MODULES`. | `daemon/tools/instance.py` |
| 6.2 | `manager.scheduling_service` mount | In `daemon/manager.py`, add a property `scheduling_service` that returns `self._scheduling_service` (a module-level instance of `daemon.services.scheduling_service` initialized during `__init__`). Phase-3 REST handlers reach the service via `manager.scheduling_service.create_schedule(...)`. The property must be set BEFORE phase-3's first request. | `daemon/manager.py` |
| 6.3 | `DYNAMIC_TOOL_NAMES` entries (`daemon/tools/_tool_registry.py:23-119`) | Add the four new tool names (`task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update`) to the `DYNAMIC_TOOL_NAMES` frozenset (mirror the pattern at `:60-63` for P0 job-visibility tools, `:114-118` for service tools). Without this, startup validation of `tools.allow` rejects the new category. | `daemon/tools/_tool_registry.py` |
| 6.4 | `create_schedule` agent existence fail-fast (OD-3 closure) | At function entry of `create_schedule`, BEFORE any DB write, call `agent_registry.exists(payload.agent)` (architecture §2 OD-3 — `daemon/registry.py:1173`); on False raise `ValueError("agent_id {X} does not exist")`. `enqueue_message_job` (`manager.py:7854-7887`) does NOT validate agent_id — failure would surface deep in `_process_message_with_tracking` AFTER the schedule row exists. Catch in tool/REST handlers as `400 Bad Request` (existing `ValueError`-to-error-string path covers this). | `daemon/services/scheduling_service.py` |

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Phase 3 (other worker) is mid-implementation when phase 2 lands. Contract drift between the two — e.g. phase 3 imports `create_schedule(payload: ScheduleCreate, ...)` while phase 2 lands `create_schedule(payload: ScheduleCreatePayload, ...)`. | High | Medium | Phase 3's contract names (`create_schedule`, `cancel_schedule`, `get_schedule`, `list_schedules`, `update_schedule`) are pinned by the existing phase-3 plan. Phase 2 honors those names exactly. The phase-5 contract test (Task 5.1) is the CI guard against drift — phase 2 lands it first. |
| 2 | `KNOWN_TOOL_NAMES` regen drifts bidirectionally. Forgetting to add one of the four tool names to the frozenset (or adding a misspelled name) trips `test_frozen_tool_name_discovery.py::test_known_tool_name_matches_source_exactly_no_drift` but only after a CI run. | Medium | Medium | Run the regen command (Task 3.2) and the drift test (Task 3.3) AS PART OF phase 2 acceptance. CI is the safety net; the regen command is the source of truth. |
| 3 | `create_scheduling_tools` factory is called per-instance (per agent), so the closure allocates 4 tool functions per agent. For agents that don't need scheduling (e.g. designer), the factory should NOT be called. | Low | Low | **OWNED BY PHASE 2** (Task 6 — runtime wiring). Phase-2 §Task 6.1 invokes `create_scheduling_tools(scheduling_service, ...)` from `daemon/tools/instance.py` for instances whose `meta.json` `tools.allow` contains `"scheduling"` (or any `task_schedule*` tool name); Phase-2 §Task 6.3 adds the four tool names to `DYNAMIC_TOOL_NAMES` (`_tool_registry.py:23-119`). Phase-4 §Task 1-3 owns the `meta.json` `tools.allow` edits (Ari MANDATORY, Leader + Jober desirable). Without phase-2 §Task 6, registration is a no-op (architecture C-6). |
| 4 | The service's `update_schedule` rejects cancelled rows with 409. If a user genuinely wants to "uncancel" (e.g. accidental cancel), they're stuck. | Low | Low | Documented contract: cancel is terminal (D5). Workaround: `create_schedule` with the same label (after delete via `delete_source_config` — note this purges history, user's choice). Phase 2 documents the contract in `ScheduleUpdateResponse` docstring + decisions.md. |
| 5 | The factory closure captures `caller_agent_id` at boot. If the agent is later renamed (rare but possible), stale closures persist until restart. | Low | Low | Documented in factory docstring. Restart picks up new agent name. Acceptable — daemon restart is the natural invalidation point. |
| 6 | Phase 2 depends on phase 1's `resolve_timezone()` helper. If phase 1's contract drifts from phase 2's expectations (e.g. return tuple shape changes), phase 2 breaks. | High | Low | Phase 2 imports the helper as `(zone, warning) = resolve_timezone(...)` — tuple unpacking is positional. Phase 1's `decisions.md` pins the helper's return shape as `(ZoneInfo, str)`. If phase 1 deviates, it's a phase-1 bug caught in phase-2's `tests/unit/services/test_scheduling_service.py`. |

## Acceptance Criteria

- [ ] `daemon/services/scheduling_service.py` exists; module imports `create_schedule`, `cancel_schedule`, `get_schedule`, `list_schedules`, `update_schedule` as module-level awaitables.
- [ ] `daemon/tools/scheduling.py` exists; 4 tools registered (`task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update`).
- [ ] `CATEGORY_MODULES` (`daemon/tools/_tool_registry.py:522-596`) contains `"scheduling": "daemon.tools.scheduling"`.
- [ ] `KNOWN_TOOL_NAMES` (`:630-842`) contains all four new tool names; alphabetical order preserved.
- [ ] `PRIVILEGED_TOOL_CATEGORIES` (`:167-171`) is **unchanged** (no `"scheduling"` entry — D18/A14 triple-pin).
- [ ] `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` PASSES.
- [ ] `tests/unit/services/test_scheduling_service.py` PASSES (create for each recurrence type; cancel rejects already-cancelled + non-existent; list hides cancelled by default; update rejects cancelled with 409; pause→resume round-trip; tz warning surfaces).
- [ ] `tests/unit/tools/test_scheduling_tools.py` PASSES (every echo surface returns both local + UTC; cancel-by-label resolution; error-string returns on bad input).
- [ ] `tests/unit/test_scheduler_api_phase3_contract.py::test_phase3_service_contract_resolved` PASSES (phase 3 imports resolve).
- [ ] **No call to `POST /api/jobs`** in `daemon/tools/scheduling.py` or `daemon/services/scheduling_service.py` (grep-verifiable).
- [ ] **No call to `delete_source_config`** in either file (cancel uses enum extension + adapter eviction, never delete).
- [ ] No daemon restart required; tools become available at next registry exposure (proven by tests, per D9).

## Constraints

- **No call to `POST /api/jobs`.** Tools create/update schedules; dispatch is the adapter's job.
- **No call to `delete_source_config`.** Cancel = `enabled=False` + `status="cancelled"`; schedule_executions preserved.
- **Tools never raise.** All errors return `"ERROR: {msg}"` strings (house pattern).
- **Local + UTC echo on every surface.** Create, list, get, update all return both.
- **Cancelled hidden by default.** `task_schedule_list(include_cancelled=False)` filters out `SourceStatus.CANCELLED.value`.
- **Pause ≠ cancel.** `paused=True` → `status=SourceStatus.STOPPED.value` (resumable). `cancel_schedule()` → `status=SourceStatus.CANCELLED.value` (terminal).
- **`PRIVILEGED_TOOL_CATEGORIES` untouched.** `scheduling` is non-privileged; grants via `meta.json` (phase 4).
- **Tool names must match contract.** Phase 3 imports these names; drift breaks phase 3.

## OPEN DECISIONS (for `decisions.md`)

> **OPEN DECISION 3:** ~~Should `task_schedule` reject when `agent_id` resolves to a non-existent agent, or allow creation and let the adapter fail at first fire?~~ — **CLOSED per architecture §2 OD-3 (ratified): FAIL-FAST at create time** via `agent_registry.exists(payload.agent)` in `create_schedule` (Task 6.4). Mapped to `daemon/registry.py:1173`. Catches typos before a stale `source_configs` row exists.

> **OPEN DECISION 4:** ~~When the service's `create_schedule` resolves tz via fallback chain (explicit → config → host-local → UTC + warning), should the warning be auto-suppressed for one-time schedules where the user said "now-ish"~~ — **CLOSED per architecture §2 OD-4 (ratified): KEEP the warning, never suppress**. Relative times anchor to daemon clock; an undetected local tz means the echo comes back at an unexpected wall-clock time — the warning is the only signal. Fleet operators kill the warning wholesale via `ENSEMBLE_SCHEDULING_DEFAULT_TZ`.

## Key Files

| File | Role |
|------|------|
| `daemon/services/scheduling_service.py` (NEW) | Shared service used by tools (this phase) and REST (phase 3) |
| `daemon/tools/scheduling.py` (NEW) | 4 agent-facing tools |
| `daemon/tools/_tool_registry.py` | `CATEGORY_MODULES` entry + `KNOWN_TOOL_NAMES` regen |
| `daemon/services/scheduling_service.py` (NEW) — payload models | `ScheduleCreatePayload`, `ScheduleUpdatePayload`, response models |
| `tests/unit/services/test_scheduling_service.py` (NEW) | Service unit tests |
| `tests/unit/tools/test_scheduling_tools.py` (NEW) | Tool unit tests |
| `tests/unit/test_scheduler_api_phase3_contract.py` (NEW) | Phase-3 service-contract CI guard |

## Deliverables

- [ ] `daemon/services/scheduling_service.py` with 5 module-level async functions + payload/response models
- [ ] `daemon/tools/scheduling.py` with 4 tools + factory function
- [ ] `CATEGORY_MODULES` entry + `KNOWN_TOOL_NAMES` regen
- [ ] Service unit tests (create/cancel/list/update)
- [ ] Tool unit tests (echo, cancel-by-label, error patterns)
- [ ] Phase-3 service-contract test (CI guard)
- [ ] OPEN DECISIONS recorded in `.agents/shared/planning/scheduled-tasks/decisions.md`
