"""Migration worker — orchestrates the full SQLite to PostgreSQL hot-swap.

This is the central coordinator for Phase 3 of the database migration. It
manages a 5-state state machine, emits SSE progress events, supports
cooperative cancellation, and guarantees that writes are always resumed
(even on failure).

Lifecycle::

    worker = MigrationWorker(manager)

    # Check if migration is possible
    availability = worker.is_migration_available()

    # Subscribe to SSE events
    queue = worker.subscribe()

    # Start migration (runs in background)
    await worker.start()

    # Poll status
    progress = worker.get_status()

    # Cancel mid-flight
    await worker.cancel()

    # Cleanup SSE subscriber
    worker.unsubscribe(queue)

State machine::

    IDLE ──► RUNNING ──► COMPLETED
                 │  ╲
                 │   ╲► FAILED
                 │
                 ╲──► CANCELLED

    FAILED / CANCELLED ──► IDLE  (on next start attempt)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlmodel import Session, SQLModel

from daemon.ensemble_config import EnsembleConfig
from daemon.migrations import MigrationCancelledError
from daemon.migrations.data_migrator import TableMigrator
from daemon.migrations.models import SchemaMigration
from daemon.repositories.factory import create_postgres_engine

logger = logging.getLogger(__name__)

# SSE keepalive interval in seconds.
_KEEPALIVE_INTERVAL = 15


class MigrationState(str, Enum):
    """States for the migration state machine."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class MigrationProgress:
    """Snapshot of migration progress for API consumers and SSE events."""

    status: MigrationState = MigrationState.IDLE
    current_phase: str | None = None
    current_table: str | None = None
    tables_completed: int = 0
    tables_total: int = 0
    checkpoints_migrated: int = 0
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    _timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict.

        ``dataclasses.asdict`` doesn't handle ``Enum`` or ``datetime``
        gracefully, so we manually coerce those two fields.

        ``requires_restart`` is True once the migration has completed
        successfully: the daemon has rewritten ``ensemble.json`` to
        ``postgres`` but the running process is still using the old
        SQLite engine. The frontend surfaces this so the operator
        knows the daemon must be restarted before traffic is routed.
        """
        d = asdict(self)
        d["status"] = self.status.value
        d["started_at"] = self.started_at.isoformat() if self.started_at else None
        d["completed_at"] = self.completed_at.isoformat() if self.completed_at else None
        d["requires_restart"] = self.status == MigrationState.COMPLETED
        return d


class MigrationWorker:
    """Orchestrates SQLite -> PostgreSQL migration with SSE progress,
    cancellation, and error handling.

    Thread-safety notes:
    - ``_lock`` (asyncio.Lock) prevents concurrent ``start()`` calls.
    - ``_cancel_event`` (threading.Event) is used for cooperative
      cancellation between sync batch loops running in
      ``asyncio.to_thread``.
    - ``_subscribers`` list is only mutated from the event-loop thread
      (subscribe/unsubscribe are async-safe as long as they're called
      from the same loop).
    - ``_last_emit`` is a monotonic float; reads/writes are atomic
      under the GIL so no lock is needed for the keepalive check.

    Args:
        manager: The :class:`InstanceManager` instance. Provides the
            current engine, write guard, ensemble config, and
            checkpointer.
    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager
        self._progress = MigrationProgress()

        # Prevents concurrent start() calls — second caller gets 409.
        self._lock = asyncio.Lock()

        # Cooperative cancellation between batches. Threading.Event
        # because the sync migrators run in asyncio.to_thread workers.
        self._cancel_event = threading.Event()

        # SSE subscriber queues. Each subscriber gets its own
        # asyncio.Queue; _emit_event fans out to all of them.
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

        # Background keepalive task handle.
        self._keepalive_task: asyncio.Task | None = None

        # Monotonic timestamp of last emitted event — used by keepalive
        # to avoid sending keepalive right after a real event.
        self._last_emit: float = 0.0

        # PG engine created for the migration (disposed after completion).
        self._pg_engine: Any = None

    # ── Public API ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the migration.

        The lock is acquired **before** any state checks so that two
        concurrent ``start()`` callers cannot both pass precondition
        validation before either acquires the lock (TOCTOU race). All
        checks happen *inside* the critical section, guaranteeing
        exclusive observation of ``_progress.status`` and of the
        ``is_migration_available()`` result.

        Raises:
            RuntimeError: If a migration is already running (caller
                should return HTTP 409).
            ValueError: If preconditions are not met (not SQLite,
                PG env not set, already completed, etc.) — caller
                should return 400/422.
        """
        async with self._lock:
            # State check FIRST (inside the lock) so we cannot race
            # with another caller that has already entered the
            # critical section and is mid-migration.
            if self._progress.status != MigrationState.IDLE:
                raise RuntimeError("Migration is already running")

            # Precondition check also inside the lock — is_migration_available
            # reads ``_progress.status`` and config, both of which must
            # be observed exclusively with the subsequent state mutation
            # in ``_run_migration``.
            availability = self.is_migration_available()
            if not availability["can_migrate"]:
                reasons = "; ".join(availability["reasons"])
                raise ValueError(f"Migration prerequisites not met: {reasons}")

            try:
                await self._run_migration()
            finally:
                # Stop keepalive if it's still running.
                if self._keepalive_task is not None:
                    self._keepalive_task.cancel()
                    self._keepalive_task = None

    async def cancel(self) -> None:
        """Request cooperative cancellation of a running migration.

        Raises:
            RuntimeError: If no migration is currently running.
        """
        if self._progress.status != MigrationState.RUNNING:
            raise RuntimeError("No migration is currently running")
        self._cancel_event.set()
        self._emit_event("log", {
            "level": "info",
            "message": "Cancellation requested",
        })

    def get_status(self) -> dict[str, Any]:
        """Return current migration progress as a dict."""
        return self._progress.to_dict()

    def is_migration_available(self) -> dict[str, Any]:
        """Check whether migration preconditions are met.

        Returns a dict with boolean flags and a human-readable ``reasons``
        list — empty when migration is possible.

        After a successful migration the worker's status flips to
        ``COMPLETED`` and a restart is required to use the new backend.
        A second migration must not be re-triggered from the same
        process: it would re-copy stale SQLite data into the already-
        populated PostgreSQL database. This check is explicit (rather
        than relying on ``config.is_sqlite`` flipping to ``False``) so
        the guard is robust against config-drift scenarios where the
        in-memory ensemble_config still reports SQLite.
        """
        # Hard guard: once a migration has completed in this process, a
        # second run is unsafe. The caller must restart the daemon.
        if self._progress.status == MigrationState.COMPLETED:
            return {
                "can_migrate": False,
                "is_sqlite": bool(
                    self._manager.ensemble_config
                    and self._manager.ensemble_config.is_sqlite
                ),
                "pg_env_available": bool(os.environ.get("POSTGRES_HOST") and os.environ.get("POSTGRES_DB")),
                "reasons": [
                    "Migration has already completed; restart the daemon to use the new database"
                ],
            }

        config = self._manager.ensemble_config
        is_sqlite = config is not None and config.is_sqlite
        pg_env_available = config.postgres_env_available if config else False

        # Also accept a full DSN env var as a PG source.
        if not pg_env_available:
            pg_env_available = bool(os.environ.get("DATABASE_URL_POSTGRES"))

        # Engine dialect as a secondary signal. The engine is the LAGGING
        # implementation detail — the config is the source of truth for
        # what the operator chose. After ``POST /api/database/switch`` the
        # config flips immediately but the engine isn't swapped until a
        # restart. Using the engine URL alone would make the UI report the
        # OLD backend even after a successful switch.
        #
        # The engine check is only used here for the ``can_migrate`` safety
        # guard (you can't run a SQLite→PG migration if the running engine
        # is already pointing at PG — you'd be copying from the wrong
        # source). It must NOT be folded into the ``is_sqlite`` report,
        # which drives the ``current_database`` field the UI displays.
        engine_url = ""
        if hasattr(self._manager, "engine") and self._manager.engine is not None:
            engine_url = str(self._manager.engine.url)
        engine_is_sqlite = "sqlite" in engine_url

        can_migrate = is_sqlite and engine_is_sqlite and pg_env_available

        reasons: list[str] = []
        if not is_sqlite:
            reasons.append("Current database is not SQLite (ensemble_config.database != 'sqlite')")
        if not engine_is_sqlite:
            reasons.append("Engine dialect is not SQLite")
        if not pg_env_available:
            reasons.append("PostgreSQL environment variables not set (POSTGRES_HOST + POSTGRES_DB)")

        return {
            "can_migrate": can_migrate,
            # Report the config, not the engine. The engine lags config
            # until restart; reporting it would make the UI show the wrong
            # current backend right after a switch.
            "is_sqlite": is_sqlite,
            "pg_env_available": pg_env_available,
            "reasons": reasons,
        }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Create and register a new SSE subscriber queue.

        Returns:
            An ``asyncio.Queue`` that will receive migration events.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue.

        Args:
            queue: The queue previously returned by :meth:`subscribe`.
        """
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    # ── Migration orchestration ─────────────────────────────────────────────

    async def _run_migration(self) -> None:
        """Main migration flow. Runs inside the asyncio lock."""
        manager = self._manager
        config = manager.ensemble_config

        # Reset state for a fresh run (allows retry after failure/cancel).
        self._cancel_event.clear()
        self._progress = MigrationProgress(
            status=MigrationState.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        # Start keepalive task.
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

        try:
            # Step 1: Create PG engine using the Phase 2 factory.
            self._set_phase("creating_pg_engine")
            self._emit_event("progress", {"message": "Creating PostgreSQL engine"})
            self._pg_engine = create_postgres_engine(config)

            # Step 2: Create PG schema via SQLModel.metadata.create_all().
            self._set_phase("creating_schema")
            self._log_callback("info", "Creating PostgreSQL schema via SQLModel.metadata.create_all()")
            await asyncio.to_thread(SQLModel.metadata.create_all, self._pg_engine)
            self._log_callback("info", "PostgreSQL schema created")

            # Step 3: Backfill schema_migrations table with all versions
            # so MigrationRunner doesn't try to replay them.
            self._set_phase("backfilling_migrations")
            self._emit_event("progress", {"message": "Backfilling schema_migrations table"})
            await asyncio.to_thread(self._backfill_schema_migrations)

            # Step 4: Pause writes — blocks until all in-flight writes drain.
            self._set_phase("pausing_writes")
            self._log_callback("info", "Pausing writes to drain in-flight sessions")
            await asyncio.to_thread(manager.pause_writes)
            self._log_callback("info", "Writes paused successfully")

            # Step 5: Migrate table data.
            self._set_phase("migrating_tables")
            table_migrator = TableMigrator(
                sqlite_engine=manager.engine,
                pg_engine=self._pg_engine,
                cancel_event=self._cancel_event,
                log_callback=self._log_callback,
            )
            self._log_callback("info", "Starting table data migration")
            counts = await asyncio.to_thread(table_migrator.migrate_all_tables)
            total_rows = sum(counts.values())
            self._progress.tables_total = len(counts)
            self._progress.tables_completed = len(counts)
            self._log_callback(
                "info",
                f"Table migration complete: {total_rows} rows across {len(counts)} tables",
            )

            # Step 6: Migrate checkpoints (async API-based).
            self._set_phase("migrating_checkpoints")
            self._emit_event("progress", {"message": "Migrating checkpoints (async API-based)"})
            checkpoint_count = await self._migrate_checkpoints()
            self._progress.checkpoints_migrated = checkpoint_count
            self._log_callback(
                "info",
                f"Checkpoint migration complete: {checkpoint_count} checkpoints migrated",
            )

            # Step 7: Validate migration (row count comparison).
            self._set_phase("validating")
            self._log_callback("info", "Validating migration (row count comparison)")
            mismatches = await asyncio.to_thread(table_migrator.validate_migration)
            if mismatches:
                self._log_callback(
                    "warning",
                    f"Validation found {len(mismatches)} table(s) with row count mismatches",
                    mismatches=mismatches,
                )
            else:
                self._log_callback("info", "Migration validation passed — all tables match")

            # Step 8: Update ensemble.json to point to PostgreSQL.
            self._set_phase("updating_config")
            self._log_callback("info", "Updating ensemble.json to use PostgreSQL")
            config.database = "postgres"
            config.save(manager.data_dir)
            self._log_callback("info", "ensemble.json updated to database='postgres'")

            # Step 9: Success.
            self._progress.status = MigrationState.COMPLETED
            self._progress.completed_at = datetime.now(timezone.utc)
            self._emit_event("complete", {
                "message": "Migration completed successfully",
                "tables_migrated": len(counts),
                "total_rows": total_rows,
                "checkpoints_migrated": checkpoint_count,
                "validation_mismatches": len(mismatches),
                # The daemon has rewritten ``ensemble.json`` to ``postgres``
                # but the running process is still on SQLite; signal the
                # frontend to prompt the operator for a restart.
                "requires_restart": True,
            })

        except MigrationCancelledError:
            self._progress.status = MigrationState.CANCELLED
            self._progress.completed_at = datetime.now(timezone.utc)
            self._emit_event("cancelled", {
                "message": "Migration was cancelled",
            })

        except Exception as e:
            logger.exception("Migration failed")
            self._progress.status = MigrationState.FAILED
            self._progress.error = str(e)
            self._progress.completed_at = datetime.now(timezone.utc)
            self._emit_event("error", {
                "message": f"Migration failed: {e}",
                "error": str(e),
                "error_type": type(e).__name__,
            })

        finally:
            # ALWAYS resume writes — even on failure or cancellation.
            # The manager.is_write_paused guard prevents a spurious
            # resume if writes were never paused (failure before step 4).
            if manager.is_write_paused:
                self._log_callback("info", "Resuming writes after migration")
                manager.resume_writes()

            # Dispose the PG engine if we created one.
            if self._pg_engine is not None:
                try:
                    self._pg_engine.dispose()
                except Exception:
                    logger.debug("Failed to dispose PG engine", exc_info=True)
                self._pg_engine = None

            self._progress.current_phase = None
            self._progress.current_table = None

    # ── Checkpoint migration ────────────────────────────────────────────────

    async def _migrate_checkpoints(self) -> int:
        """Create checkpointers and run the checkpoint migration.

        Returns the number of checkpoints migrated. Returns 0 if the
        source checkpointer has no checkpoints.
        """
        from daemon.migrations.checkpoint_migrator import CheckpointMigrator
        from daemon.persistence import get_checkpointer

        config = self._manager.ensemble_config

        # SQLite checkpointer (source) — use the manager's actually loaded
        # config so ENSEMBLE_DATA_DIR / custom ``sqlite.checkpoints_db`` paths
        # are honored. A bare ``EnsembleConfig()`` would default to
        # ``./data/checkpoints.db`` and silently migrate from the wrong file.
        sqlite_checkpointer = await get_checkpointer(config)

        # PG checkpointer (destination) — copy connection details from
        # the real config so DATABASE_URL/POSTGRES_* overrides still work.
        pg_config = EnsembleConfig(database="postgres")
        pg_config.postgres = config.postgres
        pg_checkpointer = await get_checkpointer(pg_config)

        migrator = CheckpointMigrator(
            cancel_event=self._cancel_event,
            log_callback=self._log_callback,
        )

        try:
            count = await migrator.migrate_checkpoints(
                sqlite_checkpointer.raw_saver,
                pg_checkpointer.raw_saver,
            )
        except MigrationCancelledError:
            raise
        finally:
            # Release connections regardless of success/failure.
            await self._close_checkpointer_safely(sqlite_checkpointer)
            await self._close_checkpointer_safely(pg_checkpointer)

        return count

    @staticmethod
    async def _close_checkpointer_safely(checkpointer: Any) -> None:
        """Close a checkpointer, swallowing errors."""
        try:
            await checkpointer.close()
        except Exception:
            logger.debug("Error closing temporary checkpointer", exc_info=True)

    # ── Schema migrations backfill ──────────────────────────────────────────

    def _backfill_schema_migrations(self) -> None:
        """Insert all migration version rows into PG's schema_migrations table.

        The PostgreSQL database starts with the latest schema (created via
        ``SQLModel.metadata.create_all``), so we don't replay individual
        migration files. Instead we mark every version as already applied
        so the ``MigrationRunner`` doesn't try to re-run them.

        Idempotent: existing rows are skipped via a pre-check.
        """
        versions_dir = Path(__file__).parent.parent / "migrations" / "versions"
        if not versions_dir.exists():
            self._log_callback("warning", f"Migration versions dir not found: {versions_dir}")
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        version_pattern = re.compile(r"^(\d{8}_\d{6})")
        name_pattern = re.compile(r"^\d{8}_\d{6}_(.+)$")

        with Session(self._pg_engine) as session:
            inserted = 0
            for sql_file in sorted(versions_dir.glob("*.sql")):
                version_match = version_pattern.match(sql_file.stem)
                if not version_match:
                    continue

                version = version_match.group(1)
                name_match = name_pattern.match(sql_file.stem)
                name = name_match.group(1).replace("_", " ") if name_match else "unnamed"

                # Skip if already inserted (idempotent across re-runs).
                existing = session.exec(
                    select(SchemaMigration).where(SchemaMigration.version == version)
                ).first()
                if existing:
                    continue

                session.add(SchemaMigration(
                    version=version,
                    name=name,
                    applied_at=now_iso,
                    execution_time_ms=0,
                    checksum=None,
                ))
                inserted += 1

            session.commit()

        # Count total rows after insert.
        with Session(self._pg_engine) as session:
            count_result = session.exec(
                select(func.count()).select_from(SchemaMigration)
            ).one()
            total = count_result[0] if hasattr(count_result, "__getitem__") else count_result

        self._log_callback(
            "info",
            f"Backfilled schema_migrations: {inserted} new, {total} total versions marked as applied",
        )

    # ── SSE event emission ──────────────────────────────────────────────────

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Fan-out an event to all SSE subscriber queues.

        Adds a ``timestamp`` field to ``data`` so every event has one
        without callers having to add it manually. Drops events silently
        if a subscriber's queue is full (bounded queues prevent
        unbounded memory growth for slow consumers).
        """
        self._last_emit = time.monotonic()
        event = {
            "event": event_type,
            "data": {**data, "timestamp": datetime.now(timezone.utc).isoformat()},
        }

        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop event for slow consumers rather than blocking.
                pass
            except Exception:
                dead.append(queue)

        for queue in dead:
            self._subscribers.remove(queue)

    def _log_callback(self, level: str, message: str, **kwargs: Any) -> None:
        """Callback passed to TableMigrator and CheckpointMigrator.

        Emits an SSE ``log`` event and also logs to the module logger.
        """
        self._emit_event("log", {"level": level, "message": message, **kwargs})

        log_method = getattr(logger, level, logger.info)
        log_method(message, extra=kwargs)

    async def _keepalive_loop(self) -> None:
        """Background task that sends keepalive SSE events every 15 seconds.

        Prevents SSE connection timeouts during long-running migration
        steps (e.g. large table copies) where no real events are emitted.
        """
        try:
            while True:
                await asyncio.sleep(_KEEPALIVE_INTERVAL)
                if time.monotonic() - self._last_emit >= _KEEPALIVE_INTERVAL - 1:
                    self._emit_event("keepalive", {
                        "message": "Migration in progress",
                        "phase": self._progress.current_phase,
                    })
        except asyncio.CancelledError:
            pass

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _set_phase(self, phase: str) -> None:
        """Update the current phase on the progress tracker and emit a
        progress event."""
        self._progress.current_phase = phase
        self._emit_event("progress", {"phase": phase})


__all__ = ["MigrationWorker", "MigrationState", "MigrationProgress"]
