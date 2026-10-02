# Phase 3: REST Surface — Create, Cancel, Get-by-ID

## Objective

Add three HTTP endpoints to `/api/schedules` so external systems can drive the scheduler with the same semantics the `scheduling` tool category exposes (`task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update` — see phase-2 tool surface). Specifically:

- `POST /api/schedules` — create a schedule (thin wrapper over the shared scheduling service)
- `DELETE /api/schedules/{id}` — **cancel** a schedule (terminal state, history PRESERVED; reuses existing pause/resume for non-terminal state changes via the shared service)
- `GET /api/schedules/{id}` — single-schedule fetch returning both `next_run_at_local` and `next_run_at_utc` so external callers never have to re-derive timezone

All three handlers stay thin: validation, is_write_paused → 503 gate, an `asyncio.to_thread(...)` call into the shared scheduling service (phase-2 contract, see `phase2-plan.md`), and the response model. No business logic, no adapter management, no timezone arithmetic in the route handler. The CRITICAL TZ RULE (D2) — user-stated local time stored as both human-intent keys (`recurrence` + `local_time` + `timezone`) AND a tz-qualified ISO `run_at`, surfaces echoed as BOTH `*_local` and `*_utc` fields on every response — lives in the shared service; the router echoes it.

> **Note (non-goal, mandatory):** No auth dependencies, no router-level pause gates beyond the existing `is_write_paused` 503 (`schedules.py:87-88`, `:185-186`, `:255-256`, `:347-348`). The router inherits the existing house posture (no auth on `/api/schedules`). No changes to `/api/sources` POST/DELETE routes — cancel MUST NOT reuse `DELETE /api/sources/{id}` (see Contract clause (d)).

## Coupling

- **Depends on**: phase-2 (shared scheduling service: `create_schedule()`, `cancel_schedule()`, `get_schedule()`, `list_schedules()`, `update_schedule()` — the public service seam that phase-2 worker lands; this phase is pure HTTP plumbing over it)
- **Coupling type**: tight (with phase-2; loose with phase-1)
- **Shared files with other phases**:
  - `daemon/routers/schedules.py` (this phase)
  - `daemon/models/schedule.py` (this phase adds request/response models)
  - `daemon/api.py` (mounts the router — already mounted at `api.py:2918`, no change expected)
- **Shared APIs / contracts**:
  - `SourceStatus` enum (`daemon/models/source.py:20-25`) — extends with `CANCELLED` value
  - `is_write_paused` (manager attribute) — every mutating route gates on this
  - `RESERVED_SOURCE_PREFIXES` (constants.py:763-784, includes `"scheduler"` at :783) — chat-source gate at jobs_crud.py:526-543 already prevents forged `source='scheduler:*'` jobs; this phase inherits the posture, no change
- **Why this coupling**: Cancel must NOT purge `schedule_executions` history (sources.py:374-405 + repository.py:263-295 does purge); instead cancel is a NEW terminal state — the shared service owns the state transition and history preservation, the router only validates input and delegates.

## Context

### REST surface today (`daemon/routers/schedules.py`, 458L, prefix `/schedules` tags `["schedules"]` :27; mounted api.py:2918)
- **Existing** (untouched by this phase): `GET /schedules` :39-79, `PUT /schedules/{id}` :83-172, `POST /schedules/{id}/trigger` :176-247, `POST /schedules/{id}/start` :251-339, `POST /schedules/{id}/stop` :343-387, `GET /schedules/{id}/executions` :391-458
- **CONFIRMED ABSENT** (this phase adds): `POST /schedules`, `DELETE /schedules/{id}`, `GET /schedules/{id}`
- `is_write_paused` → 503 sites: `:87-88`, `:185-186`, `:255-256`, `:347-348` (GETs ungated — this phase gates the new POST/DELETE only; new GET-by-id is also ungated per house posture)

### Schedule data model (`daemon/repositories/source/models.py:46-66`)
- `source_configs` columns: `source_id` (uuid4 PK), `source_type` idx, `name` idx, `config` JSONB, `credentials` encrypted str, `enabled` bool, `autostart` bool, `status` str default `"stopped"`, `error_message`, `created_at`/`updated_at` ISO strings
- **NO** `project_id` column or migration; `project_id` lives in `config` JSON, adapter reads `config.project_id` at `scheduler.py:134`, `:620-621`, `:689`
- `name` column is indexed — `get_source_config_by_name` exists at `repository.py:212-216`; cancel-by-label routes use this
- `SourceStatus` enum (`models/source.py:20-25`) currently has 4 values (`stopped, starting, running, error`). **No terminal `CANCELLED` value exists** — this phase extends the existing 4-value enum (stopped/starting/running/error) with CANCELLED.

