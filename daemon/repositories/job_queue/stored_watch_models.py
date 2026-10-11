"""StoredWatch model — Option A store-the-watch quarantine (2026-10-11).

Fix commission e8a61da3 (usage-limit retry-supersede double-silence,
user-picked Option A+E per the architect options review at
``.agents/shared/planning/watch-loss-fix-options/options-matrix.md``).

One row per watcher per re-arm executed by the in-transaction F6 hook
(``RetryTurn.run`` step 4, ``turn_transitions.py``) when a
usage-limit deferral re-keys ``job_watchers`` from the cancelled
parent's ``work_id`` onto the retry child's fresh ``work_id``. The row
is the durable provenance + recovery record for that coverage move:

* **original_work_id / retry_work_id** — the re-key pair. The GC leg's
  "late child arrival" fence marker is ``retry_work_id`` (the GC
  resolves it to decide whether the natural path still owns the
  carrier row).
* **watcher_instance_id** — who was watching (the row's
  ``job_watchers.instance_id``).
* **original_watch_events** — the event list the watcher ACTUALLY
  opted into before the recovery path armed ``mission_terminal`` on
  its behalf (audit fidelity: the M2 opt-in contract stays opt-in;
  this column preserves what the watcher asked for vs what the
  recovery lane armed).
* **mission_instance_id / work_instance_id** — BOTH linkages stored
  per the matrix depth-3 edge list. ``work_instance_id`` is the
  deferred worker (the retry Task's inherited ``instance_id``);
  ``mission_instance_id`` is the permanent-tree ROOT walked up via
  ``instances.parent_id`` at hook time — the GC's
  ``evaluate_mission_live`` anchor (walking from the true root covers
  the whole mission; walking from the worker would miss
  siblings/ancestors and answer "terminal" prematurely).
* **status** — the GC lifecycle: ``armed`` (hook inserted; carrier
  row re-keyed + upgraded) → ``released`` (carrier alive / coverage
  legitimately completed) | ``rehydrated`` (carrier lost; GC
  re-inserted a pure ``mission_terminal`` row on
  ``original_work_id``). The GC's status transition is a CAS on
  ``status='armed'`` — the exactly-once fence for its side effects.

NOT columns on ``job_watchers`` (three-writer contention per the
matrix depth-3 ruling) and NOT S15-piggyback (fires post-claim, wrong
moment, loses origin linkage). Standalone table, own
``.sql`` migration under ``daemon/migrations/versions/`` per the
zero-exceptions rule; PostgreSQL gets the table via
``SQLModel.metadata.create_all`` (brand-new table — snapshot
precedent; no ``_ensure_postgres_columns`` mirror needed).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import SQLModel, Field

from daemon.repositories.infra.types import JSONBType
from daemon.services.timestamps import now_utc_naive


# GC lifecycle states (see model docstring). Module-level constants so
# the repository's status CAS and the sweep's counters share one
# vocabulary — a typo fails loud at import, not silently at sweep time.
STORED_WATCH_STATUS_ARMED = "armed"
STORED_WATCH_STATUS_RELEASED = "released"
STORED_WATCH_STATUS_REHYDRATED = "rehydrated"


class StoredWatch(SQLModel, table=True):
    """Quarantine record for a recovery-armed watch (Option A depth 3)."""

    __tablename__ = "stored_watches"
    __table_args__ = (
        # First-store-wins fence (matrix residual edge: duplicate
        # deferrals on one parent). The in-txn hook INSERTs with
        # ON CONFLICT DO NOTHING against this constraint — a second
        # writer for the same (original_work_id, watcher) pair is a
        # no-op, never a duplicate row.
        UniqueConstraint(
            "original_work_id",
            "watcher_instance_id",
            name="uq_stored_watches_original_work_watcher",
        ),
        Index("idx_stored_watches_retry_work_id", "retry_work_id"),
        Index(
            "idx_stored_watches_status_created",
            "status",
            "created_at",
        ),
    )

    stored_watch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
    )

    # The re-key pair — parent (cancelled) → retry child (fresh UUID).
    original_work_id: str = Field(index=True)
    retry_work_id: str = Field()

    # Who was watching (the ``job_watchers.instance_id`` at hook time).
    watcher_instance_id: str = Field(foreign_key="instances.instance_id", index=True)

    # The watcher's ORIGINAL event list (pre-arming). JSON list of
    # strings — same dual-dialect treatment as
    # ``job_watchers.watch_events``.
    original_watch_events: list[str] = Field(sa_column=Column(JSONBType))

    # Mission linkage (BOTH stored — matrix depth-3 edge list).
    mission_instance_id: str = Field()
    work_instance_id: str = Field()

    # GC lifecycle: armed → released | rehydrated.
    status: str = Field(default=STORED_WATCH_STATUS_ARMED)

    # Naive-UTC binding convention (mission_live_guard precedent) —
    # the GC's aged-scan compares naive-against-naive; never mix
    # aware and naive datetimes in the comparisons.
    created_at: datetime = Field(default_factory=now_utc_naive)
    updated_at: datetime = Field(default_factory=now_utc_naive)
