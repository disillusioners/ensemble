"""Checkpoint migrator for SQLite to PostgreSQL migration.

Migrates checkpoints from SQLite to PostgreSQL using the API-based approach
(alist() → aput()), which handles the serialization format conversion
automatically (SQLite msgpack BLOBs → PostgreSQL JSONB).

Why API-based instead of direct SQL copy:
- SQLite stores checkpoints as msgpack BLOBs in 2 tables (checkpoints, writes)
- PostgreSQL stores as JSONB in 4 tables (checkpoints, checkpoint_writes,
  checkpoint_blobs, checkpoint_migrations)
- The aput()/aput_writes() API handles the serialization conversion transparently
- Verified with 2,061 checkpoints in Phase 2

Usage:
    migrator = CheckpointMigrator(cancel_event, log_callback=logger_cb)
    count = await migrator.migrate_checkpoints(sqlite_saver, pg_saver)
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .runner import MigrationError

logger = logging.getLogger(__name__)


class MigrationCancelledError(MigrationError):
    """Raised when a checkpoint migration is cancelled by the user."""


class CheckpointMigrator:
    """Migrates checkpoints from SQLite to PostgreSQL via API-based export/import.

    Uses AsyncSqliteSaver.alist() to read checkpoints and AsyncPostgresSaver.aput()
    to write them. This approach handles the serialization conversion automatically:

    - SQLite stores checkpoints as msgpack BLOBs in 2 tables
    - PostgreSQL stores as JSONB in 4 tables (including checkpoint_blobs)
    - The API-based approach handles this conversion transparently

    Attributes:
        failed_checkpoints: List of (thread_id, checkpoint_id, error) tuples
            for checkpoints that failed to migrate. Reset at the start of each
            migrate_checkpoints() call.
    """

    def __init__(
        self,
        cancel_event: threading.Event,
        log_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        """Initialize the checkpoint migrator.

        Args:
            cancel_event: Threading event to signal cancellation.
                Use .is_set() to check -- it's thread-safe.
            log_callback: Optional callback for progress logging.
                Signature: callback(level="info", message="...") -> None.
        """
        self._cancel_event = cancel_event
        self._log_callback = log_callback
        self.failed_checkpoints: list[tuple[str, str | None, str]] = []

    def _log(self, level: str, message: str) -> None:
        """Log a message via callback and module logger.

        Args:
            level: Log level ('info', 'warning', 'error', 'debug').
            message: Log message.
        """
        if self._log_callback:
            self._log_callback(level=level, message=message)
        log_method = getattr(logger, level, logger.info)
        log_method(message)

    def _check_cancelled(self) -> None:
        """Check if migration has been cancelled.

        Raises:
            MigrationCancelledError: If the cancel event is set.
        """
        if self._cancel_event.is_set():
            raise MigrationCancelledError("Migration cancelled by user")

    async def migrate_checkpoints(
        self,
        sqlite_checkpointer: Any,
        pg_checkpointer: Any,
    ) -> int:
        """Migrate all checkpoints from SQLite to PostgreSQL.

        Reads all checkpoints from the SQLite checkpointer using alist() and
        writes them to the PostgreSQL checkpointer using aput(). Handles
        serialization conversion automatically.

        If a single checkpoint fails, the error is logged and the migration
        continues. Failed checkpoints are tracked in self.failed_checkpoints.

        Args:
            sqlite_checkpointer: AsyncSqliteSaver instance.
            pg_checkpointer: AsyncPostgresSaver instance.

        Returns:
            Number of checkpoints successfully migrated.

        Raises:
            MigrationCancelledError: If the migration is cancelled via
                the cancel event.
        """
        self.failed_checkpoints = []
        migrated = 0

        # Step 1: Get all unique thread IDs from SQLite
        thread_ids = await self._get_thread_ids(sqlite_checkpointer)
        total_threads = len(thread_ids)

        if total_threads == 0:
            self._log("info", "No checkpoints to migrate")
            return 0

        self._log(
            "info",
            f"Starting checkpoint migration: {total_threads} threads to process",
        )

        # Step 2: Migrate each thread's checkpoints
        for thread_idx, thread_id in enumerate(thread_ids, 1):
            self._check_cancelled()

            thread_migrated = await self._migrate_thread(
                thread_id,
                thread_idx,
                total_threads,
                sqlite_checkpointer,
                pg_checkpointer,
            )
            migrated += thread_migrated

        # Step 3: Summary
        failed_count = len(self.failed_checkpoints)
        if failed_count > 0:
            self._log(
                "warning",
                f"Migration completed with {failed_count} failed checkpoints: "
                f"{migrated} succeeded, {failed_count} failed",
            )
        else:
            self._log(
                "info",
                f"Migration completed successfully: {migrated} checkpoints migrated",
            )

        return migrated

    async def _get_thread_ids(self, sqlite_checkpointer: Any) -> list[str]:
        """Get all distinct thread IDs from the SQLite checkpointer.

        Uses the saver's lock and connection directly, matching the pattern
        in SqliteCheckpointerAdapter.list_thread_ids().

        Args:
            sqlite_checkpointer: AsyncSqliteSaver instance.

        Returns:
            List of thread ID strings.
        """
        async with sqlite_checkpointer.lock:
            cursor = await sqlite_checkpointer.conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def _migrate_thread(
        self,
        thread_id: str,
        thread_idx: int,
        total_threads: int,
        sqlite_checkpointer: Any,
        pg_checkpointer: Any,
    ) -> int:
        """Migrate all checkpoints for a single thread.

        Collects all checkpoints via alist(), reverses to oldest-first order
        (so parents exist before children), then writes each to PostgreSQL.

        Args:
            thread_id: Thread ID to migrate.
            thread_idx: Current thread index (1-based) for progress.
            total_threads: Total number of threads for progress reporting.
            sqlite_checkpointer: AsyncSqliteSaver instance.
            pg_checkpointer: AsyncPostgresSaver instance.

        Returns:
            Number of checkpoints successfully migrated for this thread.
        """
        self._log(
            "info",
            f"Migrating checkpoints: {thread_idx}/{total_threads} threads"
            f" (thread {thread_id[:16]}...)",
        )

        # Collect all checkpoints for this thread.
        # alist() returns newest-first (ORDER BY checkpoint_id DESC),
        # so we reverse to process oldest-first. This ensures parent
        # checkpoints exist in PG before their children reference them.
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        try:
            tuples = [t async for t in sqlite_checkpointer.alist(config)]
        except Exception as e:
            self._log(
                "error",
                f"Failed to list checkpoints for thread {thread_id}: {e}",
            )
            self.failed_checkpoints.append((thread_id, None, str(e)))
            return 0

        tuples.reverse()

        migrated = 0
        for checkpoint_tuple in tuples:
            self._check_cancelled()

            try:
                await self._migrate_checkpoint(
                    thread_id,
                    checkpoint_tuple,
                    pg_checkpointer,
                )
                migrated += 1
            except MigrationCancelledError:
                raise
            except Exception as e:
                checkpoint_id = (
                    checkpoint_tuple.config.get("configurable", {}).get(
                        "checkpoint_id", "unknown"
                    )
                )
                self._log(
                    "warning",
                    f"Failed to migrate checkpoint {checkpoint_id} "
                    f"in thread {thread_id}: {e}",
                )
                self.failed_checkpoints.append((thread_id, checkpoint_id, str(e)))

        return migrated

    async def _migrate_checkpoint(
        self,
        thread_id: str,
        checkpoint_tuple: Any,
        pg_checkpointer: Any,
    ) -> None:
        """Migrate a single checkpoint to PostgreSQL.

        Handles the parent_config reconstruction needed by aput(), extracts
        channel_versions for correct blob storage, and migrates pending writes
        separately via aput_writes().

        Key aput() semantics:
        - config["configurable"]["checkpoint_id"] is treated as the PARENT id
        - For root checkpoints (no parent), omit checkpoint_id from config
        - new_versions must match checkpoint["channel_versions"] so PG correctly
          routes non-primitive channel values to checkpoint_blobs

        Args:
            thread_id: Thread ID this checkpoint belongs to.
            checkpoint_tuple: CheckpointTuple NamedTuple from alist()
                with fields: config, checkpoint, metadata, parent_config,
                pending_writes.
            pg_checkpointer: AsyncPostgresSaver instance.
        """
        configurable = checkpoint_tuple.config.get("configurable", {})
        checkpoint_ns = configurable.get("checkpoint_ns", "")

        # Build the parent config for aput().
        # aput() reads config["configurable"]["checkpoint_id"] as the parent.
        if checkpoint_tuple.parent_config is not None:
            parent_configurable = checkpoint_tuple.parent_config.get(
                "configurable", {}
            )
            write_config: dict[str, Any] = {
                "configurable": {
                    "thread_id": parent_configurable.get(
                        "thread_id", thread_id
                    ),
                    "checkpoint_ns": parent_configurable.get(
                        "checkpoint_ns", checkpoint_ns
                    ),
                    "checkpoint_id": parent_configurable.get("checkpoint_id"),
                }
            }
        else:
            # Root checkpoint: no parent. Omit checkpoint_id entirely.
            write_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                }
            }

        # Use the checkpoint's own channel_versions as new_versions.
        # This is critical: PG uses new_versions to decide which channel
        # values go into checkpoint_blobs (non-primitive values). Passing
        # an empty dict would silently drop non-primitive values.
        new_versions = checkpoint_tuple.checkpoint.get("channel_versions", {})

        # Defensive check: an empty ``channel_versions`` combined with
        # non-primitive channel data is a data-loss signal — PG's
        # ``aput()`` will write the row but skip blob storage for
        # any new/changed non-primitive channel value. We still
        # attempt the migration (the row itself is valuable) but
        # surface a warning so the operator can investigate the
        # source checkpoint before this becomes silent corruption.
        if not new_versions:
            channel_values = checkpoint_tuple.checkpoint.get(
                "channel_values", {}
            )
            has_non_primitive = any(
                not isinstance(v, (str, int, float, bool, type(None)))
                for v in channel_values.values()
            )
            if has_non_primitive or checkpoint_tuple.pending_writes:
                checkpoint_id = configurable.get("checkpoint_id", "unknown")
                self._log(
                    "warning",
                    f"Checkpoint {checkpoint_id} in thread {thread_id} has "
                    f"empty channel_versions but contains non-primitive "
                    f"channel data; non-primitive values may be silently "
                    f"dropped during migration",
                )

        # Write the checkpoint. aput() uses INSERT ... ON CONFLICT DO UPDATE,
        # so this is idempotent -- safe to re-run migration.
        saved_config = await pg_checkpointer.aput(
            write_config,
            checkpoint_tuple.checkpoint,
            checkpoint_tuple.metadata,
            new_versions,
        )

        # Write pending writes separately (aput does not handle them).
        # Each pending_write is (task_id, channel, value). Group by task_id
        # because aput_writes() takes a single task_id per call.
        if checkpoint_tuple.pending_writes:
            await self._migrate_pending_writes(
                saved_config,
                checkpoint_tuple.pending_writes,
                pg_checkpointer,
            )

    async def _migrate_pending_writes(
        self,
        saved_config: dict[str, Any],
        pending_writes: list[tuple[str, str, Any]],
        pg_checkpointer: Any,
    ) -> None:
        """Migrate pending writes for a checkpoint.

        Groups writes by task_id and calls aput_writes() for each group.
        aput_writes() uses INSERT ... ON CONFLICT DO UPDATE for idempotency.

        Args:
            saved_config: Config returned by aput() (includes new checkpoint_id).
            pending_writes: List of (task_id, channel, value) tuples from
                CheckpointTuple.pending_writes.
            pg_checkpointer: AsyncPostgresSaver instance.
        """
        # Group writes by task_id
        writes_by_task: dict[str, list[tuple[str, Any]]] = {}
        for task_id, channel, value in pending_writes:
            if task_id not in writes_by_task:
                writes_by_task[task_id] = []
            writes_by_task[task_id].append((channel, value))

        for task_id, writes in writes_by_task.items():
            await pg_checkpointer.aput_writes(
                saved_config,
                writes,
                task_id=task_id,
            )
