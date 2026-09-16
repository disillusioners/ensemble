"""SQLModel table definition for the ``service`` tool category.

Stores one row per daemon-managed detached process (Phase 1.A, D2 of
``.agents/shared/planning/service-tool/decisions.md``, amended per
A11 / A12 / A13 — see also ``phase1-plan.md`` task 1.A.3).

The class is intentionally minimal — Phase 1.B (``ServiceToolManager``)
and Phase 1.C (reconciliation service skeleton) own the rest of the
surface; this module is the F3 single-writer store that 1.B/1.C
consume.

Key design points (with the amended anchor that drove each)

* **A11 — partial UNIQUE index IS the D5 same-name guard.** The
  ``name`` column itself has NO ``unique=True, index=True`` — that
  would emit a FULL unique constraint and kill name-reuse-after-EXITED
  (an EXITED row is historical; a fresh service can claim the name
  again). The partial UNIQUE index
  ``idx_service_tracking_name_active`` (status filter
  ``'starting','running'``) is the only D5 guard. Precedent for the
  dual-dialect ``sqlite_where`` / ``postgresql_where`` clause shape:
  ``daemon/repositories/report_injection/models.py:227-235``
  (``uq_report_injections_oblig_triple``).

* **A12 — TEXT ISO-8601 timestamps via deferred-import helper.** The
  ``daemon.services.timestamps`` module exists at HEAD (merged
  ``f6ca8791``); ``now_utc_iso`` is the aware ISO-8601 producer used
  by 30+ call sites (skill, report_injection, job_queue,
  instance). The deferred-import wrapper sidesteps the
  ``daemon.services.__init__`` ↔ ``daemon.repositories.task.models``
  circular-import cycle (see ``task/models.py:26-37``). The old
  ``sa_column_kwargs={"onupdate": text("now()")}`` pattern is GONE —
  SQLite has no ``now()``. ``updated_at`` bumps live in the
  repository methods (mirrors ``infra/models.py:539+`` ORM
  ``before_update`` listener pattern).

* **A13 — guarded UPDATE contract (read by the repo, not by the
  model).** Status-mutating UPDATE statements in
  :mod:`daemon.repositories.service_tool.repository` MUST be guarded
  ``WHERE id=? AND status IN ('starting','running')``; the
  ``mark_exited`` row-count return is the "did I win the race?"
  signal. The model itself is the data shape — the guard is enforced
  at the repo layer.

3-site index registration (F3 reassignment, plan-time note after
1.A.7): the index names below MUST be byte-identical at the
``.sql`` migration site (this module's sibling
``daemon/migrations/versions/20260915_212810_create_service_tracking.sql``)
AND at the ``EnsembleManager._ensure_postgres_columns`` site in
``daemon/manager.py`` (lands in Phase 1.C task 1.C.13b). The
``tests/unit/repositories/test_service_tool_repository.py`` 3-site
pin test holds the contract until 1.C.13b un-skips the
``manager.py`` arm.
"""

from __future__ import annotations

import enum
from typing import Optional

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class ServiceStatus(str, enum.Enum):
    """Lifecycle states for a daemon-managed detached process.

    Three states (D2 schema):

    * ``STARTING`` — row inserted by the synchronous-fail-or-insert
      helper (``ServiceRepo.insert_with_status`` is called only on the
      ``status="exited"`` F8 path; the regular ``insert`` defaults
      here).  ``STARTING`` means "Popen returned, we have a PID, but
      we have not yet confirmed the process is alive — the
      ``service_start`` tool transitions ``STARTING → RUNNING`` after
      the first successful ``os.kill(pid, 0)`` ping".
    * ``RUNNING`` — the long-lived steady state. Rows in
      ``STARTING`` + ``RUNNING`` are the D5 "active" set gated by the
      partial UNIQUE index
      ``idx_service_tracking_name_active``.
    * ``EXITED`` — terminal. Cleared by the partial unique index, so
      a fresh service with the same ``name`` can claim the slot
      again (the A11 name-reuse-after-EXITED contract).

    Storage values are lowercase strings (the convention carried by
    30+ precedent enums; matches the partial-index predicate text
    verbatim — case-lockstep contract per
    ``report_injection/models.py`` storage literals + index predicate).
    """

    STARTING = "starting"
    RUNNING = "running"
    EXITED = "exited"


# ── helpers (module-level) ────────────────────────────────────────────


def _now_utc_iso() -> str:
    """Aware UTC ISO-8601 string for ``created_at`` / ``updated_at`` (A12).

    Deferred-import wrapper — the ``daemon.services.timestamps`` module
    exists at HEAD (merged ``f6ca8791``) but
    ``daemon.services.__init__`` eagerly imports the service classes,
    which would form a cycle if this module were imported during
    service-module load. The deferred path resolves at first
    instantiation, well after module load (mirrors
    ``daemon/repositories/task/models.py:26-37``).

    Precedent for the underlying producer: ``task/models.py`` uses
    ``now_utc_naive`` for the naive ``created_at`` column on the
    ``task`` table; ``created_at`` / ``updated_at`` on this table are
    TEXT ISO-8601 (not naive timestamps) so the aware
    ``now_utc_iso`` is the correct producer. See
    ``daemon/services/timestamps.py:63-73`` for the byte-stable
    format contract (microseconds + ``+00:00`` offset).
    """

    from daemon.services.timestamps import now_utc_iso

    return now_utc_iso()


