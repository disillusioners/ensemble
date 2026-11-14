"""Pin tests for stgh-sweep-truth sub-fixes (P1-P4)."""

from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import MagicMock

import pytest

from daemon.constants import CHAT_WORKER_POOL_SIZE, WORKER_POOL_SIZE
from daemon.repositories.task.repository import TaskRepository
from daemon.services.eligible_pending_sweep import (
    DEFAULT_MIN_PENDING_AGE_SECONDS,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    EligiblePendingSweepService,
)
from daemon.services.pool_orchestrator import PoolOrchestrator
from daemon.services.worker_pool import WorkerPool


# ── P2: notify_work returns True ──────────────────────────────────────


def test_p2_notify_work_returns_true():
    # Need a TaskRepository for WorkerPool; use an in-memory-ish engine.
    # WorkerPool requires task_processor + num_workers; we only test the
    # notify seam, so a dummy task_processor is acceptable.
    pool = WorkerPool(task_processor=MagicMock(), num_workers=1)
    assert pool.notify_work() is True


def test_p2_notify_all_one_ok_one_raising():
    orch = PoolOrchestrator(manager=MagicMock())
    ok_pool = MagicMock()
    ok_pool.notify_work.return_value = None  # success, no raise
    raising_pool = MagicMock()
    raising_pool.notify_work.side_effect = RuntimeError("boom")
    orch._pools = [ok_pool, raising_pool]

    import logging as _logging

    logger = logging.getLogger("daemon.services.pool_orchestrator")
    records = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = _H(level=logging.WARNING)
    logger.addHandler(h)
    try:
        result = orch.notify_all()
    finally:
        logger.removeHandler(h)

    assert result == 1, f"expected 1 pulsed, got {result}"
    warning_msgs = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    assert any("raised" in m for m in warning_msgs), (
        "expected a WARNING naming 'raised' for the failing pool"
    )


