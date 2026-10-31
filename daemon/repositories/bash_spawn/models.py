"""SQLModel table definition for the bash-spawn durable registry.

Bash-tool orphan reaper Phase 1 (plan:
``.agents/shared/planning/bash-tool-orphan-reaper/plan.md`` §D3).

ONE row per bash-tool spawn that carries an ``instance_id``, written
SYNCHRONOUSLY at the bash spawn seam (the F4 call-site in
``daemon/tools/bash.py``, AFTER the RAM append and OUTSIDE the registry
``asyncio.Lock``). The row is the tombstone that keeps
``(pid, pgid, tracking_id)`` reclaimable AFTER the direct child exits
(Path A) and across a daemon restart (Path F) — the RAM
``BashProcessRegistry`` loses both.

Design contract (plan D2/D3, restated so the table is self-documenting):

* **Substrate** — a NEW dedicated table, NOT ``resource_pid_map``: that
  table is an exit-history store fed by the async flusher; the reaper
  needs a SPAWN-synchronous live registry whose rows OUTLIVE the direct
  child. Brand-new-table convention: model registered BEFORE manager
  ``create_all`` (``daemon/manager.py`` import block), PG lands via
  ``create_all``, SQLite via the companion migration
  ``daemon/migrations/versions/20261031_110000_create_bash_spawn_registry.sql``.
  NO ``_ensure_postgres_columns`` mirror.
* **State machine** — the column pair ``reaped_at`` NULL/not-NULL:
  ``open → reaped``. Rows are NEVER deleted by the reaper (audit trail);
  open rows found dead are closed with ``reap_reason='gc'`` (idempotent).
  The success arm of the bash tool leaves the row OPEN by design
  (tombstone-until-cleanup-point, plan D4) — only reapers/GC close rows.
* **``start_time``** is LOAD-BEARING: Linux jiffies captured at spawn
  (``/proc/<pid>/stat`` field 22, the ``bash.py`` eager-capture value)
  drives the D9 hard start-time gate; ``None`` on macOS (no ``/proc``).
* **``boot_id``** — the uuid4 hex minted once per daemon process
  (``daemon.services.bash_spawn_reaper.CURRENT_BOOT_ID``). A row with a
  NULL/empty ``boot_id`` (a raced writer) is treated as PREVIOUS-BOOT by
  the reconcile (plan D3 adjudication 2) — pinned in the repository
  candidate-selection tests.
* **``daemon_pid``** — ``os.getpid()`` at write time; the concurrent-
  daemon guard (D8) input.
* **Timestamps** — TEXT aware-UTC ISO (``+00:00`` suffix) via
  ``now_utc_iso()`` (``daemon/services/timestamps.py``), the sanctioned
  TEXT-timestamp producer (plan m1 label fix: aware, not naive).
* **Indexes** (plan m6) — PARTIAL over the OPEN subset: ``(instance_id)
  WHERE reaped_at IS NULL`` and ``(boot_id) WHERE reaped_at IS NULL``
  (dual-dialect ``sqlite_where``/``postgresql_where``, the
  ``resource_pid_map`` precedent) + plain ``(pgid)`` for kill-site dedup.
  A ``(boot_id, reaped_at)`` composite cannot serve the
  ``reaped_at IS NULL AND boot_id != X`` reconcile predicate; the open
  subset is the small hot one every lane scans. Index names are
  byte-identical to the companion migration (3-site lockstep discipline).

Restart-lane exclusion is STRUCTURAL (plan D1): nothing outside the bash
tool seam writes this table — ``spawn_executor`` deliberately skips every
registry, and the executor env allowlist strips the ``ENSEMBLE_BASH_TRACKING_ID``
stamp, so restart-lane processes can never carry a row.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, Index, Text, text
from sqlmodel import Field, SQLModel

from daemon.services.timestamps import now_utc_iso

# Index names — byte-identical to the companion migration
# ``20261031_110000_create_bash_spawn_registry.sql`` (3-site lockstep:
# model / migration / storage test pack).
IDX_INSTANCE_OPEN = "idx_bash_spawn_registry_instance_open"
IDX_BOOT_OPEN = "idx_bash_spawn_registry_boot_open"
IDX_PGID = "idx_bash_spawn_registry_pgid"
UQ_TRACKING_ID = "uq_bash_spawn_registry_tracking_id"


class BashSpawnRecord(SQLModel, table=True):
    """One durable bash-spawn tombstone row (plan D3 schema)."""

    __tablename__ = "bash_spawn_registry"

    __table_args__ = (
        # Partial indexes over the OPEN subset only (plan m6; dual-dialect
        # WHERE via sqlite_where / postgresql_where — the
        # ``uq_resource_pid_map_pid_active`` precedent at
        # ``daemon/models/resource_monitor.py``).
        Index(
            IDX_INSTANCE_OPEN,
            "instance_id",
            sqlite_where=text("reaped_at IS NULL"),
            postgresql_where=text("reaped_at IS NULL"),
        ),
        Index(
            IDX_BOOT_OPEN,
            "boot_id",
            sqlite_where=text("reaped_at IS NULL"),
            postgresql_where=text("reaped_at IS NULL"),
        ),
        # Plain index for kill-site dedup (pgid lookups).
        Index(IDX_PGID, "pgid"),
        # tracking_id is globally unique (uuid4 hex minted pre-spawn).
        # A NAMED UNIQUE INDEX (not a UniqueConstraint) so SQLite
        # create_all and the .sql migration produce the SAME object —
        # SQLite renders unnamed UniqueConstraints as anonymous
        # autoindexes, breaking the 3-site name lockstep.
        Index(UQ_TRACKING_ID, "tracking_id", unique=True),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    pid: int = Field(description="Kernel PID of the direct child at spawn.")
    pgid: int = Field(
        description=(
            "Process group id captured at spawn (start_new_session=True "
            "→ PGID == PID for the direct child). The killpg target."
        )
    )
    start_time: Optional[int] = Field(
        default=None,
        description=(
            "Kernel starttime jiffies (Linux /proc/<pid>/stat field 22), "
            "captured eagerly at spawn. LOAD-BEARING: drives the D9 hard "
            "start-time gate. None on macOS (no /proc)."
        ),
    )
    tracking_id: str = Field(
        description=(
            "uuid4 hex minted pre-spawn; also injected into the child env "
            "as ENSEMBLE_BASH_TRACKING_ID=<tracking_id> (the secondary "
            "discriminator)."
        )
    )
    instance_id: str = Field(
        description="Owning instance (the bash tool refuses to stamp None)."
    )
    command_sanitized: str = Field(
        description=(
            "Executable + argv-shape ONLY (the D14 sanitized value the "
            "bash tool already computes) — NEVER the env overlay."
        )
    )
    boot_id: Optional[str] = Field(
        default=None,
        description=(
            "uuid4 hex minted once per daemon process "
            "(CURRENT_BOOT_ID). NULL/empty rows are treated as "
            "previous-boot by the reconcile (D3 adjudication 2)."
        ),
    )
    daemon_pid: int = Field(
        description=(
            "os.getpid() at write time — the concurrent-daemon guard (D8) "
            "input; self rows (daemon_pid == os.getpid()) always proceed "
            "through every lane (MAJOR-1)."
        )
    )
    created_at: str = Field(
        default_factory=now_utc_iso,
        sa_column=Column("created_at", Text, nullable=False),
        description=(
            "Aware-UTC ISO (``+00:00`` suffix) via now_utc_iso() — the "
            "sanctioned TEXT-timestamp producer. The D9 start-time gate "
            "compares member starts against this instant."
        ),
    )
    reaped_at: Optional[str] = Field(
        default=None,
        description=(
            "Aware-UTC ISO when the row was closed. NULL == open (the "
            "state machine: open → reaped; rows are never deleted)."
        ),
    )
    reaped_by: Optional[str] = Field(
        default=None,
        description=(
            "Cleanup point id + sweeping daemon, suffixed "
            "``@<sweeping-daemon-pid>``. EXACT value set (minor-3 — the "
            "per-lane tier distinction is unreachable without call-site "
            "churn, so the coarse label is SANCTIONED): "
            "``terminal-tier`` (terminal Tier-1/Tier-2, terminate and "
            "chart lanes all inherit this one label), ``shutdown``, "
            "``boot-reconcile``, ``gc``."
        ),
    )
    reap_reason: Optional[str] = Field(
        default=None,
        description=(
            "EXACT value set (minor-3): ``NULL`` for a reaped kill, "
            "``enumerated`` for a Phase-4 stamp-path kill, "
            "``ram-tier-dedup`` for a RAM-tier dedup close, ``gc`` for "
            "a dead-group GC close, or ``refused:<guard>`` when a "
            "verification refusal closed the row (``refused:pgid-missing`` "
            "| ``refused:pgid-invalid`` | ``refused:pgid<=1`` | "
            "``refused:daemon-group`` | ``refused:pid-missing`` | "
            "``refused:pid-invalid`` | ``refused:self-pid`` | "
            "``refused:platform-win32`` | "
            "``refused:direct-child-unprobeable`` | "
            "``refused:env-mismatch`` | ``refused:foreign-member`` | "
            "``refused:start-time-unparseable`` | "
            "``refused:start-time-gate``)."
        ),
    )
