"""PG-only threshold-gated VACUUM (ANALYZE) maintenance (commission
``backend-db-space-reclamation``).

Why a separate module:

The ``MaintenanceService`` ``registry`` (daemon/services/maintenance.py)
is shared by every periodic maintenance job. The original file
intentionally houses TWO concerns (the scheduler + the
``CheckpointCleanupJob``); the file-rationale comment at the top
states the split is conditional "only if a third cleanup section
arrives". VACUUM maintenance targets a disjoint table family
(``pg_stat_user_tables`` over the WHOLE database, not the checkpoint
adapter's tables) and uses a dedicated engine + DDL bypass, so a NEW
module avoids growing maintenance.py past its current rationale and
keeps the ``PG-only / autocommit / statement_timeout`` plumbing in one
narrow file discoverable by operators.

What it does:

* PG-only guard — ``engine.dialect.name == "postgresql"``. SQLite
  short-circuits with one INFO log line and zero DB work; VACUUM has
  no portable single-table equivalent there, and the dev install is
  safe without it (autovacuum handling on dev is not load-bearing).

* Threshold gating — a table qualifies for VACUUM (ANALYZE) when

    ``n_dead_tup >= max(vacuum_dead_tup_min, ceil(vacuum_dead_tup_scale_fraction * n_live_tup))``

  Both knobs come from ``PersistenceConfig`` and are env-overridable
  (``PERSISTENCE_VACUUM_DEAD_TUP_MIN`` /
  ``PERSISTENCE_VACUUM_DEAD_TUP_SCALE_FRACTION``). The floor guards
  against false-positives on tiny tables; the scale-fraction guards
  against false-positives on huge tables where a small absolute dead
  count is statistical noise.

* Plain VACUUM (ANALYZE) only — NEVER VACUUM FULL. VACUUM FULL takes
  ACCESS EXCLUSIVE locks and rewrites the heap; the commission brief
  forbids both. ANALYZE-only would skip the actual reclaim; plain
  VACUUM reconciles dead tuples AND refreshes the query planner's
  statistics in one statement.

* Dedicated autocommit engine — VACUUM cannot run inside a
  transaction (PG raises ``25001`` — VACUUM cannot be executed
  inside a transaction block). The ``VacuumMaintenanceJob`` opens a
  dedicated engine per call with PG ``connect_args`` carrying
  ``statement_timeout`` / ``lock_timeout`` /
  ``idle_in_transaction_session_timeout`` so a slow VACUUM cannot
  wedge production (mirrors the ``_build_repair_engine`` pattern in
  ``daemon/tools/ens_db_tools.py``). ``autocommit=True`` keeps each
  VACUUM in its own implicit txn so a partial-per-table crash does
  not leave the session in a broken state.

* Audit + cycle bookkeeping — ``MaintenanceRunLock`` (single-flight)
  and ``MaintenanceRunsRepository`` (audit row lifecycle) are
  OPTIONAL kwargs and follow the ``CheckpointCleanupJob`` precedent
  (``None`` = legacy unwired path, byte-identical to behavior
  pre-wiring). The registered job participates in the standard
  ``_is_idle`` gate, so the sweep only fires when no agent work is
  active — production safety is inherited.

Error handling:

* Any per-table VACUUM failure logs WARNING and continues (one
  bad table does not poison the rest of the sweep). The cycle
  itself ends in one INFO summary log line carrying the
  per-cycle counters. The cycle NEVER raises into the maintenance
  loop — a broken vacuum just retries on the next cycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from daemon.constants import (
    VACUUM_IDLE_IN_TXN_TIMEOUT_MS,
    VACUUM_LOCK_TIMEOUT_MS,
    VACUUM_STATEMENT_TIMEOUT_MS,
)
from daemon.services.timestamps import now_utc_iso
from daemon.services.vacuum_engine import create_vacuum_engine

# NOTE: ``MaintenanceRunLock``, ``MaintenanceRunsRepository``, and
# ``MaintenanceRun`` are imported lazily inside the methods that
# reference them. They are optional kwargs and live on the
# maintenance-runs / run-lock sub-package, which we keep out of the
# module-level graph so this module is importable during early boot
# / conftest mock teardown without dragging the audit repository
# (lazy-import precedent: ``daemon/services/maintenance.py``,
# ``CheckpointCleanupJob.execute``).

logger = logging.getLogger(__name__)


# User-facing tables excluded from the sweep. ``pg_catalog.*`` and
# ``information_schema.*`` are excluded automatically via
# ``schemaname NOT IN ('pg_catalog', 'information_schema')`` in the
# probe query; this list handles application-schema tables that
# should stay out of the sweep for one reason or another (typically
# because they are append-only fact tables that have already been
# VACUUMed by their own loader).
VACUUM_EXCLUDED_TABLES: frozenset[str] = frozenset()


@dataclass
class VacuumCycleSummary:
    """Per-cycle counters used in the summary log line.

    ``tables_qualified`` — tables WHERE n_dead_tup crossed the
    threshold BEFORE this sweep (informational, drives the operator's
    "is the sweep doing anything useful?" check).

    ``tables_vacuumed`` — tables that emitted a VACUUM (ANALYZE) in
    this cycle (drives the cycle throughput signal).

    ``tables_skipped`` — tables that qualified but failed (partial
    sweep — operator-visible WARNING already logged per-table).

    ``duration_ms`` — wall-clock per cycle (drives the "does the
    sweep fit in the idle window?" metric).
    """

    tables_qualified: int = 0
    tables_vacuumed: int = 0
    tables_skipped: int = 0
    duration_ms: int = 0
    # Per-table breadcrumbs for the audit row summary_json. Captures
    # the "did this cycle actually touch X?" log forensics without
    # scraping the log file — the maintenance_runs row carries the
    # same payload and the operator can read it back via the audit
    # channel.
    vacuumed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def to_summary_dict(self) -> dict:
        """Serialize the summary into the maintenance_runs row shape."""
        return {
            "tables_qualified": self.tables_qualified,
            "tables_vacuumed": self.tables_vacuumed,
            "tables_skipped": self.tables_skipped,
            "duration_ms": self.duration_ms,
            "vacuumed": list(self.vacuumed),
            "skipped": list(self.skipped),
        }


class VacuumMaintenanceJob:
    """PG-only threshold-gated VACUUM (ANALYZE) maintenance job.

    Registered on ``MaintenanceService`` via its async ``execute``
    method. The job is dialect-gated; SQLite short-circuits inside
    ``execute`` so the registry can always register the job without
    branching on dialect.

    Args:
        shared_engine: The daemon's main ``Engine``. Used ONLY for
            the ``dialect.name`` check (1 sync call); the actual
            VACUUM runs on a per-cycle DEDICATED engine so the
            rebuild of VACUUM can't take the daemon's pool along
            with it.

        dead_tup_min: ``n_dead_tup`` floor from
            ``PersistenceConfig.vacuum_dead_tup_min`` (env-overridable).

        dead_tup_scale_fraction: ``n_dead_tup``/``n_live_tup`` ratio
            from ``PersistenceConfig.vacuum_dead_tup_scale_fraction``
            (env-overridable).

        run_lock: Optional single-flight gate. See
            ``daemon.services.maintenance_run_lock``. When wired,
            ``execute()`` participates in the shared gate; the
            lock-holder LOSES path is non-raising (a manual run in
            flight is logged and the next tick retries).

        runs_repo: Optional audit-row repository for
            ``maintenance_runs``. ``None`` preserves the
            ``CheckpointCleanupJob`` legacy behavior (no row
            written).

        excluded_tables: Optional frozenset of table names to skip.
            Default empty (sweep all qualifying user tables).
    """

    def __init__(
        self,
        shared_engine: Engine,
        *,
        dead_tup_min: int,
        dead_tup_scale_fraction: float,
        run_lock: "MaintenanceRunLock | None" = None,
        runs_repo: "MaintenanceRunsRepository | None" = None,
        excluded_tables: frozenset[str] | None = None,
    ) -> None:
        self._shared_engine = shared_engine
        self._dead_tup_min = max(0, int(dead_tup_min))
        self._dead_tup_scale_fraction = float(dead_tup_scale_fraction)
        self._run_lock = run_lock
        self._runs_repo = runs_repo
        self._excluded_tables = excluded_tables or VACUUM_EXCLUDED_TABLES

    # ── Public surface ───────────────────────────────────────────────────

    async def execute(self) -> VacuumCycleSummary:
        """Run one VACUUM (ANALYZE) maintenance cycle.

        Idempotent: a cycle is safe to run back-to-back (plain VACUUM
        reclaims dead tuples that are still marked dead and updates
        ``pg_stat_user_tables``; on a clean DB the threshold gate
        filters everything out and the cycle exits with one INFO
        line and zero DB writes).

        Returns:
            :class:`VacuumCycleSummary` with the per-cycle counters
            (used by the audit-row writer when wired; logged at
            INFO on every cycle for log-asserting tests).

        Never raises — every failure mode is logged and swallowed so
        the maintenance loop survives the next tick.
        """
        cycle_start = datetime.now()
        summary = VacuumCycleSummary()

        # Single-flight gate (T3 / AM-4). The lock is shared with
        # ``CheckpointCleanupJob`` (the lock is process-wide, not
        # per-job) — same fail-fast semantics, same in-flight
        # forensics surface, same non-raising skip path. The AM-4
        # lock is intentionally NOT per-job; the AM-5 DB-side claim
        # is per-section (``section='vacuum-maintenance'`` on the
        # audit row) so two jobs can run concurrently on a host
        # without one blocking the other.
        ctx: "MaintenanceRunContext | None" = None
        if self._run_lock is not None:
            from daemon.services.maintenance_run_lock import (
                MaintenanceRunContext,
            )

            ctx = MaintenanceRunContext(
                run_id=_new_vacuum_run_id(),
                kind="auto",
                started_at=now_utc_iso(),
                triggered_by="system",
            )
            if not await self._run_lock.acquire(ctx):
                in_flight_id = (
                    self._run_lock.in_flight.run_id
                    if self._run_lock.in_flight is not None
                    else "unknown"
                )
                logger.debug(
                    "vacuum_maintenance skipped: maintenance run "
                    "%s in flight",
                    in_flight_id,
                )
                return summary

        # DB-side conditional INSERT (T4 / AM-5). ``None`` runs_repo
        # skips the audit-row write (legacy unwired path, same as
        # ``CheckpointCleanupJob.execute()`` — byte-identical to
        # pre-wiring behavior). Two failure modes:
        #   * ``insert`` RAISES → log warning + CONTINUE the cycle
        #     WITHOUT the audit row (audit issues never gate the
        #     cycle, INV-1 spirit).
        #   * ``insert`` returns False → AM-5 DB-side conflict;
        #     another daemon on the same PG owns the lane. Skip the
        #     cycle entirely (release the in-process lock first).
        # The ``audit_run_id`` variable ONLY carries the inserted
        # ``run_id`` when the conditional INSERT won — used by
        # ``_finalize_audit_row`` to target the UPDATE.
        audit_run_id: str | None = None
        audit_skip_cycle = False
        if self._runs_repo is not None and ctx is not None:
            audit_skip_cycle, audit_run_id = (
                self._start_audit_row(ctx)
            )

        if audit_skip_cycle:
            # AM-5 DB conflict — another daemon owns the lane.
            # The lock was acquired by us; release BEFORE the
            # return so the next tick can re-acquire cleanly.
            if self._run_lock is not None:
                self._run_lock.release()
            return summary

        try:
            # PG-only guard. SQLite is the dev install and is
            # handled gracefully — emit one INFO line and exit; the
            # production PG install gets the full sweep.
            if not self._is_pg():
                logger.info(
                    "vacuum_maintenance: non-Postgres backend — skip "
                    "(SQLite has no operationally-meaningful VACUUM "
                    "equivalent at this layer)"
                )
                return summary

            qualified = await _run_in_thread(self._select_qualifying_tables)
            summary.tables_qualified = len(qualified)

            if not qualified:
                logger.info(
                    "vacuum_maintenance: no tables exceeded dead-tuple "
                    "threshold (min=%d, scale=%.4f); cycle idle",
                    self._dead_tup_min,
                    self._dead_tup_scale_fraction,
                )
                return summary

            logger.info(
                "vacuum_maintenance: %d table(s) exceeded threshold — "
                "starting dedicated-engine sweep",
                len(qualified),
            )

            # Per-cycle dedicated engine. VACUUM cannot run inside a
            # transaction block; the dedicated engine is created
            # with ``autocommit=True`` so each VACUUM (ANALYZE)
            # commits individually. ``statement_timeout`` /
            # ``lock_timeout`` / ``idle_in_transaction_session_timeout``
            # bound the worst-case so a slow vacuum never wedges
            # production. The engine is closed in the ``finally``
            # below — one open / close per cycle, never pooled.
            vacuum_engine = self._build_vacuum_engine()
            try:
                for row in qualified:
                    table_name = row["table_name"]
                    dead_before = int(row.get("n_dead_tup") or 0)
                    live_before = int(row.get("n_live_tup") or 0)

                    try:
                        await _run_in_thread(
                            self._vacuum_one_table,
                            vacuum_engine,
                            table_name,
                        )
                    except Exception as exc:
                        # Per-table failure isolation: one bad table
                        # must not poison the rest of the sweep.
                        summary.tables_skipped += 1
                        summary.skipped.append({
                            "table": table_name,
                            "n_dead_tup": dead_before,
                            "error": str(exc),
                        })
                        logger.warning(
                            "vacuum_maintenance: skipped %s after "
                            "%d dead tuple(s) — %s",
                            table_name,
                            dead_before,
                            exc,
                        )
                        continue

                    summary.tables_vacuumed += 1
                    summary.vacuumed.append({
                        "table": table_name,
                        "n_dead_tup": dead_before,
                        "n_live_tup": live_before,
                    })
                    logger.info(
                        "vacuum_maintenance: VACUUM (ANALYZE) %s — "
                        "dead_before=%d, live_before=%d",
                        table_name,
                        dead_before,
                        live_before,
                    )
            finally:
                vacuum_engine.dispose()

            return summary
        except Exception as exc:
            # Cycle-level catch-all: nothing inside ``execute``
            # raises, but the audit path + summary computation sit
            # outside the per-table guard and must not propagate.
            logger.warning(
                "vacuum_maintenance cycle failed: %s",
                exc,
                exc_info=True,
            )
            return summary
        finally:
            cycle_end = datetime.now()
            summary.duration_ms = int(
                (cycle_end - cycle_start).total_seconds() * 1000
            )
            logger.info(
                "vacuum_maintenance cycle complete: qualified=%d "
                "vacuumed=%d skipped=%d duration_ms=%d",
                summary.tables_qualified,
                summary.tables_vacuumed,
                summary.tables_skipped,
                summary.duration_ms,
            )
            if audit_run_id is not None:
                self._finalize_audit_row(audit_run_id, summary)
            if self._run_lock is not None:
                self._run_lock.release()

    # ── Internals ───────────────────────────────────────────────────────

    def _is_pg(self) -> bool:
        return (
            self._shared_engine is not None
            and self._shared_engine.dialect.name == "postgresql"
        )

    def _select_qualifying_tables(self) -> list[dict]:
        """Probe ``pg_stat_user_tables`` for tables above the threshold.

        Synchronous — wrapped in ``asyncio.to_thread`` at the call
        site. Returns a list of ``{'table_name': str, 'n_dead_tup':
        int, 'n_live_tup': int}`` dicts. Excludes pg_catalog,
        information_schema, and the application-level
        ``VACUUM_EXCLUDED_TABLES`` set.

        Threshold gate (matches the docstring contract on the class):

            threshold = max(
                self._dead_tup_min,
                ceil(self._dead_tup_scale_fraction * n_live_tup)
            )
            qualifying if n_dead_tup >= threshold

        The probe SQL is handcrafted (no ORM mapping — the catalog
        tables are not SQLModel-managed and don't need to be). The
        CASE expression enforces the per-table floor without the
        SQLAlchemy ORM's noise.
        """
        min_threshold = self._dead_tup_min
        scale = self._dead_tup_scale_fraction

        # Empty excluded-set removal: short-circuit the ``NOT IN``
        # clause when there are zero excluded tables.
        exclude_clause = ""
        if self._excluded_tables:
            quoted = ", ".join(
                f"'{t}'" for t in sorted(self._excluded_tables)
            )
            exclude_clause = f" AND relname NOT IN ({quoted})"

        sql = text(
            f"""
            SELECT
                relname AS table_name,
                n_dead_tup,
                n_live_tup
            FROM pg_stat_user_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
              AND n_dead_tup >= GREATEST(
                  :min_threshold,
                  CEIL(:scale * n_live_tup)
              ){exclude_clause}
            ORDER BY n_dead_tup DESC
            """
        )

        with self._shared_engine.connect() as conn:
            rows = conn.execute(
                sql,
                {"min_threshold": min_threshold, "scale": scale},
            ).mappings().all()

        # Defensive: ``mappings().all()`` returns Mapping rows on
        # the standard dialect, but coerce to plain dicts so the
        # asyncio.to_thread handoff is type-stable across SQLAlchemy
        # versions (1.4 / 2.x both work, but the row type has
        # varied).
        return [dict(r) for r in rows]

    def _vacuum_one_table(self, vacuum_engine: Engine, table_name: str) -> None:
        """Run one VACUUM (ANALYZE) over ``table_name``.

        Synchronous — wrapped in ``asyncio.to_thread``. The engine
        MUST be a dedicated autocommit engine (built by
        ``_build_vacuum_engine``) so VACUUM can run outside a
        transaction block (PG refuses inside a txn with 25001).

        Quoting: ``table_name`` is sourced from
        ``pg_stat_user_tables.relname`` (the catalog enforces SQL
        identifier rules, so a direct f-string interpolation is
        safe — table names cannot carry dangerous chars at the
        catalog level).

        No VACUUM FULL. Plain VACUUM only — and ANALYZE so the
        planner stats follow the dead-tuple reconciliation.
        """
        # Identifier validation: belt-and-braces against any
        # unusual input from a future catalog probe (custom
        # dialects, partitioned-table virtual rows, etc.). PG
        # identifiers are ASCII + ``_``; anything else is a bug.
        if not table_name or not all(
            ch.isalnum() or ch == "_" for ch in table_name
        ):
            raise ValueError(
                f"vacuum_maintenance: refusing unsafe table name "
                f"{table_name!r}"
            )
        if table_name in self._excluded_tables:
            return

        # ``autocommit=True`` on the dedicated engine makes each
        # VACUUM (ANALYZE) its own implicit transaction. Plain
        # VACUUM reconciles dead tuples; ``ANALYZE`` refreshes the
        # planner's statistics so the next per-cycle probe sees the
        # updated ``n_dead_tup``.
        sql = f"VACUUM (ANALYZE) {table_name}"
        with vacuum_engine.connect() as conn:
            conn.execute(text(sql))
            # The autocommit engine commits per-statement
            # automatically; no manual commit needed.

    def _build_vacuum_engine(self) -> Engine:
        """Build the per-cycle dedicated VACUUM engine.

        Thin consumer of the shared
        ``daemon.services.vacuum_engine.create_vacuum_engine``
        (tidier pass T1) — the engine shape (URL rendered with
        credentials from the shared engine, ``autocommit=True``,
        ``NullPool``, ``echo=False``, timeouts + the UTC session
        frame merged in one libpq ``options`` string) now lives
        there; the timeout VALUES remain this lane's own
        (``VACUUM_*`` constants). Mirrors ``_build_repair_engine`` in
        ``daemon/tools/ens_db_tools.py`` for the timeout shape but
        with ``autocommit=True`` (VACUUM cannot run inside an
        explicit transaction). The UTC session option rides the
        same ``options`` parameter so SQL-side ``now()`` calls
        (none today, but a future maintenance query might use them)
        align with the daemon engine's UTC frame.

        Returns:
            A fresh SQLAlchemy ``Engine`` ready to connect. NOT
            pooled — one engine per cycle, ``dispose()`` in
            ``finally``.
        """
        return create_vacuum_engine(
            self._shared_engine,
            statement_ms=VACUUM_STATEMENT_TIMEOUT_MS,
            lock_ms=VACUUM_LOCK_TIMEOUT_MS,
            idle_ms=VACUUM_IDLE_IN_TXN_TIMEOUT_MS,
        )

    # ── Audit row plumbing (mirrors CheckpointCleanupJob) ────────────────

    def _start_audit_row(
        self, ctx: "MaintenanceRunContext"
    ) -> tuple[bool, str | None]:
        """Insert the ``running`` audit row.

        Mirrors the ``CheckpointCleanupJob.execute`` conditional
        INSERT path (manager.py:3014-3015 / T4 / AM-5). The
        ``section='vacuum-maintenance'`` discriminator is unique on
        the partial unique index ``WHERE status='running'`` so a
        concurrent daemon (same PG host) cannot double-fire the
        sweep.

        Returns ``(skip_cycle, run_id)``:

        * ``(True, None)`` — DB-side conflict (AM-5). The caller
          skips this cycle (releases the lock first).
        * ``(False, run_id)`` — INSERT won. The caller proceeds
          and finalizes the row at cycle exit.
        * ``(False, None)`` — INSERT raised an audit-table issue
          (INV-1: soft-fail; cycle proceeds without audit).

        Soft-fail: the cycle is NEVER blocked by audit-table
        failures (INV-1 spirit), only by AM-5 DB-side conflicts.
        """
        from daemon.repositories.maintenance_runs.models import (
            MaintenanceRun,
        )

        assert self._runs_repo is not None  # narrowing for type-checkers
        try:
            inserted = self._runs_repo.insert(
                MaintenanceRun(
                    run_id=ctx.run_id,
                    section="vacuum-maintenance",
                    kind=ctx.kind,
                    started_at=ctx.started_at,
                    status="running",
                    triggered_by=ctx.triggered_by,
                    env_flags_json={
                        "vacuum_dead_tup_min": self._dead_tup_min,
                        "vacuum_dead_tup_scale_fraction": (
                            self._dead_tup_scale_fraction
                        ),
                    },
                )
            )
        except Exception as exc:
            logger.warning(
                "vacuum_maintenance: audit-row insert failed (%s) — "
                "continuing cycle without audit",
                exc,
            )
            return False, None
        if not inserted:
            # Another daemon owns the slot — AM-5 conflict (other
            # daemon shares this PG). Caller releases the in-process
            # lock and skips the cycle; the boot sweep at the other
            # daemon's lifespan start will eventually pick up the
            # orphan ``running`` row.
            try:
                in_flight = self._runs_repo.get_running(
                    "vacuum-maintenance"
                )
            except Exception:
                in_flight = None
            logger.debug(
                "vacuum_maintenance: DB claim held by run %s (started "
                "%s) — skipping cycle",
                in_flight.run_id if in_flight else "unknown",
                in_flight.started_at if in_flight else "unknown",
            )
            return True, None
        return False, ctx.run_id

    def _finalize_audit_row(
        self,
        run_id: str,
        summary: VacuumCycleSummary,
    ) -> None:
        """Finalize the audit row on cycle exit (``succeeded``).

        Failures in this path are logged at WARNING — the cycle has
        already finished (the operator-visible side effect is the
        cycle summary INFO line). Status is always ``succeeded``
        because the cycle itself never raises (per-table failures
        are captured in ``summary.skipped`` and the existing log
        forensics).
        """
        assert self._runs_repo is not None
        try:
            self._runs_repo.mark_terminal(
                run_id=run_id,
                status="succeeded",
                completed_at=now_utc_iso(),
                summary_json=summary.to_summary_dict(),
            )
        except Exception as exc:
            logger.warning(
                "vacuum_maintenance: audit-row terminal update "
                "failed (%s) — cycle unaffected",
                exc,
            )


# ── helpers ─────────────────────────────────────────────────────────────────


async def _run_in_thread(func, *args):
    """Bridge a sync DB call to the asyncio loop.

    Mirrors the ``asyncio.to_thread`` precedent at every other
    maintenance site (e.g. ``CheckpointCleanupJob``'s
    ``message_metadata`` prune, manager.py:932). The bridge keeps
    the loop responsive under SQLite WAL write contention and
    under PG CONNECTION-LEVEL lock contention — same rationale.
    """
    import asyncio

    return await asyncio.to_thread(func, *args)


def _new_vacuum_run_id() -> str:
    """Mint a vacuum-section run id. Distinct prefix for grep-ability."""
    return f"vac-{now_utc_iso().replace('-', '').replace(':', '').replace('.', '')}-{_HEX8()}"


def _HEX8() -> str:
    import secrets
    return secrets.token_hex(4)


# Public re-exports for tests + callers.
__all__ = [
    "VACUUM_EXCLUDED_TABLES",
    "VacuumCycleSummary",
    "VacuumMaintenanceJob",
]