# ── P1: telemetry truth ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p1_dispatched_true_on_real_signal(tmp_path):
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel
    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.job_queue.models  # noqa: F401
    import daemon.repositories.task.models  # noqa: F401

    db_path = tmp_path / "p1.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _cfg(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    SQLModel.metadata.create_all(eng)
    try:
        from datetime import datetime, timedelta, timezone

        def _seed():
            from daemon.repositories.task.models import Task, TaskStatus

            with __import__("sqlmodel").Session(eng) as s:
                t = Task(
                    instance_id="iid-p1",
                    status=TaskStatus.PENDING.value,
                    created_at=datetime.now(timezone.utc) - timedelta(seconds=120),
                    last_heartbeat_at=None,
                    is_deferred=False,
                )
                s.add(t)
                s.commit()

        _seed()

        worker_pool = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(eng),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )

        logger = logging.getLogger("daemon.services.eligible_pending_sweep")
        records = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _H(level=logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(h)
        try:
            stats = await service.sweep_once()
        finally:
            logger.removeHandler(h)

        assert stats["notified"] == 1
        info_msgs = " ".join(r.getMessage() for r in records)
        assert "dispatched=True" in info_msgs, (
            f"expected 'dispatched=True' in logs; got: {info_msgs}"
        )
    finally:
        eng.dispose()


@pytest.mark.asyncio
async def test_p1_failed_dispatch_logs_false_with_cause(tmp_path):
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel
    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.job_queue.models  # noqa: F401
    import daemon.repositories.task.models  # noqa: F401

    db_path = tmp_path / "p1b.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _cfg(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    SQLModel.metadata.create_all(eng)
    try:
        from datetime import datetime, timedelta, timezone

        def _seed():
            from daemon.repositories.task.models import Task, TaskStatus

            with __import__("sqlmodel").Session(eng) as s:
                t = Task(
                    instance_id="iid-p1b",
                    status=TaskStatus.PENDING.value,
                    created_at=datetime.now(timezone.utc) - timedelta(seconds=120),
                    last_heartbeat_at=None,
                    is_deferred=False,
                )
                s.add(t)
                s.commit()

        _seed()

        worker_pool = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        worker_pool.notify_work.side_effect = RuntimeError("pool blip")
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(eng),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
        )

        logger = logging.getLogger("daemon.services.eligible_pending_sweep")
        records = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _H(level=logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(h)
        try:
            stats = await service.sweep_once()
        finally:
            logger.removeHandler(h)

        assert stats["notified"] == 0
        msgs = " ".join(r.getMessage() for r in records)
        assert "dispatched=False" in msgs, (
            f"expected 'dispatched=False' in logs; got: {msgs}"
        )
        # real cause: either not-wired debug or 0-pulsed cause
        assert ("not wired" in msgs) or ("0 pools pulsed" in msgs), (
            f"expected a real cause in logs; got: {msgs}"
        )
    finally:
        eng.dispose()


# ── P3: repo limit ─────────────────────────────────────────────────────


def test_p3_repo_limit_oldest_n(tmp_path):
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel
    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.job_queue.models  # noqa: F401
    import daemon.repositories.task.models  # noqa: F401

    db_path = tmp_path / "p3.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _cfg(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    SQLModel.metadata.create_all(eng)
    try:
        from datetime import datetime, timedelta, timezone
        from daemon.repositories.task.models import Task, TaskStatus

        base = datetime.now(timezone.utc) - timedelta(seconds=300)
        with __import__("sqlmodel").Session(eng) as s:
            for i in range(5):
                t = Task(
                    instance_id=f"iid-{i}",
                    status=TaskStatus.PENDING.value,
                    created_at=base + timedelta(seconds=i),
                    last_heartbeat_at=None,
                    is_deferred=False,
                )
                s.add(t)
            s.commit()

        repo = TaskRepository(eng)
        rows = repo.list_pending_tasks_older_than(60, limit=2)
        assert len(rows) == 2, f"expected 2, got {len(rows)}"
        # oldest-first order
        assert rows[0].created_at <= rows[1].created_at

        all_rows = repo.list_pending_tasks_older_than(60)
        assert len(all_rows) == 5, f"expected all 5, got {len(all_rows)}"
    finally:
        eng.dispose()


# ── P4: W-D filter off-loop ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p4_wd_filter_off_loop(tmp_path):
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel
    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.job_queue.models  # noqa: F401
    import daemon.repositories.task.models  # noqa: F401

    db_path = tmp_path / "p4.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _cfg(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    SQLModel.metadata.create_all(eng)
    try:
        from datetime import datetime, timedelta, timezone
        from daemon.repositories.task.models import Task, TaskStatus

        with __import__("sqlmodel").Session(eng) as s:
            t = Task(
                instance_id="iid-p4",
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc) - timedelta(seconds=120),
                last_heartbeat_at=None,
                is_deferred=False,
            )
            s.add(t)
            s.commit()

        recorded = {"tid": None}

        def _fake_list(ids):
            recorded["tid"] = threading.get_ident()
            return set()

        instance_repo = MagicMock()
        instance_repo.list_paused_or_terminal_instance_ids = _fake_list

        worker_pool = MagicMock()
        service = EligiblePendingSweepService(
            task_repository=TaskRepository(eng),
            worker_pool=worker_pool,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            min_pending_age_seconds=DEFAULT_MIN_PENDING_AGE_SECONDS,
            instance_repository=instance_repo,
        )

        main_tid = threading.get_ident()
        await service.sweep_once()
        assert recorded["tid"] is not None
        assert recorded["tid"] != main_tid, (
            "W-D filter must run off the event loop (to_thread)"
        )
    finally:
        eng.dispose()


# sanity: constants imported
def test_constants_present():
    assert WORKER_POOL_SIZE == 5
    assert CHAT_WORKER_POOL_SIZE == 2
