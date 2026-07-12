"""Shared Context Metadata repository (Phase 1).

Persistence layer for the ``shared_context_metadata`` table.

The repository is intentionally narrow: a thin CRUD layer on top of
:class:`SharedContextMetadata`. It deliberately avoids raw-SQL
upserts (``INSERT ... ON CONFLICT DO UPDATE``) and instead uses the
SQLModel ORM ``select → mutate → add`` loop inside a single
transaction. This keeps the upsert path portable across SQLite and
PostgreSQL without per-dialect forks (the dialect-aware upsert
helpers in :class:`SQLModelProjectRepository` exist because the
project repository has a hot write path; this repository's writes
are infrequent and the ORM path is sufficient).

All public methods are synchronous and use ``Session(self.engine)``
blocks. The shared engine singleton is provided by the manager; do
not instantiate a new engine per call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .models import SharedContextMetadata


logger = logging.getLogger(__name__)


class SharedContextMetadataRepository:
    """SQLModel-based repository for the shared metadata KV table.

    The repository takes a SQLAlchemy ``Engine`` only — same pattern
    as :class:`daemon.repositories.db_connection.repository.DbConnectionRepository`.
    All operations are scoped by ``context_key``; ``meta_key`` is
    unique within a context.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy engine bound to a SQLite or PostgreSQL
                database. The same engine should be shared across all
                repositories to avoid lock contention.
        """
        self.engine = engine

    # ==================== READ ====================

    def get_all(self, context_key: str) -> list[SharedContextMetadata]:
        """Return all metadata rows for ``context_key``.

        Args:
            context_key: The caller-supplied partition identifier.

        Returns:
            List of :class:`SharedContextMetadata` rows. Empty list if
            no rows match.
        """
        with Session(self.engine) as session:
            stmt = select(SharedContextMetadata).where(
                SharedContextMetadata.context_key == context_key
            )
            return list(session.exec(stmt))

    def get_many(
        self,
        context_key: str,
        keys: list[str],
    ) -> list[SharedContextMetadata]:
        """Return metadata rows for ``context_key`` matching ``keys``.

        Args:
            context_key: The caller-supplied partition identifier.
            keys: Subset of meta_keys to fetch.

        Returns:
            List of :class:`SharedContextMetadata` rows whose
            ``meta_key`` is in ``keys``. Empty list if no match or
            ``keys`` is empty.
        """
        if not keys:
            return []
        with Session(self.engine) as session:
            stmt = select(SharedContextMetadata).where(
                SharedContextMetadata.context_key == context_key,
                SharedContextMetadata.meta_key.in_(keys),
            )
            return list(session.exec(stmt))

    def get_all_as_dict(self, context_key: str) -> dict[str, Any]:
        """Return all metadata rows as a ``{meta_key: meta_value}`` dict.

        Args:
            context_key: The caller-supplied partition identifier.

        Returns:
            Dict mapping meta_key → meta_value. Empty dict if no
            rows exist for the context.
        """
        records = self.get_all(context_key)
        return {r.meta_key: r.meta_value for r in records}

    # ==================== WRITE ====================

    def set_many(
        self,
        context_key: str,
        kvs: dict[str, Any],
    ) -> list[SharedContextMetadata]:
        """Upsert a batch of ``(meta_key → meta_value)`` pairs.

        Loops through ``kvs``, looks up the existing row per key, and
        either updates ``meta_value`` + ``updated_at`` in place or
        inserts a new row. A single ``commit()`` covers the entire
        batch so callers either see all rows persisted or none.

        The portable ORM upsert loop is intentional — it works on
        both SQLite and PostgreSQL without dialect-specific
        ``ON CONFLICT`` syntax.

        Args:
            context_key: The caller-supplied partition identifier.
            kvs: Mapping of ``meta_key → meta_value`` to upsert.

        Returns:
            List of :class:`SharedContextMetadata` instances in the
            state they were persisted (i.e. the result of the upsert
            for each input key, in iteration order).
        """
        if not kvs:
            return []

        results: list[SharedContextMetadata] = []
        with Session(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()
            for key, value in kvs.items():
                existing = session.exec(
                    select(SharedContextMetadata).where(
                        SharedContextMetadata.context_key == context_key,
                        SharedContextMetadata.meta_key == key,
                    )
                ).first()

                if existing is not None:
                    existing.meta_value = value
                    existing.updated_at = now
                    session.add(existing)
                    results.append(existing)
                else:
                    record = SharedContextMetadata(
                        context_key=context_key,
                        meta_key=key,
                        meta_value=value,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(record)
                    results.append(record)

            session.commit()
            for record in results:
                session.refresh(record)

        return results

    # ==================== DELETE ====================

    def delete_many(self, context_key: str, keys: list[str]) -> int:
        """Delete multiple metadata rows by key.

        Args:
            context_key: The caller-supplied partition identifier.
            keys: meta_keys to delete. An empty list is a no-op
                (returns 0 without hitting the DB).

        Returns:
            Number of rows actually deleted (``result.rowcount``).
        """
        if not keys:
            return 0
        with Session(self.engine) as session:
            stmt = sql_delete(SharedContextMetadata).where(
                SharedContextMetadata.context_key == context_key,
                SharedContextMetadata.meta_key.in_(keys),
            )
            result = session.exec(stmt)
            session.commit()
            return int(result.rowcount or 0)

    def delete_all(self, context_key: str) -> int:
        """Delete every metadata row for ``context_key``.

        Args:
            context_key: The caller-supplied partition identifier.

        Returns:
            Number of rows actually deleted (``result.rowcount``).
        """
        with Session(self.engine) as session:
            stmt = sql_delete(SharedContextMetadata).where(
                SharedContextMetadata.context_key == context_key
            )
            result = session.exec(stmt)
            session.commit()
            return int(result.rowcount or 0)