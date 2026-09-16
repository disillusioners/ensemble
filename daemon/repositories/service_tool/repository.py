"""Synchronous repository for the ``service`` tool category (D2 store).

This is the **F3 frozen interface** that Phase 1.B
(``ServiceToolManager``) and Phase 1.C
(``ServiceReconciliationService``) consume — see plan task 1.A.0 +
the F3 block in
``.agents/shared/planning/service-tool/phase1-plan.md`` (lines 91-95).

Contract locked at 2026-09-15 (leader-ratified). Deviating from this
surface (renaming, adding a required kwarg, changing the
``mark_exited`` return type) silently breaks the Phase 1.B
race-safe kill path — the 1.A.0 gate at
``tests/unit/repositories/test_repo_contract.py`` is the
contract-enforcement test that gates 1.B's start.

Layering notes (per
``.agents/shared/planning/service-tool/research-persistence-migrations.md``
Q2):

* Reads via ``SQLModelSession`` (pure SELECTs through the ORM) —
  this deliberately deviates from the
  ``daemon/repositories/task/repository.py`` ``engine.connect()``-
  for-reads pattern (rationale documented there at lines 116-130).
  Pure SELECT through ``SQLModelSession`` acquires no SQLite write
  lock, so the historical write-lock regression class does not
  re-trigger here, and ``session.get()`` / ``session.exec(select(...))``
  return a fully-populated object with no extra round-trip. The
  single exception is ``get_by_id``: the cheap existence-check
  SELECT uses ``engine.connect()`` (same rationale as
  task/repository.py) and a ``SQLModelSession.get()`` re-read then
  hydrates the row.
* Multi-statement atomic writes via ``engine.begin()`` — one
  transaction per write site.
* Single-row ORM writes go through ``sqlmodel.Session`` for
  ``session.refresh()`` to load autoincrement PKs cleanly.
* All status-mutating UPDATE statements are guarded
  ``WHERE id=? AND status IN ('starting','running')`` (A13
  contract); ``mark_exited`` exposes the implicit rowcount as the
  race-lost signal (1 = transitioned, 0 = race-lost; callers treat
  0 as idempotent success per the 1.A.0 freeze).

The ``updated_at`` bump is performed in the SAME statement as the
status change (A12, since ``sa_column_kwargs={"onupdate": …}`` is
dead on SQLite — no ``now()``).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select

from daemon.services.timestamps import now_utc_iso

from .models import ServiceStatus, ServiceTracking

logger = logging.getLogger(__name__)


# Tuple of active ``status`` values — the A11 partial UNIQUE index
# predicate (`status IN ('starting','running')`) and the read-side
# active-row filter share this single source of truth. Stored as a
# frozenset for O(1) membership tests; serialized to a comma-separated
# string for the parameterized SQL guard (SQLAlchemy expands a Python
# sequence into a parameterized list only for ``expanding`` binds —
# a tuple literal is portable across SQLite + PG).
_ACTIVE_STATUSES: tuple[str, ...] = (
    ServiceStatus.STARTING.value,
    ServiceStatus.RUNNING.value,
)


class ServiceRepo:
    """Synchronous repository for the ``service_tracking`` table (D2 store).

    The 1.A.0 frozen interface — see class docstring at top of module.
    Every public method here is consumed by Phase 1.B and Phase 1.C;
    private helpers (the ``_`` prefix) are repo-local and may evolve
    freely.
    """

    def __init__(self, engine: Engine) -> None:
        """Initialize the repository with a SQLAlchemy ``Engine``.

        The repo is intentionally sync (per the research summary at
        ``research-persistence-migrations.md`` Q2); async callers
        wrap calls in ``asyncio.to_thread`` (the
        ``EligiblePendingSweepService.sweep_once`` precedent at
        ``daemon/services/eligible_pending_sweep.py:212-215``).
        """
        self.engine = engine

    # ──────────────────────────────────────────────────────────────
    # INSERT
    # ──────────────────────────────────────────────────────────────

    def insert(
        self,
        name: str,
        command: list[str] | str,
        pid: Optional[int],
        start_time: Optional[int],
        cwd: str,
        status: str,
        started_by_instance_id: str,
        started_by_agent_id: str,
        log_path: str,
        exit_code: Optional[int] = None,
    ) -> ServiceTracking:
        """Insert a fresh ``service_tracking`` row.

        F3 frozen signature (1.A.0).  Phase 1.B's ``service_start``
        calls this once per successful ``Popen``; Phase 1.C's
        reconcile sweep NEVER calls ``insert`` (the sweep only
        transitions ``STARTING`` / ``RUNNING`` → ``EXITED``).

        Args:
            name: Service name (D5 same-name guard — must NOT have an
                active sibling row in ``STARTING`` or ``RUNNING``
                state; the partial UNIQUE index
                ``idx_service_tracking_name_active`` enforces this).
            command: Argv array (JSON-serialized for storage — no
                shell expansion = no shell injection). ``list[str]``
                is the canonical shape (matches the
                ``service_spawner.spawn`` argv contract); ``str`` is
                accepted for tests that pass pre-serialized JSON.
            pid: Popen-returned PID (NULL is invalid for a healthy
                ``RUNNING`` row — the partial-unique guard does not
                check NULL on this column, but the Phase 1.B
                ``service_start`` path must populate it before
                committing to ``RUNNING``).
            start_time: Kernel start-time (Linux jiffies / macOS epoch
                seconds; see ``service_spawner.get_process_start_time``
                in Phase 1.B).
            cwd: Absolute working directory (validated by 1.B before
                insert).
            status: Storage literal — one of
                ``ServiceStatus.STARTING.value`` /
                ``.RUNNING.value`` / ``.EXITED.value``. Callers
                normally pass ``STARTING`` here and let the
                ``service_start`` tool transition to ``RUNNING``
                after the first liveness ping.
            started_by_instance_id: Owner UUID4 (the agent instance
                that started the service).
            started_by_agent_id: Owner agent name (e.g. ``worker``,
                ``maintenancer``).
            log_path: Absolute path to the stdio-to-file log
                (``data/services/<name>.log`` per D1).
            exit_code: Pre-set exit code (RARE; populated only by
                :meth:`insert_with_status` on the F8 spawn-failure
                path — see ``decisions.md`` §D3 / §F8).

        Returns:
            The inserted ``ServiceTracking`` row (with autoincrement
            ``id`` populated via ``session.refresh()``).

        Raises:
            sqlalchemy.exc.IntegrityError: When the partial UNIQUE
                index rejects the insert (active sibling row exists
                with the same ``name``). Callers MUST treat this as a
                user-facing "name already in use" error.
        """
        command_str = (
            command
            if isinstance(command, str)
            else json.dumps(list(command))
        )

        row = ServiceTracking(
            name=name,
            command=command_str,
            pid=pid,
            start_time=start_time,
            cwd=cwd,
            status=status,
            started_by_instance_id=started_by_instance_id,
            started_by_agent_id=started_by_agent_id,
            log_path=log_path,
            exit_code=exit_code,
        )
        with SQLModelSession(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def insert_with_status(
        self,
        name: str,
        command: list[str] | str,
        pid: Optional[int],
        start_time: Optional[int],
        cwd: str,
        started_by_instance_id: str,
        started_by_agent_id: str,
        log_path: str,
        *,
        status: str,
        reason: str,
        exit_code: Optional[int] = None,
    ) -> ServiceTracking:
        """F8 helper — insert with a non-default initial ``status``.

        Phase 1.B's ``service_start`` calls this on the SYNCHRONOUS
        spawn-failure path (``Popen`` raised, or ``os.kill(pid, 0)``
        after a short grace returns ``ESRCH``): the row goes
        directly to ``status="exited"`` with an ``exit_code`` set
        so the boot-time reconcile sweep can GC it on first pass.

        The ``reason`` parameter is part of the F3 frozen signature
        (1.A.0) but is NOT persisted to a schema column — the D2
        schema has no ``reason`` column. The value is logged at INFO
        level (1.B / Phase 3 dashboards rely on the log line for
        spawn-failure forensics; a future schema bump could promote
        it to a real column, at which point 1.C's
        ``_ensure_postgres_columns`` plus a migration would carry the
        contract — out of scope for 1.A per the F3 freeze).

        Args:
            name: Same as :meth:`insert`.
            command: Same as :meth:`insert` (JSON argv array or
                pre-serialized string).
            pid: Same as :meth:`insert`. On the F8 spawn-failure
                path this is typically ``None`` (Popen raised before
                returning a PID).
            start_time: Same as :meth:`insert`. ``None`` on the F8
                spawn-failure path.
            cwd: Same as :meth:`insert`.
            started_by_instance_id: Same as :meth:`insert`.
            started_by_agent_id: Same as :meth:`insert`.
            log_path: Same as :meth:`insert` — may point to an empty
                file on the spawn-failure path (the file is created
                by the ``service_spawner`` BEFORE ``Popen`` runs so
                the FD is reliable even when Popen raises).
            status: Storage literal. On the F8 path this is
                ``ServiceStatus.EXITED.value``. Exposed as a kwarg
                rather than hard-coded so the F3 freeze can be
                exercised by future callers (e.g. an operator-side
                "register a known-dead service" path) without an
                interface bump.
            reason: Free-text reason (logged at INFO; NOT persisted).
            exit_code: Exit code on the F8 path (typically ``-1``
                when ``Popen`` raised and never returned an exit
                code — the F8 spawn-failure convention).

        Returns:
            The inserted ``ServiceTracking`` row.
        """
        # F3 freeze: log the reason so spawn-failure forensics have a
        # breadcrumb even though the column is not persisted.
        logger.info(
            "[ServiceTool] insert_with_status name=%s status=%s "
            "reason=%s exit_code=%s pid=%s",
            name,
            status,
            reason,
            exit_code,
            pid,
        )
        return self.insert(
            name=name,
            command=command,
            pid=pid,
            start_time=start_time,
            cwd=cwd,
            status=status,
            started_by_instance_id=started_by_instance_id,
            started_by_agent_id=started_by_agent_id,
            log_path=log_path,
            exit_code=exit_code,
        )

    # ──────────────────────────────────────────────────────────────
    # READ
    # ──────────────────────────────────────────────────────────────

    def get_by_id(self, id: int) -> Optional[ServiceTracking]:
        """Return the row with the given primary key, or ``None``.

        Reads via ``engine.connect()`` per the SQLite write-lock
        regression fix (see module docstring). PK lookups are
        constant-time on both backends via the autoincrement index.
        """
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id FROM service_tracking WHERE id = :id"
                ),
                {"id": id},
            ).first()
        if row is None:
            return None
        # ORM re-read via Session so the returned object is fully
        # populated (the ``engine.connect()`` SELECT above is the
        # cheap existence check that avoids holding a write-lock-
        # eligible connection during the SELECT itself).
        with SQLModelSession(self.engine) as session:
            return session.get(ServiceTracking, id)

    def get_by_name(
        self,
        name: str,
        *,
        active_only: bool = True,
    ) -> Optional[ServiceTracking]:
        """Return the unique row matching ``name`` (active by default).

        ``active_only=True`` (default) restricts to ``STARTING`` or
        ``RUNNING`` — the D5 same-name slot. The partial UNIQUE index
        ``idx_service_tracking_name_active`` makes this O(log n) on
        both backends.

        ``active_only=False`` is NOT a real use-case in 1.B (the
        ``service_status`` / ``service_logs`` tools want the active
        row only; historical ``EXITED`` rows are reachable via
        :meth:`get_by_name_any_status` for one explicit operator-side
        lookup) but is exposed for symmetry with the freeze and for
        tests that need to verify the active-row filter is honored.

        Returns ``None`` if no matching row exists. Multiple rows
        are technically possible for ``active_only=False`` if the
        historical-exit + name-reuse-after-EXITED path has fired
        multiple times — in that case the LATEST ``EXITED`` row
        wins (created_at DESC).
        """
        with SQLModelSession(self.engine) as session:
            stmt = select(ServiceTracking).where(
                ServiceTracking.name == name
            )
            if active_only:
                stmt = stmt.where(
                    ServiceTracking.status.in_(list(_ACTIVE_STATUSES))
                )
            stmt = stmt.order_by(ServiceTracking.created_at.desc())
            rows = list(session.exec(stmt))
            if not rows:
                return None
            return rows[0]

    def get_by_name_any_status(self, name: str) -> Optional[ServiceTracking]:
        """Return the LATEST row matching ``name`` regardless of status.

        Distinct from :meth:`get_by_name` (``active_only=True``) which
        scopes to ``STARTING`` / ``RUNNING``. Used by the Phase 3
        ``service_status`` tool's operator-side "show me the last
        result for this name even if it's EXITED" branch.

        Returns the row with the latest ``created_at`` (the same
        ordering as :meth:`list_all`). Returns ``None`` if the name
        has never been used.
        """
        return self.get_by_name(name, active_only=False)

    def list_active(self) -> list[ServiceTracking]:
        """Return all ``STARTING`` / ``RUNNING`` rows (newest first).

        Powers the Phase 3 ``service_list`` tool's "currently running"
        projection. Reads via ``SQLModelSession`` — the same lock-
        safe pure-SELECT pattern documented at module level (pure
        SELECTs acquire no write lock, so this does not collide
        with a later write transaction's write-lock).
        """
        with SQLModelSession(self.engine) as session:
            stmt = (
                select(ServiceTracking)
                .where(
                    ServiceTracking.status.in_(list(_ACTIVE_STATUSES))
                )
                .order_by(ServiceTracking.created_at.desc())
            )
            return list(session.exec(stmt))

    def list_all(self) -> list[ServiceTracking]:
        """Return every row, including ``EXITED``, newest first.

        Powers the Phase 3 ``service_list`` tool's
        ``include_exited=True`` projection AND the Phase 1.C
        reconcile sweep's forensic dump. Ordered by ``created_at
        DESC`` so the most recent activity is at index 0 — the
        convention carried by every "all rows" listing in the
        codebase (``list_pending_tasks_older_than``, ``list_jobs``,
        etc.).
        """
        with SQLModelSession(self.engine) as session:
            stmt = select(ServiceTracking).order_by(
                ServiceTracking.created_at.desc()
            )
            return list(session.exec(stmt))

    # ──────────────────────────────────────────────────────────────
    # UPDATE — A13 atomic-guard contract
    # ──────────────────────────────────────────────────────────────

    def mark_exited(
        self,
        id: int,
        exit_code: Optional[int] = None,
    ) -> int:
        """Transition ``STARTING`` / ``RUNNING`` → ``EXITED`` (atomic).

        F3 frozen signature + return type (1.A.0). The rowcount return
        is the **A13 race-lost signal** — Phase 1.B's
        ``ServiceToolManager.stop`` (and the Phase 1.C / Phase 2
        reconcile sweep's reaper) depend on it:

        * ``1`` — row was ``STARTING`` or ``RUNNING`` and is now
          ``EXITED``. Caller treats this as the canonical
          "transition succeeded" outcome.
        * ``0`` — row was already ``EXITED`` (or otherwise not in
          the active set). This means a sweep↔stop race or a
          duplicate-stop lost the race. Caller treats this as
          IDEMPOTENT SUCCESS — the desired terminal state holds,
          no retry, no warning. See
          ``decisions.md`` §D5 ("stop-idempotent") and §D6 / A13
          ("atomic-guard UPDATEs").

        The UPDATE bumps ``updated_at`` in the same statement (A12
        — Python-side bump; the SQL DEFAULT and the
        ``sa_column_kwargs={"onupdate": …}`` are dead on SQLite).
        The active-set guard (``status IN (:status_starting,
        :status_running)``) is the **only** D5 race defense;
        without it, a concurrent ``mark_exited`` + ``update_status``
        pair could race-overwrite a freshly-promoted row.

        Args:
            id: Primary key of the row to transition.
            exit_code: Optional exit code to record (``None`` is the
                default — Phase 1.B sets it when ``os.waitpid`` (or
                the equivalent ``proc_tools.stop_process`` poll)
                returns a real exit; ``None`` is the correct value
                for ``os.kill(pid, 0)`` confirmed-dead or for a
                sweep-driven transition where the exit code is
                unknown).

        Returns:
            ``1`` if the transition happened, ``0`` if the row was
            already ``EXITED`` (or had been deleted, or never
            existed). The repo does NOT raise on the no-op path —
            "stop is idempotent" is the user-visible contract.
        """
        params = {
            "id": id,
            "exit_code": exit_code,
            "updated_at": now_utc_iso(),
            "status_starting": ServiceStatus.STARTING.value,
            "status_running": ServiceStatus.RUNNING.value,
            "status_exited": ServiceStatus.EXITED.value,
        }
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE service_tracking
                    SET status = :status_exited,
                        exit_code = :exit_code,
                        updated_at = :updated_at
                    WHERE id = :id
                      AND status IN (:status_starting, :status_running)
                    """
                ),
                params,
            )
            return int(result.rowcount or 0)

    def update_status(self, id: int, status: str) -> int:
        """Atomic-guard ``UPDATE ... SET status=:status`` (A13).

        Like :meth:`mark_exited` but with an explicit target status
        (used by the ``STARTING → RUNNING`` promotion in ``service_start``,
        by the reconcile sweep's ``EXITED → STARTING`` re-spawn guard
        — though the sweep today is EXITED-only, no re-spawn — and by
        any future status transition that is not a terminal exit).

        Returns ``1`` if the transition happened, ``0`` if the row
        was not in the active set (race-lost; idempotent success).

        The bump of ``updated_at`` rides in the same statement (A12).
        """
        params = {
            "id": id,
            "status": status,
            "updated_at": now_utc_iso(),
            "status_starting": ServiceStatus.STARTING.value,
            "status_running": ServiceStatus.RUNNING.value,
        }
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE service_tracking
                    SET status = :status,
                        updated_at = :updated_at
                    WHERE id = :id
                      AND status IN (:status_starting, :status_running)
                    """
                ),
                params,
            )
            return int(result.rowcount or 0)


__all__ = ["ServiceRepo"]