"""Execution lease models for the Execution Gate.

The Execution Gate (see ``daemon/services/execution_gate.py``) is the single
owner of ``graph.astream`` per ``thread_id`` (== ``instance_id``). To prevent
the dual-dispatcher checkpoint race documented in
``docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md``,
every call to ``graph.astream`` for an instance must hold a DB-backed lease
on that instance. Two dispatchers (JobQueue's MessageJobHandler and
WorkerPool's ProcessMessageProcessor) cannot both hold a lease for the same
instance at the same time, so concurrent calls are serialized at the lease
layer instead of racing on the langgraph checkpoint.

Schema (see ``daemon/migrations/versions/20260614_000002_create_instance_execution_leases.sql``):

- instance_id: PK. The langgraph thread_id == instance_id.
- holder_id: A unique token for the current holder. Format:
    "{holder_kind}:{entity_id}"
  e.g. "message_job:abc-123", "task:42", "resume:def-456".
  Used in the conditional DELETE on release to prevent a stale loser
  from accidentally stealing the lease after the winner releases it.
- holder_kind: One of "message_job", "task", "resume". Used by contention
  handlers to know how to back-transition the loser (e.g. message_job
  needs atomic_transition(processing -> pending); task needs to leave
  the row in 'running' and let the worker see the lease released on
  its next heartbeat).
- acquired_at: Wallclock when the lease was acquired.
- heartbeat_at: Updated periodically by the holder (currently piggybacks
  on the caller; future enhancement could be a dedicated heartbeat).
  Used by stale-lease recovery to distinguish a live lease from a
  crashed process that died holding the lease.
- process_id: OS PID of the holding process. Diagnostic only — crash
  recovery does NOT rely on this (heartbeat staleness is the canonical
  signal). Listed for visibility in debugging.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field


class LeaseHolderKind(str, enum.Enum):
    """What kind of dispatcher currently holds the execution lease.

    Note on ``RESUME``: the resume path is planned to drive
    ``graph.astream`` directly when a paused instance is resumed,
    and will hold an execution lease so it cannot race with a
    worker task that picked up a queued message for the same
    instance. The enum member is included now so the DB CHECK
    constraint does not need to be migrated when the resume path
    lands. No code path currently produces a ``RESUME`` lease.
    """

    MESSAGE_JOB = "message_job"
    TASK = "task"
    RESUME = "resume"  # planned: paused-instance resume path


class InstanceExecutionLease(SQLModel, table=True):
    """Per-instance execution lease held by whichever dispatcher is currently
    driving ``graph.astream`` for the instance.

    One row per instance, with the current holder's identity. Lease
    acquisition is atomic (``INSERT ... ON CONFLICT DO NOTHING`` /
    ``INSERT OR IGNORE``); release is atomic and conditional on
    ``holder_id`` matching so a stale loser cannot accidentally delete
    a winner's lease.
    """

    __tablename__ = "instance_execution_leases"

    instance_id: str = Field(primary_key=True)
    holder_id: str = Field(index=True)
    holder_kind: str = Field(default=LeaseHolderKind.MESSAGE_JOB.value, index=True)
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    process_id: int | None = Field(default=None)
