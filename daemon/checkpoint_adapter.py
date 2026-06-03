"""Checkpointer adapter abstraction for SQLite and PostgreSQL checkpoint databases.

This module provides a unified interface for checkpoint database operations,
abstracting away the differences between AsyncSqliteSaver and AsyncPostgresSaver.

Why this adapter exists:
- AsyncSqliteSaver exposes `.conn` and `.lock` for direct SQL access, but
  AsyncPostgresSaver does not have these attributes.
- maintenance.py (and future code) needs to perform raw SQL queries for
  checkpoint cleanup operations.
- This adapter wraps the database-specific details, allowing callers to use
  the same interface regardless of the backend.

Adapter pattern:
- CheckpointerAdapter (ABC): Defines the interface with 6 abstract methods
- SqliteCheckpointerAdapter: Wraps AsyncSqliteSaver, uses its .conn and .lock
- PostgresCheckpointerAdapter: Wraps AsyncPostgresSaver, uses asyncpg pool

The raw_saver property provides access to the underlying saver for LangGraph
operations (aget, aput, etc.) that are not covered by this adapter.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class CheckpointerAdapter(ABC):
    """Abstract base class for checkpoint database adapters.

    Provides a uniform interface for checkpoint cleanup operations,
    independent of the underlying database technology (SQLite or PostgreSQL).
    """

    @abstractmethod
    async def list_thread_ids(self) -> list[str]:
        """Return all distinct thread_ids from checkpoints table.

        Used by maintenance.py Operation A to find orphaned threads.
        """

    @abstractmethod
    async def get_checkpoint_ids(
        self, thread_id: str, checkpoint_ns: str, limit: int
    ) -> list[str]:
        """Get checkpoint_ids ordered newest-first, limited to `limit`.

        checkpoint_id is a UUID string where lexicographic ordering equals
        chronological ordering (newer UUIDs sort after older ones).

        Used by maintenance.py Operation D (_prune_thread_checkpoints)
        to determine which checkpoints to keep.
        """

    @abstractmethod
    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count.

        Used by maintenance.py Operation D to prune old checkpoints.
        """

    @abstractmethod
    async def delete_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete writes NOT in keep_ids. Returns deleted count.

        Used by maintenance.py Operation D to prune old writes
        corresponding to deleted checkpoints.
        """

    @abstractmethod
    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoint data for a thread.

        Deletes from both checkpoints and writes tables.
        Used by maintenance.py Operations A, B, C for whole-thread deletion.
        """

    @abstractmethod
    async def find_excess_checkpoint_groups(
        self, max_per_thread: int
    ) -> list[tuple[str, str, int]]:
        """Find (thread_id, checkpoint_ns, count) groups exceeding max_per_thread.

        Used by maintenance.py Operation D to find threads with more
        checkpoints than the allowed maximum.

        Returns:
            List of (thread_id, checkpoint_ns, count) tuples where
            count > max_per_thread.
        """

    @property
    @abstractmethod
    def raw_saver(self) -> Any:
        """Access to the underlying saver for LangGraph operations.

        Returns the raw AsyncSqliteSaver or AsyncPostgresSaver instance.
        This is needed for checkpoint read/write operations like aget(),
        aput(), alist() that are not covered by this adapter.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the adapter and release any underlying resources.

        Concrete adapters must override this to release any long-lived
        connections/pools they own. Called from ``InstanceManager.shutdown()``
        during application shutdown to ensure connections are released
        cleanly. Failures should be logged and swallowed so the rest of
        the shutdown sequence can still run.
        """


