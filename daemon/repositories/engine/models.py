"""SQLModel tables for the engine substrate (Engine Arc Phase 1 — T1.3).

Two tables, two migration files, two models (plan:
``.agents/shared/planning/engine-arc-phase1/phase1-plan.md`` — T1.1-T1.3,
DDL contract in section 5):

* :class:`EngineJob` — the MUTABLE registration + last-outcome row
  (``engine_jobs``). One row per registered job; the DB is the source of
  truth and the engine keeps a RAM cache for hot reads (frozen design:
  ``central-polling-engine-study/decision-report.md`` section B.2,
  lines 110-131).
* :class:`EngineRun` — the APPEND-ONLY run audit (``engine_runs``). One
  row per attempted tick; never updated by the substrate.

DUAL-DRIVER CONTRACT (memoir / tool_usage / bash_spawn precedent)
----------------------------------------------------------------
Both models are registered into ``SQLModel.metadata`` by the late-import
block in ``daemon/manager.py`` BEFORE ``create_all`` — that is the
PostgreSQL source of truth on existing databases. Fresh SQLite databases
pick the tables up from the migration runner
(``20261121_120000_create_engine_jobs.sql`` /
``20261121_120001_create_engine_runs.sql``). Brand-new tables, so there
is NO ``_ensure_postgres_columns`` mirror (that hook owns COLUMN
evolution of pre-existing tables).

3-SITE INDEX LOCKSTEP (house discipline)
----------------------------------------------------------------
The index names declared in ``__table_args__`` below are BYTE-IDENTICAL
to the ``CREATE [UNIQUE] INDEX IF NOT EXISTS`` statements in the two
migration files, and to the parity assertions in the substrate test pack.
A rename in one place without the others is a silent schema divergence.

``service_tag`` IS UNIQUE (AMD-7)
----------------------------------------------------------------
The plan's section 5 index list named a plain ``idx_engine_jobs_service_tag``.
The architect's AMD-7 hardening rules that ``service_tag`` (the stable ops
identity and the primary metrics label) must be UNIQUE FROM DAY ONE: one
tag maps to exactly one registered service, which is what protects the
future maintenancer inspect/steer surface and the ``/metrics`` UI table.
Adding UNIQUE later is the breaking change this review exists to prevent.
The plain index is therefore REPLACED by the named UNIQUE index
``uq_engine_jobs_service_tag`` — same column, unique semantics, no
redundant duplicate index. The migration file documents the swap at the
same place.

DORMANCY FENCES
----------------------------------------------------------------
* Fence F-3: this module declares NO dormant control column of the
  resource-cost-control family, and no string here pairs the
  subprocess-registry table name with a control token. Terminal state is
  ``last_outcome`` via ``stamp_job_outcome`` (the audit's word-boundary
  fence forbids even NAMING the dormant columns outside their model
  module, so this note deliberately describes them instead of spelling
  them).
* Fence F-6: ``event_kind`` is an EVENT-REGISTRY kind string (P5's
  catalog), NOT a FK or CHECK into ``event.kind`` — the SQLite
  ``event.kind`` CHECK lists only 8 of 24 ``EventKind`` members and is
  already stale.

``metadata`` IS A RESERVED SQLALCHEMY ATTRIBUTE
----------------------------------------------------------------
The declarative base owns ``.metadata``, so the Python attribute is named
``job_metadata`` while the COLUMN is ``metadata`` (the ``job_queue`` /
``dependency_bus`` / ``instance`` precedent). The migration file's column
name is ``metadata`` — that is the DB contract.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Column, Float, Index, Integer, Text
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType
from daemon.services.timestamps import now_utc_iso

# Index names — byte-identical to the companion migrations
# (``20261121_120000_create_engine_jobs.sql`` and
# ``20261121_120001_create_engine_runs.sql``) and to the substrate test
# pack (3-site lockstep).
UQ_ENGINE_JOBS_SERVICE_TAG = "uq_engine_jobs_service_tag"
IDX_ENGINE_JOBS_CATEGORY = "idx_engine_jobs_category"
IDX_ENGINE_JOBS_TRIGGER = "idx_engine_jobs_trigger"
IDX_ENGINE_RUNS_JOB_STARTED = "idx_engine_runs_job_started"
IDX_ENGINE_RUNS_OUTCOME = "idx_engine_runs_outcome"

#: Terminal outcomes the honest state machine writes into
#: ``engine_jobs.last_outcome`` / ``engine_runs.outcome``. Substrate-level
#: vocabulary only — P2 owns the transitions that produce them.
OUTCOME_SUCCEEDED = "SUCCEEDED"
OUTCOME_SKIPPED_PREFIX = "SKIPPED_"
OUTCOME_FAILED_BACKOFF = "FAILED_BACKOFF"
OUTCOME_TIMED_OUT = "TIMED_OUT"

#: Trigger kinds (exactly one per job — validated at registration).
TRIGGER_KIND_INTERVAL = "interval"
TRIGGER_KIND_EVENT = "event"

#: Timeout policies.
TIMEOUT_POLICY_CANCEL = "cancel"
TIMEOUT_POLICY_EXTEND = "extend"

#: Concurrency classes (frozen design: decision-report.md section B.2).
CONCURRENCY_CLASS_MAIN_LOOP = "MAIN_LOOP"
CONCURRENCY_CLASS_DB_OFFLOAD = "DB_OFFLOAD"
CONCURRENCY_CLASS_DEDICATED_EXECUTOR = "DEDICATED_EXECUTOR"


class EngineJob(SQLModel, table=True):
    """One registered engine job (mutable registration + last outcome).

    Identity: ``id`` is the stable ops SLUG (``orphan-watcher``,
    ``sources-gc``, ...) — NOT a uuid. Dynamic re-registration is an
    UPSERT BY ID, so the slug makes that literal and keeps operator
    greps and future ``maintenancer`` queries direct (D1.4). A rename is
    a re-registration with an explicit migration note, never a silent
    second row.

    ``version`` is bumped on every SUCCESSFUL re-registration whose
    content changed (D1.5); an identical re-registration is a no-op echo
    that returns the stored version unchanged.
    """

    __tablename__ = "engine_jobs"

    __table_args__ = (
        Index(UQ_ENGINE_JOBS_SERVICE_TAG, "service_tag", unique=True),
        Index(IDX_ENGINE_JOBS_CATEGORY, "category"),
        Index(IDX_ENGINE_JOBS_TRIGGER, "trigger_kind"),
    )

    # ── identity + registration version ────────────────────────────────
    id: str = Field(
        sa_column=Column("id", Text, primary_key=True),
        description=(
            "Stable ops slug (TEXT primary key). Re-registration upserts "
            "BY this id (D1.4) — a rename is a re-registration with an "
            "explicit migration note, never a silent second row."
        ),
    )
    version: int = Field(
        default=1,
        description=(
            "Registration version; incremented on every successful "
            "re-registration whose content changed (D1.5). Identical "
            "re-registration echoes the stored version."
        ),
    )

    # ── self-describing registration metadata (Emperor addendum) ───────
    service_tag: str = Field(
        sa_column=Column("service_tag", Text, nullable=False),
        description=(
            "Stable ops identity (UNIQUE per AMD-7). Primary metrics "
            "label alongside ``category``; feeds the future maintenancer "
            "tool and the /metrics UI table."
        ),
    )
    service_name: str | None = Field(
        default=None,
        sa_column=Column("service_name", Text, nullable=True),
        description="Human-readable service name (optional).",
    )
    category: str = Field(
        sa_column=Column("category", Text, nullable=False),
        description=(
            "Open-vocabulary category label. Starter vocabulary (D1.10): "
            "reconciler, recovery_sweep, probe, telemetry, realtime_guard, "
            "event_consumer, engine_self, housekeeping. Open by design — "
            "adding a category later forces no migration."
        ),
    )
    module_info: str | None = Field(
        default=None,
        sa_column=Column("module_info", Text, nullable=True),
        description=(
            "Origin module path / package / component of the job's "
            "implementation (debug + management aid)."
        ),
    )
    description: str | None = Field(
        default=None,
        sa_column=Column("description", Text, nullable=True),
        description="Free-form operator description of what the job does.",
    )
    job_metadata: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("metadata", JSONBType, nullable=True),
        description=(
            "Extensible JSON metadata blob (owner / docs / slo / notes "
            "today; more later). Column name is ``metadata``; the Python "
            "attribute is ``job_metadata`` because the declarative base "
            "owns ``.metadata``."
        ),
    )

    # ── trigger (exactly one kind, validated at registration) ──────────
    trigger_kind: str = Field(
        sa_column=Column("trigger_kind", Text, nullable=False),
        description="``interval`` | ``event`` — exactly one trigger per job.",
    )
    interval_seconds: float | None = Field(
        default=None,
        description="Interval trigger cadence in seconds (interval kind only).",
    )
    jitter_seconds: float | None = Field(
        default=None,
        description="Per-job first-tick jitter in seconds (>= 0).",
    )
    first_tick_kind: str | None = Field(
        default=None,
        sa_column=Column("first_tick_kind", Text, nullable=True),
        description="``immediate`` | ``delayed`` | NULL (interval kind only).",
    )
    first_tick_offset_seconds: float | None = Field(
        default=None,
        description=(
            "First-tick offset in seconds for the ``delayed`` kind — the "
            "first-class replacement for drift's ``+30s`` "
            "(decision-report.md section B.2)."
        ),
    )
    event_kind: str | None = Field(
        default=None,
        sa_column=Column("event_kind", Text, nullable=True),
        description=(
            "Event-registry kind string (event kind only). NOT a FK or "
            "CHECK into ``event.kind`` — the SQLite CHECK lists only 8 of "
            "24 members and is already stale (fence F-6)."
        ),
    )
    event_filter: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("event_filter", JSONBType, nullable=True),
        description="JSON filter for the event kind (event kind only).",
    )

    # ── execution contract ─────────────────────────────────────────────
    max_runtime_s: float = Field(
        sa_column=Column("max_runtime_s", Float, nullable=False),
        description="Hard runtime bound per attempt (must be > 0).",
    )
    timeout_policy: str = Field(
        sa_column=Column("timeout_policy", Text, nullable=False),
        description="``cancel`` | ``extend``.",
    )
    retry_max: int = Field(
        default=0,
        description="Maximum retry attempts after a failed run.",
    )
    retry_backoff_base_s: float | None = Field(
        default=None,
        description="Base backoff for retries (seconds).",
    )
    retry_backoff_cap_s: float | None = Field(
        default=None,
        description="Backoff cap for retries (seconds).",
    )
    priority: int = Field(
        default=5,
        description="Scheduling priority, 0..9 (higher = sooner).",
    )
    concurrency_class: str = Field(
        sa_column=Column("concurrency_class", Text, nullable=False),
        description=(
            "``MAIN_LOOP`` | ``DB_OFFLOAD`` | ``DEDICATED_EXECUTOR`` — "
            "how the engine dispatches the job (frozen design section B.2)."
        ),
    )
    kill_switch_key: str | None = Field(
        default=None,
        sa_column=Column("kill_switch_key", Text, nullable=True),
        description=(
            "Optional per-job kill-switch key (env/config tier). Absent = "
            "no per-job switch."
        ),
    )
    enabled: int = Field(
        default=1,
        description=(
            "Registration-level on/off (1/0). Registration-level disable "
            "mirrors unregister semantics without losing the row."
        ),
    )

    # ── last outcome (the honest-stamp surface; F-3 forbids action_*) ───
    last_run_at: str | None = Field(
        default=None,
        sa_column=Column("last_run_at", Text, nullable=True),
        description=(
            "Aware-UTC ISO of the last terminal transition. Stamped on "
            "EVERY terminal outcome including skips, so due-state cannot "
            "strand (P2 owns the transitions)."
        ),
    )
    last_outcome: str | None = Field(
        default=None,
        sa_column=Column("last_outcome", Text, nullable=True),
        description=(
            "``SUCCEEDED`` | ``SKIPPED_*`` | ``FAILED_BACKOFF`` | "
            "``TIMED_OUT``."
        ),
    )
    last_skip_reason: str | None = Field(
        default=None,
        sa_column=Column("last_skip_reason", Text, nullable=True),
        description="Reason token for the last skip outcome (skips only).",
    )
    consecutive_skips: int = Field(
        default=0,
        description="Consecutive skip counter (reset on success; backoff input).",
    )
    dead: int = Field(
        default=0,
        description=(
            "Meta-liveness flag (1 = dead). P3 sets it; for execute_fn-"
            "withheld observer specs the CS-9 carve-out defines it as zero "
            "SKIPPED_SHADOW rows in 2x interval."
        ),
    )

    # ── bookkeeping ────────────────────────────────────────────────────
    registered_at: str = Field(
        default_factory=now_utc_iso,
        sa_column=Column("registered_at", Text, nullable=False),
        description="Aware-UTC ISO of the first registration (never moves).",
    )
    updated_at: str = Field(
        default_factory=now_utc_iso,
        sa_column=Column("updated_at", Text, nullable=False),
        description=(
            "Aware-UTC ISO of the last write (moves on re-registration and "
            "on every outcome stamp)."
        ),
    )


class EngineRun(SQLModel, table=True):
    """One APPEND-ONLY run audit row (``engine_runs``).

    Rows are never updated or deleted by the substrate (retention is a
    later-phase concern per AMD-1(e)). ``boot_id`` + ``host_pid`` let an
    operator separate runs across daemon restarts (the
    ``bash_spawn_registry.boot_id`` precedent).
    """

    __tablename__ = "engine_runs"

    __table_args__ = (
        Index(IDX_ENGINE_RUNS_JOB_STARTED, "job_id", "started_at"),
        Index(IDX_ENGINE_RUNS_OUTCOME, "outcome"),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(
            "id", Integer, primary_key=True, autoincrement=True, nullable=False
        ),
        description="Autoincrement run-row id (history order on both drivers).",
    )
    job_id: str = Field(
        sa_column=Column("job_id", Text, nullable=False),
        description="The engine_jobs.id this run belongs to.",
    )
    service_tag: str | None = Field(
        default=None,
        sa_column=Column("service_tag", Text, nullable=True),
        description=(
            "Denormalized service_tag at run time (the metrics label "
            "survives a later re-registration that changes it)."
        ),
    )
    started_at: str = Field(
        sa_column=Column("started_at", Text, nullable=False),
        description="Aware-UTC ISO of the attempt start.",
    )
    finished_at: str | None = Field(
        default=None,
        sa_column=Column("finished_at", Text, nullable=True),
        description="Aware-UTC ISO of the terminal transition (NULL while running).",
    )
    outcome: str = Field(
        sa_column=Column("outcome", Text, nullable=False),
        description=(
            "``SUCCEEDED`` | ``SKIPPED_*`` | ``FAILED_BACKOFF`` | "
            "``TIMED_OUT``."
        ),
    )
    skip_reason: str | None = Field(
        default=None,
        sa_column=Column("skip_reason", Text, nullable=True),
        description="Reason token when the outcome is a skip.",
    )
    duration_seconds: float | None = Field(
        default=None,
        description="Measured runtime of the attempt, in seconds.",
    )
    attempt: int = Field(
        default=1,
        description="Attempt number within the retry budget (1 = first try).",
    )
    error_class: str | None = Field(
        default=None,
        sa_column=Column("error_class", Text, nullable=True),
        description="Exception class name for a failed attempt (never the message).",
    )
    error_digest: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("error_digest", JSONBType, nullable=True),
        description="Structured error digest (JSON) for a failed attempt.",
    )
    boot_id: str | None = Field(
        default=None,
        sa_column=Column("boot_id", Text, nullable=True),
        description=(
            "uuid4 hex of the daemon process that produced the run "
            "(restart discriminator; the bash_spawn_registry precedent)."
        ),
    )
    host_pid: int | None = Field(
        default=None,
        description="os.getpid() at write time.",
    )


__all__ = [
    "CONCURRENCY_CLASS_DB_OFFLOAD",
    "CONCURRENCY_CLASS_DEDICATED_EXECUTOR",
    "CONCURRENCY_CLASS_MAIN_LOOP",
    "EngineJob",
    "EngineRun",
    "IDX_ENGINE_JOBS_CATEGORY",
    "IDX_ENGINE_JOBS_TRIGGER",
    "IDX_ENGINE_RUNS_JOB_STARTED",
    "IDX_ENGINE_RUNS_OUTCOME",
    "OUTCOME_FAILED_BACKOFF",
    "OUTCOME_SKIPPED_PREFIX",
    "OUTCOME_SUCCEEDED",
    "OUTCOME_TIMED_OUT",
    "TIMEOUT_POLICY_CANCEL",
    "TIMEOUT_POLICY_EXTEND",
    "TRIGGER_KIND_EVENT",
    "TRIGGER_KIND_INTERVAL",
    "UQ_ENGINE_JOBS_SERVICE_TAG",
]
