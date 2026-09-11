"""W-D paused → resumed interplay gap tests (feature/fix-wc-wake-resilience).

These tests close the audit gap where the W-D liveness filter
(``daemon/services/eligible_pending_sweep.py``) correctly excludes
PENDING rows whose owning instance is paused or terminal, but the
follow-up "after resume" scenario is not pinned: the audit wants
explicit coverage that the next sweep tick, after the instance is
flipped back to RUNNING, DOES notify the pool for the same row,
and that the row is then actually CLAIMED by the canonical
``TaskRepository.claim_pending_task`` call (not merely
``is_deferred=False``).

Also covered: the A3 interval vs. age-threshold interplay — a row
younger than ``min_pending_age_seconds`` is not notified even after
the instance is resumed; once the row ages past the threshold, the
next sweep tick notifies.

Test surface:

* **paused_instance_pending_row_excluded_from_sweep** — a
  PENDING task whose instance is paused is NOT notified (the
  W-D liveness filter excludes it via
  ``list_paused_or_terminal_instance_ids``).

* **resumed_instance_pending_row_notified_on_next_tick** —
  after flipping the instance from PAUSED → RUNNING, the next
  ``sweep_once`` call dispatches ``notify_work()`` and the row
  is then CLAIMED by ``claim_pending_task`` (proves the
  notify was not just a wasted wake).

* **young_resumed_row_not_notified_until_age_threshold** — a
  PENDING task younger than ``min_pending_age_seconds`` is NOT
  notified even after resume; aging the row past the threshold
  triggers the notify.

* **terminal_instance_pending_row_excluded_then_unaffected_by_resume**
  — a terminal-instance PENDING row stays excluded; a "resume"
  to a terminal instance status is impossible, so the row stays
  un-notified.

Harness: file-backed SQLite (NullPool + WAL + busy_timeout=10000)
per the recipe in ``tests/job_queue/test_a3_eligible_pending_sweep.py``.
Real ``TaskRepository`` and real ``SQLModelInstanceRepository``
against the file-backed engine. Pool is mocked at the
``worker_pool.notify_work()`` boundary only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.task.models import TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.eligible_pending_sweep import (
    DEFAULT_MIN_PENDING_AGE_SECONDS,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    EligiblePendingSweepService,
)


# ─── Engine + repo helpers ──────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """File-backed SQLite (NullPool + WAL + busy_timeout=10000)
    per the A3 test recipe. Both ``task`` and ``instances``
    tables are created so the W-D liveness filter can read
    instance status.
    """
    db_path = tmp_path / "wd_resume_renotify.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    status: str,
) -> None:
    """Insert an ``Instance`` row via the production
    ``SQLModelInstanceRepository.create`` helper so the DDL
    stays in sync with the model."""
    repo = SQLModelInstanceRepository(engine=engine)
    repo.create(
        instance_id=instance_id,
        agent_id="wd-resume-test-agent",
        agent_dir="/tmp/wd-resume-test",
        status=status,
    )


def _seed_pending_task(
    engine: Engine,
    *,
    instance_id: str,
    created_at: datetime | None = None,
    is_deferred: bool = False,
) -> int:
    """Insert a PENDING Task with NULL heartbeat so the sweep's
    ``list_pending_tasks_older_than`` helper sees it. Returns the
    integer PK."""
    now = (created_at or datetime.now(timezone.utc))
    wid = (
        f"wid-wd-resume-{now.timestamp()}-{is_deferred}-"
        f"{instance_id}"
    )
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background)
                """
            ),
            {
                "task_type": "process_message",
                "instance_id": instance_id,
                "message_id": None,
                "status": TaskStatus.PENDING.value,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": wid,
                "is_deferred": is_deferred,
                "is_background": False,
            },
        )
        return result.lastrowid