class SqliteCheckpointerAdapter(CheckpointerAdapter):
    """Adapter wrapping AsyncSqliteSaver for checkpoint database operations.

    Uses the saver's internal .conn and .lock for thread-safe access,
    matching the existing pattern in maintenance.py.
    """

    def __init__(self, saver: Any) -> None:
        """Initialize the SQLite checkpointer adapter.

        Args:
            saver: An AsyncSqliteSaver instance.
        """
        self._saver = saver

    @property
    def raw_saver(self) -> Any:
        """Return the underlying AsyncSqliteSaver."""
        return self._saver

    async def list_thread_ids(self) -> list[str]:
        """Return all distinct thread_ids from checkpoints table."""
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def get_checkpoint_ids(
        self, thread_id: str, checkpoint_ns: str, limit: int
    ) -> list[str]:
        """Get checkpoint_ids ordered newest-first, limited to `limit`."""
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                """
                SELECT checkpoint_id FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ?
                ORDER BY checkpoint_id DESC
                LIMIT ?
                """,
                (thread_id, checkpoint_ns, limit),
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count."""
        if not keep_ids:
            return 0

        placeholders = ",".join("?" * len(keep_ids))
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                f"""
                DELETE FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ?
                AND checkpoint_id NOT IN ({placeholders})
                """,
                (thread_id, checkpoint_ns, *keep_ids),
            )
            await self._saver.conn.commit()
            return cursor.rowcount

    async def delete_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete writes NOT in keep_ids. Returns deleted count."""
        if not keep_ids:
            return 0

        placeholders = ",".join("?" * len(keep_ids))
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                f"""
                DELETE FROM writes
                WHERE thread_id = ? AND checkpoint_ns = ?
                AND checkpoint_id NOT IN ({placeholders})
                """,
                (thread_id, checkpoint_ns, *keep_ids),
            )
            await self._saver.conn.commit()
            return cursor.rowcount

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoint data for a thread."""
        await self._saver.adelete_thread(thread_id)

    async def find_excess_checkpoint_groups(
        self, max_per_thread: int
    ) -> list[tuple[str, str, int]]:
        """Find (thread_id, checkpoint_ns, count) groups exceeding max_per_thread."""
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                """
                SELECT thread_id, checkpoint_ns, COUNT(*) as cnt
                FROM checkpoints
                GROUP BY thread_id, checkpoint_ns
                HAVING cnt > ?
                """,
                (max_per_thread,),
            )
            rows = await cursor.fetchall()
            return [(row[0], row[1], row[2]) for row in rows]

    async def close(self) -> None:
        """Close the underlying aiosqlite connection held by the saver.

        The ``AsyncSqliteSaver`` keeps a long-lived ``aiosqlite.Connection``
        for the application's lifetime. We close it here so the background
        aiosqlite thread is released cleanly at shutdown.
        """
        conn = getattr(self._saver, "conn", None)
        if conn is None:
            return
        try:
            await conn.close()
            logger.debug("SQLite checkpointer connection closed")
        except Exception as e:
            # Closing during interpreter shutdown can raise benign errors;
            # log and move on so other shutdown steps still run.
            logger.warning(f"Error closing SQLite checkpointer connection: {e}")


class PostgresCheckpointerAdapter(CheckpointerAdapter):
    """Adapter wrapping AsyncPostgresSaver for checkpoint database operations.

    Uses an asyncpg connection pool for direct SQL access, adapting
    SQLite query patterns to PostgreSQL syntax.
    """

    def __init__(self, saver: Any, pool: Any) -> None:
        """Initialize the PostgreSQL checkpointer adapter.

        Args:
            saver: An AsyncPostgresSaver instance.
            pool: An asyncpg.Pool instance for direct SQL access.
        """
        self._saver = saver
        self._pool = pool

    @property
    def raw_saver(self) -> Any:
        """Return the underlying AsyncPostgresSaver."""
        return self._saver

    async def list_thread_ids(self) -> list[str]:
        """Return all distinct thread_ids from checkpoints table."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT thread_id FROM checkpoints"
            )
            return [row["thread_id"] for row in rows]

    async def get_checkpoint_ids(
        self, thread_id: str, checkpoint_ns: str, limit: int
    ) -> list[str]:
        """Get checkpoint_ids ordered newest-first, limited to `limit`."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT checkpoint_id FROM checkpoints
                WHERE thread_id = $1 AND checkpoint_ns = $2
                ORDER BY checkpoint_id DESC
                LIMIT $3
                """,
                thread_id,
                checkpoint_ns,
                limit,
            )
            return [row["checkpoint_id"] for row in rows]

    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count."""
        if not keep_ids:
            return 0

        # asyncpg accepts a Python list for the ``$N::text[]`` parameter.
        # The leading NOT inverts the IN-semantics of ``= ANY()`` so we keep
        # only rows whose checkpoint_id is in keep_ids and delete the rest.
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM checkpoints
                WHERE thread_id = $1 AND checkpoint_ns = $2
                AND NOT (checkpoint_id = ANY($3::text[]))
                """,
                thread_id,
                checkpoint_ns,
                list(keep_ids),
            )
            # asyncpg.execute returns "DELETE n" where n is count
            if result:
                parts = result.split()
                if len(parts) >= 2:
                    return int(parts[1])
            return 0

    async def delete_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete writes NOT in keep_ids. Returns deleted count.

        NOTE: The PG LangGraph saver stores writes in the ``checkpoint_writes``
        table (NOT ``writes`` — that name is the SQLite table name).
        """
        if not keep_ids:
            return 0

        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM checkpoint_writes
                WHERE thread_id = $1 AND checkpoint_ns = $2
                AND NOT (checkpoint_id = ANY($3::text[]))
                """,
                thread_id,
                checkpoint_ns,
                list(keep_ids),
            )
            if result:
                parts = result.split()
                if len(parts) >= 2:
                    return int(parts[1])
            return 0

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoint data for a thread.

        Deletes from all three PG tables used by ``AsyncPostgresSaver``:
        - ``checkpoints``  — checkpoint JSONB
        - ``checkpoint_writes`` — pending writes (NOT ``writes`` — that's SQLite)
        - ``checkpoint_blobs`` — non-primitive channel values (no SQLite equivalent)

        All DELETE statements run inside a single transaction so a failure
        on any statement cannot leave the thread in a partially deleted
        state. Order: ``checkpoint_writes`` and ``checkpoint_blobs`` first
        (no FK to ``checkpoints``), then ``checkpoints`` last.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM checkpoint_writes WHERE thread_id = $1",
                    thread_id,
                )
                await conn.execute(
                    "DELETE FROM checkpoint_blobs WHERE thread_id = $1",
                    thread_id,
                )
                await conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id = $1",
                    thread_id,
                )

    async def find_excess_checkpoint_groups(
        self, max_per_thread: int
    ) -> list[tuple[str, str, int]]:
        """Find (thread_id, checkpoint_ns, count) groups exceeding max_per_thread."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT thread_id, checkpoint_ns, COUNT(*) as cnt
                FROM checkpoints
                GROUP BY thread_id, checkpoint_ns
                HAVING COUNT(*) > $1
                """,
                max_per_thread,
            )
            return [
                (row["thread_id"], row["checkpoint_ns"], row["cnt"])
                for row in rows
            ]

    async def close(self) -> None:
        """Close the asyncpg pool and the saver's psycopg connection.

        Both resources are long-lived (one per process). The pool is closed
        first (so no new maintenance queries can be issued) and the saver's
        psycopg connection is closed last.

        Failures are logged and swallowed so the rest of the shutdown
        sequence can continue. The original errors are preserved in the
        log so they can be debugged post-mortem.
        """
        # Close the asyncpg pool first (used by maintenance operations)
        if self._pool is not None:
            try:
                await self._pool.close()
                logger.debug("PostgreSQL checkpointer asyncpg pool closed")
            except Exception as e:
                logger.warning(f"Error closing PostgreSQL checkpointer pool: {e}")

        # Close the saver's psycopg connection
        conn = getattr(self._saver, "conn", None)
        if conn is not None:
            try:
                await conn.close()
                logger.debug("PostgreSQL checkpointer saver connection closed")
            except Exception as e:
                logger.warning(
                    f"Error closing PostgreSQL checkpointer saver connection: {e}"
                )