class ServiceTracking(SQLModel, table=True):
    """SQLModel table for daemon-managed detached processes (D2).

    Mirrors the column list in ``decisions.md`` §D2 (lines 80-126,
    2026-09-15 leader-ratified). See the module-level docstring for
    the A11 / A12 / A13 amendment walkthrough; the column defaults
    below reflect each amendment verbatim.
    """

    __tablename__ = "service_tracking"

    # 3-site index registration: names MUST be byte-identical to
    # ``daemon/migrations/versions/20260915_212810_create_service_tracking.sql``
    # and to the ``EnsembleManager._ensure_postgres_columns`` clause
    # that lands in Phase 1.C task 1.C.13b. The ``tests/unit/
    # repositories/test_service_tool_repository.py`` 3-site pin test
    # is the single source of truth for drift detection until 1.C
    # un-skips the ``manager.py`` arm.
    __table_args__ = (
        # A11 — partial UNIQUE index IS the D5 same-name guard. The
        # ``name`` column itself has NO ``unique=True`` (that would
        # kill name-reuse-after-EXITED). SQLModel emits the index
        # with ``unique=True`` on BOTH dialects because the WHERE
        # clause scopes uniqueness to the active state set.
        # Dual-dialect WHERE clause (SQLite ``sqlite_where`` / PG
        # ``postgresql_where``) is the
        # ``uq_report_injections_oblig_triple`` precedent at
        # ``report_injection/models.py:227-235``.
        Index(
            "idx_service_tracking_name_active",
            "name",
            unique=True,
            sqlite_where=text("status IN ('starting','running')"),
            postgresql_where=text("status IN ('starting','running')"),
        ),
        # Index on ``pid`` — used by the D6 reconcile sweep's
        # ``get_by_pid`` lookup (Phase 1.C / Phase 2) and by the
        # ``service_stop`` PID-reuse defense (compare against the
        # kernel ``/proc/<pid>/stat`` field 22 — Phase 1.B). Plain
        # non-unique index; PIDs are recycled by the kernel so a
        # unique index here would be wrong (and would also reject
        # ``EXITED`` rows that still carry the historical PID).
        Index("idx_service_tracking_pid", "pid"),
    )

    # ── primary key (autoincrement INTEGER on both dialects) ────────
    id: Optional[int] = Field(default=None, primary_key=True)

    # ── identifying ─────────────────────────────────────────────────
    # A11: NO ``unique=True, index=True`` here. The partial UNIQUE
    # index above (``idx_service_tracking_name_active``) is the only
    # same-name guard and EXPLICITLY admits name-reuse-after-EXITED.
    name: str
    # JSON-encoded argv array (NOT a shell string — no shell expansion
    # = no shell injection). String form per
    # ``daemon/services/vscode_server_manager`` argv precedent.
    command: str
    # Optional on purpose: pre-spawn (the row is inserted only AFTER
    # Popen returns the PID, so ``pid`` is set on insert) and
    # post-exit (EXITED rows may carry the historical PID for
    # forensic log lookup).
    pid: Optional[int] = Field(default=None)
    # Kernel starttime jiffies (Linux ``/proc/<pid>/stat`` field 22)
    # or epoch seconds (macOS via ``ps -o lstart`` parse — see
    # ``service_spawner.get_process_start_time`` in Phase 1.B). Used
    # by the D6 reconcile sweep AND the ``service_stop`` PID-reuse
    # defense (D5).
    start_time: Optional[int] = Field(default=None)
    # Absolute path; validated at insert time by the Phase 1.B
    # ``ServiceToolManager`` (the store is a passive receptacle).
    cwd: str
    # A12: ``str`` storage with ``ServiceStatus.STARTING.value``
    # default (the ``report_injection/models.py:308`` precedent —
    # ``Field(default=ReportInjectionState.PENDING.value, …)``).
    # The actual ``str`` type keeps the column type-portable
    # (no native enum DDL); the partial-index predicate text and
    # the ``ServiceStatus`` enum values are LOCKSTEP — case is
    # lowercase, exact.
    status: str = Field(default=ServiceStatus.STARTING.value)
    # Owner provenance (D3 ``service_start`` shape): UUID4 instance
    # + agent name. Both NOT NULL — the D6 reconcile sweep and the
    # Phase 3 ``service_list`` enrichment paths key on this pair.
    started_by_instance_id: str
    started_by_agent_id: str
    # Absolute path to ``data/services/<name>.log`` (D1 stdoe-to-file
    # discipline; ``bash.py:243-255`` pipe-hang precedent). NOT NULL
    # so the Phase 3 ``service_logs`` tool never needs to handle a
    # missing path.
    log_path: str
    # Set only on ``EXITED`` rows; NULL while ``STARTING`` or
    # ``RUNNING``. Integer (not Optional[str]) because every POSIX
    # exit is an int — zero is a SUCCESS exit code, distinct from
    # "no exit recorded".
    exit_code: Optional[int] = Field(default=None)

    # ── timestamps (A12: TEXT ISO-8601, deferred-import factory) ────
    # ``sa_column_kwargs={"onupdate": text("now()")}`` is GONE —
    # SQLite has no ``now()``. ``updated_at`` bumps live in the
    # repository methods (mirrors
    # ``daemon/repositories/infra/repository.py:539+`` ORM
    # ``before_update`` listener pattern).
    created_at: str = Field(default_factory=_now_utc_iso)
    updated_at: str = Field(default_factory=_now_utc_iso)


__all__ = [
    "ServiceStatus",
    "ServiceTracking",
    "_now_utc_iso",
]