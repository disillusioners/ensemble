"""JobWatcher model for job subscription tracking."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import SQLModel, Field

from daemon.repositories.infra.types import JSONBType

ALL_TERMINAL_STATES: list[str] = ["completed", "settled", "failed", "cancelled", "dead_letter"]

# Mid-flight QA channel (2026-09-21, ``feature/midflight-qa-channel``) —
# four NEW NON-TERMINAL watchable events. Without registering them here,
# ``watch_job``'s events validation (daemon/tools/job_queue.py — the
# ``accepted_events`` gate) rejects them, and ``notify_work_watchers``'
# per-watcher ``status in watcher.watch_events`` filter would silently
# drop every non-terminal notification. They are non-terminal by
# definition: the notifier's non-terminal branch never claims the row,
# so the watch survives for the eventual terminal event.
# ``child_question_still_pending`` (the 5th EventKind) is deliberately
# NOT here — it is dispatched via EventBus + LiveEventHub only and never
# enters the work_notifier status map (design §3.1 / R1).
MIDFLIGHT_QA_WATCHABLE_EVENTS: list[str] = [
    "question_requested",
    "answer_received",
    "midflight_report",
    "stuck_awaiting_answer",
]

# All events a watcher can receive, including non-terminal (progress) events
ALL_WATCHABLE_EVENTS: list[str] = (
    ALL_TERMINAL_STATES + ["in_progress"] + MIDFLIGHT_QA_WATCHABLE_EVENTS
)

# M2 (mission-class, 2026-09-02, ``feature/mission-class``) — opt-in
# mission-side terminal event per contract draft §3.5. Fires ONLY when
# admission AND mission liveness are BOTH terminal. Watchers that
# include ``"mission_terminal"`` in their ``watch_events`` opt in to
# this dual-terminal semantic; default watchers (which subscribe to
# ``ALL_WATCHABLE_EVENTS`` minus ``"mission_terminal"``) preserve
# the existing transport-only behavior (back-compat).
#
# Gating lives in ``daemon/services/work_notifier.py::notify_work_watchers``
# — when ``"mission_terminal"`` is in a watcher's events list, the
# per-watcher filter requires the linked instance's canonical mission
# liveness to be terminal too. The default-omission means the
# ``ALL_WATCHABLE_EVENTS`` set stays transport-only by default — the
# new event is strictly opt-in (a caller who wants dual-terminal
# semantics adds ``"mission_terminal"`` explicitly).
#
# Mid-flight QA channel (2026-09-21): the four new non-terminal
# statuses ride ``ALL_WATCHABLE_EVENTS`` (above). They must NOT be
# added here — ``mission_live`` derivation is untouched by the QA
# channel; new kinds are non-mission-terminal by construction
# (work_notifier.py:357-389 derives mission liveness from the stored
# WorkRecord state, never from the input status).
ALL_MISSION_TERMINAL_WATCHABLE_EVENTS: list[str] = ALL_WATCHABLE_EVENTS + [
    "mission_terminal"
]


class JobWatcher(SQLModel, table=True):
    """Job watcher - subscribes to job lifecycle events.

    Allows an agent instance to receive notifications when a job reaches
    a terminal state.
    """
    __tablename__ = "job_watchers"
    __table_args__ = (
        UniqueConstraint("job_id", "instance_id", name="uq_job_watchers_job_instance"),
        Index("idx_job_watchers_job_id", "job_id"),
        Index("idx_job_watchers_instance_id", "instance_id"),
    )

    watch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True
    )

    # Phase 2 (Batch 1) of feature/virtual-job-management-surface:
    # the FK on ``job_id`` was REMOVED so ``job_watchers`` no longer
    # requires a matching ``job_queue_items.job_id`` row to exist. The
    # column is semantically a ``work_id`` (UUID4 string) — a virtual
    # job resolver correlates it with the appropriate Task/JobItem row
    # at notification time, instead of relying on a hard SQL FK that
    # blocked virtual (task-only) work from being watched. The SQLite
    # counterpart lives in
    # ``daemon/migrations/versions/20260627_000002_drop_job_watchers_fk.sql``;
    # the PostgreSQL counterpart lives in
    # ``daemon/manager.py::_ensure_postgres_columns`` (DROP CONSTRAINT).
    job_id: str = Field(index=True)

    # Foreign key to instances.instance_id
    instance_id: str = Field(foreign_key="instances.instance_id", index=True)

    # JSON list of job lifecycle events to watch for.
    # Default includes ALL events (terminal + in_progress progress updates).
    watch_events: list[str] = Field(
        default_factory=lambda: list(ALL_WATCHABLE_EVENTS),
        sa_column=Column(JSONBType)
    )

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
