"""SYNC repository for the ``message_metadata`` side table.

Phase 1 C2 of the langgraph-checkpoint-perf plan (Solution M).

Per decisions.md D14 the repository is intentionally SYNC — the engine
factory at ``daemon/repositories/factory.py:10`` returns
``sqlalchemy.Engine`` (not ``AsyncEngine``), and matching the factory
contract keeps the tap-side ``asyncio.to_thread`` bridge simple. There
is NO ``async def`` in this module; the call sites bridge via
``asyncio.to_thread(self._repo.upsert_batch, ...)``.

The two primitives the tap site needs:

* :meth:`MessageMetadataRepository.upsert_batch` — batch idempotent
  upsert keyed on ``(thread_id, message_id)`` via
  ``INSERT ... ON CONFLICT DO NOTHING`` (PG) / the SQLite dialect
  equivalent. Returns the rowcount so the call site can log it.
* :meth:`MessageMetadataRepository.get_for_thread` — per-thread batch
  lookup returning ``{message_id: (created_at, seq)}``; missing thread
  → empty dict. This is the read primitive the C1 / PERF-1 read-flip
  will eventually call (PR3 scope; the call site is wired in PR2
  readiness, but the actual call from ``get_instance_messages`` is the
  PR3 PR).
* :meth:`MessageMetadataRepository.delete_for_thread` — per-thread
  bulk delete returning the affected rowcount. T5.19 (merge
  precondition, architect §3): wired into
  ``maintenance.py::_cleanup_instance`` so a fully-cleaned instance
  leaves no side-table rows behind. Bridges via ``asyncio.to_thread``
  like the other consumers.

Both methods are SYNC. The engine is the shared
``daemon.repos...factory.create_engine_from_config()`` engine — same
singleton pattern as every other repository in this codebase.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import MessageMetadata

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)


# Items tuple shape: ``(message_id, created_at, seq)``. Thread id is
# passed separately because every call in the tap site is single-thread
# (the tap fires for ONE ``configurable.thread_id`` at a time).
Items = list[tuple[str, str, int | None]]


class MessageMetadataRepository:
    """SYNC repository for ``message_metadata`` (Phase 1 C2).

    The repo is constructed against a shared sync ``sqlalchemy.Engine``;
    the call sites bridge to async via ``asyncio.to_thread(...)``
    (D14). Idempotent by construction: the PK enforces first-write-wins
    semantics so a revive / compaction re-tap is a no-op at the DB
    layer (D3).
    """

    def __init__(self, engine: "Engine") -> None:
        # Engine is a sync ``sqlalchemy.Engine`` from the shared
        # ``daemon/repositories/factory.py`` engine factory. See
        # decisions.md D14 — repo is SYNC, not async.
        self._engine = engine

    def upsert_batch(
        self,
        thread_id: str,
        items: Items,
    ) -> int:
        """Idempotent batch upsert. Returns rows affected (0..len(items)).

        Idempotency is enforced via ``INSERT ... ON CONFLICT DO
        NOTHING`` against the composite PK ``(thread_id,
        message_id)`` — see decisions.md D3. The PG dialect path uses
        :func:`sqlalchemy.dialects.postgresql.insert.on_conflict_do_nothing`;
        the SQLite path uses the SQLite dialect equivalent. ``created_at``
        is NOT re-stamped on a conflict — first tap wins.

        An empty ``items`` list short-circuits to ``0`` without
        issuing SQL (D3 + the "no execute on empty" spec at
        phase1-plan.md:462).
        """
        if not items:
            return 0

        rows = [
            {
                "thread_id": thread_id,
                "message_id": mid,
                "created_at": ts,
                "seq": seq,
            }
            for (mid, ts, seq) in items
        ]

        with self._engine.begin() as conn:  # SYNC transaction
            dialect = conn.dialect.name  # "postgresql" | "sqlite"
            tbl = MessageMetadata.__table__
            if dialect == "postgresql":
                stmt = pg_insert(tbl).values(rows)
            else:
                stmt = sqlite_insert(tbl).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["thread_id", "message_id"],
            )
            result = conn.execute(stmt)
            # SQLite and PG both return the count of *newly inserted* rows
            # under ``ON CONFLICT DO NOTHING`` — so a no-op conflict yields
            # rowcount=0 in BOTH dialects (the value is the number of new
            # rows written, not the total considered).
            return result.rowcount or 0

    def get_for_thread(
        self,
        thread_id: str,
    ) -> dict[str, tuple[str, int | None]]:
        """Return ``{message_id: (created_at, seq)}`` for ``thread_id``.

        Missing thread returns an empty dict (no row exists, no
        KeyError). Used by the PR3 C1 read-flip to populate
        ``msg_timestamps`` without a checkpoint history walk; PR2 keeps
        this read primitive available for the C1 caller but does NOT
        touch ``get_instance_messages`` (PR3 scope per the plan
        Hard Constraint #1 — no read-path changes in PR2).
        """
        with self._engine.connect() as conn:
            stmt = select(
                MessageMetadata.message_id,
                MessageMetadata.created_at,
                MessageMetadata.seq,
            ).where(MessageMetadata.thread_id == thread_id)
            rows = conn.execute(stmt).fetchall()
            return {r[0]: (r[1], r[2]) for r in rows}

    def delete_for_thread(self, thread_id: str) -> int:
        """Delete every ``message_metadata`` row for ``thread_id``.

        Returns the number of rows deleted (0 when the thread has no
        rows — DELETE matching zero rows is a no-op, not an error,
        mirroring :meth:`get_for_thread`'s empty-dict contract).

        T5.19 (🔴 merge precondition — architect §3): the sole caller
        is ``maintenance.py::_cleanup_instance``, wired AFTER
        ``adelete_thread`` and BEFORE the in-memory cleanup callback so
        a fully-cleaned instance leaves ZERO side-table rows behind
        (the side table has no FK on either backend, so without this
        call a deleted instance's rows accumulate forever — growth ≈
        2–4 rows/turn × turns × instances).

        Deliberate-non-action semantics: Operation D checkpoint-prune
        orphans are TOLERATED by design — rows whose parent checkpoints
        were pruned by Operation D are over-record-only and never join
        the read path (the read flip resolves timestamps only for
        messages surfaced from LIVE checkpoints; per PR2 review §3).
        Pinned / revivable instances keep their rows permanently by
        design: there is NO FK from ``message_metadata`` to ``instances``
        on either backend, so nothing cascades into this table except
        this explicit delete.

        Dialect note: :meth:`upsert_batch` branches on the connection
        dialect because ``INSERT ... ON CONFLICT`` syntax differs
        between PostgreSQL and SQLite; the DELETE here needs no branch —
        it is dialect-portable core SQL, so this single statement
        serves both the PG path and the SQLite path.
        """
        with self._engine.begin() as conn:  # SYNC transaction
            stmt = delete(MessageMetadata).where(
                MessageMetadata.thread_id == thread_id
            )
            result = conn.execute(stmt)
            return result.rowcount or 0


__all__ = ["MessageMetadataRepository", "Items"]
