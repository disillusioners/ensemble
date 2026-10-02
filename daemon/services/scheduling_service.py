"""Shared scheduling service — the single seam behind agent tools AND REST.

This module is the CANONICAL HOME (phase-2, architecture §5) for the
create / cancel / get / list / update schedule flows. Phase-3 REST
handlers import these functions and the Pydantic models below verbatim;
the agent tools (``daemon/tools/scheduling.py``) call the same functions
through the factory closure. Neither surface duplicates the logic.

Pydantic-at-boundary (ADR-013 / architecture §2 OD-2)
-----------------------------------------------------
``create_schedule`` / ``update_schedule`` accept **either** a typed
``ScheduleCreatePayload`` / ``ScheduleUpdatePayload`` **or** a plain
``dict``. A dict is bridged via ``Model.model_validate(payload)`` at
function entry. Tools always pass a typed model; REST builds a typed
model too (so the bridge is bypassed); the dict-accept branch serves
tests and future internal callers. One validated contract downstream.

Cancel is terminal (ADR-005 / ADR-007)
--------------------------------------
``cancel_schedule`` is the SOLE writer of ``SourceStatus.CANCELLED``
(architecture §5.4 single-writer). It writes via the atomic
``cancel_source_config`` (one session: ``enabled=False`` +
``status='cancelled'``), preserves ``schedule_executions`` history, and
NEVER purges the history rows. Cancelled rows are hidden from the
default list and skipped by the boot filter (phase-1).

Concurrency model
-----------------
* **Per-source asyncio.Lock.** ``update_schedule`` and ``cancel_schedule``
  serialize on ONE shared per-source lock (keyed by ``source_id``, held
  across stop → mutate → start). This is the cancel-vs-cancel and
  cancel-vs-update race answer: both writers take the same lock, the
  cancelled-check runs INSIDE the lock on a freshly re-read row, and the
  atomic ``cancel_source_config`` write is therefore the deterministic
  last-write inside the critical section. ``create_schedule`` needs no
  lock (brand-new PK; duplicate-label check may still race two creates
  with the same label — accepted: table has no unique constraint on
  ``name``, second row wins the listing and the first keeps firing; both
  are visible and cancellable).
* **Lock lifetime.** Locks are created on demand and deliberately NEVER
  evicted: evicting a lock a waiter still references would split
  mutual exclusion (a third caller would build a fresh lock while the
  waiter drains the old one). Growth is O(number of schedules ever
  updated/cancelled) — a few hundred bytes each, bounded in practice by
  operator activity, never per-fire.
* **Evict-before-stop (architecture §5.2).** Both flows evict the
  registry entry BEFORE the graceful drain so concurrent trigger lookups
  cannot reach a draining adapter. Eviction uses the public
  ``SourceRegistry.unregister`` (pops the adapter, cancels the supervisor
  and autostart tasks synchronously), then the captured adapter
  reference is stopped directly — the registry's own ``stop_adapter``
  would no-op after eviction (it re-looks-up ``_adapters``) and its
  evict-at-the-end ordering re-opens the window §5.2 closes. The
  captured-reference pattern mirrors the one stop_adapter itself uses
  below its mid-function pop (``registry.py:686-696``).
* **Single-writer residual (architecture §5.4).** There is no version
  column on ``source_configs``; adapter status writes and service config
  writes remain last-write-wins. The per-source lock plus
  evict-before-stop shrinks the window to the adapter's own writers,
  which D4 already tolerates. Do NOT add a version column for this
  feature.

In-flight-job residual (architecture §5.3)
------------------------------------------
A trigger that already enqueued its JobItem is OUTSIDE the cancel
surface — ``cancel_schedule`` cannot recall it, and the job runs to
completion for a now-cancelled schedule. ``cancel_schedule`` therefore
echoes ``last_execution_id`` (latest row from ``schedule_executions`` by
``triggered_at``) so the operator can cancel the in-flight JobItem
directly. A cancel racing a due fire may still produce one final
execution; its ``triggered`` history row is correct and preserved.Adapter-rebuild seam (architecture §5.1)
----------------------------------------
PUT ``/schedules/{id}`` today is DB-only (no adapter rebuild) — a
pre-existing latent defect. THIS service is the adapter-rebuild seam:
stop → mutate → create_adapter_from_config → register → start. The
rebuild is NEW logic, not a reuse. Documented rebuild gap: a fire that
lands inside the <1s stop→start window is missed and acceptable (the
user explicitly asked for the change); history shows the truth.

Non-goal (mandatory): this service NEVER dispatches — it never mints
JobItems and never touches the HTTP job-creation endpoint or the
manager's message-enqueue path. It creates and mutates schedule rows;
the SchedulerAdapter fires them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone as _stdlib_timezone
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from croniter import CroniterBadCronError
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# The source_configs.source_type value every scheduler row carries. The
# service refuses to touch rows of any other type (chat sources are not
# schedules).
SCHEDULER_SOURCE_TYPE = "scheduler"

# Human-facing recurrence keys stored in config_json. The adapter itself
# keys off run_at / schedule / interval_seconds; the recurrence key is a
# human-intent echo so list/get surfaces can say "daily 06:30 UTC" without
# re-deriving intent from raw cron.
_RECURRENCE_ONCE = "once"
_RECURRENCE_DAILY = "daily"
_RECURRENCE_WEEKLY = "weekly"
_RECURRENCE_CRON = "cron"
_RECURRENCES = (_RECURRENCE_ONCE, _RECURRENCE_DAILY, _RECURRENCE_WEEKLY, _RECURRENCE_CRON)

_VALID_INSTANCE_MODES = ("new_instance", "reuse_instance")


# ---------------------------------------------------------------------------
# Payload / response models (canonical home — phase-3 imports these)
# ---------------------------------------------------------------------------


class ScheduleCreatePayload(BaseModel):
    """Create-side contract. CANONICAL field names (architecture §5):

    ``timezone`` (not ``tz``), ``cron_expression`` (not ``raw_cron``),
    ``weekday`` is 0=Sun..6=Sat (matches cron DOW). Phase-3 REST may
    surface Mon-Sun for UI convention but maps at its boundary.
    """

    label: str = Field(..., min_length=1, max_length=128)
    agent: str = Field(..., description="Agent ID to invoke")
    message: str = Field(..., min_length=1)
    when: str = Field(
        ...,
        description=(
            "User-stated local time: ISO 8601 (naive or tz-aware) for "
            "recurrence=once, HH:MM for daily/weekly. Unused for cron."
        ),
    )
    timezone: str | None = Field(
        default=None,
        description=(
            "IANA timezone (e.g. 'America/New_York'); None = "
            "SchedulingConfig.default_timezone, then host-local, then UTC "
            "with a loud warning echoed in the response."
        ),
    )
    recurrence: str = Field(..., description="once | daily | weekly | cron")
    weekday: int | None = Field(
        default=None,
        ge=0,
        le=6,
        description="0=Sun..6=Sat for recurrence=weekly (canonical, matches cron DOW)",
    )
    cron_expression: str | None = Field(
        default=None,
        description="Raw cron expression. Required when recurrence=cron.",
    )
    project_id: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    instance_mode: str = Field(
        default="new_instance",
        description="new_instance (default) | reuse_instance",
    )


class ScheduleCreateResponse(BaseModel):
    source_id: str
    label: str
    status: str
    next_run_at_local: str | None  # ISO in the schedule's timezone
    next_run_at_utc: str | None  # ISO in UTC
    tz_warning: str = ""  # non-empty when the tz chain fell back


class ScheduleCancelResponse(BaseModel):
    source_id: str
    status: str  # "cancelled"
    cancelled_at: str  # ISO UTC
    last_execution_id: str | None = None  # §5.3 echo — cancel the in-flight JobItem with this


class ScheduleDetail(BaseModel):
    source_id: str
    label: str
    status: str
    recurrence: str | None  # once | daily | weekly | cron
    local_time: str | None  # HH:MM for daily/weekly; full user-stated ISO for once
    timezone: str | None  # IANA name (resolved, as stored)
    weekday: int | None  # 0=Sun..6=Sat (canonical)
    cron_expression: str | None  # raw cron for recurrence="cron"
    instance_mode: str | None
    next_run_at_local: str | None
    next_run_at_utc: str | None
    agent: str | None  # = REST `agent_id`
    project_id: str | None
    last_run_at: str | None
    cancelled_at: str | None = None  # populated when status == "cancelled"
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


# ---------------------------------------------------------------------------
# Dependency wiring — configured by InstanceManager.scheduling_service
# (lazy property mount); phase-3 REST reaches the same configured module.
# ---------------------------------------------------------------------------


class _Deps:
    """Service dependencies. ``None`` until ``configure()`` runs."""

    def __init__(self) -> None:
        self.source_repo: Any = None
        self.source_registry: Any = None


_deps = _Deps()

# Per-source serialization (see module docstring "Concurrency model").
_source_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _get_source_lock(source_id: str) -> asyncio.Lock:
    """Return the per-source lock, creating it on first use (never evicted)."""
    async with _locks_guard:
        lock = _source_locks.get(source_id)
        if lock is None:
            lock = asyncio.Lock()
            _source_locks[source_id] = lock
        return lock


def configure(source_repo: Any = None, source_registry: Any = None) -> None:
    """Wire the repository / registry dependencies (idempotent).

    Called by ``InstanceManager.scheduling_service`` on first access and
    safe to call again with richer deps. Passing ``None`` leaves the
    existing value untouched so partial test doubles do not clobber a
    previously-configured dep.
    """
    if source_repo is not None:
        _deps.source_repo = source_repo
    if source_registry is not None:
        _deps.source_registry = source_registry


def _require_repo() -> Any:
    if _deps.source_repo is None:
        raise RuntimeError(
            "scheduling_service is not configured: source_repo missing "
            "(mount via InstanceManager.scheduling_service first)"
        )
    return _deps.source_repo


def _require_registry() -> Any:
    if _deps.source_registry is None:
        raise RuntimeError(
            "scheduling_service is not configured: source_registry missing "
            "(mount via InstanceManager.scheduling_service first)"
        )
    return _deps.source_registry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _default_scheduling_timezone() -> str | None:
    """Best-effort read of SchedulingConfig.default_timezone (None on boot-order gaps)."""
    try:
        from daemon.config import SchedulingConfig

        return SchedulingConfig().default_timezone
    except Exception as exc:  # noqa: BLE001 — bootstrap-safety (mirrors adapter)
        logger.debug("scheduling_service: SchedulingConfig unavailable (%s)", exc)
        return None


def _zone_name(zone: Any) -> str:
    """IANA name for a resolved zone; ``datetime.timezone.utc`` renders as 'UTC'."""
    if isinstance(zone, ZoneInfo):
        return str(zone)
    return "UTC"


def _parse_hhmm(when: str) -> tuple[int, int]:
    """Parse 'HH:MM' (or 'HH:MM:SS') into (hour, minute). Raises ValueError."""
    text = when.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.hour, parsed.minute
    raise ValueError(
        f"Invalid when '{when}' for a recurring schedule: expected HH:MM (local wall-clock)"
    )


def _resolve_schedule_timezone(payload_tz: str | None) -> tuple[Any, str, str]:
    """Resolve the payload timezone through the D2 chain.

    Returns ``(zone, zone_name, tz_warning)``. The warning is NEVER
    suppressed (OD-4) — callers echo it into every response surface.
    """
    from daemon.util.tz import resolve_timezone

    zone, warning = resolve_timezone(
        payload_tz,
        default=_default_scheduling_timezone(),
        for_tool=True,
    )
    return zone, _zone_name(zone), (warning or "")


def _build_config_json(
    *,
    payload: ScheduleCreatePayload,
    zone_name: str,
    run_at_iso: str | None,
    cron_expression: str | None,
    local_time: str,
) -> dict[str, Any]:
    """Build the ``source_configs.config`` dict for one schedule row.

    The adapter reads: run_at / schedule / timezone / agent / message /
    project_id / priority / instance_mode. The recurrence / local_time /
    timezone keys are human-intent echoes for the list/get surfaces.
    """
    config: dict[str, Any] = {
        "agent": payload.agent,
        "message": payload.message,
        "timezone": zone_name,
        "recurrence": payload.recurrence,
        "local_time": local_time,
    }
    if payload.project_id:
        config["project_id"] = payload.project_id
    if payload.priority != 5:
        config["priority"] = payload.priority
    # One-time schedules are forced to new_instance by the adapter anyway;
    # store the requested value so the echo is honest.
    config["instance_mode"] = payload.instance_mode
    if run_at_iso is not None:
        config["run_at"] = run_at_iso
    if cron_expression is not None:
        config["schedule"] = cron_expression
    return config


def _compute_next_run(config: dict[str, Any]) -> tuple[str | None, str | None, str]:
    """Compute (next_run_local_iso, next_run_utc_iso, tz_warning) for a config dict.

    Mirrors ``SchedulerAdapter._get_next_trigger_time`` semantics:
    ``once`` → stored run_at (None once past); recurring → croniter from
    now in the stored timezone. Returns (None, None, "") when the
    timezone is unresolvable or the schedule has no next occurrence.
    """
    tz_name = config.get("timezone")
    run_at_raw = config.get("run_at")
    cron_expr = config.get("schedule")
    try:
        zone = ZoneInfo(tz_name) if tz_name else None
    except Exception:  # noqa: BLE001 — bad stored tz → no echo rather than raise
        zone = None
    if zone is None:
        return None, None, ""

    now = datetime.now(zone)
    next_dt: datetime | None = None
    if run_at_raw:
        try:
            run_at = datetime.fromisoformat(str(run_at_raw).replace("Z", "+00:00"))
        except ValueError:
            return None, None, ""
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=zone)
        # Past-due one-shots are not "next" — the adapter either fires them
        # immediately (within the lateness cap) or skips them (D3); either
        # way the operator reads the truth from the executions history.
        if run_at > now:
            next_dt = run_at
    elif cron_expr:
        try:
            next_dt = croniter(cron_expr, now).get_next(datetime)
        except (CroniterBadCronError, ValueError):
            return None, None, ""

    if next_dt is None:
        return None, None, ""
    local_iso = next_dt.isoformat()
    utc_iso = next_dt.astimezone(_stdlib_timezone.utc).isoformat()
    return local_iso, utc_iso, ""


def _scheduler_row_or_none(config: Any) -> Any:
    """Return the row when it exists and is a scheduler row, else None."""
    if config is None:
        return None
    if getattr(config, "source_type", None) != SCHEDULER_SOURCE_TYPE:
        return None
    return config


async def _resolve_schedule_ref(schedule_id: str) -> tuple[Any, Any]:
    """Resolve an id-or-label reference to a scheduler row.

    Tries ``source_id`` first, then falls back to the table-level name
    column (the schedule label) — REST DELETE/GET accept either form
    (phase-3 §1.5). Returns ``(row_or_None, repo)``.
    """
    repo = _require_repo()
    row = _scheduler_row_or_none(await asyncio.to_thread(repo.get_source_config, schedule_id))
    if row is None:
        row = _scheduler_row_or_none(
            await asyncio.to_thread(repo.get_source_config_by_name, schedule_id)
        )
    return row, repo


async def _evict_and_stop_adapter(source_id: str) -> None:
    """Evict the registry entry FIRST, then drain the captured adapter (§5.2).

    Uses the public ``unregister`` (pops ``_adapters`` and cancels the
    supervisor / autostart tasks synchronously) so concurrent trigger
    lookups lose sight of the adapter immediately, then stops the
    captured reference directly (grace period drains in-flight
    ``_execute_run``). ``stop_adapter`` is deliberately NOT used after
    eviction — it re-looks-up ``_adapters`` and would no-op.
    """
    registry = _require_registry()
    adapter = registry.get(source_id)
    if adapter is None:
        return
    try:
        registry.unregister(source_id)
    except Exception as exc:  # noqa: BLE001 — eviction is best-effort; drain continues
        logger.warning("scheduling_service: unregister failed for %s: %s", source_id, exc)
    try:
        await adapter.stop()
    except Exception as exc:  # noqa: BLE001 — drain failure must not block the DB write
        logger.warning("scheduling_service: adapter drain failed for %s: %s", source_id, exc)


async def _rebuild_register_start(source_id: str, row: Any) -> bool:
    """Create a fresh adapter from the CURRENT persisted config, register, start.

    The rebuild is NEW logic (architecture §5.1): no existing public seam
    rebuilds an adapter outside ``start_all``. Uses the registry's
    ``_create_adapter_from_config`` (read-only use of the existing
    builder — no registry API change) + the public ``register`` /
    ``start_adapter``. Returns True when the adapter is running.
    """
    registry = _require_registry()
    try:
        adapter = await registry._create_adapter_from_config(row)
        if adapter is None:
            raise RuntimeError("adapter builder returned None")
        registry.register(adapter)
    except Exception as exc:  # noqa: BLE001 — create persists regardless; fires on next boot
        logger.warning(
            "scheduling_service: adapter rebuild failed for %s (%s); "
            "schedule persists and will start on next boot",
            source_id,
            exc,
        )
        return False
    try:
        return bool(await registry.start_adapter(source_id))
    except Exception as exc:  # noqa: BLE001 — same boot-fallback contract
        logger.warning(
            "scheduling_service: start_adapter failed for %s (%s); "
            "schedule persists and will start on next boot",
            source_id,
            exc,
        )
        return False


def _last_execution_id(schedule_id: str) -> str | None:
    """Latest ``schedule_executions.execution_id`` by ``triggered_at`` (§5.3 echo)."""
    repo = _require_repo()
    try:
        latest = repo.get_latest_execution(schedule_id)
    except Exception as exc:  # noqa: BLE001 — history echo is best-effort
        logger.warning(
            "scheduling_service: execution lookup failed for %s: %s", schedule_id, exc
        )
        return None
    return latest.execution_id if latest is not None else None


def _last_run_at(schedule_id: str) -> str | None:
    """Latest ``schedule_executions.triggered_at`` for the detail surfaces."""
    repo = _require_repo()
    try:
        latest = repo.get_latest_execution(schedule_id)
    except Exception as exc:  # noqa: BLE001 — history echo is best-effort
        logger.warning(
            "scheduling_service: execution lookup failed for %s: %s", schedule_id, exc
        )
        return None
    return latest.triggered_at if latest is not None else None


def _row_to_detail(row: Any, *, tz_warning: str = "") -> ScheduleDetail:
    """Map a SourceConfig scheduler row onto the canonical ScheduleDetail."""
    config = row.config or {}
    next_local, next_utc, next_warning = _compute_next_run(config)
    warning = tz_warning or next_warning
    cancelled_at = None
    if row.status == "cancelled":
        cancelled_at = getattr(row, "updated_at", None)
    return ScheduleDetail(
        source_id=row.source_id,
        label=row.name,
        status=row.status,
        recurrence=config.get("recurrence"),
        local_time=config.get("local_time"),
        timezone=config.get("timezone"),
        weekday=None,  # stored cron DOW is authoritative; weekday echo is create-time only
        cron_expression=config.get("schedule") if config.get("recurrence") == _RECURRENCE_CRON else None,
        instance_mode=config.get("instance_mode"),
        next_run_at_local=next_local,
        next_run_at_utc=next_utc,
        agent=config.get("agent"),
        project_id=config.get("project_id"),
        last_run_at=_last_run_at(row.source_id),
        cancelled_at=cancelled_at,
        tz_warning=warning,
    )


# ---------------------------------------------------------------------------
# Service functions (module-level async; phase 3 imports these names verbatim)
# ---------------------------------------------------------------------------


async def create_schedule(
    payload: ScheduleCreatePayload | dict,
    *,
    caller_instance_id: str,
    caller_agent_id: str,
) -> ScheduleCreateResponse:
    """Create a schedule row and best-effort start its adapter.

    Raises ``ValueError`` on invalid input (duplicate label, unknown
    agent, bad recurrence/when/timezone-cron mismatch) — callers map to
    4xx / error strings. ``caller_instance_id`` / ``caller_agent_id``
    identify the caller for audit logging; the invoked agent comes from
    ``payload.agent``.
    """
    if isinstance(payload, dict):
        payload = ScheduleCreatePayload.model_validate(payload)

    repo = _require_repo()

    # (1) Label validation + uniqueness (table-level name column IS the label).
    label = payload.label.strip()
    if not label:
        raise ValueError("label must be a non-empty string")
    existing = await asyncio.to_thread(repo.get_source_config_by_name, label)
    if existing is not None:
        raise ValueError(f"label already exists: {label}")

    # (2) Fail-fast agent existence (OD-3) — BEFORE any DB write.
    from daemon.registry import get_registry

    if not get_registry().exists(payload.agent):
        raise ValueError(f"agent_id {payload.agent} does not exist")

    # (3) Recurrence shape validation.
    recurrence = payload.recurrence
    if recurrence not in _RECURRENCES:
        raise ValueError(
            f"Invalid recurrence '{recurrence}': expected one of {', '.join(_RECURRENCES)}"
        )
    if recurrence == _RECURRENCE_CRON:
        if not payload.cron_expression:
            raise ValueError("cron_expression is required when recurrence='cron'")
        try:
            zone_probe, zone_name, tz_warning = _resolve_schedule_timezone(payload.timezone)
            now_probe = datetime.now(zone_probe)
            croniter(payload.cron_expression, now_probe)
        except (CroniterBadCronError, ValueError) as exc:
            raise ValueError(f"Invalid cron_expression '{payload.cron_expression}': {exc}") from exc
        run_at_iso: str | None = None
        cron_expression: str | None = payload.cron_expression
        local_time = ""
    elif recurrence == _RECURRENCE_ONCE:
        if payload.cron_expression:
            raise ValueError("cron_expression is only valid when recurrence='cron'")
        if payload.weekday is not None:
            raise ValueError("weekday is only valid when recurrence='weekly'")
        try:
            parsed_when = datetime.fromisoformat(payload.when.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Invalid when '{payload.when}' for recurrence='once': expected ISO 8601"
            ) from exc
        zone, zone_name, tz_warning = _resolve_schedule_timezone(payload.timezone)
        anchored, anchor_warning = (parsed_when, "")
        if parsed_when.tzinfo is None:
            from daemon.util.tz import anchor_local_to_utc

            anchored, anchor_warning = anchor_local_to_utc(parsed_when, zone)
        tz_warning = tz_warning or anchor_warning
        run_at_iso = anchored.isoformat()
        cron_expression = None
        local_time = payload.when.strip()
    else:  # daily / weekly
        if payload.cron_expression:
            raise ValueError("cron_expression is only valid when recurrence='cron'")
        hour, minute = _parse_hhmm(payload.when)
        zone, zone_name, tz_warning = _resolve_schedule_timezone(payload.timezone)
        if recurrence == _RECURRENCE_DAILY:
            if payload.weekday is not None:
                raise ValueError("weekday is only valid when recurrence='weekly'")
            cron_expression = f"{minute} {hour} * * *"
        else:
            if payload.weekday is None:
                raise ValueError("weekday is required when recurrence='weekly' (0=Sun..6=Sat)")
            cron_expression = f"{minute} {hour} * * {payload.weekday}"
        run_at_iso = None
        local_time = payload.when.strip()

    if payload.instance_mode not in _VALID_INSTANCE_MODES:
        raise ValueError(
            f"Invalid instance_mode '{payload.instance_mode}': "
            f"expected one of {', '.join(_VALID_INSTANCE_MODES)}"
        )

    # (4) Persist the row.
    config_json = _build_config_json(
        payload=payload,
        zone_name=zone_name,
        run_at_iso=run_at_iso,
        cron_expression=cron_expression,
        local_time=local_time,
    )
    row = await asyncio.to_thread(
        repo.create_source_config,
        SCHEDULER_SOURCE_TYPE,
        label,
        config_json,
        None,
        True,  # enabled — lifecycle flows through update/cancel, never create
        True,  # autostart — boot filter honours status/enabled
    )

    # (5) Best-effort rebuild + register + start (no daemon restart needed).
    started = False
    try:
        started = await _rebuild_register_start(row.source_id, row)
    except Exception as exc:  # noqa: BLE001 — row persists; adapter fires on next boot
        logger.warning(
            "scheduling_service: post-create start failed for %s: %s", row.source_id, exc
        )

    next_local, next_utc, next_warning = _compute_next_run(config_json)
    status = row.status
    if started:
        refreshed = await asyncio.to_thread(repo.get_source_config, row.source_id)
        status = refreshed.status if refreshed is not None else row.status
    logger.info(
        "scheduling_service: created schedule %s (label=%r, agent=%s, caller=%s/%s, started=%s)",
        row.source_id,
        label,
        payload.agent,
        caller_agent_id,
        caller_instance_id,
        started,
    )
    return ScheduleCreateResponse(
        source_id=row.source_id,
        label=row.name,
        status=status,
        next_run_at_local=next_local,
        next_run_at_utc=next_utc,
        tz_warning=tz_warning or next_warning,
    )


async def cancel_schedule(schedule_id: str) -> ScheduleCancelResponse:
    """Terminal cancel — atomic row write, history preserved, §5.3 echo.

    Raises ``ValueError`` when the reference does not resolve to a
    scheduler row (404) or when the row is already cancelled (409).
    Serializes against ``update_schedule`` / concurrent cancels via the
    shared per-source lock; the cancelled-check runs INSIDE the lock on
    a freshly re-read row.
    """
    row, repo = await _resolve_schedule_ref(schedule_id)
    if row is None:
        raise ValueError(f"Schedule not found: {schedule_id}")

    lock = await _get_source_lock(row.source_id)
    async with lock:
        # Fresh re-read inside the critical section — a concurrent update
        # or cancel may have landed between the outer resolve and the lock.
        row = _scheduler_row_or_none(
            await asyncio.to_thread(repo.get_source_config, row.source_id)
        )
        if row is None:
            raise ValueError(f"Schedule not found: {schedule_id}")
        if row.status == "cancelled":
            raise ValueError(f"Schedule already cancelled: {row.source_id}")

        # §5.2: evict BEFORE the drain so trigger lookups cannot reach the
        # draining adapter. The atomic cancel write follows the drain so a
        # still-draining execution cannot observe a half-cancelled row.
        await _evict_and_stop_adapter(row.source_id)
        cancelled_row = await asyncio.to_thread(repo.cancel_source_config, row.source_id)
        if cancelled_row is None:
            # Lost a race to a concurrent delete (operators can still purge
            # non-scheduler sources via /api/sources). Surface as 404-shape.
            raise ValueError(f"Schedule not found: {schedule_id}")

    cancelled_at = datetime.now(_stdlib_timezone.utc).isoformat()
    execution_id = _last_execution_id(row.source_id)
    logger.info(
        "scheduling_service: cancelled schedule %s (label=%r, last_execution_id=%s)",
        row.source_id,
        row.name,
        execution_id,
    )
    return ScheduleCancelResponse(
        source_id=row.source_id,
        status="cancelled",
        cancelled_at=cancelled_at,
        last_execution_id=execution_id,
    )


async def get_schedule(schedule_id: str) -> ScheduleDetail | None:
    """Fetch one schedule (id or label) with BOTH local + UTC next-run echo.

    Returns ``None`` when the reference does not resolve to a scheduler
    row. Cancelled rows ARE returned (the "did I actually cancel X?"
    path — architecture §3.4).
    """
    row, _repo = await _resolve_schedule_ref(schedule_id)
    if row is None:
        return None
    return _row_to_detail(row)


async def list_schedules(
    *,
    project_id: str | None = None,
    status: str | None = None,
    include_cancelled: bool = False,
    caller_agent_id: str | None = None,
) -> list[ScheduleListItem]:
    """List scheduler rows; cancelled hidden by default (ADR-005).

    Filters: ``status`` (exact row status), ``project_id`` (config-JSON
    key match), ``caller_agent_id`` (caller-scoped — matches
    ``config.agent``). ``include_cancelled=True`` un-hides cancelled
    rows. The repository page cap is 100 rows (``list_source_configs``
    default) — operator-scale, not a scan surface.
    """
    repo = _require_repo()
    rows = await asyncio.to_thread(repo.list_source_configs, None, status)
    items: list[ScheduleListItem] = []
    for row in rows:
        if getattr(row, "source_type", None) != SCHEDULER_SOURCE_TYPE:
            continue
        if not include_cancelled and row.status == "cancelled":
            continue
        config = row.config or {}
        if project_id is not None and config.get("project_id") != project_id:
            continue
        if caller_agent_id is not None and config.get("agent") != caller_agent_id:
            continue
        detail = _row_to_detail(row)
        items.append(ScheduleListItem(**detail.model_dump()))
    return items


async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdatePayload | dict,
) -> ScheduleUpdateResponse:
    """Reschedule / re-message / pause / resume under the per-source lock.

    Stop → mutate → (rebuild → register → start) sequence (§8 item 10);
    the registry entry is evicted BEFORE the drain (§5.2). Raises
    ``ValueError`` on unknown reference (404), already-cancelled row
    (409 — cancel is terminal), or invalid payload fields. A fire that
    lands inside the brief rebuild gap is missed and acceptable (§5.1).
    """
    if isinstance(payload, dict):
        payload = ScheduleUpdatePayload.model_validate(payload)

    row, repo = await _resolve_schedule_ref(schedule_id)
    if row is None:
        raise ValueError(f"Schedule not found: {schedule_id}")

    lock = await _get_source_lock(row.source_id)
    async with lock:
        # Fresh re-read inside the critical section.
        row = _scheduler_row_or_none(
            await asyncio.to_thread(repo.get_source_config, row.source_id)
        )
        if row is None:
            raise ValueError(f"Schedule not found: {schedule_id}")
        if row.status == "cancelled":
            raise ValueError(
                f"Schedule is cancelled and cannot be updated: {row.source_id}"
            )

        config = dict(row.config or {})
        tz_warning = ""

        if payload.timezone is not None and payload.timezone != config.get("timezone"):
            _, zone_name, tz_warning = _resolve_schedule_timezone(payload.timezone)
            config["timezone"] = zone_name

        when_changed = False
        if payload.when is not None:
            when_text = payload.when.strip()
            recurrence = config.get("recurrence")
            if not when_text:
                raise ValueError("when must be a non-empty string")
            if recurrence == _RECURRENCE_ONCE:
                try:
                    parsed_when = datetime.fromisoformat(when_text.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid when '{when_text}' for recurrence='once': expected ISO 8601"
                    ) from exc
                try:
                    zone = ZoneInfo(config.get("timezone")) if config.get("timezone") else None
                except Exception:  # noqa: BLE001
                    zone = None
                anchored = parsed_when
                if parsed_when.tzinfo is None and zone is not None:
                    from daemon.util.tz import anchor_local_to_utc

                    anchored, anchor_warning = anchor_local_to_utc(parsed_when, zone)
                    tz_warning = tz_warning or anchor_warning
                config["run_at"] = anchored.isoformat()
            elif recurrence in (_RECURRENCE_DAILY, _RECURRENCE_WEEKLY):
                hour, minute = _parse_hhmm(when_text)
                cron_expr = config.get("schedule")
                if cron_expr:
                    parts = cron_expr.split()
                    if len(parts) >= 2:
                        parts[0] = str(minute)
                        parts[1] = str(hour)
                        config["schedule"] = " ".join(parts)
            else:
                raise ValueError(
                    f"when is not updatable for recurrence={recurrence!r} (cron schedules "
                    f"are rescheduled via their raw expression)"
                )
            config["local_time"] = when_text
            when_changed = True

        if payload.message is not None:
            if not payload.message.strip():
                raise ValueError("message must be a non-empty string")
            config["message"] = payload.message
        if payload.priority is not None:
            config["priority"] = payload.priority
        if payload.label is not None:
            new_label = payload.label.strip()
            if not new_label:
                raise ValueError("label must be a non-empty string")
            if new_label != row.name:
                clash = await asyncio.to_thread(repo.get_source_config_by_name, new_label)
                if clash is not None:
                    raise ValueError(f"label already exists: {new_label}")

        was_enabled = bool(row.enabled)
        pausing = payload.paused is True
        resuming = payload.paused is False

        # §5.2: evict BEFORE the drain so no trigger lookup reaches the
        # draining adapter while the config mutates underneath it.
        await _evict_and_stop_adapter(row.source_id)

        if pausing:
            config_enabled = False
            status_value = "stopped"
        elif resuming:
            config_enabled = True
            status_value = "stopped"  # adapter flips to RUNNING on start
        else:
            # Rebuild path without an explicit pause/resume (reschedule /
            # re-message): keep the previous enabled flag so an already
            # paused schedule stays paused after an edit.
            config_enabled = was_enabled
            status_value = "stopped"

        updated = await asyncio.to_thread(
            repo.update_source_config,
            row.source_id,
            None,
            payload.label.strip() if payload.label is not None else None,
            config,
            None,
            config_enabled,
            True,  # autostart preserved
        )
        if updated is None:
            raise ValueError(f"Schedule not found: {schedule_id}")
        # The adapter was drained above, so the row status is explicitly
        # STOPPED regardless of pause/resume/edit (the plan's "let the
        # adapter flip to RUNNING on start" sequence). Without this write
        # the row would keep its stale status (e.g. 'running' for a paused
        # schedule) and mislead the list/detail surfaces.
        await asyncio.to_thread(repo.update_source_status, row.source_id, "stopped", None)

        started = False
        if config_enabled:
            started = await _rebuild_register_start(row.source_id, updated)

        status = updated.status
        if started:
            refreshed = await asyncio.to_thread(repo.get_source_config, row.source_id)
            status = refreshed.status if refreshed is not None else updated.status

    next_local, next_utc, next_warning = _compute_next_run(config)
    logger.info(
        "scheduling_service: updated schedule %s (paused=%s, started=%s, when_changed=%s)",
        row.source_id,
        bool(pausing),
        started,
        when_changed,
    )
    return ScheduleUpdateResponse(
        source_id=updated.source_id,
        label=updated.name,
        status=status,
        paused=not config_enabled,
        next_run_at_local=next_local,
        next_run_at_utc=next_utc,
        tz_warning=tz_warning or next_warning,
    )