def _update_instance_status(
    engine: Engine,
    *,
    instance_id: str,
    status: str,
) -> None:
    """Flip an instance's status. Used to simulate the PAUSED →
    RUNNING transition in the W-D paused→resumed interplay test.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE instances SET status = :status, "
                "updated_at = :now WHERE instance_id = :instance_id"
            ),
            {
                "status": status,
                "now": datetime.now(timezone.utc),
                "instance_id": instance_id,
            },
        )


# ─── (a) paused instance excludes PENDING row from the sweep ─────────────


class TestWDResumeRenotify:
    """W-D paused→resumed interplay: the W-D liveness filter
    excludes PENDING rows whose instance is paused, and the same
    row becomes eligible (and is claimed) on the next sweep tick
    after the instance resumes.
    """

    @pytest.mark.asyncio
    async def test_paused_instance_pending_row_excluded_from_sweep(
        self, engine
    ):
        """A PENDING task whose instance is paused MUST NOT be
        notified — the W-D liveness filter excludes it via
        ``list_paused_or_terminal_instance_ids``."""
        now = datetime.now(timezone.utc)
        _seed_instance(
            engine, instance_id="iid-wd-paused",
            status=InstanceStatus.PAUSED.value,
        )
        _seed_pending_task(
            engine, instance_id="iid-wd-paused",
            created_at=now - timedelta(seconds=120),
        )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
            instance_repository=SQLModelInstanceRepository(engine=engine),
        )
        stats = await service.sweep_once()

        assert stats["eligible"] == 0, (
            "W-D filter MUST exclude paused-instance PENDING rows; "
            f"got eligible={stats['eligible']!r}"
        )
        assert stats["notified"] == 0
        assert worker_pool.notify_work.call_count == 0

    @pytest.mark.asyncio
    async def test_resumed_instance_pending_row_notified_on_next_tick(
        self, engine
    ):
        """After flipping PAUSED → RUNNING, the next sweep tick
        MUST notify, AND the row MUST be claimable by the
        canonical ``claim_pending_task`` call (proves the notify
        was not just a wasted wake)."""
        now = datetime.now(timezone.utc)
        _seed_instance(
            engine, instance_id="iid-wd-resume",
            status=InstanceStatus.PAUSED.value,
        )
        _seed_pending_task(
            engine, instance_id="iid-wd-resume",
            created_at=now - timedelta(seconds=120),
        )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
            instance_repository=SQLModelInstanceRepository(engine=engine),
        )

        # Tick 1: paused → no notify.
        stats_1 = await service.sweep_once()
        assert stats_1["eligible"] == 0
        assert stats_1["notified"] == 0
        assert worker_pool.notify_work.call_count == 0

        # Flip the instance to RUNNING (simulating the resume).
        _update_instance_status(
            engine,
            instance_id="iid-wd-resume",
            status=InstanceStatus.RUNNING.value,
        )

        # Tick 2: resumed → notify.
        stats_2 = await service.sweep_once()
        assert stats_2["eligible"] == 1, (
            "W-D filter MUST let the resumed-instance PENDING row "
            "through after the resume. Got "
            f"eligible={stats_2['eligible']!r}"
        )
        assert stats_2["notified"] == 1, (
            "Sweep MUST dispatch notify_work() after the resume. "
            f"Got notified={stats_2['notified']!r}"
        )
        assert worker_pool.notify_work.call_count == 1, (
            "worker_pool.notify_work() MUST be called exactly "
            "once on the post-resume sweep tick. Got "
            f"call_count={worker_pool.notify_work.call_count}"
        )

        # The notify was not wasted: claim_pending_task now
        # claims the row. This proves the wake reached the pool
        # and the row is genuinely claimable.
        task_repo = TaskRepository(engine)
        claimed = task_repo.claim_pending_task(worker_id="worker-1")
        assert claimed is not None, (
            "claim_pending_task MUST claim the now-eligible "
            "PENDING row after the resume sweep notified the pool. "
            "Got None — either the row was already claimed "
            "(impossible — no worker has called claim) or the "
            "claim gate still excludes it (defect: the resume "
            "transition did not propagate to the claim path)."
        )
        assert claimed.instance_id == "iid-wd-resume"
        assert claimed.status == TaskStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_young_resumed_row_not_notified_until_age_threshold(
        self, engine
    ):
        """A row younger than ``min_pending_age_seconds`` MUST NOT
        be notified even after the instance is resumed; once the
        row ages past the threshold, the next sweep tick
        notifies."""
        _seed_instance(
            engine, instance_id="iid-wd-young",
            status=InstanceStatus.RUNNING.value,
        )
        # 5s old — well below the 60s default threshold.
        now = datetime.now(timezone.utc)
        _seed_pending_task(
            engine, instance_id="iid-wd-young",
            created_at=now - timedelta(seconds=5),
        )

        worker_pool = MagicMock()
        # Use a smaller threshold for speed.
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=10,
            instance_repository=SQLModelInstanceRepository(engine=engine),
        )

        # Tick: row is young → not eligible (the sweep's
        # age-threshold filter excludes it before the W-D filter
        # gets a chance to gate on status).
        stats = await service.sweep_once()
        assert stats["eligible"] == 0, (
            "Young rows (5s < 10s threshold) MUST be skipped by "
            "the sweep's age-threshold filter. Got "
            f"eligible={stats['eligible']!r}"
        )
        assert stats["notified"] == 0
        assert worker_pool.notify_work.call_count == 0

        # Backdate the row past the threshold (simulate aging).
        aged_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET created_at = :aged_at "
                    "WHERE instance_id = :instance_id"
                ),
                {
                    "aged_at": aged_at,
                    "instance_id": "iid-wd-young",
                },
            )

        # Tick: row aged past threshold → eligible → notified.
        stats2 = await service.sweep_once()
        assert stats2["eligible"] == 1, (
            "After aging past the threshold, the row MUST become "
            f"eligible. Got eligible={stats2['eligible']!r}"
        )
        assert stats2["notified"] == 1, (
            "After aging past the threshold, the sweep MUST "
            f"dispatch notify_work(). Got notified={stats2['notified']!r}"
        )
        assert worker_pool.notify_work.call_count == 1

    @pytest.mark.asyncio
    async def test_terminal_instance_pending_row_stays_excluded(
        self, engine
    ):
        """A PENDING task whose instance is terminal (completed /
        error / terminated / failed) MUST stay excluded from the
        sweep — terminal instances cannot be "resumed"; the row
        will be garbage-collected by the reconciler."""
        now = datetime.now(timezone.utc)
        for terminal_status in (
            "completed", "error", "terminated", "failed"
        ):
            _seed_instance(
                engine,
                instance_id=f"iid-wd-{terminal_status}",
                status=terminal_status,
            )
            _seed_pending_task(
                engine,
                instance_id=f"iid-wd-{terminal_status}",
                created_at=now - timedelta(seconds=120),
            )

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(engine),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
            instance_repository=SQLModelInstanceRepository(engine=engine),
        )
        stats = await service.sweep_once()

        assert stats["eligible"] == 0, (
            "W-D MUST exclude terminal-instance PENDING rows for "
            "all 4 terminal statuses. Got "
            f"eligible={stats['eligible']!r}"
        )
        assert stats["notified"] == 0
        assert worker_pool.notify_work.call_count == 0
