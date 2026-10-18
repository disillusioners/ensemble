"""``resource_monitor`` — observability spine for the resource-cost-control
commission, Phase 1.

Four SQLModel tables per ``architecture-recommendation.md`` §2.4
(binding). Storage posture:

* **SQLite** — companion migrations in
  ``daemon/migrations/versions/20261018_090001..090004_create_resource_*.sql``
  are the canonical DDL; the migration runner applies them on every
  startup (idempotent ``CREATE TABLE IF NOT EXISTS``).
* **PostgreSQL** — schema comes from ``SQLModel.metadata.create_all``
  with THESE models as the canonical source. Registration into the
  metadata happens via the one-line late-import block in
  ``daemon/manager.py`` (the app_settings / snapshot /
  maintenance_runs / drift_events precedent), placed BEFORE the
  ``create_all`` call so brand-new tables arrive on existing PG
  databases on the next boot with zero operator action.
* 3-site index lockstep (D7): SQLModel ``__table_args__`` +
  ``.sql`` migration + index names BYTE-IDENTICAL. ``AC3`` enforces
  via the storage test pack.
* TZ posture (D9): ``bucket_ts`` and all new TIMESTAMP columns are
  NAIVE-UTC (house convention — the ``task`` table lineage, NOT the
  ``event`` table's aware-UTC). Reads normalize via
  ``coerce_to_aware_utc`` (``daemon/services/timestamps.py:107``) at
  the read seam (P3 traceback owns the read-side normalization).
* P1 boundary (D13): T1 attribution columns are written NULL/0
  defaults; P2 populates them. Keeps the slice independently
  mergeable.

Tables (all dual-dialect, no ``now()``, no native enums, no
``USING``):

* **T1** ``resource_daemon_bucket`` — 1 row / tick; carries the
  global daemon + cgroup state plus sampler self-cost.
* **T2** ``resource_instance_sample`` — ≤ 1 row / (instance, tick)
  with cardinality cap ≤64/tick (top-N + ``__overflow__`` sentinel).
* **T3** ``resource_pid_map`` — generalized subprocess registry
  (the A11 service_tracking pattern; partial UNIQUE ``pid`` over
  ``exited_at IS NULL``, dual-dialect WHERE). Carries dormant v2
  ``action`` / ``action_by`` / ``acted_at`` columns for P6.
* **T4** ``resource_limit_policy`` — control seam. EMPTY in v1;
  ``enabled`` default false, ``user_gated`` NOT NULL default true.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, Index, Integer, Text, text
from sqlmodel import Field, SQLModel


# ── T1 — resource_daemon_bucket ─────────────────────────────────────────────


class ResourceDaemonBucket(SQLModel, table=True):
    """ONE row per sampler tick (architecture-recommendation §2.4 T1).

    Carries global daemon + cgroup v2 state plus sampler self-cost
    counters. ``bucket_ts`` is the per-tick wall-clock anchor; the
    UNIQUE constraint on it makes inserts idempotent on retries and
    gives the roll-down pass a natural per-bucket boundary.
    """

    __tablename__ = "resource_daemon_bucket"

    __table_args__ = (
        # Architecture §2.4 T1 indexes: ``UNIQUE(bucket_ts)`` only.
        # SQLModel emits the UNIQUE index when we set
        # ``unique=True`` on the column below (Field-level), so the
        # 3-site name parity check is enforced by byte-comparing the
        # model + .sql index declarations in the storage test pack.
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    # Naive-UTC TIMESTAMP per D9. NOT NULL — every bucket has a
    # well-defined wall-clock anchor. The companion .sql migration
    # declares the column as ``TEXT`` (ISO-8601 naive digits) for
    # dialect parity; the Python side stamps via
    # ``daemon.services.timestamps.now_utc_naive``.
    bucket_ts: str = Field(
        sa_column=Column("bucket_ts", Text, nullable=False, unique=True),
        description="Naive-UTC ISO-8601 string. UNIQUE — one bucket per tick.",
    )

    interval_s: Optional[int] = Field(default=None)
    rss_kb: Optional[int] = Field(default=None)
    pss_kb: Optional[int] = Field(default=None, description="PSS (1/10 cadence, env-gated)")
    anon_kb: Optional[int] = Field(default=None)
    swap_kb: Optional[int] = Field(default=None)
    vsize_kb: Optional[int] = Field(default=None)
    hw_peak_kb: Optional[int] = Field(default=None)
    threads: Optional[int] = Field(default=None)
    fds: Optional[int] = Field(default=None)
    cpu_user_usec: Optional[int] = Field(default=None)
    cpu_sys_usec: Optional[int] = Field(default=None)
    cg_current_kb: Optional[int] = Field(default=None)
    cg_anon_kb: Optional[int] = Field(default=None)
    cg_file_kb: Optional[int] = Field(default=None)
    cg_slab_kb: Optional[int] = Field(default=None)
    cg_peak_kb: Optional[int] = Field(default=None)
    child_rss_total_kb: Optional[int] = Field(default=None)
    # D13 — attribution columns exist but stay NULL/0 in P1.
    attributed_bytes: Optional[int] = Field(default=None)
    unattributed_kb: Optional[int] = Field(default=None)
    coverage_pct: Optional[float] = Field(default=None)
    # Sampler self-cost counters (D12 — alarmable).
    sample_cost_ms: Optional[float] = Field(default=None)
    sampler_lag_ms: Optional[float] = Field(default=None)
    missed_samples: Optional[int] = Field(default=None)


# ── T2 — resource_instance_sample ───────────────────────────────────────────


class ResourceInstanceSample(SQLModel, table=True):
    """≤ 1 row / (instance, tick) — per-instance cost attribution.

    Architecture §2.4 T2. ``estimated`` is NOT NULL because per-
    instance in-process CPU/RAM is partial by physics (the daemon
    shares one process); the column labels the row as a charged
    estimate vs. an exact read.
    """

    __tablename__ = "resource_instance_sample"

    __table_args__ = (
        # 3-site index lockstep — names MUST match
        # ``daemon/migrations/versions/20261018_090002_create_resource_instance_sample.sql``.
        Index("idx_resource_instance_sample_instance_bucket",
              "instance_id", "bucket_ts"),
        Index("idx_resource_instance_sample_bucket", "bucket_ts"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    bucket_ts: str = Field(
        sa_column=Column("bucket_ts", Text, nullable=False),
        description="Naive-UTC ISO-8601 — joins with T1.bucket_ts.",
    )
    instance_id: str = Field(
        sa_column=Column("instance_id", Text, nullable=False),
        description="Instance row id; joinable to daemon.instances.",
    )
    agent_id: Optional[str] = Field(default=None)
    status: Optional[str] = Field(
        default=None,
        description="Sample-time status snapshot.",
    )
    scan_kind: Optional[str] = Field(
        default=None,
        description="``counter`` (cheap charge seam) or ``deep`` (reconcile walk).",
    )
    # P1 boundary: attribution counters written NULL/0 (D13). P2 fills.
    charged_bytes: Optional[int] = Field(default=None)
    deep_bytes: Optional[int] = Field(default=None)
    graph_cache_present: Optional[bool] = Field(default=None)
    in_flight_tasks: Optional[int] = Field(default=None)
    all_state_messages_len: Optional[int] = Field(default=None)
    child_rss_kb: Optional[int] = Field(default=None)
    child_cpu_user_usec: Optional[int] = Field(default=None)
    cpu_share_usec_est: Optional[int] = Field(default=None)
    # Labelled — NOT NULL to surface the residual honestly.
    estimated: bool = Field(
        default=True,
        sa_column=Column("estimated", Integer, nullable=False),
        description=(
            "Whether per-instance CPU/RAM is an estimate (in-process "
            "shared process; per-byte attribution is partial by physics)."
        ),
    )


# ── T3 — resource_pid_map ───────────────────────────────────────────────────


class ResourcePidMap(SQLModel, table=True):
    """Subprocess registry — generalized from ``service_tracking`` A11.

    Architecture §2.4 T3. The partial UNIQUE ``(pid) WHERE
    exited_at IS NULL`` is the DUAL-DIALECT live-pid guard (the
    precedent ``daemon/repositories/service_tool/models.py:142-166``).

    Carries DORMANT v2 columns ``action`` / ``action_by`` /
    ``acted_at`` so P6 (control seam) does NOT re-create schema
    (per phase1-plan T3). All NULL until P6 lands.
    """

    __tablename__ = "resource_pid_map"

    __table_args__ = (
        # Partial UNIQUE over live rows only (A11 precedent):
        # dialect-neutral via ``sqlite_where`` / ``postgresql_where``
        # (the ``uq_report_injections_oblig_triple`` pattern at
        # ``daemon/repositories/report_injection/models.py:227-235``).
        Index(
            "uq_resource_pid_map_pid_active",
            "pid",
            unique=True,
            sqlite_where=text("exited_at IS NULL"),
            postgresql_where=text("exited_at IS NULL"),
        ),
        # 3-site index lockstep — names MUST match
        # ``daemon/migrations/versions/20261018_090003_create_resource_pid_map.sql``.
        Index("idx_resource_pid_map_instance_spawned",
              "instance_id", "spawned_at"),
        Index("idx_resource_pid_map_kind_spawned",
              "kind", "spawned_at"),
        Index("idx_resource_pid_map_pid", "pid"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    pid: Optional[int] = Field(
        default=None,
        description="Kernel PID. Live-pid uniqueness gated by the partial UNIQUE.",
    )
    pgid: Optional[int] = Field(default=None)
    start_time: Optional[int] = Field(
        default=None,
        description="Kernel starttime jiffies (Linux /proc/<pid>/stat field 22).",
    )
    instance_id: Optional[str] = Field(default=None)
    agent_id: Optional[str] = Field(default=None)
    kind: Optional[str] = Field(
        default=None,
        description="``bash`` | ``chart_render`` | ``mcp_child`` | ``service`` | ``proc``.",
    )
    command: Optional[str] = Field(default=None)
    detail: Optional[str] = Field(default=None)
    parent_pid: Optional[int] = Field(default=None)
    spawned_at: Optional[str] = Field(
        default=None,
        sa_column=Column("spawned_at", Text, nullable=False),
        description="Naive-UTC ISO-8601 spawn time (P3 owns the read-seam normalization).",
    )
    exited_at: Optional[str] = Field(default=None)
    exit_code: Optional[int] = Field(default=None)
    exit_kind: Optional[str] = Field(default=None)
    peak_rss_kb: Optional[int] = Field(default=None)
    cpu_user_usec: Optional[int] = Field(default=None)
    # Dormant v2 columns (P6 will populate; schema lives here so
    # P6 doesn't re-create).
    action: Optional[str] = Field(default=None)
    action_by: Optional[str] = Field(default=None)
    acted_at: Optional[str] = Field(default=None)


# ── T4 — resource_limit_policy ──────────────────────────────────────────────


class ResourceLimitPolicy(SQLModel, table=True):
    """Control surface — EMPTY in v1; the chromium concurrency cap
    (O2) lands later as a ``child_kind='chart_render'`` policy.

    Architecture §2.4 T4. ``user_gated`` is NOT NULL DEFAULT true
    because EVERY policy must default to gated — un-gated
    policies can only land via the user-ceremony nonce gate.
    """

    __tablename__ = "resource_limit_policy"

    __table_args__ = (
        # 3-site index lockstep — names MUST match
        # ``daemon/migrations/versions/20261018_090004_create_resource_limit_policy.sql``.
        Index("idx_resource_limit_policy_scope_target_metric",
              "scope", "target", "metric"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    scope: Optional[str] = Field(
        default=None,
        description="``daemon`` | ``agent`` | ``child_kind``.",
    )
    target: Optional[str] = Field(
        default=None,
        description="Target identity (e.g. agent name or child kind).",
    )
    metric: Optional[str] = Field(
        default=None,
        description="Metric the threshold applies to (``rss_mb``, ``cpu_pct``...).",
    )
    op: Optional[str] = Field(
        default=None,
        description="``alert`` | ``throttle`` | ``kill``.",
    )
    threshold: Optional[float] = Field(default=None)
    window_s: Optional[int] = Field(default=None)
    enabled: bool = Field(
        default=False,
        sa_column=Column("enabled", Integer, nullable=False),
        description="Default FALSE — v1 ships observe-only.",
    )
    user_gated: bool = Field(
        default=True,
        sa_column=Column("user_gated", Integer, nullable=False),
        description=(
            "NOT NULL DEFAULT TRUE — policies must be gated by default; "
            "the un-gated form lands via the user-ceremony nonce gate."
        ),
    )
    created_at: Optional[str] = Field(default=None)
    updated_at: Optional[str] = Field(default=None)


__all__ = [
    "ResourceDaemonBucket",
    "ResourceInstanceSample",
    "ResourcePidMap",
    "ResourceLimitPolicy",
]