### Cancel contract — must NOT reuse `DELETE /api/sources/{id}`
- `DELETE /api/sources/{id}` (sources.py:374-405) does NOT reject schedulers: stops+evicts adapter (:394-398) then `delete_source_config` (:403) which in ONE transaction deletes `InstanceMappings`, `ProcessedMessages`, `ScheduleExecutions`, then the row (repository.py:263-295) — **history PURGED**, app-level (no `ondelete`).
- This phase adds `DELETE /api/schedules/{id}` that calls the shared service's `cancel_schedule()` (phase-2) which transitions `status → "cancelled"` in-place, **preserves** `schedule_executions`, and removes the adapter from the live registry. Source row stays.

### TZ rule (D2 — binding across all phases)
- User-stated time ALWAYS means their LOCAL time; storage carries BOTH human-intent keys (`recurrence`, `local_time`, `timezone`) AND a tz-qualified ISO `run_at` (or generated `schedule` cron + `timezone` for daily/weekly)
- All list/echo surfaces show BOTH `*_local` and `*_utc` fields
- Default tz resolution order: explicit param → `ENSEMBLE_SCHEDULING_DEFAULT_TZ` env → host-local auto-detect → UTC-with-loud-warning (configured in phase-1's `SchedulingConfig`)

### Idempotency (D4 — must NEVER double-dispatch)
- Phase-1 worker designs the boot-time/enqueue-time fix; this phase inherits it. The cancel endpoint MUST persist `enabled=False` AND set `status="cancelled"` in the SAME transaction — boot-time adapter enumeration reads `enabled` (registry.py:281-283) and `status` (:290-294), so a cancelled schedule that was live when the daemon restarted must not be revived. (The phase-1 worker specifies the exact mechanism; this phase only references it via the shared service.)

### House pattern observed (this phase mirrors verbatim)
- Module-level `APIRouter(prefix="/schedules", tags=["schedules"])` (schedules.py:27)
- `_get_manager(request: Request)` helper (schedules.py:30-32) returns `request.app.state.manager`
- `request`/`response` models in `daemon/models/schedule.py` (NEVER inline in the router)
- `ErrorResponse` / `ErrorCodes` from `daemon.models` (schedules.py:10-21)
- `asyncio.to_thread(...)` around every repository call (schedules.py:47, :91, :135, :189, :259, :432)
- `validate_instance_mode` (utils) for `instance_mode` validation (schedules.py:121-125)
- `parse_utc_datetime` (utils) for ISO string parsing (schedules.py:65, :74, :75, :159, :169, :448, :452)
- 503 gate text: `"Writes are paused for database migration"` (exact string across all four sites)

## Tasks

### Task 1: Pydantic request/response models + REST↔service field-mapping (no business logic)

> **🔴 CRITICAL (ITEM 1 reconciliation):** Phase-2's `daemon.services.scheduling_service` is the **CANONICAL HOME** for create-payload + response models (`ScheduleCreatePayload` / `ScheduleDetail` / `ScheduleCancelResponse`). Phase-3's Pydantic models are a **THIN REST SURFACE** that maps to phase-2 via an explicit translation function. Without this, the specced `scheduling_service.create_schedule(payload.model_dump())` would fail `model_validate` on every REST create (phase-2 fields `label/agent/when` differ from phase-3 fields `name/agent_id/local_time+run_at`). This task rewrites Task 1 to make the mapping explicit + unify uniqueness + unify response shapes.

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 1.0 | **Canonical ownership** (no work — reference only) | `SourceStatus.CANCELLED = "cancelled"` is added to **BOTH** enum sites by **phase-1** (`daemon/models/source.py:8-14` and `daemon/repositories/source/models.py:20-25`). Phase-3 does NOT add it (would be a duplicate). Phase-3 only USES the value. See phase-1 §Task 7.1. | (no edit) |
| 1.1 | Add **thin REST request body** `ScheduleCreate` (FastAPI) | DIFFERENT shape from phase-2's `ScheduleCreatePayload`; phase-3 is the HTTP surface and uses REST conventions (`source_id` + `name` as separate fields, where `name` is a human-readable display name; `source_id` is the URL-stable identifier). Fields: `source_id: str` (regex `^[a-zA-Z0-9_-]+$` 1-64 — this IS the schedule's canonical id, used as `source_configs.source_id` PRIMARY KEY on the DB row); `name: str` (1-128, human-readable display name, OPTIONAL, defaults to `source_id` if omitted); `agent_id: str`; `message: str`; `project_id: str`; `priority: int = 5`; `instance_mode: Literal["new_instance", "reuse_instance"] = "new_instance"`; `recurrence: Literal["once", "daily", "weekly", "cron"]`; **`timezone: str \| None`** (canonical name — matches phase-2); **`weekday: int \| None`** (canonical base = `0=Sun..6=Sat` per phase-2 / cron DOW); **`cron_expression: str \| None`** (canonical name — matches phase-2); `local_time: str \| None` (HH:MM for daily/weekly); `run_at: datetime \| None` (tz-aware preferred; naive → treated as local per resolved tz). **NO `enabled` / `autostart` fields** — schedules are always created enabled+autostart; lifecycle via `update`/pause/cancel (see `task_schedule_update` / POST `/schedules/{id}/stop|start` / DELETE `/schedules/{id}`). Cross-field validation: `cron_expression` required iff `recurrence == "cron"`; `weekday` required iff `recurrence == "weekly"`; exactly one of `local_time` / `run_at` required for `once`; for `daily`/`weekly`, `local_time` required. | `daemon/models/schedule.py` |
| 1.2 | **REST↔Service field-mapping function** `def _schedule_create_to_payload(req: ScheduleCreate) -> ScheduleCreatePayload` (NEW, in `daemon/routers/schedules.py`) | Maps REST surface to phase-2 canonical model. Body: see code below. Critical mappings: (a) `source_id` → `label` (phase-2's `ScheduleCreatePayload.label`); (b) `name` → merged into `label` ONLY if `source_id != name` (else drop `name` to avoid duplication in human-echo); (c) `agent_id` → `agent`; (d) `local_time` OR `run_at` → `when` (one of them per recurrence); (e) `timezone` → `timezone` (same name); (f) `cron_expression` → `cron_expression` (same name); (g) `weekday` → `weekday`; (h) `priority` / `project_id` / `instance_mode` → passthrough. **REST-only fields `enabled`/`autostart` are REMOVED** (F2): schedules are always created enabled+autostart per phase-2 §Task 1.2 step (6) hardcoded `enabled=True, autostart=True`. Lifecycle control flows through `update`/pause/cancel, NOT through create. | `daemon/routers/schedules.py` |
| 1.3 | **Unified uniqueness semantics — REST CREATE: `source_id == label`** | Phase-2's `ScheduleCreatePayload` uniqueness check (409 Conflict on duplicate `label`) is the canonical check. Phase-3 REST CREATE calls `_schedule_create_to_payload(req)` first, which translates `source_id` → `label` (and `name` defaults to `source_id` when omitted); then the service raises `ValueError("label already exists")` → handler maps to `409 Conflict`. **Single source of truth for uniqueness** — phase-3 does NOT add a separate `source_id`-based uniqueness check. The DB row's PRIMARY KEY is `source_configs.source_id` (`models.py:50-52`), but uniqueness is enforced semantically on the human-readable `label` per phase-2's contract. **Operational rule:** REST CREATE requires `source_id == label` (i.e. operators must provide a unique URL-stable `source_id` and the service uses `source_id` as the `label`). Tools (`task_schedule`) accept the full REST surface as `label` directly and can use any string — tools do NOT enforce `source_id == label`. GET/DELETE/UPDATE REST handlers accept either `source_id` or `label` as identifier (Task 1.5 / Task 2.4 — service resolves via `get_source_config_by_name`). | (no edit — doc + Task 2.1 enforcement) |
| 1.4 | Add **REST response models** that wrap phase-2's canonical models | **REST responses use a thin wrapper** that ECHOES the phase-2 response. The handler calls the service, gets `ScheduleCreateResponse` / `ScheduleDetail` / `ScheduleCancelResponse`, then constructs a REST model with REST-conventional field names: `ScheduleCreateRestResponse` wraps phase-2's `ScheduleCreateResponse` and re-exposes its fields under REST names (`source_id` = phase-2's `source_id`, `label` = phase-2's `label`); `ScheduleDetailRestResponse` wraps phase-2's `ScheduleDetail` (adds `id` field = `source_id` for REST URL consistency); `ScheduleCancelRestResponse` wraps phase-2's `ScheduleCancelResponse` and **adds `last_execution_id: str \| None`** (architecture §5.3 echo) + `message: str`. Single source of truth for response shape = phase-2. Phase-3 only renames fields for REST conventions. | `daemon/models/schedule.py` |
| 1.5 | Cancel-by-label semantics (REST) | `DELETE /api/schedules/{schedule_id}` accepts both forms: (a) `source_id` (REST URL-stable id = phase-2 `label`); (b) `label` directly. **Path param regex** does NOT enforce UUID; the service resolves either via `get_source_config_by_name` (`repository.py:212-216`). Handler passes `schedule_id` (the path string) directly to `scheduling_service.cancel_schedule(schedule_id)`; the service decides whether it's an id or a label. | (no edit — doc + Task 2.3-2.4) |
| 1.6 | Export new symbols | Add REST models (`ScheduleCreate`, `ScheduleCreateRestResponse`, `ScheduleDetailRestResponse`, `ScheduleCancelRestResponse`) to module `__all__`. **Do NOT re-export phase-2 models from this module** — phase-3 REST code imports phase-2 models directly via `from daemon.services.scheduling_service import ScheduleCreatePayload, ...` (canonical home stays phase-2). | `daemon/models/schedule.py:__all__` |
| 1.7 | Freeze field-mapping code (canonical reference for implementer) | ```python\n# daemon/routers/schedules.py (NEW mapping function, frozen)\ndef _schedule_create_to_payload(req: 'ScheduleCreate') -> ScheduleCreatePayload:\n    \"\"\"REST ScheduleCreate → phase-2 ScheduleCreatePayload.\n\n    Mapping: source_id → label (URL-stable id == label for REST).\n              name → label (override; defaults to source_id if name omitted).\n              agent_id → agent.\n              local_time OR run_at → when (one per recurrence).\n              timezone / cron_expression / weekday → passthrough.\n              priority / project_id / instance_mode → passthrough.\n    \"\"\"\n    label = req.name or req.source_id\n    if req.recurrence == \"once\":\n        when = req.run_at.isoformat() if req.run_at else (req.local_time or \"\")\n    else:  # daily / weekly / cron\n        when = req.local_time or \"\"\n    return ScheduleCreatePayload(\n        label=label,\n        agent=req.agent_id,\n        message=req.message,\n        when=when,\n        timezone=req.timezone,\n        recurrence=req.recurrence,\n        weekday=req.weekday,\n        cron_expression=req.cron_expression,\n        project_id=req.project_id,\n        priority=req.priority,\n        instance_mode=req.instance_mode,\n    )\n``` | `daemon/routers/schedules.py` |

> **Note (non-goal, mandatory):** phase-3 does NOT redefine `ScheduleCreatePayload` / `ScheduleDetail` / `ScheduleCancelResponse`. Those live in `daemon/services/scheduling_service.py` (canonical home). Phase-3 only declares THIN REST wrappers (`ScheduleCreate` request body, REST response wrappers) and the explicit mapping function. The Pydantic-at-boundary contract (architecture §2 OD-2) is the layer where phase-2 validates; phase-3's mapping function builds a fully-typed `ScheduleCreatePayload` BEFORE calling the service, so the service's `model_validate` bridge is bypassed for REST callers — single validated contract downstream.

**REST request body schema (frozen for implementer):**

```python
# daemon/models/schedule.py — REST surface only (canonical models live in daemon/services/scheduling_service.py)
class ScheduleCreate(BaseModel):
    source_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", min_length=1, max_length=64,
                           description="URL-stable id; used as label for create (uniqueness)")
    name: str | None = Field(default=None, max_length=128,
                             description="Human-readable display name; defaults to source_id")
    agent_id: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    priority: int = Field(default=5, ge=1, le=10)
    instance_mode: Literal["new_instance", "reuse_instance"] = "new_instance"
    recurrence: Literal["once", "daily", "weekly", "cron"]
    local_time: str | None = Field(default=None, description="HH:MM for daily/weekly; full ISO for once (alternative to run_at)")
    timezone: str | None = Field(default=None, description="IANA name; service resolves default chain")
    cron_expression: str | None = Field(default=None)
    weekday: int | None = Field(default=None, ge=0, le=6, description="0=Sun..6=Sat (matches phase-2 / cron DOW)")
    run_at: datetime | None = Field(default=None, description="tz-aware preferred; naive → treated as local per resolved tz")
    # NOTE: NO `enabled` or `autostart` fields (F2). Schedules are created enabled+autostart;
    # lifecycle via update/pause/cancel — see task_schedule_update / POST /schedules/{id}/stop|start
    # / DELETE /schedules/{id}. Adding these fields would require plumbing through phase-2's
    # canonical signature (which does NOT carry them) — least contract churn is to keep them out.

    @model_validator(mode="after")
    def _validate_recurrence_requirements(self):
        if self.recurrence == "cron" and not self.cron_expression:
            raise ValueError("cron_expression required when recurrence == 'cron'")
        if self.recurrence == "weekly" and self.weekday is None:
            raise ValueError("weekday required when recurrence == 'weekly'")
        if self.recurrence == "once" and not (self.run_at or self.local_time):
            raise ValueError("either run_at or local_time required when recurrence == 'once'")
        if self.recurrence in {"daily", "weekly"} and not self.local_time:
            raise ValueError("local_time required when recurrence in {'daily', 'weekly'}")
        return self

    model_config = ConfigDict(json_schema_extra={"example": {
        "source_id": "morning-briefing",
        "name": "Morning Briefing",
        "agent_id": "ari",
        "message": "Give me a morning briefing",
        "project_id": "default",
        "recurrence": "daily",
        "local_time": "06:00",
        "timezone": "America/New_York",
    }})
```

**REST response wrapper schemas (frozen for implementer):**

```python
# daemon/models/schedule.py — REST wrappers around phase-2 canonical models
class ScheduleCreateRestResponse(BaseModel):
    """Wraps phase-2 ScheduleCreateResponse; adds REST-conventional field names."""
    id: str  # = phase-2 source_id
    source_id: str
    label: str
    status: str
    next_run_at_local: str | None
    next_run_at_utc: str | None
    tz_warning: str = ""


class ScheduleDetailRestResponse(BaseModel):
    """Wraps phase-2 ScheduleDetail; adds 'id' field for REST URL consistency."""
    id: str  # = source_id (= label for REST creates)
    source_id: str
    label: str
    status: str
    recurrence: str | None
    local_time: str | None
    timezone: str | None
    cron_expression: str | None
    weekday: int | None
    next_run_at_local: str | None
    next_run_at_utc: str | None
    last_run_at: str | None
    agent_id: str | None  # = phase-2 agent
    project_id: str | None
    instance_mode: str | None
    cancelled_at: str | None
    tz_warning: str = ""


class ScheduleCancelRestResponse(BaseModel):
    """Wraps phase-2 ScheduleCancelResponse; adds last_execution_id echo (architecture §5.3) + message."""
    id: str
    source_id: str
    status: str
    cancelled_at: str
    last_execution_id: str | None  # architecture §5.3: echo for in-flight job cancel
    message: str  # e.g. "Schedule {id} cancelled (history retained)"
```

> **ALIGNED ✅** — every phase-3 REST field has a precise mapping to phase-2 canonical; single source of truth for response shape; uniqueness check happens once at phase-2 `label`.

### Task 2: New router endpoints (thin handlers)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 2.1 | Add `POST /schedules` handler | 503 `is_write_paused` gate FIRST (mirror `:87-88`); 400 on `instance_mode` invalid (`validate_instance_mode`); **build canonical payload via `_schedule_create_to_payload(req)`** (Task 1.2/1.7) — NOT `req.model_dump()` (the model fields don't match phase-2 canonical); **`enabled`/`autostart` are NOT in the REST body** (F2) — phase-2 §Task 1.2 step (6) hardcodes `enabled=True, autostart=True`; lifecycle via update/pause/cancel (Task 2.5 reuses existing `/schedules/{id}/stop|start`); call `await asyncio.to_thread(manager.scheduling_service.create_schedule, payload)` where `payload: ScheduleCreatePayload` (already typed — service's `model_validate` bridge is bypassed for REST callers); respond `201 Created` with **flat** `ScheduleCreateRestResponse` (no `detail=` wrapping). **Uniqueness semantics (Task 1.3):** 409 on duplicate `source_id` is enforced by phase-2's `ScheduleCreatePayload` check on `label` (since `source_id == label` for REST creates) — handler maps `ValueError("label already exists")` → `409 Conflict`. Single source of truth for uniqueness. | `daemon/routers/schedules.py` |
| 2.2 | Add `GET /schedules/{id}` handler | No 503 gate (GET house posture). 404 if not found OR not `source_type == "scheduler"` (mirror `:91-99`, `:101-108`); call `await asyncio.to_thread(manager.scheduling_service.get_schedule, schedule_id)`; respond `ScheduleDetailRestResponse` (Task 1.4 wrapper around phase-2 `ScheduleDetail`). **`GET /schedules/{id}` DOES return cancelled rows** (architecture §3.4 — the "did I actually cancel X?" path); `cancelled_at` is populated in the response when `status == "cancelled"`. Service computes BOTH local+UTC from canonical config (phase-2 §Task 1.5). | `daemon/routers/schedules.py` |
| 2.3 | Add `DELETE /schedules/{id}` handler | 503 `is_write_paused` gate FIRST; 404 if not found OR not scheduler type; call `await asyncio.to_thread(manager.scheduling_service.cancel_schedule, schedule_id)` (atomic via `cancel_source_config` — phase-1 §Task 7.3); respond `200 OK` with `ScheduleCancelRestResponse` carrying `last_execution_id` echo (architecture §5.3) + `message="Schedule {id} cancelled (history retained)"`. **MUST NOT** call `manager._source_repository.delete_source_config` (purges history — see Context "Cancel contract"); the service uses the atomic method which preserves history. | `daemon/routers/schedules.py` |
| 2.4 | Cancel-by-label resolver | If `schedule_id` path param looks like a label (no uuid4 hex pattern), the shared service must resolve via `get_source_config_by_name` (repository.py:212-216) and return the underlying row; the router delegates the resolution to the service, NOT the router. (Implementation note: the path param `schedule_id` accepts both forms; the service decides. Router only validates path shape via the regex above.) | `daemon/routers/schedules.py` |
| 2.5 | Pause/Resume routing (D5) | Pause and resume are NOT new endpoints — they reuse `POST /schedules/{id}/stop` and `POST /schedules/{id}/start` respectively (existing routes :251-339 and :343-387). Update the openapi docstring on each route to clarify "stop = pause (resumable)" / "start = resume". Do NOT add new `pause`/`resume` routes. | `daemon/routers/schedules.py:251, :343` |

**Handler skeleton (frozen for implementer — REWRITTEN to match Task 1 mapping + flat REST responses):**

```python
# POST /schedules - Create a new schedule
@router.post("", response_model=ScheduleCreateRestResponse, status_code=201)
async def create_schedule(req: ScheduleCreate, request: Request):
    """Create a new scheduled task.

    The body mirrors the scheduling tool category's `task_schedule` semantics
    (see agents/ari/tools_note.md → Scheduling). User-stated `local_time` is
    ALWAYS interpreted in the supplied `timezone` (or the configured default
    chain). The response echoes BOTH local and UTC for `next_run_at_*` so
    external callers never have to re-derive timezone.

    Schedules are created enabled+autostart; lifecycle via update/pause/cancel
    (see `task_schedule_update` / POST `/schedules/{id}/stop|start` /
    DELETE `/schedules/{id}`).
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    # 400 on instance_mode invalid — validate_instance_mode handles (mirror schedules.py:121-125)
    validate_instance_mode(instance_mode=req.instance_mode, config={})

    # REST ↔ canonical mapping (phase-3 §Task 1.2/1.7; canonical code at :79):
    # source_id → label (REST CREATE enforces source_id == label — Task 1.3);
    # name defaults to source_id when omitted (so source_id == label holds);
    # agent_id → agent; for `recurrence == "once"`, run_at.isoformat() OR local_time → when;
    # for daily/weekly/cron, local_time → when; timezone/cron_expression/weekday/priority/project_id/instance_mode
    # pass through. enabled/autostart are NOT in the canonical payload (F2 — phase-2 §Task 1.2
    # hardcodes enabled=True, autostart=True — lifecycle via update/pause/cancel).
    payload = _schedule_create_to_payload(req)

    # 409 on duplicate label: phase-2's uniqueness check on `label` raises ValueError;
    # handler maps to 409 Conflict. Per Task 1.3, the REST body uses source_id
    # (which becomes label via the mapping), so a 409 here indicates a duplicate
    # REST source_id — operationally identical to a duplicate label.
    try:
        created = await asyncio.to_thread(
            manager.scheduling_service.create_schedule, payload,
        )
    except ValueError as e:
        if "label" in str(e).lower() and "exists" in str(e).lower():
            raise HTTPException(status_code=409, detail={
                "code": "SCHEDULE_LABEL_CONFLICT",
                "message": str(e),
            })
        raise

    # Flat ScheduleCreateRestResponse (no `detail=` wrapping).
    return ScheduleCreateRestResponse(
        id=created.source_id,
        source_id=created.source_id,
        label=created.label,
        status=created.status,
        next_run_at_local=created.next_run_at_local,
        next_run_at_utc=created.next_run_at_utc,
        tz_warning=created.tz_warning,
    )


# GET /schedules/{schedule_id} - Single fetch (returns cancelled rows per architecture §3.4)
@router.get("/{schedule_id}", response_model=ScheduleDetailRestResponse)
async def get_schedule(schedule_id: str, request: Request):
    """Fetch a single schedule by source_id (or label — service resolves).

    Returns cancelled rows too: this is the "did I actually cancel X?" path
    (architecture §3.4). Service computes BOTH local+UTC from canonical config.
    """
    manager = _get_manager(request)
    detail = await asyncio.to_thread(
        manager.scheduling_service.get_schedule, schedule_id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail={
            "code": "SCHEDULE_NOT_FOUND",
            "message": f"Schedule not found: {schedule_id}",
        })
    return ScheduleDetailRestResponse(
        id=detail.source_id,
        source_id=detail.source_id,
        label=detail.label,
        status=detail.status,
        recurrence=detail.recurrence,
        local_time=detail.local_time,
        timezone=detail.timezone,
        cron_expression=detail.cron_expression,
        weekday=detail.weekday,
        next_run_at_local=detail.next_run_at_local,
        next_run_at_utc=detail.next_run_at_utc,
        last_run_at=detail.last_run_at,
        agent_id=detail.agent,
        project_id=detail.project_id,
        instance_mode=detail.instance_mode,
        cancelled_at=detail.cancelled_at,
        tz_warning=detail.tz_warning,
    )


# DELETE /schedules/{schedule_id} - Cancel a schedule (terminal)
@router.delete("/{schedule_id}", response_model=ScheduleCancelRestResponse)
async def cancel_schedule(schedule_id: str, request: Request):
    """Cancel a schedule.

    Terminal — the schedule is hidden from default list output, never fires
    post-restart, and history (schedule_executions) is PRESERVED. Pause/resume
    remain the non-terminal alternative (use POST /schedules/{id}/stop|start).
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")

    cancelled = await asyncio.to_thread(
        manager.scheduling_service.cancel_schedule, schedule_id,
    )
    return ScheduleCancelRestResponse(
        id=cancelled.source_id,
        source_id=cancelled.source_id,
        status=cancelled.status,
        cancelled_at=cancelled.cancelled_at,
        last_execution_id=cancelled.last_execution_id,  # architecture §5.3 echo
        message=f"Schedule {schedule_id} cancelled (history retained)",
    )
```

### Task 3: Wire router into existing module

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 3.1 | Verify router is already mounted | `daemon/routers/schedules.py` is mounted at `api.py:2918` with `prefix="/schedules"` (router :27) and `tags=["schedules"]`. No `daemon/api.py` change needed unless `manager.scheduling_service` is wired through `request.app.state.manager` (it must be — handler delegates to `manager.scheduling_service`). | `daemon/api.py:2918` |
| 3.2 | Document the openapi group | The three new endpoints inherit `tags=["schedules"]` automatically (router-level). No change. | — |

### Task 4: REST list-filter + DELETE guard placement (architecture §2 OD-5 + §3.4)

| # | Sub-task | Details | Key Files |
|---|----------|---------|-----------|
| 4.1 | `GET /schedules` default-exclude cancelled + `?include_cancelled=true` query param | Architecture §3.4: today `GET /schedules` (`schedules.py:39-79`) iterates `list_source_configs()` unconditionally (`:47-50`) → cancelled rows pollute the default list, contradicting ADR-005 hidden-by-default. Add `include_cancelled: bool = False` query parameter (mirror tool flag). When `False` (default), filter out `status == SourceStatus.CANCELLED.value` BEFORE returning. `GET /schedules/{id}` (Task 2.2) **DOES** return cancelled rows — that is the "did I actually cancel X?" path; pin in the openapi docstring. | `daemon/routers/schedules.py` (existing GET) |
| 4.2 | DELETE guard in `sources.py` — placed AFTER the get-or-404 | Architecture §2 OD-5 placement correction: extend `_reject_scheduler_lifecycle` (`sources.py:44-66`, used at `:429` start / `:530` stop) to also cover DELETE. **Placement:** insert the call AFTER the get-or-404 at `:382` and BEFORE `:394`, so unknown ids still 404 (NOT 400). Response code `SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED`. ~3 lines + one test. Closes the cancel-confusion hazard (D5) at the HTTP boundary. | `daemon/routers/sources.py:374-405` |
| 4.3 | Test — DELETE guard placement + 404 vs 400 | `tests/test_scheduler_api.py::TestDeleteGuardPlacement` (NEW): assert DELETE on unknown id returns 404 (not 400); DELETE on scheduler source returns 400 with code `SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED`. | `tests/test_scheduler_api.py` (extend) |

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | The phase-2 shared scheduling service surface does not yet exist — `create_schedule`/`cancel_schedule`/`get_schedule` are aspirational symbols referenced by name only; cancel semantics (history preservation, boot-time filter) are NOT yet landed | High | High | This phase pins the SERVICE-CONTRACT NAMES (create_schedule, cancel_schedule, get_schedule) and CANNOT BE MERGED until phase-2 lands the matching service. Add a CI guard: `tests/unit/test_scheduler_api_phase3_contract.py::test_phase3_service_contract_resolved` asserts the three names are symbols on `manager.scheduling_service` at import — implementer runs this test against a stub first, real impl when phase-2 lands. |
| 2 | `DELETE /api/schedules/{id}` may be confused with `DELETE /api/sources/{id}` by external callers; reusing the wrong one PURGES history | High | Low | **RESOLVED by Task 4.2 DELETE guard** (architecture §2 OD-5, placement corrected to AFTER get-or-404). Scheduler DELETEs at `/api/sources` now return 400 with `SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED`, pointing operators at the correct route. OpenAPI tag is `schedules` not `sources`; cancel response carries explicit `cancelled_at` and `message="... history retained"`. |
| 3 | `next_run_at_local` computation in `GET /schedules/{id}` may diverge from `next_run_at_utc` if the adapter caches one but not the other (current code at schedules.py:52-58 computes ONE value via `_get_next_trigger_time`) | Medium | Medium | Hand off BOTH computations to the shared service. Service reads canonical config (`recurrence`, `local_time`, `timezone`, `cron_expression`) and computes the next trigger TWICE — once in local tz, once in UTC. Service stores/caches the canonical pair; router echoes. Do NOT compute in router. |
| 4 | `GET /api/schedules/{id}` is ungated (no 503), so a paused daemon may surface stale schedules during a database migration | Low | Low | Document in the openapi docstring that GET is ungated and may return rows whose config is mid-migration; this is house posture (matches `GET /schedules` at :39 and `GET /schedules/{id}/executions` at :391). |
| 5 | `SourceStatus.CANCELLED` is added to the enum; existing UI/frontend may not handle the new value and silently treats it as ERROR | Medium | High | Frontend filtering already runs on `source_type == "scheduler"` (schedules.py:50) — status enum is only rendered. Add the new value to the frontend status badge map in a follow-up; mark as known gap in the PR description. Phase-5 acceptance covers it. |
| 6 | Cancel during a running tick race — schedule_executions row may be in-flight when status flips to CANCELLED | Medium | Low | The shared service's cancel_schedule MUST acquire the same row lock the registry uses during dispatch (phase-2 worker specifies). Until phase-2 lands, document the gap: a tick that was already in `await manual_trigger` when cancel arrives will complete its execution row (history is preserved), but no further tick will fire. Acceptable; mirror the existing pause-during-tick semantics (schedules.py:294-308). |
| 7 | `is_write_paused` text mismatch: existing routes use `"Writes are paused for database migration"` (schedules.py:87-88, :185-186, :255-256, :347-348) — the new routes must use the SAME text or frontend will treat 503s differently | Low | Low | Use the exact same text verbatim; this phase enforces it via a unit test scanning all 503 sites in `daemon/routers/schedules.py` for the literal string. |

## Acceptance Criteria

- [ ] `POST /api/schedules` exists and returns `201 Created` with **flat** `ScheduleCreateRestResponse` body (no `detail=` envelope); wraps phase-2's canonical `ScheduleCreateResponse` (validates against the frozen Pydantic example at Task 1.4)
- [ ] `GET /api/schedules/{id}` exists, returns `200 OK` with **flat** `ScheduleDetailRestResponse` body (no `detail=` envelope); wraps phase-2's canonical `ScheduleDetail`; surfaces BOTH `next_run_at_local` AND `next_run_at_utc` (both `null` when no upcoming run — never only one populated); returns cancelled rows too (architecture §3.4 "did I actually cancel X?" path)
- [ ] `DELETE /api/schedules/{id}` exists, returns `200 OK` with **flat** `ScheduleCancelRestResponse` body (no `detail=` envelope); wraps phase-2's canonical `ScheduleCancelResponse`; sets `status="cancelled"`, echoes `last_execution_id` (architecture §5.3); NEVER calls `delete_source_config` (history preservation contract); accepts either `source_id` OR `label` as identifier (Task 1.5 / Task 2.4 cancel-by-label)
- [ ] All three handlers gate on `is_write_paused` with the literal string `"Writes are paused for database migration"` (POST/DELETE only — GET is ungated per house posture)
- [ ] All three handlers use `asyncio.to_thread(...)` around every service call (matches existing `:47, :91, :135, :189, :259, :432` pattern)
- [ ] All three handlers get the manager via `request.app.state.manager` (existing `_get_manager` helper)
- [ ] No auth dependencies added to any new route (house posture preserved)
- [ ] `SourceStatus.CANCELLED` enum value exists and is exported in `__all__` (daemon/models/source.py:20-25)
- [ ] `ScheduleCreate` model enforces: `source_id` regex `^[a-zA-Z0-9_-]+$` 1-64, `name` 1-128, `instance_mode` ∈ {new_instance, reuse_instance}, `recurrence` ∈ {once, daily, weekly, cron}, `weekday` 0-6, `priority` 1-10
- [ ] `ScheduleCreate` cross-field validation: `cron_expression` required iff `recurrence == "cron"`; `weekday` required iff `recurrence == "weekly"`; `local_time` required iff `recurrence in {daily, weekly}` OR for `once` (in which case `run_at` accepted instead)
- [ ] `ScheduleDetail` includes `cancelled_at: datetime | None` (null when status != CANCELLED)
- [ ] Phase-5 contract test: importing `daemon.routers.schedules` does not raise; all three new symbols are present in the router (`create_schedule`, `cancel_schedule`, `get_schedule_by_id`)
- [ ] `daemon/routers/schedules.py` does not import or call `delete_source_config` (verified via grep)
- [ ] `daemon/routers/sources.py` DELETE handler **IS** extended via `Task 4.2` (cancel-confusion guard) — the guard rejects scheduler DELETEs with `400 SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED` AFTER the get-or-404 at `:382` and BEFORE `:394`, so unknown ids still 404 (not 400). Phase-3 owns this edit (the `daemon/routers/sources.py` extension is in scope per architecture §2 OD-5 ADOPT-NOW).
- [ ] Phase-5 contract test verifies the three service methods resolve on `manager.scheduling_service` (skeleton or real)
- [ ] Existing schedules API tests (`tests/test_scheduler_api.py`) remain green — no existing route behavior changed
- [ ] All new Pydantic models exported via `__all__` in `daemon/models/schedule.py`

