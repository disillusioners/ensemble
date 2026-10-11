"""Repository for StoredWatch rows (Option A depth 3, fix e8a61da3).

The WRITE side of the quarantine table is owned by the in-transaction
F6 hook (``RetryTurn.apply`` — raw SQL inside the caller's
``engine.begin()`` transaction, atomic with the re-key by
construction). This repository owns everything OUTSIDE that
transaction:

* ``list_aged_armed`` — the GC leg's bounded, time-boxed candidate
  scan (rows still ``armed`` past the mission-live orphan window).
* ``transition_status`` — the GC's exactly-once fence: a CAS UPDATE
  ``status = :to WHERE stored_watch_id = :id AND status = 'armed'``.
  Returns ``True`` only for the winning writer.
* ``get_by_original_work`` — provenance lookup (tests + audit).

Design note — why the repository never INSERTs: the hook's INSERT
must share the retry transaction (matrix depth 1: "the in-txn hook
makes cancel→persist→re-arm atomic by construction — daemon death =
all-or-nothing"). A repository-level INSERT on its own session would
break that atomicity, so no create method exists here on purpose.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import Session as SQLModelSession

from daemon.services.timestamps import now_utc_naive
from daemon.repositories.job_queue.stored_watch_models import (
    STORED_WATCH_STATUS_ARMED,
    StoredWatch,
)

logger = logging.getLogger(__name__)


class StoredWatchRepository:
    """Persistence for recovery-armed watch quarantine rows."""

    def __init__(self, engine: Any):
        """Initialize with a database engine."""
        self.engine = engine

    def list_aged_armed(
        self,
        *,
        older_than_seconds: int,
        limit: int = 64,
        now: datetime | None = None,
    ) -> list[StoredWatch]:
        """Bounded candidate scan for the GC leg.

        Returns rows still ``armed`` whose ``created_at`` is older
        than ``older_than_seconds`` (the GC passes
        ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` — 6h — so the leg
        only ever touches watches whose coverage window has genuinely
        expired), oldest first, capped at ``limit`` (time-boxed scan
        per the sweep contract).
        """
        now_val = now or now_utc_naive()
        cutoff = now_val - timedelta(seconds=older_than_seconds)
        with SQLModelSession(self.engine) as session:
            rows = session.execute(
                text(
                    "SELECT * FROM stored_watches "
                    "WHERE status = :armed AND created_at < :cutoff "
                    "ORDER BY created_at ASC LIMIT :lim"
                ),
                {
                    "armed": STORED_WATCH_STATUS_ARMED,
                    "cutoff": cutoff,
                    "lim": int(limit),
                },
            ).all()
            return [self._row_to_model(r) for r in rows]

    def transition_status(
        self,
        stored_watch_id: str,
        to_status: str,
    ) -> bool:
        """CAS the row out of ``armed`` — the GC's exactly-once fence.

        Returns ``True`` iff THIS call won the
        ``armed → to_status`` transition (rowcount 1). A concurrent
        GC tick or test double that already transitioned the row
        returns ``False`` and the loser performs no side effects.
        """
        with SQLModelSession(self.engine) as session:
            result = session.execute(
                text(
                    "UPDATE stored_watches SET status = :to_status, "
                    "updated_at = :now "
                    "WHERE stored_watch_id = :id AND status = :armed"
                ),
                {
                    "to_status": to_status,
                    "now": now_utc_naive(),
                    "id": stored_watch_id,
                    "armed": STORED_WATCH_STATUS_ARMED,
                },
            )
            session.commit()
            return bool(result.rowcount)

    def get_by_original_work(
        self,
        original_work_id: str,
    ) -> list[StoredWatch]:
        """Provenance lookup: all quarantine rows for a parent work_id."""
        with SQLModelSession(self.engine) as session:
            rows = session.execute(
                text(
                    "SELECT * FROM stored_watches "
                    "WHERE original_work_id = :orig "
                    "ORDER BY created_at ASC"
                ),
                {"orig": original_work_id},
            ).all()
            return [self._row_to_model(r) for r in rows]

    @staticmethod
    def _row_to_model(row: Any) -> StoredWatch:
        """Materialize a raw row into the SQLModel (events JSON parsed)."""
        import json as _json

        if isinstance(row, StoredWatch):
            return row
        events = row.original_watch_events
        if isinstance(events, str):
            try:
                events = _json.loads(events)
            except (TypeError, ValueError):
                events = []
        return StoredWatch(
            stored_watch_id=row.stored_watch_id,
            original_work_id=row.original_work_id,
            retry_work_id=row.retry_work_id,
            watcher_instance_id=row.watcher_instance_id,
            original_watch_events=list(events or []),
            mission_instance_id=row.mission_instance_id,
            work_instance_id=row.work_instance_id,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
