"""Shared Context Metadata repository (Phase 1).

Persistence layer for the ``shared_context_metadata`` table.

The repository is intentionally narrow: a thin CRUD layer on top of
:class:`SharedContextMetadata`. The write path uses dialect-aware
``INSERT ... ON CONFLICT DO UPDATE`` via SQLAlchemy's
``sqlite.insert`` / ``postgresql.insert`` (same pattern as
:class:`SQLModelProjectRepository.set_metadata_record`) so concurrent
writers cannot race a stale ``SELECT → INSERT/UPDATE`` loop. Each
supported dialect's insert callable exposes ``on_conflict_do_update``;
:meth:`_get_dialect_insert` selects the right one at runtime based on
the bound engine.

All public methods are synchronous and use ``Session(self.engine)``
blocks. The shared engine singleton is provided by the manager; do
not instantiate a new engine per call.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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

    def _get_dialect_insert(self, session: Session):
        """Return the dialect-specific insert callable for upsert support.

        Generic ``sqlalchemy.insert()`` does not expose
        ``on_conflict_do_update()`` — that method is dialect-specific.
        This helper returns the dialect-specific insert callable so the
        caller can chain ``on_conflict_do_update`` for both SQLite and
        PostgreSQL. Mirrors
        :meth:`SQLModelProjectRepository._get_dialect_insert`.

        Args:
            session: SQLAlchemy Session whose bound engine determines dialect.

        Returns:
            Dialect-specific insert callable. Both the SQLite and
            PostgreSQL dialect inserts support ``on_conflict_do_update``.
        """
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            return pg_insert
        return sqlite_insert

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
        """Upsert a batch of ``(meta_key → meta_value)`` pairs atomically.

        Bounds enforcement (P0-1):
            * ``meta_key`` length must be <= 128 characters.
            * Serialized ``meta_value`` length must be <= 4096 characters.
            * Batch size must be <= 100 ``(meta_key, meta_value)`` pairs.

            All bounds checks run BEFORE any DB operation. If any pair
            or the batch as a whole violates a limit, a ``ValueError``
            is raised and the entire call is rejected (no partial
            writes — atomic, all-or-nothing).

        Atomic upsert (P0-2):
            Each pair is written via a dialect-aware
            ``INSERT ... ON CONFLICT (context_key, meta_key) DO UPDATE``
            so concurrent writers cannot interleave a stale
            ``SELECT → INSERT/UPDATE`` and lose updates. The composite
            ``UniqueConstraint`` on ``(context_key, meta_key)`` is the
            conflict target. SQLite uses ``sqlite.insert``; PostgreSQL
            uses ``postgresql.insert`` (selected at runtime by
            :meth:`_get_dialect_insert`).

        Args:
            context_key: The caller-supplied partition identifier.
            kvs: Mapping of ``meta_key → meta_value`` to upsert.

        Returns:
            List of :class:`SharedContextMetadata` instances reflecting
            the persisted state for each input key. Returned rows are
            fetched from the database after the upsert so callers see
            the assigned ``id`` and final ``updated_at`` timestamp.

        Raises:
            ValueError: If any ``meta_key`` exceeds 128 chars, any
                serialized ``meta_value`` exceeds 4096 chars, or the
                batch contains more than 100 pairs. No DB operations
                are performed when a bounds check fails.
        """
        if not kvs:
            return []

        # P0-1 bounds enforcement — reject the entire call before any
        # DB operation (atomic, all-or-nothing).
        if len(kvs) > 100:
            raise ValueError(f"Too many KV pairs: {len(kvs)} > 100")
        for key, value in kvs.items():
            if len(key) > 128:
                raise ValueError(f"meta_key too long: {len(key)} > 128")
            serialized_len = len(json.dumps(value))
            if serialized_len > 4096:
                raise ValueError(
                    f"meta_value too large for key '{key}': {serialized_len} > 4096"
                )

        with Session(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()
            insert_fn = self._get_dialect_insert(session)

            # P0-2 atomic upsert — one INSERT ... ON CONFLICT per key,
            # so concurrent writers cannot lose updates via a stale
            # SELECT → INSERT/UPDATE race.
            for key, value in kvs.items():
                stmt = insert_fn(SharedContextMetadata).values(
                    context_key=context_key,
                    meta_key=key,
                    meta_value=value,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=['context_key', 'meta_key'],
                    set_={'meta_value': value, 'updated_at': now},
                )
                session.execute(stmt)

            session.commit()

            # Read back the persisted state so callers see the
            # assigned id and final updated_at.
            stmt = select(SharedContextMetadata).where(
                SharedContextMetadata.context_key == context_key,
                SharedContextMetadata.meta_key.in_(list(kvs.keys())),
            )
            return list(session.exec(stmt))

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
